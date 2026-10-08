"""
Task 9: realtime conversational interview -- server side.

The REAL RealtimeInterviewSession, the REAL stable publisher, the REAL routes and (in the integration
tests) the REAL interview records + brain run against an isolated in-memory database; only the realtime
model connection, the LiveKit SDK and Groq's HTTP endpoint are faked. These tests prove provider
selection, context injection, once-only recording, interruption, completion, reconnect handling,
security and HR-record compatibility -- not model quality (the live run is separate). No jobpilot.db,
no network, no Gemini call.
"""

import asyncio
import json
import os
import sys
import time
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_voice_turn_local_pipeline_v2 as VT
import test_livekit_transport_v2 as LT

from livekit import rtc as real_rtc

from backend.app.api.deps import get_current_user
from backend.app.api.routes import interview_realtime_v2 as rt_route
from backend.app.database.database import get_db
from backend.app.services.assessment import interview_llm_provider as prov
from backend.app.services.assessment import livekit_audio_transport_v2 as transport
from backend.app.services.assessment import realtime_interview_v2 as rt

H = {"X-CSRF-Token": "t"}
ENV = ("INTERVIEW_REALTIME_PROVIDER", "GEMINI_API_KEY", "AI_REALTIME_MODEL")


def _clean_env():
    for k in ENV:
        os.environ.pop(k, None)
    rt._sessions.clear()


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ fakes
class FakeConn:
    def __init__(self, fail_after=None):
        self.q = asyncio.Queue()
        self.audio, self.notes, self.closed = [], [], False
        self.fail_after, self._n = fail_after, 0

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def send_note(self, text, turn_complete=False):
        self.notes.append((text, turn_complete))

    async def events(self):
        while True:
            item = await self.q.get()
            if isinstance(item, Exception):
                raise item
            if item is None:
                return
            self._n += 1
            yield item

    async def close(self):
        self.closed = True

    def feed(self, kind, data=None):
        self.q.put_nowait(rt.LiveEvent(kind, data))


class FakePublisher:
    def __init__(self):
        self.log, self.seq, self.cb, self.streams = [], 0, None, {}
        self.active = None

    async def start_listening(self, cb):
        self.cb = cb
        self.log.append("listen")

    async def prepare_audio_track(self):
        self.log.append("prepare")

    async def stream_begin(self):
        self.seq += 1
        self.active = self.seq
        self.streams[self.seq] = {"pcm": b"", "ended": False}
        self.log.append(("begin", self.seq))
        return self.seq

    async def stream_push(self, seq, pcm):
        if seq != self.active:
            return False
        self.streams[seq]["pcm"] += pcm
        return True

    async def stream_end(self, seq, close_after=False):
        self.streams[seq]["ended"] = True
        self.log.append(("end", seq))

    async def interrupt(self):
        self.log.append("interrupt")
        self.active = None
        return True

    async def send_state(self, state, **extra):
        self.log.append(("state", state))


class FakeWork:
    def __init__(self, complete_at=None, plan=None):
        self.answers, self.spoken, self.complete_at = [], [], complete_at
        self.plan = plan or rt.InterviewPlan(instruction="INSTRUCTION-TEXT", opening_message="Hi Arjun", first_question="Tell me about your Hospital Management System.", max_turns=10)

    def load_plan(self):
        return self.plan

    def record_answer(self, text):
        self.answers.append(text)
        n = len(self.answers)
        if self.complete_at and n >= self.complete_at:
            return {"interview_complete": True, "note": "PRIVATE NOTE: close the interview", "turn_number": None}
        return {"interview_complete": False, "note": f"PRIVATE NOTE: question {n + 1}", "turn_number": n + 1}

    def record_ai_question(self, text):
        self.spoken.append(text)
        return len(self.spoken)


async def _make(work=None, conn=None, lead=0.0):
    conn = conn or FakeConn()
    pub = FakePublisher()
    conns = [conn]

    async def factory(instruction, handle):
        factory.calls.append((instruction, handle))
        return conns.pop(0) if conns else FakeConn()
    factory.calls = []
    session = rt.RealtimeInterviewSession(7, connection_factory=factory, publisher=pub, work=work or FakeWork(), opening_lead_seconds=lead)
    return session, conn, pub, factory, conns


async def _settle(n=6):
    for _ in range(n):
        await asyncio.sleep(0.02)


PCM = b"\x01\x02" * 480


# ------------------------------------------------------------ provider selection
def test_provider_selection_default_realtime_and_explicit_pipelines():
    _clean_env()
    assert rt.get_realtime_provider() == "groq_pipeline"                    # default: the existing pipeline
    for value, expected in (("realtime", "realtime"), (" REALTIME ", "realtime"), ("groq_pipeline", "groq_pipeline"),
                            ("gemini_pipeline", "gemini_pipeline"), ("gemini", "groq_pipeline"), ("", "groq_pipeline")):
        os.environ["INTERVIEW_REALTIME_PROVIDER"] = value
        assert rt.get_realtime_provider() == expected, value
    _clean_env()


def test_pipeline_providers_pin_the_brain_and_realtime_leaves_it_alone_with_no_automatic_fallback():
    _clean_env()
    saved_provider = os.environ.get("AI_INTERVIEW_PROVIDER")
    os.environ["AI_INTERVIEW_PROVIDER"] = "gemini"
    assert prov.get_interview_llm_provider().name == "gemini"               # nothing pinned: the explicit AI_INTERVIEW_PROVIDER rules
    os.environ["INTERVIEW_REALTIME_PROVIDER"] = "groq_pipeline"
    assert prov.get_interview_llm_provider().name == "groq"
    os.environ["INTERVIEW_REALTIME_PROVIDER"] = "realtime"
    assert prov.get_interview_llm_provider().name == "gemini"               # realtime does not touch the analysis brain choice
    if saved_provider is None:
        os.environ.pop("AI_INTERVIEW_PROVIDER", None)
    else:
        os.environ["AI_INTERVIEW_PROVIDER"] = saved_provider
    _clean_env()


def test_realtime_needs_a_server_side_key_and_model_is_configurable():
    _clean_env()
    assert rt.is_realtime_configured() is False
    os.environ["GEMINI_API_KEY"] = "x"
    assert rt.is_realtime_configured() is True and rt.get_realtime_model() == "gemini-3.1-flash-live-preview"
    os.environ["AI_REALTIME_MODEL"] = "some-other-live-model"
    assert rt.get_realtime_model() == "some-other-live-model"
    _clean_env()


# ------------------------------------------------------------ context injection
def test_the_instruction_carries_the_real_job_candidate_evidence_and_the_rules():
    from backend.app.schemas.interview_context_v2 import AptitudeEvidenceV2, CandidateContextV2, InterviewContextV2, JobContextV2, ProgrammingEvidenceSummaryV2
    from backend.app.services.assessment.interview_context_builder_v2 import build_interview_blueprint_v2
    ctx = InterviewContextV2(
        job=JobContextV2(title="Python Backend Developer", company="JobPilot Test Technologies", description="Build REST APIs with FastAPI and PostgreSQL.",
                         required_skills="Python, FastAPI, REST APIs, PostgreSQL, SQL, Authentication, Git", responsibilities="Design SQL schemas; implement authentication",
                         requirements="JWT/session authentication", technologies="Python, FastAPI, PostgreSQL, Git", employment_type="Full-time", work_mode="Remote"),
        candidate=CandidateContextV2(full_name="Arjun", experience_level="Entry Level", education="B.Sc Computer Science, 2025", skills=["Python", "FastAPI", "PostgreSQL"],
                                     resume_summary="Junior backend developer.", resume_projects_or_experience=["Hospital Management System (project): Python, FastAPI, PostgreSQL",
                                                                                                                 "Software Development Intern (6 months)"],
                                     profile_projects="Hospital Management System using Python/FastAPI/PostgreSQL"),
        aptitude=AptitudeEvidenceV2(available=True, percentage=100.0, passed=True, category_results={"logical_reasoning": "strong"}),
        programming=ProgrammingEvidenceSummaryV2(available=True, strengths=["Balanced Brackets"], languages_used=["python"], highest_difficulty_passed="hard"),
    )
    text = rt.build_realtime_instruction(ctx, build_interview_blueprint_v2(ctx), "Hi Arjun", "Tell me about the Hospital Management System you built.", 8)
    for needle in ("Python Backend Developer", "JobPilot Test Technologies", "Design SQL schemas", "JWT/session authentication", "Python, FastAPI, PostgreSQL, Git",
                   "B.Sc Computer Science", "Hospital Management System", "Software Development Intern", "Balanced Brackets", "logical_reasoning",
                   "FastAPI (priority: high", "PRIVATE NOTE", "exactly 8 questions", "Tell me about the Hospital Management System you built.",
                   "Never invent facts", "interrupts you"):
        assert needle in text, needle
    assert "GEMINI" not in text and "api_key" not in text.lower()


# ------------------------------------------------------------ session behaviour
def test_start_opens_the_model_with_the_plan_listens_and_speaks_the_persisted_first_question():
    async def go():
        session, conn, pub, factory, _ = await _make(lead=0.05)
        await session.start()
        assert session.state == "ready" and factory.calls == [("INSTRUCTION-TEXT", None)]
        assert pub.log[:2] == ["listen", "prepare"]                          # subscribed + track published BEFORE the first word
        assert conn.notes == []                                              # the opening waits for the lead time
        await asyncio.sleep(0.15)
        (text, turn_complete), = conn.notes
        assert turn_complete is True and "Tell me about your Hospital Management System." in text
        await session.close()
    _run(go())


def test_candidate_microphone_audio_is_forwarded_and_only_while_the_interview_runs():
    async def go():
        session, conn, pub, *_ = await _make()
        await session.start()
        await pub.cb(PCM); await pub.cb(PCM)                                 # 2 x 960 bytes arrive as 10 ms-ish frames...
        assert b"".join(conn.audio) == PCM + PCM and all(len(a) == 640 for a in conn.audio)   # ...and go out in 20 ms chunks
        n = len(conn.audio)
        await session.close()
        await pub.cb(PCM)
        assert len(conn.audio) == n                                          # nothing after close
    _run(go())


def test_ai_audio_is_streamed_once_per_utterance_in_order_and_announced():
    async def go():
        session, conn, pub, *_ = await _make()
        await session.start()
        for chunk in (b"AAAA", b"BBBB", b"CCCC"):
            conn.feed("audio", chunk)
        conn.feed("output_text", "Hello Arjun, ")
        conn.feed("output_text", "tell me about your project.")
        conn.feed("turn_complete")
        await _settle()
        assert [e for e in pub.log if isinstance(e, tuple) and e[0] in ("begin", "end")] == [("begin", 1), ("end", 1)]
        assert pub.streams[1]["pcm"] == b"AAAABBBBCCCC" and pub.streams[1]["ended"]
        await session.close()
    _run(go())


def test_each_candidate_answer_is_recorded_exactly_once_and_the_model_is_armed_with_its_next_step():
    async def go():
        work = FakeWork()
        session, conn, pub, *_ = await _make(work)
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "Welcome, tell me about your project."); conn.feed("turn_complete")   # opening
        await _settle()
        conn.feed("input_text", "I built a REST API "); conn.feed("input_text", "using FastAPI and PostgreSQL.")
        await _settle()
        assert ("state", "candidate_speaking") in pub.log
        conn.feed("audio", PCM)                                              # the model starts answering -> the candidate's turn is over
        conn.feed("output_text", "How did you handle authentication?"); conn.feed("turn_complete")
        await _settle(10)
        assert work.answers == ["I built a REST API using FastAPI and PostgreSQL."]
        assert ("PRIVATE NOTE: question 2", False) not in conn.notes         # the brain's after-the-fact hint is no longer sent: it arrived after the model had begun to reply
        armed = [text for text, turn_complete in conn.notes if text.startswith("NEXT STEP") and not turn_complete]
        assert armed and "question 2 of 10" in armed[-1]                      # the scripted next step was sent BEFORE the answer, as a note that forces no response
        assert work.spoken == ["How did you handle authentication?"]        # what was actually said is written back
        await session.close()
    _run(go())


def test_the_opening_is_never_overwritten_and_backchannels_are_not_interview_turns():
    async def go():
        work = FakeWork()
        session, conn, pub, *_ = await _make(work)
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "Hi, welcome. Tell me about your project."); conn.feed("turn_complete")
        await _settle()
        assert work.spoken == []                                             # the persisted first question stays as JobPilot wrote it
        conn.feed("input_text", "hmm yes")
        conn.feed("audio", PCM); conn.feed("output_text", "Could you say a bit more about that?"); conn.feed("turn_complete")
        await _settle(10)
        assert work.answers == [] and session.stats["skipped_noise"] == 1
        assert work.spoken == ["Could you say a bit more about that?"]      # the clarification replaces the open turn's question
        await session.close()
    _run(go())


def test_interruption_stops_the_ai_audio_at_once_and_the_next_utterance_is_a_new_stream():
    async def go():
        session, conn, pub, *_ = await _make(FakeWork())
        await session.start()
        conn.feed("audio", b"AAAA"); conn.feed("output_text", "So, tell me about the")
        await _settle()
        assert pub.active == 1
        await pub.cb(_loud(50))                                              # the candidate is genuinely speaking (short of the local barge-in threshold)
        t = time.perf_counter()
        conn.feed("interrupted")                                             # ...and the model reports the interruption
        for _ in range(100):
            if "interrupt" in pub.log:
                break
            await asyncio.sleep(0.005)
        assert "interrupt" in pub.log and time.perf_counter() - t < 0.2 and pub.active is None          # audio stopped immediately
        conn.feed("input_text", "wait, I meant something else entirely")
        conn.feed("audio", b"ZZZZ"); conn.feed("output_text", "Of course, go ahead."); conn.feed("turn_complete")
        await _settle(10)
        assert pub.streams[1]["pcm"] == b"AAAA" and not pub.streams[1]["ended"]     # the cancelled utterance never resumes
        assert pub.streams[2]["pcm"] == b"ZZZZ" and session.stats["interruptions"] == 1
        await session.close()
    _run(go())


def test_completion_closes_the_interview_after_the_goodbye_and_the_session_ends():
    async def go():
        work = FakeWork(complete_at=1)
        session, conn, pub, *_ = await _make(work)
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "Opening question here please."); conn.feed("turn_complete")
        await _settle()
        conn.feed("input_text", "I built a REST API using FastAPI and PostgreSQL.")
        conn.feed("audio", PCM); conn.feed("output_text", "One more thing: any tests?")           # the model was already mid-reply
        await _settle(8)
        assert not any("close the interview" in n[0] for n in conn.notes)    # not yet: it finishes the sentence it is on
        conn.feed("turn_complete")
        await _settle(8)
        (close_note,) = [n for n in conn.notes if "close the interview" in n[0]]
        assert close_note[1] is True                                         # this one DOES trigger the goodbye
        conn.feed("audio", PCM); conn.feed("output_text", "Thank you, the interview is complete."); conn.feed("turn_complete")
        await _settle(100)          # closing now genuinely waits for the goodbye audio to finish playing (~0.6s margin + _close_soon's 0.5s)
        assert ("state", "complete") in pub.log and session.state == "closed" and conn.closed
        assert work.spoken == []                                             # after completion there is no open turn to rewrite, and the goodbye is never stored as a question
    _run(go())


def test_a_lost_connection_is_retried_with_the_resumption_handle_then_reported_without_any_other_provider():
    async def go():
        session, conn, pub, factory, conns = await _make()
        second = FakeConn(); conns.append(second)
        await session.start()
        conn.feed("resumption", "HANDLE-1")
        conn.q.put_nowait(ConnectionError("socket dropped"))
        await _settle(10)
        assert factory.calls[1][1] == "HANDLE-1" and session.stats["reconnects"] == 1 and session.state == "ready"
        # keeps working on the new connection
        second.feed("audio", PCM); await _settle()
        assert ("begin", 1) in pub.log
        # exhaust the retry budget
        for _ in range(3):
            live = session.conn
            live.q.put_nowait(ConnectionError("again"))
            conns.append(FakeConn())
            await _settle(10)
        assert session.state == "failed" and "connection" in session.error.lower()
        assert ("state", "error") in pub.log
    _run(go())


def test_startup_failure_is_an_honest_failed_state_and_never_switches_provider():
    async def go():
        async def broken(instruction, handle):
            raise rt.LiveConnectionError("Could not open the realtime session (TimeoutError).")
        pub = FakePublisher()
        session = rt.RealtimeInterviewSession(9, connection_factory=broken, publisher=pub, work=FakeWork(), opening_lead_seconds=0)
        await session.start()
        assert session.state == "failed" and "realtime session" in session.error
        assert pub.log == [("state", "error")]                               # only the honest error notice: no listening, no track, nothing else started
    _run(go())


# ------------------------------------------------------------ real publisher: streaming + listening
class _Rtc:
    def __init__(self):
        world = self
        self.published, self.unpublished, self.data, self.frames, self.cleared, self.subscribed, self.connect_options = [], [], [], [], 0, [], []
        self.pending_streams = {}

        class LocalParticipant:
            async def publish_track(self, track, options):
                world.published.append(track); return types.SimpleNamespace(sid=f"TR_{len(world.published)}")

            async def unpublish_track(self, sid): world.unpublished.append(sid)

            async def publish_data(self, payload, reliable=True, destination_identities=None, topic=""):
                world.data.append(json.loads(payload))

        class Room:
            def __init__(self):
                self.local_participant = LocalParticipant(); self.handlers = {}; self.remote_participants = {}; self.disconnects = 0
                world.room = self

            def on(self, name, fn): self.handlers[name] = fn

            async def connect(self, url, token, options=None): world.connect_options.append(options)

            async def disconnect(self): self.disconnects += 1

        class AudioSource:
            def __init__(self, rate, channels): pass

            async def capture_frame(self, frame):
                world.frames.append(bytes(frame.data)); await asyncio.sleep(0)

            def clear_queue(self): world.cleared += 1

            async def wait_for_playout(self): pass

        class AudioFrame:
            @staticmethod
            def create(rate, channels, samples):
                import array
                return types.SimpleNamespace(data=memoryview(array.array("h", [0] * (samples * channels))))

        class AudioStream:
            def __init__(self, track, sample_rate=None, num_channels=None):
                self.track = track

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for pcm in self.track.frames:
                    yield types.SimpleNamespace(frame=types.SimpleNamespace(data=pcm))
                    await asyncio.sleep(0)

            async def aclose(self): pass

        self.module = types.SimpleNamespace(
            Room=Room, AudioSource=AudioSource, AudioFrame=AudioFrame, AudioStream=AudioStream,
            RoomOptions=lambda auto_subscribe=True: types.SimpleNamespace(auto_subscribe=auto_subscribe),
            LocalAudioTrack=types.SimpleNamespace(create_audio_track=lambda name, source: types.SimpleNamespace(name=name, source=source)),
            TrackPublishOptions=lambda source: types.SimpleNamespace(source=source),
            TrackSource=types.SimpleNamespace(SOURCE_MICROPHONE="SOURCE_MICROPHONE"),
            TrackKind=real_rtc.TrackKind,
        )


class _Env:
    def __enter__(self):
        LT._configure(livekit=True, avatar=False)
        self.fake = _Rtc()
        self.saved = transport.rtc
        transport.rtc = self.fake.module
        transport._publishers.clear()
        return self.fake

    def __exit__(self, *a):
        transport.rtc = self.saved
        transport._publishers.clear()
        for k in LT.ENV_KEYS:
            os.environ.pop(k, None)


def _tok(a):
    return {"url": "wss://x", "token": "t"}


def test_streaming_writes_frames_in_order_announces_started_and_ended_and_flushes_the_tail():
    async def go():
        p = await transport.get_publisher(81, _tok)
        seq = await p.stream_begin()
        pcm = bytes(range(256)) * 4                    # 1024 bytes = 2 full 10ms frames (480B) + a 64B tail
        assert await p.stream_push(seq, pcm[:300]) and await p.stream_push(seq, pcm[300:])
        await p.stream_end(seq)
        await asyncio.sleep(0.05)
        return seq, pcm
    with _Env() as fake:
        seq, pcm = _run(go())
    assert seq == 1 and len(fake.frames) == 3 and all(len(f) == 480 for f in fake.frames)
    assert b"".join(fake.frames)[:len(pcm)] == pcm and set(b"".join(fake.frames)[len(pcm):]) <= {0}     # the exact PCM, then zero padding
    assert [(m["state"], m["seq"]) for m in fake.data] == [("started", 1), ("ended", 1)]
    assert len(fake.published) == 1


def test_interrupting_a_stream_clears_the_queue_announces_it_and_drops_later_chunks_of_that_utterance():
    async def go():
        p = await transport.get_publisher(82, _tok)
        s1 = await p.stream_begin()
        await p.stream_push(s1, b"\x01\x00" * 480)
        await p.interrupt()
        late = await p.stream_push(s1, b"\x02\x00" * 480)                    # the model kept sending after the cut
        s2 = await p.stream_begin()                                          # next utterance
        await p.stream_push(s2, b"\x03\x00" * 480)
        await p.stream_end(s2)
        await asyncio.sleep(0.05)
        return s1, s2, late, p
    with _Env() as fake:
        s1, s2, late, p = _run(go())
    assert late is False and (s1, s2) == (1, 2) and fake.cleared >= 1 and len(fake.published) == 1     # ONE track for every utterance
    assert [(m["state"], m["seq"], m.get("interrupted")) for m in fake.data] == [("started", 1, None), ("ended", 1, True), ("started", 2, None), ("ended", 2, False)]
    assert not any(f[:2] == b"\x02\x00" for f in fake.frames)               # the stale audio never reached the track


def test_the_publisher_only_subscribes_to_the_candidates_microphone_and_forwards_16k_pcm():
    async def go():
        p = await transport.get_publisher(83, listen_to_candidate=True)
        got = []

        async def cb(pcm): got.append(pcm)
        await p.start_listening(cb)
        room = p._room
        audio_pub = lambda: types.SimpleNamespace(kind=real_rtc.TrackKind.KIND_AUDIO, set_subscribed=lambda v: subs.append(v))
        video_pub = lambda: types.SimpleNamespace(kind=real_rtc.TrackKind.KIND_VIDEO, set_subscribed=lambda v: subs.append("VIDEO"))
        subs = []
        room.handlers["track_published"](audio_pub(), types.SimpleNamespace(identity="candidate-5"))
        room.handlers["track_published"](video_pub(), types.SimpleNamespace(identity="candidate-5"))       # the camera is NOT subscribed
        room.handlers["track_published"](audio_pub(), types.SimpleNamespace(identity="someone-else"))      # nobody else is
        track = types.SimpleNamespace(kind=real_rtc.TrackKind.KIND_AUDIO, frames=[b"\x01\x00" * 320, b"\x02\x00" * 320])
        room.handlers["track_subscribed"](track, None, types.SimpleNamespace(identity="candidate-5"))
        await asyncio.sleep(0.1)
        return subs, got, p
    with _Env() as fake:
        subs, got, p = _run(go())
    assert subs == [True] and got == [b"\x01\x00" * 320, b"\x02\x00" * 320]
    assert fake.connect_options[0].auto_subscribe is False                   # manual subscription: never the camera video


def test_a_normal_publisher_cannot_listen_and_its_token_stays_deaf_unless_realtime_asks():
    import jwt
    LT._configure(livekit=True, avatar=False)
    from backend.app.services.assessment import avatar_token_service_v2 as tokens
    deaf = jwt.decode(tokens.generate_audio_publisher_token_v2(5)["token"], LT.SECRET, algorithms=["HS256"])["video"]
    hears = jwt.decode(tokens.generate_audio_publisher_token_v2(5, subscribe_candidate_audio=True)["token"], LT.SECRET, algorithms=["HS256"])["video"]
    assert not deaf.get("canSubscribe") and hears["canSubscribe"] is True
    assert hears["canPublishSources"] == ["microphone"] and hears["room"] == "jobpilot-interview-v2-5"       # still one room, mic-only publishing
    async def go():
        p = await transport.get_publisher(84)
        try:
            await p.start_listening(lambda pcm: None)
        except transport.AudioTransportError as exc:
            return str(exc)
    with _Env():
        assert "not allowed to listen" in _run(go())
    for k in LT.ENV_KEYS:
        os.environ.pop(k, None)


# ------------------------------------------------------------ routes
def _client(db, holder):
    api = FastAPI()
    api.include_router(rt_route.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: holder["user"]
    return TestClient(api, cookies={"csrf_token": "t"})


def _u(app_id, tail):
    return f"/api/assessment/sessions/{app_id}/interview/v2/realtime/{tail}"


def test_routes_are_disabled_by_default_and_start_nothing():
    _clean_env()
    LT._configure(livekit=True, avatar=False)
    db, user, app, session, stranger = LT._fixture()
    with _client(db, {"user": user}) as c:
        r = c.post(_u(app.id, "start"), headers=H)
        st = c.get(_u(app.id, "status")).json()
    assert r.json() == {"state": "disabled", "provider": "groq_pipeline", "error": None} and st["state"] == "disabled"
    assert rt._sessions == {}
    for k in LT.ENV_KEYS:
        os.environ.pop(k, None)


def test_routes_enforce_csrf_ownership_active_interview_and_configuration_in_realtime_mode():
    _clean_env()
    LT._configure(livekit=True, avatar=False)
    os.environ["INTERVIEW_REALTIME_PROVIDER"] = "realtime"
    db, user, app, session, stranger = LT._fixture()
    started = []

    async def fake_start(application_id, **kw):
        started.append(application_id)
        return types.SimpleNamespace(status=lambda: {"state": "ready", "error": None, "provider": "realtime", "stats": {}})

    saved = rt.start_realtime_session
    rt.start_realtime_session = fake_start
    try:
        with _client(db, {"user": user}) as c:
            assert c.post(_u(app.id, "start")).status_code == 403                                     # CSRF
            assert c.post(_u(app.id, "start"), headers=H).status_code == 503                          # no GEMINI key yet
            os.environ["GEMINI_API_KEY"] = "server-side-secret-value"
            r = c.post(_u(app.id, "start"), headers=H)
            assert r.status_code == 200 and r.json()["state"] == "ready" and "server-side-secret-value" not in r.text
        with _client(db, {"user": stranger}) as c:
            assert c.post(_u(app.id, "start"), headers=H).status_code == 404                          # not their interview
            assert c.get(_u(app.id, "status")).status_code == 404
            assert c.post(_u(app.id, "stop"), headers=H).status_code == 404
        session.status = "completed"; db.commit()
        with _client(db, {"user": user}) as c:
            assert c.post(_u(app.id, "start"), headers=H).status_code == 409                          # no active interview
    finally:
        rt.start_realtime_session = saved
        _clean_env()
        for k in LT.ENV_KEYS:
            os.environ.pop(k, None)
    assert started == [app.id]                                                                       # exactly one start, for the owner only


def test_no_route_response_or_frontend_file_can_contain_a_provider_key():
    root = os.path.join(os.path.dirname(__file__), "..", "frontend")
    for rel in ("js/ai-interview-v2.js", "js/interview-voice-v2.js", "js/interview-avatar-v2.js", "ai-interview-v2.html"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "GEMINI_API_KEY" not in src and "AIza" not in src and "GROQ_API_KEY" not in src, rel


# ------------------------------------------------------------ integration with the REAL interview records + brain
def _work_fixture():
    db = VT._db(shared=True)
    user, app, session = VT._interview(db)
    return db, user, app, session, rt.DbRealtimeWork(app.id, session_factory=lambda: db)


def test_plan_comes_from_the_persisted_interview_and_the_persisted_first_question_is_spoken():
    with VT._isolated(VT.FakeGroq(None)):
        db, user, app, session, work = _work_fixture()
        plan = work.load_plan()
    first = VT._competencies(session)
    assert plan.max_turns == 10 and "Backend Developer" in plan.instruction and "TestCo" in plan.instruction
    assert plan.first_question in plan.instruction and plan.turn_number == 1 and first[0] in plan.instruction


def test_recorded_answers_use_the_existing_brain_and_records_and_the_spoken_question_is_written_back():
    db = VT._db(shared=True)
    user, app, session = VT._interview(db)
    work = rt.DbRealtimeWork(app.id, session_factory=lambda: db)
    with VT._isolated(VT.FakeGroq(session)):
        result = work.record_answer("I built a REST API using Python and FastAPI. I implemented authentication and connected it to PostgreSQL.")
        turns = VT._turns(db, session)
        assert result["interview_complete"] is False and result["turn_number"] == 2 and "PRIVATE NOTE" in result["note"]
        assert "authentication" in result["note"].lower() and "Question 2 of at most 10" in result["note"]
        assert len(turns) == 2 and turns[0].candidate_answer.startswith("I built a REST API") and turns[0].answer_analysis_json and turns[0].next_action
        assert turns[1].candidate_answer is None
        work.record_ai_question("Nice. How did you keep the FastAPI authentication tokens secure?")
        db.expire_all()
        turns = VT._turns(db, session)
        assert turns[1].question == "Nice. How did you keep the FastAPI authentication tokens secure?"      # what was actually said
        assert turns[0].question != turns[1].question
        assert work.record_ai_question("ok") is None                                                     # too short to be a question: ignored


def test_a_full_realtime_conversation_finalizes_through_the_existing_finalizer_and_stays_hr_readable():
    from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2
    from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
    db = VT._db(shared=True)
    user, app, session = VT._interview(db)
    app_id, session_id = app.id, session.id                                        # (the work object closes the shared session after every call)
    work = rt.DbRealtimeWork(app_id, session_factory=lambda: db)
    with VT._isolated(VT.FakeGroq(session)):
        outcomes = []
        for i in range(1, 11):
            outcomes.append(work.record_answer(f"Answer number {i}: I used FastAPI with PostgreSQL and wrote tests for the authentication endpoints."))
            if not outcomes[-1]["interview_complete"]:
                work.record_ai_question(f"Spoken question {i + 1}: how did you design the schema for that part?")
        db.expire_all()
        turns = db.query(AIInterviewTurnV2).filter(AIInterviewTurnV2.session_id == session_id).order_by(AIInterviewTurnV2.turn_number).all()
    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
    fresh = db.query(AIInterviewSessionV2).filter(AIInterviewSessionV2.application_id == app_id).first()
    assert outcomes[-1]["interview_complete"] is True and "PRIVATE NOTE" in outcomes[-1]["note"] and "complete" in outcomes[-1]["note"]
    assert fresh.status == "completed" and fresh.final_evaluation_json                            # the existing finalizer ran
    assert len(turns) == 10 and all(t.candidate_answer for t in turns)
    for t in turns:
        AnswerAnalysisV2.model_validate_json(t.answer_analysis_json)                             # every stored analysis is HR-readable
    assert turns[1].question.startswith("Spoken question 2")                                    # HR sees the real conversation
    assert "FINAL question" in outcomes[8]["note"] or outcomes[8]["turn_number"] == 10            # the model is warned before the last one


def test_realtime_never_touches_gemini_text_stt_or_tts_in_the_analysis_path_and_uses_the_configured_brain_only():
    db = VT._db(shared=True)
    user, app, session = VT._interview(db)
    work = rt.DbRealtimeWork(app.id, session_factory=lambda: db)
    with VT._isolated(VT.FakeGroq(session)), VT._gemini_tripwire() as gem:
        work.record_answer("I built a REST API using Python and FastAPI with JWT authentication.")
        assert gem["n"] == 0
        assert prov.get_call_counts() == {"groq": 1}                                            # the pinned test brain, once per answer


def test_requests_to_repeat_the_question_are_not_answers_but_real_short_answers_are():
    for text in ("Can you ask this question again?", "could you please repeat that", "Sorry, I didn't catch that", "what was the question", "Pardon?"):
        assert rt.is_meta_request(text), text
    for text in ("I used FastAPI and PostgreSQL for the repeat visit scheduling module.", "Yes, I would use an index on the foreign key.",
                 "We could repeat the migration script safely because it is idempotent and I tested it twice on staging data."):
        assert not rt.is_meta_request(text), text

    async def go():
        work = FakeWork()
        session, conn, pub, *_ = await _make(work)
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "Welcome. Tell me about your project."); conn.feed("turn_complete")
        await _settle()
        conn.feed("input_text", "Can you ask this question again?")
        conn.feed("audio", PCM); conn.feed("output_text", "Sure: how did you design the API?"); conn.feed("turn_complete")
        await _settle(10)
        assert work.answers == [] and session.stats["skipped_noise"] == 1          # the turn was NOT consumed
        assert work.spoken == ["Sure: how did you design the API?"]                # the re-asked question replaces the open one
        await session.close()
    _run(go())


def test_silence_is_fed_to_the_model_only_while_no_candidate_audio_arrives():
    import backend.app.services.assessment.realtime_interview_v2 as mod

    async def go():
        session, conn, pub, *_ = await _make()
        await session.start()
        await asyncio.sleep(0.5)                                  # a muted / DTX-silent mic: nothing arrives
        silent_frames = [a for a in conn.audio if set(a) == {0}]
        assert len(silent_frames) >= 10 and all(len(a) == 640 for a in silent_frames)      # real-time 20 ms silence frames
        assert session.stats["silence_fills"] >= 10
        conn.audio.clear()
        for _ in range(15):                                        # real candidate audio arriving continuously
            await pub.cb(PCM)
            await asyncio.sleep(0.02)
        assert not any(set(a) == {0} for a in conn.audio) and len(conn.audio) == 22        # 15 x 960 B = 22 full 640 B chunks; no filler mixed in while audio flows
        await session.close()
        n = len(conn.audio)
        await asyncio.sleep(0.2)
        assert len(conn.audio) == n                                # and none after close
    _run(go())


def _loud(ms=10, amp=0.3):
    import numpy as np
    n = 16 * ms
    return (np.sin(np.arange(n) * 0.3) * amp * 32767).astype("<i2").tobytes()


def _quiet(ms=10):
    return _loud(ms, amp=0.005)


def test_the_candidate_talking_over_the_ai_cuts_the_audio_within_a_third_of_a_second_and_stale_audio_is_dropped():
    async def go():
        session, conn, pub, *_ = await _make(FakeWork())
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "So, tell me about the"); await _settle()
        assert pub.active == 1
        frames = 0
        for _ in range(60):                                      # loud speech in 10 ms frames
            await pub.cb(_loud())
            frames += 1
            await asyncio.sleep(0.005)
            if "interrupt" in pub.log:
                break
        assert "interrupt" in pub.log and pub.active is None
        assert 25 <= frames <= 28, frames                        # cut after 250 ms of loud voice (25 x 10 ms), not before, not later
        assert session.stats["barge_ins"] == 1
        conn.feed("audio", b"STALE"); await _settle()            # the model is still sending the rest of the cancelled sentence
        assert pub.seq == 1 and pub.streams[1]["pcm"] == PCM     # never played, never a new stream
        conn.feed("interrupted"); await _settle()                # the model's own signal arrives later (its reply was cancelled)
        conn.feed("audio", b"NEW-REPLY"); conn.feed("output_text", "Of course, go ahead."); conn.feed("turn_complete"); await _settle()
        assert pub.streams[2]["pcm"] == b"NEW-REPLY"             # the NEXT reply is played normally
        await session.close()
    _run(go())


def test_quiet_noise_and_echo_below_the_threshold_never_interrupt_the_ai():
    async def go():
        session, conn, pub, *_ = await _make(FakeWork())
        await session.start()
        conn.feed("audio", PCM); await _settle()
        for _ in range(60):
            await pub.cb(_quiet()); await asyncio.sleep(0.005)
        for _ in range(12):                                      # a short cough/click (120 ms) is not a barge-in either
            await pub.cb(_loud())
        assert "interrupt" not in pub.log and session.stats["barge_ins"] == 0 and pub.active == 1
        await session.close()
    _run(go())


def test_a_spurious_interrupted_signal_with_nobody_speaking_does_not_cut_the_sentence():
    async def go():
        session, conn, pub, *_ = await _make(FakeWork())
        await session.start()
        conn.feed("audio", PCM); await _settle()
        conn.feed("interrupted"); await _settle()
        assert "interrupt" not in pub.log and pub.active == 1 and session.stats["ignored_spurious_interrupts"] == 1
        await pub.cb(_loud(400))                                 # but with the candidate really speaking, the same signal DOES cut
        conn.feed("audio", PCM); await _settle()
        assert session.stats["barge_ins"] == 1 and "interrupt" in pub.log
        await session.close()
    _run(go())


def test_a_natural_pause_inside_the_interruption_does_not_reset_the_detector():
    async def go():
        session, conn, pub, *_ = await _make(FakeWork())
        await session.start()
        conn.feed("audio", PCM); await _settle()
        for _ in range(20):                                      # "Wait,"        200 ms of voice
            await pub.cb(_loud())
        for _ in range(20):                                      # natural pause  200 ms of quiet
            await pub.cb(_quiet())
        assert "interrupt" not in pub.log
        for _ in range(16):                                      # "I meant..."   the detector kept the earlier voice
            await pub.cb(_loud())
        assert "interrupt" in pub.log and session.stats["barge_ins"] == 1
        await session.close()
    _run(go())


def test_integrity_notice_is_spoken_through_the_live_model_and_is_not_recorded_as_a_question():
    import asyncio
    from backend.app.services.assessment import realtime_interview_v2 as rt

    async def go():
        session = rt.RealtimeInterviewSession(1, connection_factory=None, publisher=None, work=None)
        notes = []

        class Conn:
            async def send_note(self, text, turn_complete=False):
                notes.append((text, turn_complete))

        session.conn = Conn()
        assert await session.say_notice("Please keep your eyes on the screen.") is False      # not ready: no live connection to speak through
        session.state = "ready"
        assert await session.say_notice("Please keep your eyes on the screen.") is True
        assert notes and notes[0][1] is True and "Please keep your eyes on the screen." in notes[0][0] and "PRIVATE NOTE" in notes[0][0]
        assert session._notice_turns == 1

    asyncio.run(go())


def test_speaking_text_is_streamed_live_and_cleared_when_the_turn_ends():
    """Regression: the caption used to show the pre-spoken 'suggested' question while the model said something different,
    then only correct itself after the whole sentence had already been spoken. The live transcript must arrive as it is
    generated, and be cleared the moment the turn ends so the persisted (corrected) transcript can take over."""

    import asyncio
    from backend.app.services.assessment import realtime_interview_v2 as rt

    async def go():
        session = rt.RealtimeInterviewSession(1, connection_factory=None, publisher=None, work=None)
        sent = []

        class Publisher:
            async def send_state(self, state, **extra):
                sent.append((state, extra))
            async def stream_end(self, seq, close_after=False):
                pass
            async def interrupt(self):
                pass

        session.publisher = Publisher()
        session.conn = type("C", (), {"send_note": staticmethod(lambda *a, **k: asyncio.sleep(0))})()
        session._opening_spoken = True

        await session._handle(rt.LiveEvent("output_text", "You mentioned "))
        await asyncio.sleep(0.13)                                      # past the 120ms throttle
        await session._handle(rt.LiveEvent("output_text", "Redis caching, tell me more."))
        await asyncio.sleep(0.01)                                       # let the scheduled send_state task actually run
        texts = [e["text"] for s, e in sent if s == "speaking_text"]
        assert texts and texts[-1] == "You mentioned Redis caching, tell me more."

        session.work = type("W", (), {"record_answer": staticmethod(lambda a: {"interview_complete": False, "note": "", "turn_number": 2})})()
        session._ai_seq = None
        await session._on_ai_turn_end()                                 # a spoken (non-notice) turn with no audio seq: still clears
        assert ("speaking_text", {"text": ""}) in sent

    asyncio.run(go())


def test_closing_waits_for_the_goodbye_audio_to_actually_finish_playing():
    """Regression: 'complete' + close() used to fire 0.5 s after the goodbye TEXT finished generating, cutting off the
    spoken goodbye mid-sentence. The browser must not be told "complete" until _ai_audible() says the audio genuinely
    finished playing out (plus a playout margin)."""

    import asyncio
    from backend.app.services.assessment import realtime_interview_v2 as rt

    async def go():
        session = rt.RealtimeInterviewSession(1, connection_factory=None, publisher=None, work=None)
        events = []

        class Publisher:
            async def send_state(self, state, **extra):
                events.append((time.monotonic(), state))
            async def stream_end(self, seq, close_after=False):
                pass

        session.publisher = Publisher()
        session._closing_sent = True
        session._closing_ai_turn = 0
        session.stats["ai_turns"] = 1
        session._ai_text = "Thank you for your time today, the interview is now complete."

        # simulate a long goodbye still audibly playing out (1.2 seconds of audio pushed, playback just started)
        session._ai_play_started = time.monotonic()
        session._ai_pushed_seconds = 1.2
        session._ai_seq = None

        # _on_ai_turn_end() fires the wait-then-close as a background task (it must return promptly so the event/message
        # loop keeps flowing) -- call the underlying coroutine directly here so the test can await its own completion.
        t0 = time.monotonic()
        await session._finish_goodbye_then_close()
        elapsed = time.monotonic() - t0

        assert elapsed >= 1.2, f"closed after only {elapsed:.1f}s while 1.2s of goodbye audio was still playing"
        assert any(s == "complete" for _, s in events)
        assert session.state == "closed"

    import time
    asyncio.run(go())


def test_the_closing_speech_requirements_are_a_real_goodbye_before_the_interview_ends():
    text = rt._CLOSING_REQUIREMENTS

    assert "MUST speak a proper closing speech" in text
    assert "HR will contact them if they are selected" in text
    assert "responses have been saved" in text and "close this window" in text


class FinalQuestionWork(FakeWork):
    """The 2nd answer is to the FINAL question: record_answer flags it, then the interview completes on that same answer."""

    def record_answer(self, text):
        self.answers.append(text)
        n = len(self.answers)
        if n >= 2:
            return {"interview_complete": True, "note": "PRIVATE NOTE: close the interview", "turn_number": None}
        return {"interview_complete": False, "note": "PRIVATE NOTE: final question", "turn_number": 2, "final": True}


def test_the_models_reply_to_the_final_answer_is_the_goodbye_and_closes_the_screen_without_a_second_closing():
    async def go():
        work = FinalQuestionWork()
        session, conn, pub, *_ = await _make(work)
        await session.start()
        conn.feed("audio", PCM); conn.feed("output_text", "Opening question here please."); conn.feed("turn_complete")
        await _settle()
        conn.feed("input_text", "I built a REST API using FastAPI and PostgreSQL, with tests.")
        conn.feed("audio", PCM); conn.feed("output_text", "Last question: why are you a fit?"); conn.feed("turn_complete")
        await _settle(10)                                    # answer 1 recorded: the note says the next question is the FINAL one
        assert session._final_question_open is True

        conn.feed("input_text", "I have shipped similar systems and I learn quickly on the job.")
        conn.feed("audio", PCM); conn.feed("output_text", "Thank you so much, the interview is complete. HR will contact you.")
        await _settle(10)
        assert session._closing_sent is True                 # decided the moment the reply started: this reply IS the goodbye
        conn.feed("turn_complete")
        await _settle(100)

        assert not any("close the interview" in n[0] for n in conn.notes)      # no second closing note was ever sent
        assert ("state", "complete") in pub.log and session.state == "closed" and conn.closed
    _run(go())


def test_a_backchannel_after_the_final_question_does_not_count_as_the_final_answer():
    async def go():
        session, conn, pub, *_ = await _make(FinalQuestionWork())
        await session.start()
        session._final_question_open = True
        session._opening_spoken = True
        conn.feed("input_text", "hmm")
        conn.feed("audio", PCM)
        await _settle(10)
        assert session._closing_sent is False
    _run(go())

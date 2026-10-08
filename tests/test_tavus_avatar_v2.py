"""
Task 7: the existing Tavus avatar inside the existing LiveKit interview -- server side.

The REAL stable publisher, the REAL Tavus avatar session and the REAL routes run
against an isolated in-memory database; only the LiveKit SDK (rooms, byte streams,
RPC) and the Tavus HTTP API are faked, so these tests prove identity/room reuse,
one-audible-path, turn sequencing, interruption, lifecycle, fallback and security --
not the network (the live run is separate). No jobpilot.db, no Gemini, no real Tavus call.
"""

import asyncio
import json
import os
import sys
import time
import types

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_voice_turn_local_pipeline_v2 as VT
import test_livekit_transport_v2 as LT

from livekit import rtc as real_rtc

from backend.app.api.deps import get_current_user
from backend.app.api.routes import interview_avatar_v2 as avatar_route
from backend.app.api.routes import interview_voice_v2 as voice_route
from backend.app.database.database import get_db
from backend.app.services.assessment import interview_voice_integration_v2 as bridge
from backend.app.services.assessment import livekit_audio_transport_v2 as transport
from backend.app.services.assessment import tavus_avatar_session_v2 as tavus

H = {"X-CSRF-Token": "t"}
FACE_ID = "face_abc123"                       # LT._configure(avatar=True) sets JOBPILOT_AI_INTERVIEWER_AVATAR_ID to this
AVATAR = "tavus-avatar-agent"


class FakeWorld:
    """Fake livekit.rtc + Tavus HTTP API + LiveKit admin API, with a controllable avatar."""

    def __init__(self, join_delay=0.0, create_error=None, never_joins=False, finish_delay=0.02, auto_finish=True, stream_error=None):
        self.rooms, self.published, self.unpublished, self.data, self.streams, self.rpcs = [], [], [], [], [], []
        self.conversations, self.ended, self.removed, self.tokens = [], [], [], []
        self.create_error, self.never_joins, self.join_delay = create_error, never_joins, join_delay
        self.finish_delay, self.auto_finish, self.stream_error = finish_delay, auto_finish, stream_error
        self.handlers = {}                       # rpc method -> handler registered by the session
        self.frames = []
        world = self

        class Writer:
            def __init__(self, info):
                self.info, self.chunks, self.closed = info, [], False

            async def write(self, chunk):
                if world.stream_error:
                    raise world.stream_error
                self.chunks.append(bytes(chunk))

            async def aclose(self):
                self.closed = True
                if world.auto_finish:
                    world.schedule_finished(False)

        class LocalParticipant:
            def register_rpc_method(self, name, handler=None):
                world.handlers[name] = handler

            async def perform_rpc(self, *, destination_identity, method, payload, response_timeout=None, max_round_trip_latency=None):
                world.rpcs.append((destination_identity, method))
                if method == "lk.clear_buffer" and world.auto_finish:
                    world.schedule_finished(True)
                return ""

            async def stream_bytes(self, name, *, total_size=None, mime_type="", attributes=None, stream_id=None, destination_identities=None, topic=""):
                w = Writer({"name": name, "topic": topic, "attributes": attributes, "to": destination_identities})
                world.streams.append(w)
                return w

            async def publish_track(self, track, options):
                world.published.append(track)
                return types.SimpleNamespace(sid=f"TR_{len(world.published)}")

            async def unpublish_track(self, sid):
                world.unpublished.append(sid)

            async def publish_data(self, payload, reliable=True, destination_identities=None, topic=""):
                world.data.append((json.loads(payload), topic))

        class Room:
            def __init__(self):
                self.local_participant = LocalParticipant()
                self.handlers, self.disconnects, self.remote_participants = {}, 0, {}
                world.rooms.append(self)

            def on(self, name, handler):
                self.handlers[name] = handler

            def emit(self, name, *args):
                if name in self.handlers:
                    self.handlers[name](*args)

            async def connect(self, url, token):
                pass

            async def disconnect(self):
                self.disconnects += 1

        class AudioSource:
            def __init__(self, rate, channels): pass

            async def capture_frame(self, frame):
                world.frames.append(bytes(frame.data)); await asyncio.sleep(0)

            def clear_queue(self): pass

            async def wait_for_playout(self): pass

        class AudioFrame:
            @staticmethod
            def create(rate, channels, samples):
                import array
                return types.SimpleNamespace(data=memoryview(array.array("h", [0] * (samples * channels))))

        self.rtc = types.SimpleNamespace(
            Room=Room, AudioSource=AudioSource, AudioFrame=AudioFrame,
            LocalAudioTrack=types.SimpleNamespace(create_audio_track=lambda name, source: types.SimpleNamespace(name=name, source=source)),
            TrackPublishOptions=lambda source: types.SimpleNamespace(source=source),
            TrackSource=types.SimpleNamespace(SOURCE_MICROPHONE="SOURCE_MICROPHONE"),
        )

        class Api:
            async def create_conversation(self, face_id, url, token):
                if world.create_error:
                    raise world.create_error
                world.conversations.append(face_id)
                world.tokens.append(token)
                cid = f"conv_{len(world.conversations)}"
                if not world.never_joins:
                    async def join():
                        await asyncio.sleep(world.join_delay)
                        world.rooms[-1].remote_participants[AVATAR] = types.SimpleNamespace(
                            identity=AVATAR, track_publications={"v": types.SimpleNamespace(kind=real_rtc.TrackKind.KIND_VIDEO)})
                    asyncio.ensure_future(join())
                return cid

            async def end_conversation(self, conversation_id):
                world.ended.append(conversation_id)

        self.Api = Api

    def schedule_finished(self, interrupted):
        async def later():
            await asyncio.sleep(self.finish_delay)
            handler = self.handlers.get("lk.playback_finished")
            if handler:
                handler(types.SimpleNamespace(caller_identity=AVATAR, payload=json.dumps({"playback_position": 1.0, "interrupted": interrupted})))
        asyncio.ensure_future(later())

    def states(self, seq=None):
        return [(m["state"], m["seq"], m.get("interrupted")) for m, _t in self.data if seq is None or m["seq"] == seq]


class Env:
    def __init__(self, world, configured=True, tavus_key=True, visual="avatar"):
        self.world, self.configured, self.tavus_key, self.visual = world, configured, tavus_key, visual

    def __enter__(self):
        LT._configure(livekit=True, avatar=self.configured)
        os.environ["AI_INTERVIEW_VISUAL_MODE"] = self.visual          # the optional Tavus mode is opt-in (the default is the fixed image)
        if self.tavus_key:
            os.environ["TAVUS_API_KEY"] = "test-tavus-key-must-never-leak"
        else:
            os.environ.pop("TAVUS_API_KEY", None)
        self.saved = (transport.rtc, tavus._DefaultTavusApi, tavus._default_remove_participant, bridge.get_stt_provider, bridge.get_tts_provider,
                      tavus.AVATAR_JOIN_TIMEOUT_SECONDS, transport.CANDIDATE_LEFT_GRACE_SECONDS, transport.AVATAR_READY_WAIT_SECONDS)
        transport.rtc = self.world.rtc
        tavus._DefaultTavusApi = self.world.Api

        async def remove(room_name, identity):
            self.world.removed.append((room_name, identity))
        tavus._default_remove_participant = remove
        self.stt, self.tts = VT.FakeStt(), VT.FakeTts()
        bridge.get_stt_provider, bridge.get_tts_provider = (lambda: self.stt), (lambda: self.tts)
        transport._publishers.clear()
        return self

    def __exit__(self, *exc):
        (transport.rtc, tavus._DefaultTavusApi, tavus._default_remove_participant, bridge.get_stt_provider, bridge.get_tts_provider,
         tavus.AVATAR_JOIN_TIMEOUT_SECONDS, transport.CANDIDATE_LEFT_GRACE_SECONDS, transport.AVATAR_READY_WAIT_SECONDS) = self.saved
        transport._publishers.clear()
        os.environ.pop("TAVUS_API_KEY", None)
        os.environ.pop("AI_INTERVIEW_VISUAL_MODE", None)
        for k in LT.ENV_KEYS:
            os.environ.pop(k, None)


WAV = VT._wav(1.0)
PCM = transport.decode_wav_pcm16(WAV)[0]


async def _publisher(app_id=71, world=None):
    p = await transport.get_publisher(app_id, lambda a: {"url": "wss://x", "token": "t"})
    return p


async def _ready(publisher, timeout=3.0):
    await publisher.start_avatar()
    assert await publisher.avatar.wait_ready(timeout), publisher.avatar_status()


# ------------------------------------------------------------ identity + room
def test_existing_tavus_identity_and_room_are_used_and_the_token_is_scoped():
    world = FakeWorld()

    async def go():
        p = await _publisher(72)
        await _ready(p)
        return p

    with Env(world):
        p = asyncio.run(go())
    assert world.conversations == [FACE_ID]                                  # the CONFIGURED face id, nothing generated
    claims = jwt.decode(world.tokens[0], LT.SECRET, algorithms=["HS256"])
    assert claims["sub"] == AVATAR and claims["kind"] == "agent"
    assert claims["video"]["room"] == "jobpilot-interview-v2-72"                # the SAME room as the candidate and the audio publisher
    assert claims["attributes"]["lk.publish_on_behalf"] == "jobpilot-tts-72"
    assert claims["video"]["roomJoin"] is True
    for forbidden in ("roomCreate", "roomList", "roomAdmin", "roomRecord", "hidden", "recorder"):
        assert not claims["video"].get(forbidden), forbidden                 # no admin power for the avatar participant
    assert claims["exp"] - claims["nbf"] == 15 * 60


def test_avatar_agent_token_never_reaches_the_browser_routes():
    os.environ["LIVEKIT_URL"], os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"] = "wss://x", "k", LT.SECRET
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "api", "routes", "interview_avatar_v2.py"), encoding="utf-8").read()
    assert "generate_avatar_agent_token_v2" not in src                       # only the server-side session mints it
    for k in LT.ENV_KEYS:
        os.environ.pop(k, None)


def test_start_is_idempotent_one_conversation_one_room_connection_however_often_it_is_called():
    world = FakeWorld(join_delay=0.05)

    async def go():
        p = await _publisher(73)
        await asyncio.gather(p.start_avatar(), p.start_avatar(), p.start_avatar())
        await p.start_avatar()
        assert await p.avatar.wait_ready(3)
        await p.start_avatar()
        return p

    with Env(world):
        p = asyncio.run(go())
    assert len(world.conversations) == 1 and len(world.rooms) == 1
    assert p.avatar.stats["conversations_created"] == 1 and p.avatar_status() == {"state": "ready", "error": None}


# ------------------------------------------------------------ audio: ONE audible path
def test_avatar_mode_streams_the_same_pcm_to_the_avatar_and_publishes_no_audio_track_of_its_own():
    world = FakeWorld()

    async def go():
        p = await _publisher(74)
        await _ready(p)
        m = await p.speak(WAV)
        await asyncio.sleep(0.3)
        return p, m

    with Env(world):
        p, m = asyncio.run(go())
    assert m["mode"] == "avatar" and m["seq"] == 1
    (stream,) = world.streams
    assert stream.info["topic"] == "lk.audio_stream" and stream.info["to"] == [AVATAR]
    assert stream.info["attributes"] == {"sample_rate": "24000", "num_channels": "1"} and stream.closed
    assert b"".join(stream.chunks) == PCM                                     # the exact Kokoro PCM, once, unmodified
    assert world.published == [] and world.frames == []                      # NO second audible source
    assert world.states() == [("started", 1, None), ("ended", 1, False)]


def test_a_track_published_before_the_avatar_was_ready_is_removed_so_only_the_avatar_is_audible():
    world = FakeWorld(join_delay=0.1)

    async def go():
        p = await _publisher(75)
        await p.speak(WAV)                                                    # question 1: no avatar yet -> plain track
        await asyncio.sleep(0.1)
        assert len(world.published) == 1
        await p.start_avatar()
        assert await p.avatar.wait_ready(3)
        m = await p.speak(WAV)
        await asyncio.sleep(0.3)
        return m

    with Env(world):
        m = asyncio.run(go())
    assert m["mode"] == "avatar" and world.unpublished == ["TR_1"]           # the old track is gone
    assert len(world.published) == 1 and len(world.streams) == 1              # nothing new published, one avatar segment


def test_avatar_not_ready_yet_falls_back_to_the_plain_audio_track_for_that_response_only():
    world = FakeWorld(never_joins=True)

    async def go():
        transport.AVATAR_READY_WAIT_SECONDS = 0.1
        tavus.AVATAR_JOIN_TIMEOUT_SECONDS = 0.6
        p = await _publisher(76)
        await p.start_avatar()
        m = await p.speak(WAV)                                                # avatar still starting -> bounded wait -> track
        await asyncio.sleep(0.2)
        return p, m

    with Env(world):
        p, m = asyncio.run(go())
    assert m["mode"] == "track" and len(world.published) == 1 and world.streams == []


def test_kokoro_output_is_not_synthesized_by_the_avatar_layer():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "services", "assessment", "tavus_avatar_session_v2.py"), encoding="utf-8").read().lower()
    imports = [l for l in src.splitlines() if l.startswith(("import ", "from "))]
    for line in imports:
        for banned in ("gemini", "genai", "kokoro", "voice_provider", "interview_brain", "interview_integration", "local_tts", "local_stt"):
            assert banned not in line, line
    assert "synthesize(" not in src


# ------------------------------------------------------------ turn sequence, interruption, staleness
def test_turns_are_sequenced_and_a_late_finish_of_an_interrupted_turn_never_ends_the_next_one():
    world = FakeWorld(auto_finish=False)

    async def go():
        p = await _publisher(77)
        await _ready(p)
        await p.speak(WAV)                                                    # turn 1 (seq 1)
        await asyncio.sleep(0.1)
        interrupt = asyncio.ensure_future(p.interrupt())                      # candidate talks over it
        await asyncio.sleep(0.05)
        world.schedule_finished(True)                                         # the avatar reports the interrupted segment (late)
        await interrupt
        await p.speak(WAV)                                                    # turn 2 (seq 2)
        await asyncio.sleep(0.1)
        assert world.states(2) == [("started", 2, None)]                      # NOT ended by turn 1's late report
        world.schedule_finished(False)
        await asyncio.sleep(0.2)
        return p

    with Env(world):
        p = asyncio.run(go())
    assert world.states(1) == [("started", 1, None), ("ended", 1, True)]      # turn 1 ends exactly once, marked interrupted
    assert world.states(2) == [("started", 2, None), ("ended", 2, False)]     # turn 2 spoke and finished on its own
    assert ("tavus-avatar-agent", "lk.clear_buffer") in world.rpcs
    assert len(world.streams) == 2 and all(s.closed for s in world.streams)


def test_a_new_response_replaces_one_the_avatar_is_still_speaking():
    world = FakeWorld(auto_finish=False)

    async def go():
        p = await _publisher(78)
        await _ready(p)
        await p.speak(WAV)
        await asyncio.sleep(0.05)
        t = asyncio.ensure_future(p.speak(WAV))                               # replaces turn 1
        await asyncio.sleep(0.05)
        world.schedule_finished(True)                                         # avatar acknowledges the clear
        m2 = await t
        world.schedule_finished(False)
        await asyncio.sleep(0.2)
        return m2

    with Env(world):
        m2 = asyncio.run(go())
    assert m2["seq"] == 2 and ("tavus-avatar-agent", "lk.clear_buffer") in world.rpcs
    assert world.states(1)[-1] == ("ended", 1, True) and world.states(2)[-1] == ("ended", 2, False)


def test_only_the_avatar_participant_may_report_playback():
    world = FakeWorld(auto_finish=False)

    async def go():
        p = await _publisher(79)
        await _ready(p)
        await p.speak(WAV)
        await asyncio.sleep(0.05)
        verdict = world.handlers["lk.playback_finished"](types.SimpleNamespace(caller_identity="candidate-1", payload="{}"))
        await asyncio.sleep(0.05)
        return verdict

    with Env(world):
        verdict = asyncio.run(go())
    assert verdict == "reject" and [s for s, _q, _i in world.states() if s == "ended"] == []


def test_avatar_stream_failure_is_announced_as_failed_and_never_double_plays_on_the_track():
    world = FakeWorld(stream_error=RuntimeError("stream broke token=abc"))

    async def go():
        p = await _publisher(80)
        await _ready(p)
        await p.speak(WAV)
        await asyncio.sleep(0.2)

    with Env(world):
        asyncio.run(go())
    assert world.states() == [("started", 1, None), ("failed", 1, None)]
    assert world.published == [] and world.frames == []


# ------------------------------------------------------------ lifecycle
def test_close_ends_the_tavus_conversation_removes_the_avatar_and_leaves_the_room_once():
    world = FakeWorld()

    async def go():
        p = await _publisher(81)
        await _ready(p)
        await p.speak(WAV)
        await asyncio.sleep(0.1)
        await p.close()
        await p.close()
        await transport.close_all_publishers()
        return p

    with Env(world):
        p = asyncio.run(go())
    assert world.ended == ["conv_1"] and world.removed == [("jobpilot-interview-v2-81", AVATAR)]
    assert world.rooms[0].disconnects == 1 and transport.peek_publisher(81) is None and p.avatar is None


def test_interview_completion_closes_the_avatar_after_the_closing_message_finishes():
    world = FakeWorld()

    async def go():
        p = await _publisher(82)
        await _ready(p)
        await p.speak(WAV, close_after=True)
        await asyncio.sleep(0.5)

    with Env(world):
        asyncio.run(go())
    assert world.states() == [("started", 1, None), ("ended", 1, False)]
    assert world.ended == ["conv_1"] and world.rooms[0].disconnects == 1


def test_interview_completion_during_avatar_speech_is_clean():
    world = FakeWorld(auto_finish=False)

    async def go():
        p = await _publisher(83)
        await _ready(p)
        await p.speak(WAV)
        await asyncio.sleep(0.1)
        c = asyncio.ensure_future(p.close())                                  # interview cancelled/ended mid-speech
        await asyncio.sleep(0.05)
        world.schedule_finished(True)
        await c

    with Env(world):
        asyncio.run(go())
    assert world.ended == ["conv_1"] and world.states(1)[-1] == ("ended", 1, True)
    assert world.rooms[0].disconnects == 1


def test_candidate_leaving_closes_everything_after_the_grace_period_and_a_reconnect_cancels_it():
    world = FakeWorld()

    async def go():
        transport.CANDIDATE_LEFT_GRACE_SECONDS = 0.15
        p = await _publisher(84)
        await _ready(p)
        room = world.rooms[0]
        room.emit("participant_disconnected", types.SimpleNamespace(identity="candidate-9"))
        await asyncio.sleep(0.05)
        room.emit("participant_connected", types.SimpleNamespace(identity="candidate-9"))      # page refresh: back in time
        await asyncio.sleep(0.3)
        assert p.connected and world.ended == []
        room.emit("participant_disconnected", types.SimpleNamespace(identity="candidate-9"))   # really gone
        room.emit("participant_disconnected", types.SimpleNamespace(identity=AVATAR))          # other participants are irrelevant
        await asyncio.sleep(0.4)
        return p

    with Env(world):
        p = asyncio.run(go())
    assert world.ended == ["conv_1"] and world.removed and world.rooms[0].disconnects == 1
    assert transport.peek_publisher(84) is None


def test_livekit_room_loss_ends_the_avatar_and_voice_continues_on_the_plain_track_then_a_fresh_avatar_can_start():
    world = FakeWorld()

    async def go():
        p = await _publisher(85)
        await _ready(p)
        world.rooms[0].emit("disconnected", "CONNECTION_LOST")
        await asyncio.sleep(0.1)
        assert p.avatar is None and world.ended == ["conv_1"]                 # the old conversation was ended, not orphaned
        m = await p.speak(WAV)                                                # voice keeps working, audio-only
        await asyncio.sleep(0.1)
        await p.start_avatar()                                                # the page asks for a fresh avatar
        assert await p.avatar.wait_ready(3)
        return m

    with Env(world):
        m = asyncio.run(go())
    assert m["mode"] == "track" and len(world.rooms) == 2 and world.conversations == [FACE_ID, FACE_ID]


# ------------------------------------------------------------ errors: voice interview stays functional
def test_tavus_rejecting_the_conversation_is_an_honest_failed_state_without_leaking_the_key():
    world = FakeWorld(create_error=RuntimeError("401 unauthorized key=test-tavus-key-must-never-leak"))

    async def go():
        p = await _publisher(86)
        await p.start_avatar()
        await asyncio.sleep(0.2)
        m = await p.speak(WAV)                                                # the voice interview still works
        await asyncio.sleep(0.1)
        return p, m

    with Env(world):
        p, m = asyncio.run(go())
    status = p.avatar_status()
    assert status["state"] == "failed" and "RuntimeError" in status["error"]
    assert "test-tavus-key" not in json.dumps(status) and "unauthorized" not in json.dumps(status)
    assert m["mode"] == "track" and len(world.published) == 1


def test_avatar_that_never_joins_fails_after_the_timeout_and_its_conversation_is_ended():
    world = FakeWorld(never_joins=True)

    async def go():
        tavus.AVATAR_JOIN_TIMEOUT_SECONDS = 0.4
        p = await _publisher(87)
        await p.start_avatar()
        await asyncio.sleep(0.9)
        return p

    with Env(world):
        p = asyncio.run(go())
    assert p.avatar_status()["state"] == "failed" and "did not join" in p.avatar_status()["error"]
    assert world.ended == ["conv_1"]                                          # no orphaned billed conversation


def test_avatar_module_never_logs_or_returns_secrets_and_gemini_is_never_called():
    world = FakeWorld(create_error=RuntimeError("boom"))
    with Env(world), VT._gemini_tripwire() as gem:
        async def go():
            p = await _publisher(88)
            await p.start_avatar(); await asyncio.sleep(0.1)
            await p.speak(WAV); await asyncio.sleep(0.1)
        asyncio.run(go())
    assert gem["n"] == 0


# ------------------------------------------------------------ routes: ownership / security / fallback
def _client(db, holder):
    api = FastAPI()
    api.include_router(avatar_route.router)
    api.include_router(voice_route.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: holder["user"]
    return TestClient(api, cookies={"csrf_token": "t"})


def _url(app_id, tail):
    return f"/api/assessment/sessions/{app_id}/interview/v2/avatar/{tail}"


def test_start_route_starts_one_avatar_and_status_reports_ready():
    world = FakeWorld()
    with Env(world):
        db, user, app, session, stranger = LT._fixture()
        with _client(db, {"user": user}) as c:
            assert c.get(_url(app.id, "status")).json() == {"state": "idle", "error": None}
            r = c.post(_url(app.id, "start"), headers=H)
            assert r.status_code == 200 and r.json()["state"] in ("starting", "ready")
            c.post(_url(app.id, "start"), headers=H)
            time.sleep(0.5)
            assert c.get(_url(app.id, "status")).json() == {"state": "ready", "error": None}
    assert len(world.conversations) == 1 and len(world.rooms) == 1


def test_routes_enforce_csrf_ownership_active_interview_and_configuration():
    world = FakeWorld()
    with Env(world) as env:
        db, user, app, session, stranger = LT._fixture()
        with _client(db, {"user": user}) as c:
            assert c.post(_url(app.id, "start")).status_code == 403                             # CSRF
        with _client(db, {"user": stranger}) as c:
            assert c.post(_url(app.id, "start"), headers=H).status_code == 404                  # someone else's interview
            assert c.get(_url(app.id, "status")).status_code == 404
            assert c.post(_url(99999, "start"), headers=H).status_code == 404
        session.status = "completed"; db.commit()
        with _client(db, {"user": user}) as c:
            assert c.post(_url(app.id, "start"), headers=H).status_code == 409                  # no active interview
    assert world.conversations == [] and world.rooms == []                                       # nothing was started for any refused call


def test_start_route_without_tavus_configuration_is_an_honest_503_and_starts_nothing():
    world = FakeWorld()
    with Env(world, tavus_key=False):
        db, user, app, session, stranger = LT._fixture()
        with _client(db, {"user": user}) as c:
            r = c.post(_url(app.id, "start"), headers=H)
    assert r.status_code == 503 and "not configured" in r.json()["detail"].lower()
    assert world.rooms == [] and world.conversations == []


def test_unauthenticated_and_company_callers_cannot_use_the_avatar_routes():
    world = FakeWorld()
    with Env(world):
        db, user, app, session, stranger = LT._fixture()
        api = FastAPI(); api.include_router(avatar_route.router)
        api.dependency_overrides[get_db] = lambda: db
        with TestClient(api, cookies={"csrf_token": "t"}) as c:
            assert c.post(_url(app.id, "start"), headers=H).status_code == 401
            c.cookies.set("jobpilot_company_session", "x")
            assert c.post(_url(app.id, "start"), headers=H).status_code == 401
            assert c.get(_url(app.id, "status")).status_code == 401


def test_voice_turn_reports_avatar_mode_and_still_returns_no_inline_audio():
    world = FakeWorld()
    with Env(world) as env:
        db, user, app, session, stranger = LT._fixture()
        with VT._isolated(VT.FakeGroq(session)), VT._gemini_tripwire() as gem, _client(db, {"user": user}) as c:
            c.post(_url(app.id, "start"), headers=H)
            time.sleep(0.4)
            r = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/turn", files={"audio": ("a.webm", b"x", "audio/webm")},
                       data={"transport": "livekit"}, headers=H)
            time.sleep(0.4)
    body = r.json()
    assert r.status_code == 200 and body["audio_delivery"] == "livekit" and body["audio"] is None and body["livekit"]["mode"] == "avatar"
    assert len(env.tts.texts) == 1 and len(world.streams) == 1 and world.published == []      # Kokoro once, avatar once, no second audible path
    assert gem["n"] == 0


def test_voice_turn_with_a_failed_avatar_is_the_normal_audio_only_livekit_turn():
    world = FakeWorld(create_error=RuntimeError("down"))
    with Env(world) as env:
        db, user, app, session, stranger = LT._fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            c.post(_url(app.id, "start"), headers=H)
            time.sleep(0.3)
            assert c.get(_url(app.id, "status")).json()["state"] == "failed"
            r = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/turn", files={"audio": ("a.webm", b"x", "audio/webm")},
                       data={"transport": "livekit"}, headers=H)
            time.sleep(0.3)
    body = r.json()
    assert r.status_code == 200 and body["livekit"]["mode"] == "track" and body["audio_delivery"] == "livekit"
    assert world.streams == [] and len(world.published) == 1


def test_worker_reference_is_no_longer_the_speaking_path():
    # the browser must not also drive the old Gemini avatar worker
    page = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "js", "ai-interview-v2.js"), encoding="utf-8").read()
    assert "setRemoteSpeaker({" not in page


# ------------------------------------------------------------ Task 8: fixed-image mode (the default)
def test_default_visual_mode_is_the_fixed_image_and_avatar_is_explicit_opt_in():
    from backend.app.services.assessment.avatar_provider_v2 import get_visual_mode
    os.environ.pop("AI_INTERVIEW_VISUAL_MODE", None)
    assert get_visual_mode() == "image"
    for value, expected in (("image", "image"), ("IMAGE", "image"), ("", "image"), ("tavus", "image"), ("avatar", "avatar"), (" Avatar ", "avatar")):
        os.environ["AI_INTERVIEW_VISUAL_MODE"] = value
        assert get_visual_mode() == expected, value
    os.environ.pop("AI_INTERVIEW_VISUAL_MODE", None)


def test_image_mode_never_contacts_tavus_or_the_room_from_the_start_route_even_with_full_credentials():
    world = FakeWorld()
    with Env(world, visual="image"):
        class Tripwire:
            def __init__(self):
                raise AssertionError("Tavus must not be contacted in fixed-image mode")
        tavus._DefaultTavusApi = Tripwire
        db, user, app, session, stranger = LT._fixture()
        with _client(db, {"user": user}) as c:
            r = c.post(_url(app.id, "start"), headers=H)
            r2 = c.post(_url(app.id, "start"), headers=H)
            status = c.get(_url(app.id, "status")).json()
    assert r.status_code == 200 and r.json() == {"state": "disabled", "error": None, "visual_mode": "image"} and r2.json()["state"] == "disabled"
    assert world.rooms == [] and world.conversations == [] and world.streams == [] and status == {"state": "idle", "error": None}


def test_image_mode_still_enforces_csrf_ownership_and_an_active_interview_on_the_start_route():
    world = FakeWorld()
    with Env(world, visual="image"):
        db, user, app, session, stranger = LT._fixture()
        with _client(db, {"user": user}) as c:
            assert c.post(_url(app.id, "start")).status_code == 403
        with _client(db, {"user": stranger}) as c:
            assert c.post(_url(app.id, "start"), headers=H).status_code == 404
        session.status = "completed"; db.commit()
        with _client(db, {"user": user}) as c:
            assert c.post(_url(app.id, "start"), headers=H).status_code == 409


def test_image_mode_voice_turn_is_the_plain_kokoro_livekit_flow_with_no_tavus_and_no_gemini():
    world = FakeWorld()
    with Env(world, visual="image") as env, VT._gemini_tripwire() as gem:
        class Tripwire:
            def __init__(self):
                raise AssertionError("Tavus must not be contacted in fixed-image mode")
        tavus._DefaultTavusApi = Tripwire
        db, user, app, session, stranger = LT._fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            c.post(_url(app.id, "start"), headers=H)                      # what the page does on connect
            r = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/turn", files={"audio": ("a.webm", b"x", "audio/webm")},
                       data={"transport": "livekit"}, headers=H)
            time.sleep(0.4)
    body = r.json()
    assert r.status_code == 200 and body["audio_delivery"] == "livekit" and body["livekit"]["mode"] == "track" and body["audio"] is None
    assert len(env.tts.texts) == 1 and len(world.published) == 1 and world.streams == [] and world.conversations == [] and gem["n"] == 0

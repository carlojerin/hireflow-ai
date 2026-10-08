"""
Task 3: the complete local-pipeline voice turn  (audio -> STT -> Groq brain -> TTS).

Everything runs against an isolated in-memory database. The REAL
submit_answer_v2 / interview brain code runs; only the outermost model
calls are faked (Groq's HTTP endpoint, the STT model, the TTS model),
so these prove wiring, error isolation, metadata and no-Gemini -- not
model quality. The real models are exercised by the live verification run.
"""

import contextlib
import io
import json
import os
import wave
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.database.database import Base
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.user import User
from backend.app.services.assessment import interview_integration_v2 as integration
from backend.app.services.assessment import interview_llm_provider as prov
from backend.app.services.assessment import interview_voice_integration_v2 as bridge
from backend.app.services.assessment import local_stt_provider_v2 as local_stt
from backend.app.services.assessment import local_tts_provider_v2 as local_tts
from backend.app.services.assessment import voice_provider_v2 as vp
from backend.app.services.assessment.assignment_service import create_assignment
from backend.app.services.assessment.voice_provider_v2 import SynthesizedAudioV2, TranscriptionResultV2, VoiceProviderError

TRANSCRIPT = "I built a REST API using Python and FastAPI. I implemented authentication and connected it to PostgreSQL."
SECRET = "SECRET-INTERNAL-DETAIL"


# ---------------------------------------------------------------- fixtures
def _db(shared=False):
    kw = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool} if shared else {}
    engine = create_engine("sqlite://", **kw)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _interview(db):
    """A real, in-progress V2 interview (deterministic opening) in an isolated DB."""
    user = User(full_name="Voice Tester", username="voicetester", email="", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(title="Backend Developer", company="TestCo", required_skills="Python, SQL")
    db.add(job); db.flush()
    app = JobApplication(job_id=job.id, user_id=user.id, status="programming_passed")
    db.add(app); db.flush()
    sr = AssessmentSession(application_id=app.id, current_stage="programming")
    db.add(sr)
    db.add(AptitudeTest(application_id=app.id, questions="[]", total_questions=10, passed=True, status="completed", score=9))
    now = datetime.utcnow()
    db.add(ProgrammingTest(application_id=app.id, questions="[]", total_questions=5, passed=True, status="completed", score=90,
                           total_score=100, assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now))
    create_assignment(db, app.id, "ai_interview", assigned_by_id=user.id, assigned_by_type="system")
    db.commit()
    session, _ = integration.start_interview_v2(db, sr, app, job, user, None, None, use_llm=False)
    return user, app, session


def _competencies(session):
    return [c["name"] for c in json.loads(session.blueprint_json)["competencies"]]


def _wav(seconds=0.5, rate=24000):
    t = np.arange(int(rate * seconds)) / rate
    pcm = (0.4 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return buf.getvalue()


class FakeStt:
    provider_name = "faster-whisper"

    def __init__(self, transcript=TRANSCRIPT, raises=None):
        self.transcript, self.raises, self.calls = transcript, raises, []

    def transcribe(self, audio_bytes, mime_type):
        self.calls.append((len(audio_bytes), mime_type))
        if self.raises:
            raise self.raises
        return TranscriptionResultV2(transcript=self.transcript)


class FakeTts:
    provider_name = "kokoro"

    def __init__(self, raises=None, result=None):
        self.raises, self.result, self.texts = raises, result, []

    def synthesize(self, text):
        self.texts.append(text)
        if self.raises:
            raise self.raises
        return self.result or SynthesizedAudioV2(audio_bytes=_wav(), mime_type="audio/wav", sample_rate=24000)


GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class FakeGroq:
    def __init__(self, session, action="FOLLOW_UP", raises=None):
        self.session, self.action, self.raises = session, action, raises
        self.payloads, self.urls = [], []

    def __call__(self, url, json=None, headers=None, timeout=None, **kw):
        self.urls.append(url)
        self.payloads.append(json)
        if self.raises:
            raise self.raises
        comp = _competencies(self.session)[0]
        body = {
            "answer_analysis": {"competency": comp, "strengths": [], "weaknesses": [], "evidence": ["built a REST API with FastAPI"],
                                "claims": ["implemented authentication"], "missing_depth": ["no detail on auth design"], "strength_level": "moderate"},
            "next_action": self.action, "reason": "r", "next_competency": comp, "next_difficulty": "intermediate",
            "next_question": "How did you design the authentication for that API?", "next_question_type": "TECHNICAL",
        }
        text = __import__("json").dumps(body)

        class R:
            status_code = 200
            def json(self_i): return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 900, "completion_tokens": 120}}
        return R()


@contextlib.contextmanager
def _isolated(fake_post=None):
    """Clean provider env + call counts, optionally with a fake Groq transport."""
    keys = ("AI_INTERVIEW_PROVIDER", "GEMINI_API_KEY", "AI_STT_PROVIDER", "AI_TTS_PROVIDER", "GROQ_API_KEY")
    old = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ["AI_INTERVIEW_PROVIDER"] = "groq"        # these tests fake Groq's HTTP endpoint: select it explicitly (default is Gemini)
    os.environ["GROQ_API_KEY"] = "test-key"
    prov.reset_call_counts()
    saved_post = prov.requests.post
    if fake_post:
        prov.requests.post = fake_post
    try:
        yield
    finally:
        prov.requests.post = saved_post
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _gemini_tripwire():
    from google import genai

    calls = {"n": 0}
    original = genai.Client

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("Gemini must not be called")

    genai.Client = boom
    try:
        yield calls
    finally:
        genai.Client = original


def _turns(db, session):
    return db.query(AIInterviewTurnV2).filter(AIInterviewTurnV2.session_id == session.id).order_by(AIInterviewTurnV2.turn_number).all()


def _expect_stage_error(fn, stage, status=None):
    try:
        fn()
    except bridge.VoiceTurnErrorV2 as exc:
        assert exc.stage == stage, (exc.stage, exc.message)
        if status:
            assert exc.status_code == status, exc.status_code
        for banned in (SECRET, "Traceback", "api_key", "GEMINI"):
            assert banned not in exc.message
        return exc
    raise AssertionError("expected VoiceTurnErrorV2")


# ---------------------------------------------------------------- tests
def test_complete_local_voice_turn_success_and_provider_metadata():
    db = _db(); user, app, session = _interview(db)
    groq = FakeGroq(session); stt, tts = FakeStt(), FakeTts()
    with _isolated(groq), _gemini_tripwire() as gem:
        out = bridge.run_voice_turn_v2(db, app, b"candidate-audio", "audio/webm", stt_provider=stt, tts_provider=tts)
        assert gem["n"] == 0
    assert out.transcript == TRANSCRIPT and stt.calls == [(len(b"candidate-audio"), "audio/webm")]
    assert out.response_text == "How did you design the authentication for that API?"
    assert tts.texts == [out.response_text]                                     # TTS speaks exactly the brain's reply
    assert (out.stt_provider, out.brain_provider, out.tts_provider) == ("faster-whisper", "groq", "kokoro")
    assert out.audio_bytes and out.audio_mime_type == "audio/wav" and out.sample_rate == 24000 and out.audio_seconds == 0.5
    assert out.tts_error is None and out.interview_complete is False
    assert out.session["current_question"]["question"] == out.response_text
    assert set(out.latency) == {"stt_seconds", "brain_seconds", "tts_seconds", "total_seconds", "turn_number"}
    assert out.latency["total_seconds"] >= out.latency["stt_seconds"]
    assert out.latency["turn_number"] == 2  # Phase 1: a non-sensitive identifier (the newly-created next turn), matched against logs/voice_turn_latency.jsonl
    # existing persistence: the transcript is the persisted answer, next turn generated_by groq
    turns = _turns(db, session)
    assert turns[0].candidate_answer == TRANSCRIPT and turns[1].generated_by == "groq" and len(turns) == 2


def test_brain_receives_the_transcript_and_full_interview_context():
    db = _db(); user, app, session = _interview(db)
    groq = FakeGroq(session)
    with _isolated(groq):
        bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=FakeTts())
    assert groq.urls == [GROQ_CHAT_URL]
    prompt = groq.payloads[0]["messages"][-1]["content"]
    assert TRANSCRIPT in prompt                                                # the spoken answer reaches the brain verbatim
    assert "Backend Developer" in prompt and "Python, SQL" in prompt           # job context: unchanged
    assert "INTERVIEW COMPETENCIES" in prompt and "CONVERSATION SO FAR" in prompt and "APTITUDE EVIDENCE" in prompt


def test_empty_audio_is_rejected_before_any_provider_is_called():
    db = _db(); user, app, session = _interview(db); stt, tts = FakeStt(), FakeTts()
    with _isolated():
        _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"", "audio/webm", stt_provider=stt, tts_provider=tts), "stt", 400)
    assert stt.calls == [] and tts.texts == [] and len(_turns(db, session)) == 1


def test_stt_failure_is_safe_and_never_reaches_the_brain_or_tts():
    db = _db(); user, app, session = _interview(db)
    stt, tts, groq = FakeStt(raises=VoiceProviderError(f"boom {SECRET}")), FakeTts(), FakeGroq(session)
    with _isolated(groq), _gemini_tripwire() as gem:
        exc = _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=stt, tts_provider=tts), "stt", 502)
        assert gem["n"] == 0
    assert "unavailable" in exc.message.lower()
    assert groq.payloads == [] and tts.texts == []
    turns = _turns(db, session)
    assert len(turns) == 1 and turns[0].candidate_answer is None                # nothing persisted


def test_silence_returns_no_speech_error_and_does_not_call_the_brain():
    db = _db(); user, app, session = _interview(db); groq = FakeGroq(session)
    with _isolated(groq):
        exc = _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(transcript="  "), tts_provider=FakeTts()), "stt", 422)
    assert "no speech" in exc.message.lower() and groq.payloads == []


def test_overlong_transcript_is_rejected():
    db = _db(); user, app, session = _interview(db); groq = FakeGroq(session)
    with _isolated(groq):
        _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(transcript="word " * 2000), tts_provider=FakeTts()), "stt", 422)
    assert groq.payloads == []


def test_brain_failure_is_safe_rolls_back_and_nothing_is_synthesized():
    db = _db(); user, app, session = _interview(db); tts = FakeTts()

    def broken_brain(*a, **k):
        raise RuntimeError(f"brain exploded {SECRET}")

    with _isolated(), _gemini_tripwire() as gem:
        exc = _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=tts, brain=broken_brain), "brain", 502)
        assert gem["n"] == 0
    assert "could not process" in exc.message.lower() and tts.texts == []
    assert len(_turns(db, session)) == 1


def test_brain_business_error_keeps_its_own_status_and_detail():
    db = _db(); user, app, session = _interview(db)

    def refusing_brain(*a, **k):
        raise integration.InterviewIntegrationV2Error(409, "This AI interview has already been completed.")

    with _isolated():
        exc = _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=FakeTts(), brain=refusing_brain), "brain", 409)
    assert "already been completed" in exc.message


def test_groq_down_uses_the_deterministic_engine_honestly_and_never_gemini():
    import requests
    db = _db(); user, app, session = _interview(db); tts = FakeTts()
    with _isolated(FakeGroq(session, raises=requests.ConnectionError("down"))), _gemini_tripwire() as gem:
        os.environ["GEMINI_API_KEY"] = "would-be-used-if-a-fallback-existed"
        out = bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=tts)
        assert gem["n"] == 0
    assert out.brain_provider == "fallback"                                   # reported truthfully, never claimed as groq
    assert out.response_text and tts.texts == [out.response_text]


def test_tts_failure_preserves_transcript_and_reply_and_claims_no_audio():
    db = _db(); user, app, session = _interview(db)
    tts = FakeTts(raises=VoiceProviderError(f"synth boom {SECRET}"))
    with _isolated(FakeGroq(session)), _gemini_tripwire() as gem:
        out = bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=tts)
        assert gem["n"] == 0
    assert out.audio_bytes is None and out.audio_mime_type is None and out.audio_seconds is None
    assert out.transcript == TRANSCRIPT and out.response_text == "How did you design the authentication for that API?"
    assert out.brain_provider == "groq" and out.tts_error and SECRET not in out.tts_error
    assert _turns(db, session)[0].candidate_answer == TRANSCRIPT             # the interview state still advanced


def test_invalid_synthesized_audio_is_treated_as_a_tts_failure():
    bad_results = [
        SynthesizedAudioV2(audio_bytes=b"", mime_type="audio/wav", sample_rate=24000),                 # empty
        SynthesizedAudioV2(audio_bytes=b"not a wav file", mime_type="audio/wav", sample_rate=24000),   # undecodable
        SynthesizedAudioV2(audio_bytes=_wav(), mime_type="text/plain", sample_rate=24000),             # wrong type
        SynthesizedAudioV2(audio_bytes=_wav(), mime_type="audio/wav", sample_rate=0),                  # no rate
    ]
    for bad in bad_results:
        db = _db(); user, app, session = _interview(db)
        with _isolated(FakeGroq(session)):
            out = bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=FakeTts(result=bad))
        assert out.audio_bytes is None and out.tts_error and out.response_text


def test_interview_completion_speaks_a_closing_message():
    db = _db(); user, app, session = _interview(db); tts = FakeTts()

    def finishing_brain(db_, session_, app_, sr_, text):
        return {"interview_complete": True, "session": {"status": "completed", "current_question": None}}

    with _isolated():
        out = bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=FakeStt(), tts_provider=tts, brain=finishing_brain)
    assert out.interview_complete is True and out.response_text == bridge.CLOSING_MESSAGE
    assert tts.texts == [bridge.CLOSING_MESSAGE] and out.audio_bytes


def test_inactive_or_missing_interview_is_refused_before_stt():
    db = _db(); user, app, session = _interview(db); stt = FakeStt()
    session.status = "completed"; db.commit()
    with _isolated():
        _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=stt, tts_provider=FakeTts()), "session", 409)
        session.status = "in_progress"; session.expires_at = datetime.utcnow() - timedelta(minutes=1); db.commit()
        _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=stt, tts_provider=FakeTts()), "session")
    other_job = Job(title="Other Role", company="TestCo", required_skills="Go"); db.add(other_job); db.flush()
    other = JobApplication(job_id=other_job.id, user_id=user.id, status="new"); db.add(other); db.commit()   # an application with NO interview
    with _isolated():
        _expect_stage_error(lambda: bridge.run_voice_turn_v2(db, other, b"a", "audio/webm", stt_provider=stt, tts_provider=FakeTts()), "session", 404)
    assert stt.calls == []


def test_default_providers_are_the_local_ones_with_stable_names():
    with _isolated():
        assert vp.get_stt_provider().provider_name == "faster-whisper"
        assert vp.get_tts_provider().provider_name == "flash_live"                              # the voice default is Gemini Flash Live; local Kokoro is opt-in
        os.environ["AI_TTS_PROVIDER"] = "local"
        assert vp.get_tts_provider().provider_name == "kokoro"
        os.environ.pop("AI_TTS_PROVIDER", None)
        assert prov.get_interview_llm_provider().name == "groq"
        assert vp.GeminiSpeechToTextProviderV2.provider_name == vp.GeminiTextToSpeechProviderV2.provider_name == "gemini"
    assert isinstance(vp.get_stt_provider(), local_stt.LocalWhisperSpeechToTextProviderV2)
    assert isinstance(vp.get_tts_provider(), vp.FlashLiveTextToSpeechProviderV2)


def test_no_gemini_call_in_any_failure_path_even_with_a_key_present():
    import requests
    with _isolated(), _gemini_tripwire() as gem:
        os.environ["GEMINI_API_KEY"] = "present-but-must-never-be-used"
        scenarios = [
            dict(stt=FakeStt(raises=RuntimeError("x")), tts=FakeTts(), groq_raises=None),
            dict(stt=FakeStt(), tts=FakeTts(raises=RuntimeError("x")), groq_raises=None),
            dict(stt=FakeStt(), tts=FakeTts(), groq_raises=requests.ConnectionError("down")),
        ]
        for sc in scenarios:
            db = _db(); user, app, session = _interview(db)
            prov.requests.post = FakeGroq(session, raises=sc["groq_raises"])
            try:
                bridge.run_voice_turn_v2(db, app, b"a", "audio/webm", stt_provider=sc["stt"], tts_provider=sc["tts"])
            except bridge.VoiceTurnErrorV2:
                pass
        assert gem["n"] == 0


def test_route_returns_stage_tagged_json_with_playable_audio_and_enforces_ownership():
    import base64
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.deps import get_current_user
    from backend.app.api.routes import interview_voice_v2 as route
    from backend.app.database.database import get_db

    db = _db(shared=True); user, app_row, session = _interview(db)
    stranger = User(full_name="Other", username="stranger", email="stranger@example.com", hashed_password="x", role="user"); db.add(stranger); db.commit()

    api = FastAPI(); api.include_router(route.router)
    current = {"user": user}
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: current["user"]

    fake_stt, fake_tts = FakeStt(), FakeTts()
    saved = (bridge.get_stt_provider, bridge.get_tts_provider)
    bridge.get_stt_provider, bridge.get_tts_provider = (lambda: fake_stt), (lambda: fake_tts)
    client = TestClient(api, cookies={"csrf_token": "t"})
    url = f"/api/assessment/sessions/{app_row.id}/interview/v2/voice/turn"
    files = lambda: {"audio": ("a.webm", b"candidate-audio", "audio/webm")}
    try:
        with _isolated(FakeGroq(session)):
            ok = client.post(url, files=files(), headers={"X-CSRF-Token": "t"})
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["providers"] == {"stt": "faster-whisper", "brain": "groq", "tts": "kokoro"}
            assert body["transcript"] == TRANSCRIPT and body["tts_error"] is None
            with wave.open(io.BytesIO(base64.b64decode(body["audio"]["base64"])), "rb") as w:
                assert w.getframerate() == 24000 and w.getnframes() > 0
            assert "answer_analysis" not in json.dumps(body["session"])        # no evaluation internals reach the candidate

            assert client.post(url, files=files()).status_code == 403                                     # CSRF still enforced
            assert client.post(url, files={"audio": ("a.txt", b"x", "text/plain")}, headers={"X-CSRF-Token": "t"}).status_code == 400

            fake_stt.raises = VoiceProviderError(SECRET)
            bad = client.post(url, files=files(), headers={"X-CSRF-Token": "t"})
            assert bad.status_code == 502 and bad.json()["detail"]["stage"] == "stt" and SECRET not in bad.text

            current["user"] = stranger
            assert client.post(url, files=files(), headers={"X-CSRF-Token": "t"}).status_code == 404      # someone else's application
    finally:
        bridge.get_stt_provider, bridge.get_tts_provider = saved

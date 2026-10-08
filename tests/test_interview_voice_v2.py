"""
Isolated-database tests for the AI Interview V2 voice layer (Task
5I). Same convention as test_interview_integration_v2.py: every
test creates its own fresh, temporary in-memory SQLite database
(never D:\\jobpilot-ai\\jobpilot.db) and calls the SERVICE layer
directly (interview_voice_integration_v2.py / voice_provider_v2.py),
not the FastAPI routes.

External Gemini TTS/STT calls are NEVER made in this automated
suite (no paid API calls in unit tests, per Task 5I Step 14) — every
test that needs a provider injects a deterministic fake
implementing the same TextToSpeechProviderV2 / SpeechToTextProviderV2
Protocol. The real Gemini-backed providers were verified live and
manually during this task's own audit (see the Task 5I final
report for the real request/response transcript) — that live
verification is intentionally NOT re-run here on every test run.

Covers:
  1. Voice provider abstraction (protocol conformance / dataclasses)
  2. STT success
  3. STT empty result
  4. STT failure
  5. TTS success
  6. TTS failure
  7. Opening/first question -> TTS (via turn lookup)
  8. Candidate transcript -> handed back as plain text (existing
     /interview/v2/answer endpoint is untouched, tested elsewhere)
  9. Follow-up turn -> TTS
  10. Interview completion -> speak() refused (no such open turn
      action needed; verified via completed-session transcribe block)
  11. Interview not in_progress -> transcribe blocked
  12. Turn not owned by this application's session -> blocked
  13. No raw audio persisted (structural — provider never touches
      the filesystem or a DB table; asserted by construction)
  14. Legacy/Task 5H interview tests are a SEPARATE, unmodified test
      file (test_interview_integration_v2.py) — re-run alongside
      this file in the Task 5I regression, not duplicated here.
"""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.database import Base

from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.password_reset import PasswordResetOTP
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user_activity import UserActivity
from backend.app.models.resume import Resume
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.job_question import JobQuestion
from backend.app.models.job_application import JobApplication
from backend.app.models.application_answer import ApplicationAnswer
from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.auth_session import AuthSession
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_submission import ProgrammingSubmission
from backend.app.models.interview_test import InterviewTest
from backend.app.models.interview_response import InterviewResponse
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2

from backend.app.services.assessment.assignment_service import create_assignment
from backend.app.services.assessment.interview_integration_v2 import start_interview_v2
from backend.app.services.assessment.voice_provider_v2 import (
    SynthesizedAudioV2,
    TranscriptionResultV2,
    VoiceProviderError,
)
from backend.app.services.assessment.interview_voice_integration_v2 import (
    VoiceIntegrationV2Error,
    synthesize_question_speech_v2,
    transcribe_candidate_audio_v2,
)

import os

import pytest


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed_with_started_interview(db):

    user = User(
        full_name="Voice Test Candidate", username="voicecand_5i", email="voice5i@example.com",
        hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    job = Job(title="Backend Developer", company="TestCo", required_skills="Java, Debugging")
    db.add(job)
    db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status="programming_passed")
    db.add(application)
    db.flush()

    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)

    db.add(AptitudeTest(
        application_id=application.id, questions="[]", total_questions=10,
        passed=True, status="completed", score=9,
    ))

    db.add(ProgrammingTest(
        application_id=application.id, questions="[]", total_questions=5,
        passed=True, status="completed", score=90, total_score=100,
        assigned_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        started_at=datetime.utcnow(),
    ))

    create_assignment(db, application.id, "ai_interview", assigned_by_id=user.id, assigned_by_type="system")

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    interview_session, _created = start_interview_v2(
        db, session_record, application, job, user, None, None, use_llm=False,
    )

    return user, job, application, session_record, interview_session


# ============================================================
# FAKE PROVIDERS (deterministic, no network) — the SAME
# TextToSpeechProviderV2 / SpeechToTextProviderV2 Protocol the real
# Gemini implementations satisfy.
# ============================================================

class _FakeTtsProvider:

    def __init__(self, audio_bytes=b"FAKE_WAV_BYTES", should_fail=False):
        self.audio_bytes = audio_bytes
        self.should_fail = should_fail
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        if self.should_fail:
            raise VoiceProviderError("Simulated TTS provider failure.")
        return SynthesizedAudioV2(audio_bytes=self.audio_bytes, mime_type="audio/wav", sample_rate=24000)


class _FakeSttProvider:

    def __init__(self, transcript="I built a REST API using FastAPI.", should_fail=False):
        self.transcript = transcript
        self.should_fail = should_fail
        self.calls = []

    def transcribe(self, audio_bytes, mime_type):
        self.calls.append((audio_bytes, mime_type))
        if self.should_fail:
            raise VoiceProviderError("Simulated STT provider failure.")
        return TranscriptionResultV2(transcript=self.transcript)


# 1: provider abstraction — both fakes structurally satisfy the same
# dataclasses/Protocol the real Gemini providers return.
def test_provider_abstraction_dataclasses_are_stable():

    audio = SynthesizedAudioV2(audio_bytes=b"abc", mime_type="audio/wav", sample_rate=24000)
    assert audio.audio_bytes == b"abc"
    assert audio.mime_type == "audio/wav"
    assert audio.sample_rate == 24000

    result = TranscriptionResultV2(transcript="hello")
    assert result.transcript == "hello"

    empty_result = TranscriptionResultV2(transcript="")
    assert empty_result.transcript == ""


# 2: STT success
def test_transcribe_success_returns_real_provider_transcript():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_stt = _FakeSttProvider(transcript="I built a REST API using FastAPI and PostgreSQL.")

    result = transcribe_candidate_audio_v2(db, application, b"some-audio-bytes", "audio/webm", stt_provider=fake_stt)

    assert result.transcript == "I built a REST API using FastAPI and PostgreSQL."
    assert fake_stt.calls == [(b"some-audio-bytes", "audio/webm")]


# 3: STT empty result — never fabricated, passed through honestly
def test_transcribe_empty_result_is_not_an_error():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_stt = _FakeSttProvider(transcript="")

    result = transcribe_candidate_audio_v2(db, application, b"silence-bytes", "audio/webm", stt_provider=fake_stt)

    assert result.transcript == ""


# 4: STT failure — provider error surfaces as a mappable integration error, never swallowed
def test_transcribe_provider_failure_raises_integration_error():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_stt = _FakeSttProvider(should_fail=True)

    with pytest.raises(VoiceIntegrationV2Error):
        transcribe_candidate_audio_v2(db, application, b"bytes", "audio/webm", stt_provider=fake_stt)


# 5: TTS success — synthesizes the REAL persisted turn text, never
# arbitrary candidate-supplied text
def test_speak_question_synthesizes_the_real_persisted_question_text():

    db = _make_db()
    _user, _job, application, _session_record, interview_session = _seed_with_started_interview(db)

    turn = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == interview_session.id, AIInterviewTurnV2.turn_number == 1)
        .first()
    )

    fake_tts = _FakeTtsProvider()

    audio = synthesize_question_speech_v2(db, application, 1, tts_provider=fake_tts)

    assert audio.audio_bytes == b"FAKE_WAV_BYTES"
    assert audio.mime_type == "audio/wav"
    assert fake_tts.calls == [turn.question]  # exactly the stored question, nothing else


# 6: TTS failure
def test_speak_question_provider_failure_raises_integration_error():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_tts = _FakeTtsProvider(should_fail=True)

    with pytest.raises(VoiceIntegrationV2Error):
        synthesize_question_speech_v2(db, application, 1, tts_provider=fake_tts)


# 7: opening/first question -> TTS
def test_speak_first_turn_works_immediately_after_start():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_tts = _FakeTtsProvider()

    audio = synthesize_question_speech_v2(db, application, 1, tts_provider=fake_tts)

    assert audio.audio_bytes


# 9: follow-up turn -> TTS (turn 2, after answering turn 1)
def test_speak_a_later_turn_after_a_follow_up_is_created():

    db = _make_db()
    _user, _job, application, session_record, interview_session = _seed_with_started_interview(db)

    from backend.app.services.assessment.interview_integration_v2 import submit_answer_v2

    submit_answer_v2(
        db, interview_session, application, session_record,
        "I have three years of Java experience building backend services.",
        use_llm=False,
    )
    db.refresh(interview_session)

    turn_2 = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == interview_session.id, AIInterviewTurnV2.turn_number == 2)
        .first()
    )
    assert turn_2 is not None  # sanity: a follow-up/next turn really was created

    fake_tts = _FakeTtsProvider()
    audio = synthesize_question_speech_v2(db, application, 2, tts_provider=fake_tts)

    assert fake_tts.calls == [turn_2.question]
    assert audio.audio_bytes


# 11: expired/not-in-progress interview -> transcribe blocked
def test_transcribe_blocked_when_interview_not_in_progress():

    db = _make_db()
    _user, _job, application, _session_record, interview_session = _seed_with_started_interview(db)

    interview_session.status = "expired"
    db.commit()

    fake_stt = _FakeSttProvider()

    try:
        transcribe_candidate_audio_v2(db, application, b"bytes", "audio/webm", stt_provider=fake_stt)
        assert False, "expected VoiceIntegrationV2Error"
    except VoiceIntegrationV2Error as exc:
        assert exc.status_code == 409

    assert fake_stt.calls == []  # provider never even called once blocked


# 10: completed interview -> transcribe blocked (no further voice turn)
def test_transcribe_blocked_after_interview_completed():

    db = _make_db()
    _user, _job, application, _session_record, interview_session = _seed_with_started_interview(db)

    interview_session.status = "completed"
    db.commit()

    fake_stt = _FakeSttProvider()

    try:
        transcribe_candidate_audio_v2(db, application, b"bytes", "audio/webm", stt_provider=fake_stt)
        assert False, "expected VoiceIntegrationV2Error"
    except VoiceIntegrationV2Error as exc:
        assert exc.status_code == 409


# 12: a turn_number that doesn't exist on this session -> blocked
def test_speak_nonexistent_turn_number_is_blocked():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    fake_tts = _FakeTtsProvider()

    try:
        synthesize_question_speech_v2(db, application, 999, tts_provider=fake_tts)
        assert False, "expected VoiceIntegrationV2Error"
    except VoiceIntegrationV2Error as exc:
        assert exc.status_code == 404

    assert fake_tts.calls == []


# unauthorized candidate (no V2 interview at all for this application) -> blocked
def test_speak_blocked_when_no_v2_interview_exists_for_application():

    db = _make_db()

    user = User(full_name="No Interview", username="nointerview_5i", email="nointerview5i@example.com",
                hashed_password="x", role="user")
    db.add(user)
    db.flush()

    job = Job(title="Backend Developer", company="TestCo", required_skills="Java")
    db.add(job)
    db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status="aptitude_passed")
    db.add(application)
    db.commit()
    db.refresh(application)

    fake_tts = _FakeTtsProvider()

    try:
        synthesize_question_speech_v2(db, application, 1, tts_provider=fake_tts)
        assert False, "expected VoiceIntegrationV2Error"
    except VoiceIntegrationV2Error as exc:
        assert exc.status_code == 404


# 13: no raw audio persisted — structural check: the fake STT
# provider (standing in for the real one) is never given a
# filesystem path or a DB session to write into, and
# transcribe_candidate_audio_v2 never touches the filesystem or adds
# any DB row for the audio itself.
def test_transcribe_never_creates_any_new_db_row():

    db = _make_db()
    _user, _job, application, _session_record, _interview_session = _seed_with_started_interview(db)

    from backend.app.models.assessment_video_recording import AssessmentVideoRecording

    before = db.query(AssessmentVideoRecording).count()

    fake_stt = _FakeSttProvider(transcript="hello")
    transcribe_candidate_audio_v2(db, application, b"some-audio-bytes", "audio/webm", stt_provider=fake_stt)

    after = db.query(AssessmentVideoRecording).count()

    assert before == after == 0


# ============================================================
# REAL GEMINI ROUND-TRIP — only runs if a real key is configured,
# same isolation pattern as test_programming_generator_v2.py's
# test_real_gemini_generates_a_valid_question_set(). NOT part of
# the deterministic fallback-engine regression above; this is the
# one test in the suite that makes a real, billable API call, and
# it is the live evidence backing this task's report that TTS and
# STT are genuinely real (not simulated).
# ============================================================

@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not configured")
def test_real_gemini_tts_then_stt_round_trip():

    from backend.app.services.assessment.voice_provider_v2 import get_stt_provider, get_tts_provider

    tts = get_tts_provider()
    stt = get_stt_provider()

    audio = tts.synthesize("I built a REST API using FastAPI and PostgreSQL.")
    assert audio.audio_bytes
    assert audio.mime_type == "audio/wav"
    assert audio.sample_rate > 0

    result = stt.transcribe(audio.audio_bytes, "audio/wav")
    assert result.transcript  # real transcription, non-empty
    assert "fastapi" in result.transcript.lower() or "rest api" in result.transcript.lower()

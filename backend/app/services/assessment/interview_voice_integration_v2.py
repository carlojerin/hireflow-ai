import io
import logging
import inspect
from datetime import datetime
import os
import json
import time
import wave
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.job_application import JobApplication

from backend.app.services.assessment.interview_integration_v2 import (
    InterviewIntegrationV2Error,
    expire_if_due_v2,
    submit_answer_v2,
)
from backend.app.services.assessment.session_service import get_session_for_application
from backend.app.services.assessment.voice_provider_v2 import (
    MAX_STT_AUDIO_BYTES,
    SynthesizedAudioV2,
    TranscriptionResultV2,
    VoiceProviderError,
    get_stt_provider,
    get_tts_provider,
)


logger = logging.getLogger("jobpilot.assessment.voice_turn")


# ============================================================
# JOBPILOT AI - AI INTERVIEW V2 VOICE <-> SESSION BRIDGE (Task 5I)
#
# Testable business logic for the voice layer, kept separate from
# interview_voice_v2.py (the thin FastAPI route wrappers — auth,
# CSRF, HTTPException translation) exactly the way
# interview_integration_v2.py separates from assessment_sessions.py.
#
# NO interview decision logic lives here either — these two
# functions only ever (1) look up an already-persisted, owned
# question and hand it to a TTS provider, or (2) hand uploaded
# audio bytes to an STT provider and return the transcript. Neither
# function calls interview_brain_v2.py, writes to
# AIInterviewTurnV2.candidate_answer, or changes session.status.
# Submitting the transcribed answer remains a SEPARATE, explicit
# call to the EXISTING submit_answer_v2() from the browser.
#
# run_voice_turn_v2() (Task 3, below) is the ONE orchestrator that
# chains them for a complete local voice turn:
#
#   candidate audio -> STT provider -> EXISTING submit_answer_v2()
#   (interview brain: unchanged, full context) -> TTS provider
#
# It is glue only: it makes no interview decision, builds no prompt,
# and never simplifies what the brain receives -- submit_answer_v2
# loads the same job/candidate/resume/blueprint/evidence/turn state
# it always did. No provider fallback exists anywhere in this chain:
# a failing stage reports its own safe error and stops.
# ============================================================


class VoiceIntegrationV2Error(Exception):

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _get_owned_interview_v2_session(db: Session, application: JobApplication) -> AIInterviewSessionV2:

    session = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not session:
        raise VoiceIntegrationV2Error(404, "No V2 AI interview exists for this application yet.")

    return session


def synthesize_question_speech_v2(
    db: Session,
    application: JobApplication,
    turn_number: int,
    tts_provider=None,
) -> SynthesizedAudioV2:
    """
    Ownership is the caller's responsibility (the FastAPI route
    already resolves `application` via get_owned_application()
    before this is called) — this function only additionally
    verifies the requested turn actually belongs to THIS
    application's own interview session.
    """

    session = _get_owned_interview_v2_session(db, application)

    turn = (
        db.query(AIInterviewTurnV2)
        .filter(
            AIInterviewTurnV2.session_id == session.id,
            AIInterviewTurnV2.turn_number == turn_number,
        )
        .first()
    )

    if not turn:
        raise VoiceIntegrationV2Error(404, "This interview turn does not exist.")

    provider = tts_provider or get_tts_provider()

    try:
        return provider.synthesize(turn.question)
    except VoiceProviderError as exc:
        raise VoiceIntegrationV2Error(502, str(exc))


def transcribe_candidate_audio_v2(
    db: Session,
    application: JobApplication,
    audio_bytes: bytes,
    mime_type: str,
    stt_provider=None,
) -> TranscriptionResultV2:

    session = _get_owned_interview_v2_session(db, application)

    if session.status != "in_progress":
        raise VoiceIntegrationV2Error(409, "This AI interview is not currently active.")

    provider = stt_provider or get_stt_provider()

    try:
        return provider.transcribe(audio_bytes, mime_type)
    except VoiceProviderError as exc:
        raise VoiceIntegrationV2Error(502, str(exc))


# ============================================================
# COMPLETE LOCAL VOICE TURN (Task 3)
# ============================================================

MAX_ANSWER_CHARS = 8000  # same limit SubmitInterviewAnswerV2Request enforces on typed answers

CLOSING_MESSAGE = "Thank you. That completes the interview. Your responses have been recorded for the hiring team."

# Fixed, candidate-safe messages: never an exception text, path, key or trace.
_MSG_STT_FAILED = "Speech recognition is unavailable right now. Please try again, or type your answer."
_MSG_NO_SPEECH = "No speech was detected in the recording. Please try again."
_MSG_BRAIN_FAILED = "The interviewer could not process your answer right now. Please try again."
_MSG_TTS_FAILED = "The interviewer's voice is unavailable right now. Please read the reply below."


class VoiceTurnErrorV2(Exception):
    """
    A voice-turn stage failed BEFORE anything was committed that the
    candidate must not lose: stage is "session" | "stt" | "brain".
    (A TTS failure is NOT an error: the answer and the interviewer's
    reply already exist, so they are returned with `tts_error` set.)
    """

    def __init__(self, stage: str, status_code: int, message: str):
        self.stage = stage
        self.status_code = status_code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class VoiceTurnOutputV2:
    transcript: str
    response_text: str
    audio_bytes: bytes | None
    audio_mime_type: str | None
    sample_rate: int | None
    audio_seconds: float | None
    stt_provider: str
    brain_provider: str          # provider name ("gemini" default, "groq" if configured) decided; "fallback" = its deterministic engine did
    tts_provider: str
    tts_error: str | None
    interview_complete: bool
    session: dict                # the existing candidate-safe view (no evaluation internals)
    latency: dict = field(default_factory=dict)


def _provider_name(provider, default: str) -> str:
    return getattr(provider, "provider_name", None) or default


def _validate_synthesized_audio(audio) -> float:
    """Returns the audio duration in seconds, or raises VoiceProviderError."""

    data = getattr(audio, "audio_bytes", None)

    if not data or not isinstance(data, (bytes, bytearray)):
        raise VoiceProviderError("Speech synthesis returned no audio.")

    if not str(getattr(audio, "mime_type", "")).startswith("audio/") or not getattr(audio, "sample_rate", 0):
        raise VoiceProviderError("Speech synthesis returned invalid audio metadata.")

    if audio.mime_type == "audio/wav":

        try:
            with wave.open(io.BytesIO(bytes(data)), "rb") as wav:
                frames, rate = wav.getnframes(), wav.getframerate()
        except Exception as exc:
            raise VoiceProviderError("Speech synthesis returned undecodable audio.") from exc

        if frames <= 0 or rate <= 0:
            raise VoiceProviderError("Speech synthesis returned empty audio.")

        return round(frames / rate, 2)

    return 0.0


def _stt_vocabulary_hint(db, application) -> str | None:
    """The job's and candidate's own technical terms, so recognition favours "FastAPI" over "fast a p i" / "APA Roots"."""

    try:
        from backend.app.models.candidate_profile import CandidateProfile
        from backend.app.models.job import Job

        job = db.query(Job).filter(Job.id == application.job_id).first()
        profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == application.user_id).first()
        terms: list[str] = []
        for source in (
            getattr(job, "required_skills", None), getattr(job, "technologies", None),
            getattr(job, "preferred_skills", None), getattr(profile, "skills", None),
        ):
            for term in str(source or "").replace("[", "").replace("]", "").replace('"', "").split(","):
                term = term.strip()
                if term and term.lower() not in {t.lower() for t in terms}:
                    terms.append(term)
        if not terms:
            return None
        return ("Technical job interview. Terms that may be spoken: " + ", ".join(terms))[:400]
    except Exception:
        return None


def _log_turn_latency(stt_s, brain_s, tts_s, total_s, brain_provider, answer_chars, reply_chars, audio_s, session_id=None, turn_number=None) -> None:
    """One line per voice turn (numbers only, never text) so a slow interview can be diagnosed afterwards: logs/voice_turn_latency.jsonl.
    session_id/turn_number: a non-sensitive request identifier (Phase 1) -- just integers, never a candidate-identifying value -- so a
    given turn's server-side breakdown here can be matched against the SAME turn's client-side timing (see interview-voice-v2.js's
    [voice-turn-latency] console.debug lines), without ever comparing the two different clocks directly."""

    line = {
        "at": datetime.utcnow().isoformat(timespec="seconds"), "session_id": session_id, "turn_number": turn_number,
        "stt_s": round(stt_s, 2), "brain_s": round(brain_s, 2), "tts_s": round(tts_s, 2),
        "total_s": round(total_s, 2), "brain": brain_provider, "answer_chars": answer_chars, "reply_chars": reply_chars,
        "reply_audio_s": round(audio_s, 1) if audio_s else None,
    }

    logger.info("voice turn latency %s", line)

    try:
        os.makedirs("logs", exist_ok=True)
        with open(os.path.join("logs", "voice_turn_latency.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except Exception:
        pass


def run_voice_turn_v2(
    db: Session,
    application: JobApplication,
    audio_bytes: bytes,
    mime_type: str,
    stt_provider=None,
    tts_provider=None,
    brain=None,
) -> VoiceTurnOutputV2:
    """
    One complete local voice turn. Ownership is the caller's
    responsibility (as for the other functions in this module).

    Stage failures are independent and NEVER trigger another provider:
      STT   -> VoiceTurnErrorV2("stt"): the brain is not called
      brain -> VoiceTurnErrorV2("brain"): nothing is synthesized
      TTS   -> returned normally with audio None + tts_error, and the
               transcript/response text preserved
    """

    started_total = time.perf_counter()

    try:
        session = _get_owned_interview_v2_session(db, application)
    except VoiceIntegrationV2Error as exc:
        raise VoiceTurnErrorV2("session", exc.status_code, exc.detail)

    session_record = get_session_for_application(db, application.id)

    # Same guards the typed-answer route applies before it accepts an answer.
    if expire_if_due_v2(db, session, application, session_record):
        raise VoiceTurnErrorV2("session", 410, "This AI interview has expired.")

    if session.status != "in_progress":
        raise VoiceTurnErrorV2("session", 409, "This AI interview is not currently active.")

    # ---- 1. STT -------------------------------------------------------
    if not audio_bytes:
        raise VoiceTurnErrorV2("stt", 400, "Empty audio upload.")

    if len(audio_bytes) > MAX_STT_AUDIO_BYTES:
        raise VoiceTurnErrorV2("stt", 400, "The recording is too large.")

    stt = stt_provider or get_stt_provider()
    tts = tts_provider or get_tts_provider()
    stt_name = _provider_name(stt, "unknown-stt")
    tts_name = _provider_name(tts, "unknown-tts")

    started = time.perf_counter()

    try:
        hint = _stt_vocabulary_hint(db, application)
        if hint and "prompt" in inspect.signature(stt.transcribe).parameters:
            transcript = stt.transcribe(audio_bytes, mime_type, prompt=hint).transcript.strip()
        else:
            transcript = stt.transcribe(audio_bytes, mime_type).transcript.strip()
    except Exception as exc:
        logger.warning("voice turn: STT stage failed (%s): %s", type(exc).__name__, exc)
        raise VoiceTurnErrorV2("stt", 502, _MSG_STT_FAILED)

    stt_seconds = time.perf_counter() - started

    from backend.app.services.assessment.voice_provider_v2 import is_hint_echo

    if transcript and is_hint_echo(transcript, hint if 'hint' in locals() else None):
        transcript = ""                                # the model handed the vocabulary hint back for silence/noise: no speech

    if not transcript:
        raise VoiceTurnErrorV2("stt", 422, _MSG_NO_SPEECH)

    if len(transcript) > MAX_ANSWER_CHARS:
        raise VoiceTurnErrorV2("stt", 422, "The recorded answer is too long. Please shorten it and try again.")

    # ---- 2. Interview brain (the EXISTING path, unchanged) ---------
    brain_fn = brain or submit_answer_v2

    started = time.perf_counter()

    try:
        result = brain_fn(db, session, application, session_record, transcript)
    except InterviewIntegrationV2Error as exc:
        db.rollback()
        raise VoiceTurnErrorV2("brain", exc.status_code, exc.detail)
    except Exception as exc:
        db.rollback()
        logger.warning("voice turn: interview brain stage failed (%s)", type(exc).__name__)
        raise VoiceTurnErrorV2("brain", 502, _MSG_BRAIN_FAILED)

    brain_seconds = time.perf_counter() - started

    view = result["session"]
    complete = bool(result.get("interview_complete"))

    if complete:
        response_text = CLOSING_MESSAGE
    else:
        response_text = ((view.get("current_question") or {}).get("question") or "").strip()

    # Which engine produced this decision: the turn the brain just created
    # (generated_by is the configured provider name, "fallback" otherwise).
    latest_turn = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.desc())
        .first()
    )
    brain_provider = (latest_turn.generated_by if latest_turn else None) or "unknown"

    # ---- 3. TTS ------------------------------------------------------------
    audio = None
    audio_seconds = None
    tts_error = None

    started = time.perf_counter()

    if not response_text:
        tts_error = _MSG_TTS_FAILED
    else:
        try:
            audio = tts.synthesize(response_text)
            audio_seconds = _validate_synthesized_audio(audio)
        except Exception as exc:
            logger.warning("voice turn: TTS stage failed (%s)", type(exc).__name__)
            audio, audio_seconds, tts_error = None, None, _MSG_TTS_FAILED

    tts_seconds = time.perf_counter() - started

    _log_turn_latency(
        stt_seconds, brain_seconds, tts_seconds, time.perf_counter() - started_total, brain_provider,
        len(transcript), len(response_text or ""), audio_seconds,
        session_id=session.id, turn_number=latest_turn.turn_number if latest_turn else None,
    )

    return VoiceTurnOutputV2(
        transcript=transcript,
        response_text=response_text,
        audio_bytes=audio.audio_bytes if audio else None,
        audio_mime_type=audio.mime_type if audio else None,
        sample_rate=audio.sample_rate if audio else None,
        audio_seconds=audio_seconds,
        stt_provider=stt_name,
        brain_provider=brain_provider,
        tts_provider=tts_name,
        tts_error=tts_error,
        interview_complete=complete,
        session=view,
        latency={
            "stt_seconds": round(stt_seconds, 3),
            "brain_seconds": round(brain_seconds, 3),
            "tts_seconds": round(tts_seconds, 3),
            "total_seconds": round(time.perf_counter() - started_total, 3),
            # Phase 1: a non-sensitive identifier so the client's own [voice-turn-latency] console line for this
            # SAME turn can be matched against this row in logs/voice_turn_latency.jsonl -- never used to align clocks.
            "turn_number": latest_turn.turn_number if latest_turn else None,
        },
    )

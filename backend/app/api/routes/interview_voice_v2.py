import base64

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.database.database import get_db

from backend.app.models.user import User

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.services.assessment.session_service import get_owned_application
from backend.app.services.assessment.interview_voice_integration_v2 import (
    VoiceIntegrationV2Error,
    VoiceTurnErrorV2,
    run_voice_turn_v2,
    synthesize_question_speech_v2,
    transcribe_candidate_audio_v2,
)
from backend.app.services.assessment import livekit_audio_transport_v2 as audio_transport
from backend.app.services.assessment.interview_spoken_notices_v2 import synthesize_notice
from backend.app.services.assessment.voice_provider_v2 import VoiceProviderError


# ============================================================
# JOBPILOT AI - AI INTERVIEW V2 VOICE LAYER (Task 5I)
#
# ADDITIVE ONLY. Sits entirely ON TOP of the existing, unchanged
# Task 5H interview brain (interview_brain_v2.py /
# interview_integration_v2.py) and its existing routes
# (/interview/v2/start, GET /interview/v2, /interview/v2/answer
# in assessment_sessions.py — none of them are touched by this
# file). Thin wrappers only: auth/ownership/CSRF here, real logic
# in interview_voice_integration_v2.py (mirrors how
# assessment_sessions.py wraps interview_integration_v2.py).
#
# This router makes ZERO interview decisions: it never reads or
# writes AnswerAnalysisV2, next_action, competency evidence, or
# session status transitions. It cannot advance, finalize, or
# reinterpret the interview in any way — it can only speak an
# already-decided question, or listen for an answer that the
# EXISTING brain endpoint still evaluates exactly as it always has.
#
# SECURITY — no open text-to-speech proxy: /voice/speak takes a
# `turn_number`, never candidate-supplied free text. The server
# looks up the OWNED, PERSISTED AIInterviewTurnV2.question for
# that turn and synthesizes exactly that — the candidate can only
# ever hear questions their own interview actually asked.
#
# PRIVACY (Section 9): /voice/transcribe processes the uploaded
# audio with the LOCAL faster-whisper provider by default (the audio
# never leaves this machine; Gemini STT is opt-in only via
# AI_STT_PROVIDER=gemini). The audio exists on disk only in a
# temporary file that is always deleted, and is never persisted to
# any database table. No audio-storage table was created.
# ============================================================


router = APIRouter(
    prefix="/api/assessment/sessions",
    tags=["AI Interview V2 Voice"],
)


logger = logging.getLogger("jobpilot.assessment.voice")

MAX_UPLOAD_AUDIO_BYTES = 15 * 1024 * 1024

TRANSPORT_LOCAL = "local"
TRANSPORT_LIVEKIT = "livekit"

ALLOWED_AUDIO_MIME_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/wav",
    "audio/mp4",
    "audio/mpeg",
    "audio/x-m4a",
}


def _base_mime_type(mime_type: str) -> str:
    return (mime_type or "").split(";")[0].strip().lower()


def _normalize_transport(value) -> str:
    return TRANSPORT_LIVEKIT if (value or "").strip().lower() == TRANSPORT_LIVEKIT else TRANSPORT_LOCAL


async def _deliver_via_livekit(application_id: int, wav_bytes: bytes, close_after: bool = False) -> dict:
    """
    Publishes ALREADY-SYNTHESIZED audio through the interview's stable LiveKit
    publisher. Returns {"delivered": True, ...metrics} or {"delivered": False,
    "error": <safe text>}. Never raises: the caller then falls back to
    returning the same audio for local playback (it is never re-synthesized,
    and never a different provider).
    """

    if not audio_transport.is_livekit_configured():
        return {"delivered": False, "error": "LiveKit is not configured."}

    try:
        publisher = await audio_transport.get_publisher(application_id)
        metrics = await publisher.speak(wav_bytes, close_after=close_after)
    except audio_transport.AudioTransportError as exc:
        return {"delivered": False, "error": str(exc)}
    except Exception as exc:
        logger.warning("livekit delivery failed (%s)", type(exc).__name__)
        return {"delivered": False, "error": f"LiveKit delivery failed ({type(exc).__name__})."}

    return {"delivered": True, **metrics}


@router.post("/{application_id}/interview/v2/voice/speak")
async def speak_interview_question_v2(
    application_id: int,
    turn_number: int,
    request: Request,
    transport: str = "local",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    try:
        audio = await run_in_threadpool(synthesize_question_speech_v2, db, application, turn_number)
    except VoiceIntegrationV2Error as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if _normalize_transport(transport) == TRANSPORT_LIVEKIT:

        delivery = await _deliver_via_livekit(application.id, audio.audio_bytes)

        if delivery["delivered"]:
            return {
                "audio_delivery": TRANSPORT_LIVEKIT,
                "speech_seq": delivery["seq"],
                "audio_seconds": delivery["audio_seconds"],
            }

        # LiveKit was asked for but could not carry it: the same audio goes back for local playback.
        return Response(
            content=audio.audio_bytes,
            media_type=audio.mime_type,
            headers={"X-Audio-Delivery": TRANSPORT_LOCAL, "X-Livekit-Error": delivery["error"][:120]},
        )

    return Response(content=audio.audio_bytes, media_type=audio.mime_type, headers={"X-Audio-Delivery": TRANSPORT_LOCAL})


@router.post("/{application_id}/interview/v2/voice/say")
async def say_fixed_interview_line_v2(
    application_id: int,
    kind: str,
    request: Request,
    event: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The AI's welcome and its spoken integrity notices. Fixed texts only: the browser sends a kind + event name."""

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    # In a realtime interview the interviewer's own voice (Gemini Live) says the notice, so it is the same voice as the interview
    if kind == "notice":
        from backend.app.services.assessment import realtime_interview_v2 as realtime
        from backend.app.services.assessment.interview_spoken_notices_v2 import NOTICE_TEXT

        live = realtime.peek_session(application.id)
        text = NOTICE_TEXT.get(event or "")

        if live is not None and text and await live.say_notice(text):
            return {"audio_delivery": "realtime"}

    try:
        audio = await run_in_threadpool(synthesize_notice, kind, event, application, db)
    except VoiceProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    if audio is None:
        raise HTTPException(status_code=400, detail="Unknown spoken line.")

    return Response(content=audio.audio_bytes, media_type=audio.mime_type, headers={"X-Audio-Delivery": "local"})


@router.post("/{application_id}/interview/v2/voice/transcribe")
async def transcribe_interview_answer_v2(
    application_id: int,
    request: Request,
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    mime_type = _base_mime_type(audio.content_type)

    if mime_type not in ALLOWED_AUDIO_MIME_TYPES:

        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type: {audio.content_type or 'unknown'}.",
        )

    audio_bytes = await audio.read()

    if not audio_bytes:

        raise HTTPException(status_code=400, detail="Empty audio upload.")

    if len(audio_bytes) > MAX_UPLOAD_AUDIO_BYTES:

        raise HTTPException(
            status_code=400,
            detail=f"Audio exceeds the maximum allowed size ({MAX_UPLOAD_AUDIO_BYTES // (1024 * 1024)} MB).",
        )

    try:
        result = transcribe_candidate_audio_v2(db, application, audio_bytes, mime_type)
    except VoiceIntegrationV2Error as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return {"transcript": result.transcript}


# ============================================================
# COMPLETE LOCAL VOICE TURN (Task 3)
#
# audio -> local STT -> the EXISTING interview brain -> local TTS,
# in one request. Same auth/ownership/CSRF/MIME rules as /transcribe.
# The blocking local models (STT -> brain -> Kokoro) run in the threadpool
# so the event loop stays free (Task 6: the route is async only because
# publishing to LiveKit is async).
#
# Task 6: an optional `transport` form field (local | livekit, default
# local). Kokoro synthesizes ONCE per turn either way. With `livekit`, the
# same bytes are published through the interview's stable LiveKit
# publisher and NO inline audio is returned (so the browser cannot also
# play it); if publishing fails, the same already-synthesized audio is
# returned inline instead (`audio_delivery: "local"` + `livekit_error`).
# STT/brain failures return a stage-tagged, candidate-safe error; a TTS
# failure still returns 200 with the reply text and `tts_error`.
# ============================================================

@router.post("/{application_id}/interview/v2/voice/turn")
async def voice_turn_interview_v2(
    application_id: int,
    request: Request,
    audio: UploadFile = File(...),
    transport: str = Form(TRANSPORT_LOCAL),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    mime_type = _base_mime_type(audio.content_type)

    if mime_type not in ALLOWED_AUDIO_MIME_TYPES:

        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type: {audio.content_type or 'unknown'}.",
        )

    audio_bytes = await audio.read(MAX_UPLOAD_AUDIO_BYTES + 1)

    if len(audio_bytes) > MAX_UPLOAD_AUDIO_BYTES:

        raise HTTPException(
            status_code=400,
            detail=f"Audio exceeds the maximum allowed size ({MAX_UPLOAD_AUDIO_BYTES // (1024 * 1024)} MB).",
        )

    try:
        turn = await run_in_threadpool(run_voice_turn_v2, db, application, audio_bytes, mime_type)
    except VoiceTurnErrorV2 as exc:
        raise HTTPException(status_code=exc.status_code, detail={"stage": exc.stage, "message": exc.message})

    delivery = {"delivered": False}
    requested = _normalize_transport(transport)

    if requested == TRANSPORT_LIVEKIT and turn.audio_bytes:
        delivery = await _deliver_via_livekit(application.id, turn.audio_bytes, close_after=turn.interview_complete)

    latency = dict(turn.latency or {})

    if delivery["delivered"]:
        latency["livekit_first_frame_seconds"] = delivery["first_frame_seconds"]
        latency["livekit_connect_seconds"] = delivery["connect_seconds"]

    return {
        "transcript": turn.transcript,
        "response_text": turn.response_text,
        "interview_complete": turn.interview_complete,
        "session": turn.session,
        "audio": (
            {
                "base64": base64.b64encode(turn.audio_bytes).decode("ascii"),
                "mime_type": turn.audio_mime_type,
                "sample_rate": turn.sample_rate,
                "seconds": turn.audio_seconds,
            }
            if turn.audio_bytes and not delivery["delivered"] else None
        ),
        "audio_delivery": TRANSPORT_LIVEKIT if delivery["delivered"] else TRANSPORT_LOCAL,
        "speech_seq": delivery.get("seq") if delivery["delivered"] else None,
        "livekit": (
            {
                "audio_seconds": delivery["audio_seconds"],
                "reused_connection": delivery["reused_connection"],
                "reused_track": delivery["reused_track"],
                "mode": delivery.get("mode", "track"),
            }
            if delivery["delivered"] else None
        ),
        "livekit_error": delivery.get("error") if requested == TRANSPORT_LIVEKIT and not delivery["delivered"] and turn.audio_bytes else None,
        "tts_error": turn.tts_error,
        "providers": {
            "stt": turn.stt_provider,
            "brain": turn.brain_provider,
            "tts": turn.tts_provider,
        },
        "latency": latency,
    }


# ============================================================
# INTERRUPT (Task 6): the candidate started speaking over the AI while its
# audio was arriving through LiveKit. Stops the interview's publisher at
# once and drops anything still queued, so no stale audio can play.
# Auth/ownership/CSRF exactly like every other route here; it can only ever
# touch the caller's OWN application's publisher.
# ============================================================

@router.post("/{application_id}/interview/v2/voice/interrupt")
async def interrupt_interview_speech_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    publisher = audio_transport.peek_publisher(application.id)

    interrupted = await publisher.interrupt() if publisher is not None else False

    return {"interrupted": interrupted}

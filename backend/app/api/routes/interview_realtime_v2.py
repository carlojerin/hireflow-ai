from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.database.database import get_db
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.user import User
from backend.app.services.assessment import realtime_interview_v2 as realtime
from backend.app.services.assessment.avatar_provider_v2 import AvatarConfigError
from backend.app.services.assessment.livekit_audio_transport_v2 import AudioTransportError, is_livekit_configured
from backend.app.services.assessment.session_service import get_owned_application


# ============================================================
# JOBPILOT AI - REALTIME CONVERSATIONAL INTERVIEW ROUTES (Task 9)
#
# start  -> starts the ONE realtime conversational session of the caller's OWN active
#           interview (idempotent). With INTERVIEW_REALTIME_PROVIDER not set to "realtime"
#           it answers {"state": "disabled"} and starts nothing: the browser then uses the
#           existing voice-turn pipeline. It NEVER falls back to another provider by itself.
# status -> honest state for the page.
# stop   -> ends the session (the interview record is untouched).
# Same auth / ownership / CSRF / active-interview rules as every other interview route;
# provider keys never leave the server.
# ============================================================

router = APIRouter(
    prefix="/api/assessment/sessions",
    tags=["AI Interview V2 Realtime"],
)


def _owned_active_interview(db, application_id: int, user_id: int):

    application = get_owned_application(db, application_id, user_id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    interview = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not interview or interview.status != "in_progress":
        raise HTTPException(status_code=409, detail="There is no active AI interview for this application.")

    return application


@router.post("/{application_id}/interview/v2/realtime/start")
async def start_realtime_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = _owned_active_interview(db, application_id, current_user.id)

    provider = realtime.get_realtime_provider()

    if provider != realtime.PROVIDER_REALTIME:
        return {"state": "disabled", "provider": provider, "error": None}

    if not realtime.is_realtime_configured():
        raise HTTPException(status_code=503, detail="The realtime interview provider is not configured.")

    if not is_livekit_configured():
        raise HTTPException(status_code=503, detail="LiveKit is not configured.")

    try:
        session = await realtime.start_realtime_session(application.id)
    except (AudioTransportError, AvatarConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return session.status()


@router.post("/{application_id}/interview/v2/realtime/text")
async def realtime_typed_answer_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = _owned_active_interview(db, application_id, current_user.id)

    body = await request.json()
    text = str((body or {}).get("answer_text") or "").strip()[:4000]

    if not text:
        raise HTTPException(status_code=400, detail="Please type an answer first.")

    session = realtime.peek_session(application.id)

    if session is None or not await session.submit_typed_answer(text):
        raise HTTPException(status_code=409, detail="The live interview is not ready for a typed answer right now.")

    return {"state": "accepted"}


@router.get("/{application_id}/interview/v2/realtime/status")
async def realtime_status_v2(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    provider = realtime.get_realtime_provider()
    session = realtime.peek_session(application.id)

    if session is None:
        return {"state": "idle" if provider == realtime.PROVIDER_REALTIME else "disabled", "provider": provider, "error": None}

    return session.status()


@router.post("/{application_id}/interview/v2/realtime/stop")
async def stop_realtime_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    session = realtime.peek_session(application.id)

    if session is not None:
        await session.close()

    return {"state": "closed"}

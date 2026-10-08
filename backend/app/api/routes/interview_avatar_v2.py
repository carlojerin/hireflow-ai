from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.user import User

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.services.assessment.session_service import get_owned_application
from backend.app.services.assessment.avatar_provider_v2 import AvatarConfigError, VISUAL_MODE_IMAGE, get_visual_mode
from backend.app.services.assessment.avatar_token_service_v2 import (
    generate_avatar_room_token_v2,
)
from backend.app.services.assessment.livekit_audio_transport_v2 import (
    AudioTransportError,
    get_publisher,
    peek_publisher,
)
from backend.app.services.assessment.tavus_avatar_session_v2 import is_tavus_configured


# ============================================================
# JOBPILOT AI - AI INTERVIEW V2 AVATAR LAYER (Task 5K)
#
# ADDITIVE ONLY. Sits on top of the existing, unmodified Task 5H
# brain and Task 5I/5J voice layer — this router never reads or
# writes AIInterviewTurnV2, never calls interview_brain_v2.py, and
# cannot influence a single interview decision (it only READS whether the
# interview session is in_progress, so a room token is never minted for an
# interview that is not active). It does exactly one
# thing: mint a scoped, short-lived LiveKit room token for the
# CALLER'S OWN interview, so their browser can (if a real avatar
# provider is configured) display the fixed AI interviewer avatar.
#
# Same auth/ownership/CSRF pattern as interview_voice_v2.py.
# ============================================================


router = APIRouter(
    prefix="/api/assessment/sessions",
    tags=["AI Interview V2 Avatar"],
)


@router.post("/{application_id}/interview/v2/avatar/token")
def get_avatar_room_token_v2(
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

    # Exactly one room per ACTIVE interview: no token for an application whose
    # V2 interview was never started, or is already completed/expired.
    interview = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not interview or interview.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="There is no active AI interview for this application.",
        )

    try:
        return generate_avatar_room_token_v2(application)
    except AvatarConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ============================================================
# TASK 7: the Tavus avatar inside the interview room.
#
# start  -> attaches the ONE fixed Tavus avatar to this interview's stable
#           publisher connection (idempotent). The avatar then shows the
#           SAME Kokoro response the voice flow already produces; this route
#           never synthesizes or decides anything.
# status -> honest state for the page: idle | starting | ready | failed.
# Same auth / ownership / CSRF / active-interview rules as the token route.
# If the avatar cannot start, the voice interview keeps working (audio-only).
# ============================================================

def _owned_active_interview(db, application_id: int, user_id: int):

    application = get_owned_application(db, application_id, user_id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    interview = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not interview or interview.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="There is no active AI interview for this application.",
        )

    return application


@router.post("/{application_id}/interview/v2/avatar/start")
async def start_avatar_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = _owned_active_interview(db, application_id, current_user.id)

    if get_visual_mode() == VISUAL_MODE_IMAGE:

        # Fixed-image mode (the default): no live avatar, Tavus is never contacted.
        return {"state": "disabled", "error": None, "visual_mode": VISUAL_MODE_IMAGE}

    if not is_tavus_configured():

        raise HTTPException(status_code=503, detail="The AI interviewer avatar is not configured.")

    try:
        publisher = await get_publisher(application.id)
        return await publisher.start_avatar()
    except AudioTransportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/{application_id}/interview/v2/avatar/status")
async def avatar_status_v2(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    publisher = peek_publisher(application.id)

    return publisher.avatar_status() if publisher is not None else {"state": "idle", "error": None}

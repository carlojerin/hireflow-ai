from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.database.database import get_db
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.user import User
from backend.app.services.assessment import realtime_interview_v2 as realtime
from backend.app.services.assessment.avatar_provider_v2 import AvatarConfigError
from backend.app.services.assessment.avatar_token_service_v2 import generate_avatar_room_token_v2
from backend.app.services.assessment.livekit_audio_transport_v2 import AudioTransportError, is_livekit_configured
from backend.app.services.assessment.session_service import get_owned_application
from backend.app.services.sjt.practical_roleplay import RolePlayWork, load_row


# ============================================================
# JOBPILOT AI - PRACTICAL ROLE-PLAY CALL (voice)
#
# The voice role-play of a practical assessment: the candidate speaks with an AI customer / prospect / client. It runs on the SAME live voice
# engine and LiveKit room as the AI interview, but for the candidate's practical test (stage 3) instead of an interview session, so these routes
# mirror interview_avatar_v2 / interview_realtime_v2 with one difference: the gate is "this application has an in-progress role-play practical
# test" instead of "an in-progress interview".
#
#   avatar/token   -> a scoped LiveKit room token for the caller's OWN application (the browser joins the room to talk and listen)
#   avatar/start, avatar/status -> "disabled": the call is audio only (these exist so the shared browser room module needs no changes)
#   start / text / status / stop -> the live call (idempotent start; a typed reply is the fallback for a broken microphone)
# Same authentication, ownership and CSRF rules as every other candidate route. The character's private facts and the rubric never leave the server.
# ============================================================

router = APIRouter(
    prefix="/api/assessment/sessions",
    tags=["Practical Role-play"],
)


def _owned_roleplay_application(db: Session, application_id: int, user_id: int):

    application = get_owned_application(db, application_id, user_id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    test = db.query(SituationalJudgmentTest).filter(SituationalJudgmentTest.application_id == application.id).first()

    if test is None or test.kind != "practical" or test.status != "in_progress" or '"roleplay"' not in (test.payload_json or ""):
        raise HTTPException(status_code=409, detail="There is no active call simulation for this application.")

    row = load_row(db, application.id)

    if row is None:
        raise HTTPException(status_code=409, detail="There is no active call simulation for this application.")

    return application, row


@router.post("/{application_id}/roleplay/v2/avatar/token")
def roleplay_room_token(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application, _row = _owned_roleplay_application(db, application_id, current_user.id)

    try:
        return generate_avatar_room_token_v2(application)
    except AvatarConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/{application_id}/roleplay/v2/avatar/start")
def roleplay_avatar_start(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_csrf(request)
    _owned_roleplay_application(db, application_id, current_user.id)
    return {"state": "disabled", "error": None}


@router.get("/{application_id}/roleplay/v2/avatar/status")
def roleplay_avatar_status(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned_roleplay_application(db, application_id, current_user.id)
    return {"state": "disabled", "error": None}


@router.post("/{application_id}/roleplay/start")
async def start_roleplay_call(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application, row = _owned_roleplay_application(db, application_id, current_user.id)

    if row.status == "done":
        raise HTTPException(status_code=409, detail="This call has already ended.")

    if not realtime.is_realtime_configured() or realtime.get_realtime_provider() != realtime.PROVIDER_REALTIME:
        raise HTTPException(status_code=503, detail="The live voice call is not available right now.")

    if not is_livekit_configured():
        raise HTTPException(status_code=503, detail="LiveKit is not configured.")

    try:
        session = await realtime.start_realtime_session(application.id, work=RolePlayWork(application.id))
    except (AudioTransportError, AvatarConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return session.status()


@router.post("/{application_id}/roleplay/text")
async def roleplay_typed_reply(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application, _row = _owned_roleplay_application(db, application_id, current_user.id)

    body = await request.json()
    text = str((body or {}).get("answer_text") or "").strip()[:1500]

    if not text:
        raise HTTPException(status_code=400, detail="Type your reply first.")

    session = realtime.peek_session(application.id)

    if session is None or not await session.submit_typed_answer(text):
        raise HTTPException(status_code=409, detail="The call is not ready for a typed reply right now.")

    return {"state": "accepted"}


@router.get("/{application_id}/roleplay/status")
async def roleplay_status(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    session = realtime.peek_session(application.id)
    row = load_row(db, application.id)

    if session is None:
        return {"state": "idle", "call": row.status if row else None, "error": None}

    return {**session.status(), "call": row.status if row else None}


@router.post("/{application_id}/roleplay/stop")
async def stop_roleplay_call(
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

    if session is not None and getattr(getattr(session, "_plan", None), "kind", "interview") == "roleplay":
        await session.close()

    return {"state": "closed"}

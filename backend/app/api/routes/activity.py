from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import uuid

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.user_activity import UserActivity


router = APIRouter(
    prefix="/api/activity",
    tags=["User Activity"]
)


# --------------------------------------------------
# START SESSION
# --------------------------------------------------

@router.post("/start")
def start_session(
    user_id: int,
    page: str | None = None,
    db: Session = Depends(get_db)
):

    user = db.query(User).filter(
        User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    now = datetime.utcnow()

    session_id = str(uuid.uuid4())

    activity = UserActivity(
        user_id=user_id,
        session_id=session_id,
        login_at=now,
        last_seen=now,
        active_seconds=0,
        page=page,
        status="online"
    )

    db.add(activity)
    db.commit()
    db.refresh(activity)

    return {
        "message": "Activity session started",
        "session_id": session_id,
        "activity_id": activity.id
    }


# --------------------------------------------------
# HEARTBEAT
# --------------------------------------------------

@router.post("/heartbeat")
def heartbeat(
    user_id: int,
    session_id: str,
    page: str | None = None,
    active_seconds: int = 0,
    db: Session = Depends(get_db)
):

    activity = db.query(UserActivity).filter(
        UserActivity.user_id == user_id,
        UserActivity.session_id == session_id
    ).first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity session not found"
        )

    now = datetime.utcnow()

    activity.last_seen = now
    activity.status = "online"

    if page:
        activity.page = page

    if active_seconds > 0:
        activity.active_seconds += active_seconds

    db.commit()

    return {
        "message": "Heartbeat recorded",
        "status": "online",
        "active_seconds": activity.active_seconds
    }


# --------------------------------------------------
# UPDATE PAGE
# --------------------------------------------------

@router.post("/page")
def update_page(
    user_id: int,
    session_id: str,
    page: str,
    db: Session = Depends(get_db)
):

    activity = db.query(UserActivity).filter(
        UserActivity.user_id == user_id,
        UserActivity.session_id == session_id
    ).first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity session not found"
        )

    activity.page = page
    activity.last_seen = datetime.utcnow()
    activity.status = "online"

    db.commit()

    return {
        "message": "Page activity updated",
        "page": page
    }


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------

@router.post("/logout")
def logout(
    user_id: int,
    session_id: str,
    db: Session = Depends(get_db)
):

    activity = db.query(UserActivity).filter(
        UserActivity.user_id == user_id,
        UserActivity.session_id == session_id
    ).first()

    if not activity:
        raise HTTPException(
            status_code=404,
            detail="Activity session not found"
        )

    now = datetime.utcnow()

    activity.logout_at = now
    activity.last_seen = now
    activity.status = "offline"

    db.commit()

    return {
        "message": "Activity session ended",
        "status": "offline",
        "logout_at": now
    }

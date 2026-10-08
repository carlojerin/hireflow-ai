import os
import secrets
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User

from backend.app.services.auth.session_service import (
    create_session,
    generate_csrf_token,
    set_csrf_cookie,
    set_session_cookie,
)
from backend.app.services.email.notifications import send_candidate_scheduled_confirmation_email


router = APIRouter(
    prefix="/api/invites",
    tags=["Candidate Invites"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _frontend_base_url() -> str:
    return (os.getenv("FRONTEND_BASE_URL") or "http://localhost:5500").rstrip("/")


def _unique_username(db: Session, email: str) -> str:

    base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_") or "candidate"
    candidate = base
    suffix = 1

    while db.query(User).filter(User.username == candidate).first():
        suffix += 1
        candidate = f"{base}{suffix}"

    return candidate


# ============================================================
# PUBLIC MAGIC LINK -- HR-sourced candidate invite (Phase 1)
#
# The token IS the authentication (same trust model as the existing
# password-reset OTP): whoever holds the emailed link may enter. Not
# single-use -- every visit re-authenticates so the candidate can always
# come back to an in-progress assessment. First visit auto-creates the
# account and the job application; later visits just log the same
# account back in.
# ============================================================

@router.get("/{token}")
def preview_invite(token: str, db: Session = Depends(get_db)):
    """Read-only: lets the landing page show the job/company before entering."""

    invite = db.query(CandidateInvite).filter(CandidateInvite.token == token).first()

    if not invite:
        raise HTTPException(status_code=404, detail="This invite link is not valid.")

    if datetime.utcnow() > invite.expires_at:
        raise HTTPException(status_code=410, detail="Your assessment deadline has passed. This link has expired.")

    job = db.query(Job).filter(Job.id == invite.job_id).first()

    return {
        "candidate_name": invite.candidate_name,
        "job_title": job.title if job else None,
        "company_name": job.company if job else None,
        "expires_at": invite.expires_at.isoformat(),
        "scheduled_at": invite.scheduled_at.isoformat() if invite.scheduled_at else None,
    }


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


@router.post("/{token}/schedule")
def schedule_invite(token: str, payload: ScheduleRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Candidate picked a time on schedule-assessment.html. Purely an
    informational anchor for the reminder job (reminder_scheduler.py) --
    the magic link keeps working regardless, right up until expires_at."""

    invite = db.query(CandidateInvite).filter(CandidateInvite.token == token).first()

    if not invite:
        raise HTTPException(status_code=404, detail="This invite link is not valid.")

    now = datetime.utcnow()

    if now > invite.expires_at:
        raise HTTPException(status_code=410, detail="Your assessment deadline has passed. This link has expired.")

    scheduled_at = payload.scheduled_at.replace(tzinfo=None)

    if scheduled_at <= now:
        raise HTTPException(status_code=400, detail="Please pick a time in the future.")

    if scheduled_at > invite.expires_at:
        raise HTTPException(
            status_code=400,
            detail="That time is after your assessment deadline. Please pick an earlier time.",
        )

    invite.scheduled_at = scheduled_at
    db.commit()

    job = db.query(Job).filter(Job.id == invite.job_id).first()
    invite_url = f"{_frontend_base_url()}/start-assessment.html?token={token}"

    # the confirmation email goes out after the reply (an SMTP round trip takes 10-15 s and the candidate is waiting on this page)
    background_tasks.add_task(
        send_candidate_scheduled_confirmation_email,
        to_email=invite.candidate_email,
        candidate_name=invite.candidate_name,
        job_title=job.title if job else None,
        company_name=job.company if job else None,
        invite_url=invite_url,
        scheduled_at=scheduled_at,
        deadline=invite.expires_at,
    )

    return {
        "scheduled_at": invite.scheduled_at.isoformat(),
        "email_sent": True,                                     # queued; the send itself happens right after this reply
    }


@router.post("/{token}/enter")
def enter_via_invite(token: str, response: Response, db: Session = Depends(get_db)):

    invite = db.query(CandidateInvite).filter(CandidateInvite.token == token).first()

    if not invite:
        raise HTTPException(status_code=404, detail="This invite link is not valid.")

    if datetime.utcnow() > invite.expires_at:
        invite.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Your assessment deadline has passed. This link has expired.")

    if invite.status == "claimed" and invite.claimed_user_id:

        user = db.query(User).filter(User.id == invite.claimed_user_id).first()

        if not user:
            raise HTTPException(status_code=500, detail="Your account could not be found.")

    else:

        # An account with this email may already exist (e.g. the candidate
        # registered on their own earlier) -- reuse it rather than fail on
        # the unique-email constraint.
        user = db.query(User).filter(User.email == invite.candidate_email).first()

        if not user:

            user = User(
                full_name=invite.candidate_name,
                username=_unique_username(db, invite.candidate_email),
                email=invite.candidate_email,
                mobile=None,
                hashed_password=pwd_context.hash(secrets.token_urlsafe(24)),
                role="user",
            )
            db.add(user)
            db.flush()

        invite.status = "claimed"
        invite.claimed_user_id = user.id

    application = (
        db.query(JobApplication)
        .filter(JobApplication.job_id == invite.job_id, JobApplication.user_id == user.id)
        .first()
    )

    if not application:

        application = JobApplication(
            job_id=invite.job_id,
            user_id=user.id,
            # HR already vetted this candidate via their resume before inviting them, so this
            # skips the normal "must be shortlisted first" gate (session_service.py's
            # ASSESSMENT_START_STATUSES) that self-applied candidates go through.
            status="shortlisted",
            ai_match_score=None,
            ai_shortlist=False,
            recruiter_shortlisted=True,
            recruiter_notes="Sourced by HR via resume invite.",
            applied_date=datetime.utcnow(),
            updated_date=datetime.utcnow(),
        )
        db.add(application)
        db.flush()

    db.commit()
    db.refresh(user)
    db.refresh(application)

    raw_token, _ = create_session(
        db,
        subject_type="user",
        subject_id=user.id,
        role_at_login=user.role,
    )

    set_session_cookie(response, raw_token)
    set_csrf_cookie(response, generate_csrf_token())

    return {
        "redirect_url": f"assessment.html?application_id={application.id}",
        # candidate-facing pages (assessment.html, aptitude-test.html, programming-*.html,
        # ai-interview-v2.html) all gate on a client-side "is someone logged in" check that reads
        # this shape from localStorage["jobpilot_user"] -- normally set by login.js, which this
        # magic-link flow never goes through, so the frontend must set it itself from this response.
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "email": user.email,
            "mobile": user.mobile,
            "role": user.role,
        },
    }

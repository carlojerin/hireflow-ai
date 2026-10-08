import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from backend.app.database.database import SessionLocal
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.job import Job
from backend.app.services.email.notifications import (
    send_candidate_deadline_reminder_email,
    send_candidate_deadline_expired_email,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT DEADLINE REMINDERS
#
# A periodic in-process job (no Celery/Redis in this codebase) that emails
# candidates as their invite deadline approaches. Each threshold fires at
# most once per invite, tracked by CandidateInvite.reminder_24h_sent /
# reminder_2h_sent so a restart or a slow tick never double-sends.
#
# Scoped to invites still "pending" or "claimed" (not yet expired) with a
# real candidate email on file -- an invite that already expired is left
# alone (enter_via_invite already marks those "expired" on next visit).
# ============================================================

logger = logging.getLogger("jobpilot.reminders")

REMINDER_CHECK_INTERVAL_MINUTES = 15

# (hours-remaining ceiling, human label, flag column name)
REMINDER_THRESHOLDS = (
    (24, "24 hours", "reminder_24h_sent"),
    (2, "2 hours", "reminder_2h_sent"),
)


def _frontend_base_url() -> str:
    return (os.getenv("FRONTEND_BASE_URL") or "http://localhost:5500").rstrip("/")


def send_due_reminders(db: Session) -> int:
    """Checks every still-live invite against each threshold and sends any
    reminder that's now due. Returns how many emails were sent (useful for
    tests and startup logging)."""

    now = datetime.utcnow()

    invites = (
        db.query(CandidateInvite)
        .filter(
            CandidateInvite.status.in_(["pending", "claimed"]),
            CandidateInvite.expires_at > now,
        )
        .all()
    )

    sent_count = 0

    for invite in invites:

        hours_left = (invite.expires_at - now).total_seconds() / 3600.0

        for threshold_hours, label, flag_name in REMINDER_THRESHOLDS:

            if hours_left > threshold_hours:
                continue

            if getattr(invite, flag_name):
                continue

            job = db.query(Job).filter(Job.id == invite.job_id).first()
            invite_url = f"{_frontend_base_url()}/start-assessment.html?token={invite.token}"

            email_sent = send_candidate_deadline_reminder_email(
                to_email=invite.candidate_email,
                candidate_name=invite.candidate_name,
                job_title=job.title if job else None,
                company_name=job.company if job else None,
                invite_url=invite_url,
                time_left_label=label,
                deadline=invite.expires_at,
            )

            # Marked sent regardless of email_sent -- a permanently
            # misconfigured/failing mailer must never retry every 15
            # minutes forever; the "email_sent" flag on the invite-creation
            # response is where delivery failures actually surface to HR.
            setattr(invite, flag_name, True)

            if email_sent:
                sent_count += 1

            logger.info(
                "Deadline reminder (%s) for invite %s: email_sent=%s",
                label, invite.id, email_sent,
            )

    if invites:
        db.commit()

    return sent_count


def expire_overdue_invites(db: Session) -> int:
    """Flips any invite whose deadline just passed to status="expired" and
    sends a one-time "your window has closed" email. Runs before
    send_due_reminders() in the same tick so a newly-expired invite is
    handled here, not treated as still "due for a reminder".

    The status flip itself is the dedupe guard: this query only ever
    matches pending/claimed rows, so a row already marked "expired" is
    never revisited and never emailed twice.
    """

    now = datetime.utcnow()

    overdue_invites = (
        db.query(CandidateInvite)
        .filter(
            CandidateInvite.status.in_(["pending", "claimed"]),
            CandidateInvite.expires_at <= now,
        )
        .all()
    )

    notified_count = 0

    for invite in overdue_invites:

        job = db.query(Job).filter(Job.id == invite.job_id).first()

        email_sent = send_candidate_deadline_expired_email(
            to_email=invite.candidate_email,
            candidate_name=invite.candidate_name,
            job_title=job.title if job else None,
            company_name=job.company if job else None,
            deadline=invite.expires_at,
        )

        invite.status = "expired"

        if email_sent:
            notified_count += 1

        logger.info(
            "Deadline expired for invite %s: email_sent=%s",
            invite.id, email_sent,
        )

    if overdue_invites:
        db.commit()

    return notified_count


def _run_reminder_check() -> None:

    db = SessionLocal()

    try:
        expire_overdue_invites(db)
        send_due_reminders(db)
    except Exception as exc:
        logger.error("Reminder check failed: %s", exc)
    finally:
        db.close()


_scheduler: BackgroundScheduler | None = None


def start_reminder_scheduler() -> None:
    """Called once from main.py's startup event. Safe to call more than
    once -- a second call is a no-op if the scheduler is already running."""

    global _scheduler

    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        _run_reminder_check,
        "interval",
        minutes=REMINDER_CHECK_INTERVAL_MINUTES,
        id="assessment_deadline_reminders",
        next_run_time=datetime.utcnow(),  # also run once immediately at startup
    )

    _scheduler.start()

    logger.info(
        "Assessment deadline reminder scheduler started (every %s minutes).",
        REMINDER_CHECK_INTERVAL_MINUTES,
    )


def stop_reminder_scheduler() -> None:

    global _scheduler

    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None

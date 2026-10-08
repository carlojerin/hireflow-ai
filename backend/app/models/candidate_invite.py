from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - HR-SOURCED CANDIDATE INVITE (Phase 1)
#
# HR uploads a candidate's resume for a job without the candidate having an
# account yet. This row holds that invite until the candidate clicks the
# emailed link -- deliberately NOT a User/JobApplication until then, so an
# invite nobody responds to never creates a junk account.
#
# The emailed link is a standing "magic link", not single-use: every click
# re-authenticates the candidate (auto-creating the account on the very
# first click), so they can always return to an in-progress assessment
# without a separate login screen.
# ============================================================

class CandidateInvite(Base):

    __tablename__ = "candidate_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)

    candidate_name: Mapped[str] = mapped_column(String(150), nullable=False)

    candidate_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    resume_file_path: Mapped[str] = mapped_column(String(500), nullable=False)

    resume_analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "pending" (not yet clicked) | "claimed" (account created, at least one visit) | "expired"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    claimed_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # ==========================================
    # SCHEDULING + DEADLINE REMINDERS
    # ==========================================

    # Set when the candidate picks "Schedule It For Later" and chooses a
    # time -- purely informational/a reminder anchor, not a hard gate: the
    # magic link still works before or after this time, right up until
    # expires_at.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Each fires at most once per invite, checked by a periodic background
    # job (see reminder_scheduler.py) against time remaining until expires_at.
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    reminder_2h_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

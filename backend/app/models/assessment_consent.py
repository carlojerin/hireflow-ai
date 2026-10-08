from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT CONSENT
#
# One row per (application, assessment stage): the candidate read the instructions (question count, time, how they are
# monitored, the rules) and ticked "I agree / I promise" BEFORE the assessment started. It is the audit record that
# camera / microphone monitoring was disclosed and accepted, shown to the recruiter with the integrity review.
# statement_version lets a later change of the wording be told apart from what an earlier candidate accepted.
# ============================================================

class AssessmentConsent(Base):

    __tablename__ = "assessment_consents"

    __table_args__ = (
        UniqueConstraint("application_id", "assessment_type", name="uq_assessment_consent_application_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"), nullable=False, index=True)

    assessment_type: Mapped[str] = mapped_column(String(20), nullable=False)

    statement_version: Mapped[str] = mapped_column(String(20), nullable=False)

    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)

    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

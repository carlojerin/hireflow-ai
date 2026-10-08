from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class JobApplication(Base):
    __tablename__ = "job_applications"

    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "user_id",
            name="uq_job_application_job_user",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # =========================================================
    # JOB
    # =========================================================

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=False,
        index=True,
    )

    # =========================================================
    # CANDIDATE
    # =========================================================

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # =========================================================
    # RECRUITMENT STATUS
    # =========================================================

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="new",
        index=True,
    )

    # =========================================================
    # AI MATCH SCORE
    # =========================================================

    ai_match_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # =========================================================
    # AI SHORTLIST RECOMMENDATION
    # =========================================================

    ai_shortlist: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # =========================================================
    # AI RECOMMENDATION TEXT
    #
    # Examples:
    # Strongly Recommended
    # Recommended
    # Review
    # Not Recommended
    # Resume analysis required
    # =========================================================

    ai_recommendation: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # =========================================================
    # AI COMPONENT SCORES
    # =========================================================

    ai_skill_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_experience_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_role_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_location_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_technology_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    ai_education_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # =========================================================
    # AI SKILL DETAILS
    #
    # Stored as JSON text.
    #
    # Example:
    # ["Python", "FastAPI", "SQL"]
    # =========================================================

    ai_matched_skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # AI MISSING SKILLS
    #
    # Stored as JSON text.
    #
    # Example:
    # ["AWS", "Docker"]
    # =========================================================

    ai_missing_skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # AI ANALYSIS TIMESTAMP
    #
    # Tells us when this application was last
    # evaluated by the AI matching engine.
    # =========================================================

    ai_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # =========================================================
    # RECRUITER SHORTLIST
    # =========================================================

    recruiter_shortlisted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # =========================================================
    # RECRUITER NOTES
    # =========================================================

    recruiter_notes: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    # =========================================================
    # APPLICATION TIMESTAMPS
    # =========================================================

    applied_date: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    updated_date: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # =========================================================
    # ARCHIVE (HR visibility only — never a recruitment status,
    # never a deletion. Archiving/restoring never touches
    # `status` or application_status_history.)
    # =========================================================

    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
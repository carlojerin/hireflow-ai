from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSION
#
# One row per application's assessment journey — mirrors the
# existing AptitudeTest model's shape exactly (a single unique
# FK to job_applications.id as the only relationship anchor;
# candidate/job/company are never duplicated here, always
# derived through that same join JobApplication -> Job ->
# Company already used everywhere else in this project).
#
# current_stage is the authoritative state machine position:
# not_started | self_intro | aptitude | programming |
# ai_interview | completed | cancelled
#
# This task (4A) only implements the not_started -> self_intro
# -> aptitude transitions. The remaining stage names exist so
# the column's vocabulary is stable for later tasks — nothing
# in this task writes "programming", "ai_interview",
# "completed", or "cancelled" into this column.
# ============================================================

class AssessmentSession(Base):

    __tablename__ = "assessment_sessions"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    current_stage: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_started",
        index=True,
    )

    # =========================================================
    # SELF-INTRODUCTION STAGE
    #
    # not_started | in_progress | completed | expired
    # =========================================================

    self_intro_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="not_started",
    )

    self_intro_started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    self_intro_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Computed server-side at completion time from
    # (self_intro_completed_at - self_intro_started_at) — never
    # accepted from the client.
    self_intro_duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Optional plain-text introduction the candidate may submit
    # alongside (or instead of) the in-browser recording. No
    # video is stored — see the self-introduction design note
    # in the Task 4A report for why.
    self_intro_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # TIMESTAMPS
    # =========================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

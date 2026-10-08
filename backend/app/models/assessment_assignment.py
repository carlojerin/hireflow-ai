from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT ASSIGNMENT (Task 4D-F1)
#
# The smallest additive structure needed to make the Programming
# and AI Interview stages HR-controlled rather than fully
# candidate self-service: one row means "HR has enabled this
# stage for this application". No relationship() attributes, no
# FK to AptitudeTest/ProgrammingTest/InterviewTest — this table
# tracks intent/permission only, never the test content or
# result itself (those stay exactly where they already live).
#
# Deliberately NOT used for the Self Introduction -> Aptitude
# transition — that stage remains driven purely by
# AssessmentSession.self_intro_status, unchanged from Task 4B.
#
# One row per (application_id, assessment_type): a real unique
# constraint (not just an application-level check) so a race
# between two HR requests can never create two assignments for
# the same stage.
# ============================================================

class AssessmentAssignment(Base):

    __tablename__ = "assessment_assignments"

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "assessment_type",
            name="uq_assessment_assignment_application_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        index=True,
    )

    # "programming" | "ai_interview" — never "aptitude", which
    # has its own pre-existing assign mechanism on AptitudeTest
    # itself (status == "assigned") and is left untouched.
    assessment_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # "assigned" is the only value used today. Kept as a real
    # column (rather than the row's mere existence) so a future
    # task can add "revoked" without a schema change.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="assigned",
    )

    assigned_by_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="hr",
    )

    assigned_by_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

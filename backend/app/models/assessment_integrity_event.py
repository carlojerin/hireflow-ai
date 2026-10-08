from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT INTEGRITY EVENT (Task 4D-STRICT-1)
#
# One row per integrity signal (fullscreen exit, tab hidden,
# window blur, etc.) reported during a timed assessment. No
# relationship() attributes, matching every other model in this
# codebase.
#
# Ownership anchor is application_id (not assessment_session_id)
# — the same anchor AptitudeTest/ProgrammingTest/InterviewTest
# already use, and the one that is ALWAYS populated regardless
# of whether a given test was created via the Task 4A self-
# service flow or the older direct HR-assign flow (which never
# touches AssessmentSession at all). assessment_session_id is
# still recorded when one exists, for reporting/traceability
# only — it is never used for ownership or authorization.
#
# Never stores screenshots, video, audio, or camera data — see
# docs/strict-assessment-mode.md. metadata_json is a small,
# server-controlled JSON blob (e.g. {"violation_count": 2}),
# never arbitrary client-supplied content.
# ============================================================

class AssessmentIntegrityEvent(Base):

    __tablename__ = "assessment_integrity_events"

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

    # Populated opportunistically when an AssessmentSession row
    # exists for this application; NULL otherwise (e.g. a
    # legacy HR-assigned aptitude test with no session row).
    # Never used for ownership checks — see module docstring.
    assessment_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_sessions.id"),
        nullable=True,
        index=True,
    )

    # "aptitude" | "programming" | "ai_interview" — only
    # "aptitude" is actually wired end-to-end in this task; the
    # others are accepted by the schema for forward-reusability
    # but rejected at the route layer until integrated.
    assessment_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # fullscreen_entered | fullscreen_exited | tab_hidden |
    # window_blur | window_focus | assessment_page_left |
    # assessment_page_returned | violation_warning |
    # assessment_auto_terminated
    #
    # The last two are never accepted from the client — they are
    # written only by the server itself as an audit trail when a
    # warning threshold or termination is reached.
    event_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    # "info" | "violation" | "duplicate" | "warning"
    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="info",
    )

    # True only for a violation-eligible event that actually
    # incremented the violation count (i.e. survived the
    # dedup/cooldown check). Lets violation counting be a simple
    # filtered COUNT(*) instead of re-deriving dedup logic on
    # every read.
    counted_as_violation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Server-recorded receipt time — never accepted from the
    # client, so a candidate cannot backdate/forge event timing.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Small, server-generated JSON blob only (e.g. the violation
    # count at the time of a warning event). Never free-form
    # client content, never personal data, never media of any
    # kind.
    metadata_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT VIDEO RECORDING (Task 4 - Video
# Recording Foundation)
#
# One row per (application, stage) — the completed browser-side
# MediaRecorder output for that ONE stage, uploaded ONCE the
# candidate finishes it. Never one giant recording for the whole
# assessment; never a row for a stage that was never actually
# completed/uploaded.
#
# Ownership anchor is application_id, exactly like
# AssessmentIntegrityEvent — the one anchor that is always
# populated regardless of which historical path created the
# underlying test row. assessment_session_id is recorded when a
# session exists, for traceability only, never for authorization.
#
# stage mirrors AssessmentSession.current_stage's own values
# exactly: "self_intro" | "aptitude" | "programming" |
# "ai_interview" — no new stage concept invented.
#
# This table stores METADATA only. The actual video bytes live
# on the filesystem under the dedicated data/assessment_videos/
# directory (see backend/app/api/routes/assessment_videos.py) —
# never a BLOB column, never raw camera frames, never analyzed by
# any AI model. file_path is always a path RELATIVE to that
# directory, never an absolute path and never anything the
# browser supplied directly — see the upload route for the
# server-generated-filename logic.
#
# The unique constraint means a second successful upload for a
# (application, stage) pair that already has one is rejected
# (409) rather than silently creating a duplicate — there is no
# "pending"/"failed" status stored here at all: a row only ever
# exists once the browser produced a real, complete Blob AND the
# server finished writing it to disk. A failed/incomplete upload
# simply never creates a row, so the candidate's own retry of the
# exact same request is always safe.
# ============================================================

class AssessmentVideoRecording(Base):

    __tablename__ = "assessment_video_recordings"

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "stage",
            name="uq_assessment_video_application_stage",
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

    # Populated opportunistically when an AssessmentSession row
    # exists for this application; NULL otherwise. Never used for
    # ownership/authorization — see module docstring.
    assessment_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_sessions.id"),
        nullable=True,
        index=True,
    )

    # "self_intro" | "aptitude" | "programming" | "ai_interview"
    stage: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    # Relative to the dedicated video storage root only (e.g.
    # "{application_id}/{stage}/{uuid}.webm") — never an absolute
    # filesystem path, and never derived from anything the browser
    # sent directly. See assessment_videos.py's upload route.
    file_path: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # The browser's own File.name, kept purely for display/
    # diagnostics — never used to build a filesystem path.
    original_filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    mime_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Reported by the browser (MediaRecorder start/stop timing);
    # advisory/display-only, never used for any security decision.
    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # "uploaded" is the only value this task ever writes — kept
    # as a real column (rather than the row's mere existence) so a
    # future task can add e.g. "processing"/"failed" without a
    # schema change.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="uploaded",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

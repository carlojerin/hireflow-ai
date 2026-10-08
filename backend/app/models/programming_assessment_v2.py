from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - PROGRAMMING ASSESSMENT V2 (Programming Engine
# V2, Live Integration task)
#
# NEW, ADDITIVE table — never replaces, never modifies, and has
# no foreign key relationship to the legacy `programming_tests` /
# `programming_submissions` tables. One row per application, the
# same "lazy stage entry" pattern the legacy ProgrammingTest
# already uses: created once, the first time a candidate starts
# the V2 programming stage, and never regenerated afterward.
#
# STABLE SNAPSHOT: `question_set_json` is the FULL, validated
# ProgrammingQuestionSetV2 (including hidden test stdin/expected_
# stdout — this row is never sent to the candidate as-is; see
# programming_integration_v2.py's candidate_view_v2() for the
# redacted view). Written exactly once at creation and never
# regenerated for the SAME attempt — calling the generator again
# later (e.g. after a Gemini prompt change) must never alter what
# a candidate already mid-attempt is looking at.
#
# `application_id` is unique — mirrors AssessmentVideoRecording's
# own module docstring: application_id is the one ownership
# anchor that is always populated, used the exact same way here.
# ============================================================


class ProgrammingAssessmentV2(Base):

    __tablename__ = "programming_assessments_v2"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # The full, validated ProgrammingQuestionSetV2, as JSON —
    # ProgrammingQuestionSetV2.model_dump_json(). Contains hidden
    # test data; never served verbatim to a candidate.
    question_set_json: Mapped[str] = mapped_column(Text, nullable=False)

    # Snapshot of the languages permitted for this attempt (the
    # question set's own per-question allowed_languages, unioned,
    # for quick candidate-facing display) — derivable from
    # question_set_json but kept denormalized for a cheap read on
    # every GET without re-parsing the full snapshot.
    allowed_languages_json: Mapped[str] = mapped_column(Text, nullable=False)

    generated_by: Mapped[str] = mapped_column(String(20), nullable=False)  # "gemini" | "fallback"

    # Autosave: {question_id: {"language": str, "source_code": str}},
    # written by the candidate's own periodic autosave calls while
    # status == "in_progress". Distinct from the per-question
    # ProgrammingSubmissionV2 rows, which are only ever written on an
    # explicit Run/Submit click -- this is what lets code the
    # candidate typed but never ran survive a refresh/disconnect
    # instead of being lost while the exam clock keeps running (the
    # same class of bug already fixed for the aptitude stage).
    draft_code_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # "in_progress" | "completed" | "expired"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(nullable=True)

    # Server-authoritative timer, exactly mirroring the legacy
    # ProgrammingTest's own assigned_at/expires_at pattern.
    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

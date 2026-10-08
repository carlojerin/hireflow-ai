from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - PROGRAMMING SUBMISSION V2 (Programming Engine
# V2, Live Integration task)
#
# NEW, ADDITIVE table, mirroring the legacy `programming_
# submissions` table's shape/intent but anchored to
# `programming_assessments_v2` (never the legacy
# `programming_tests`) — the exact same separation of concerns
# personalized_programming_v2 has kept throughout: V2 data never
# lives in or overwrites a legacy row.
#
# One row per (assessment, question, kind="run"|"submit") attempt
# — a "run" row is written for every "Run Code" click against the
# PUBLIC examples only; a "submit" row is written once per
# question at final submission, after every hidden test for that
# question has executed. `result_json` stores the redacted,
# candidate-safe score breakdown (programming_scoring_v2's
# dataclasses, serialized) — never a hidden test's stdin/expected_
# stdout, since those were never present on those dataclasses to
# begin with.
# ============================================================


class ProgrammingSubmissionV2(Base):

    __tablename__ = "programming_submissions_v2"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("programming_assessments_v2.id"),
        nullable=False,
        index=True,
    )

    question_id: Mapped[int] = mapped_column(Integer, nullable=False)

    submission_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # "run" | "submit"

    language: Mapped[str] = mapped_column(String(20), nullable=False)

    source_code: Mapped[str] = mapped_column(Text, nullable=False)

    # For submission_kind="submit": the candidate-safe scored
    # result (programming_scoring_v2.MainQuestionScoreResult,
    # serialized as JSON) -- never contains hidden stdin/expected_
    # stdout. NULL for submission_kind="run" (a run is diagnostic
    # only, never scored).
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    earned_points: Mapped[float | None] = mapped_column(Float, nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow,
    )

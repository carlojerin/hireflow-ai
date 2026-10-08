from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - PROGRAMMING SUBMISSION (Task 4C)
#
# One row per code-execution attempt — both "Run Code" (against
# visible examples only, unlimited feedback up to a rate limit,
# never affects score) and "Submit Final Assessment" (against
# hidden tests, exactly once per question, authoritative for
# score). `submission_kind` is the field that distinguishes
# these two per the spec's explicit requirement to never
# conflate "code submission" with "final assessment submission".
#
# No relationship() — same convention as every other model in
# this project (see models/aptitude_test.py, models/
# assessment_session.py).
# ============================================================

class ProgrammingSubmission(Base):

    __tablename__ = "programming_submissions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    programming_test_id: Mapped[int] = mapped_column(
        ForeignKey("programming_tests.id"),
        nullable=False,
        index=True,
    )

    question_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # "run" (visible-examples feedback only, does not affect
    # score, rate-limited) | "final" (hidden-test grading, one
    # per question per test, immutable once the test is
    # completed).
    submission_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    language: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    source_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # passed | failed | error | timeout | rejected
    #   "rejected" = blocked by the sandbox's static safety
    #   check before any code ever executed.
    execution_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # 0-100 for a "final" submission (hidden-test pass
    # percentage for this one question). NULL for a "run"
    # submission — running against visible examples is
    # feedback only and never contributes to the score.
    score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    execution_time_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Not measured in this environment (no OS-level sandbox
    # available to meter memory) — always NULL. Kept as a real
    # column so a future production sandbox (Part 6 note) can
    # populate it without a schema change.
    memory_used_kb: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

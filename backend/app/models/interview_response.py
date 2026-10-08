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
# JOBPILOT AI - INTERVIEW RESPONSE (Task 4D)
#
# One row per answered question — mirrors
# models/programming_submission.py's role (an immutable, append
# -only record of one candidate action) but for a single text
# answer + its AI evaluation, rather than a code execution.
#
# A question_id only ever gets ONE row here per interview_test
# (unlike ProgrammingSubmission's repeatable "run" kind) — once
# evaluated, api/routes/interview_tests.py returns the stored
# row instead of ever re-evaluating, which is both the one-
# attempt-per-question rule and the rate-limiting/cost-control
# mechanism required by Part 24 of the task spec.
# ============================================================

class InterviewResponse(Base):

    __tablename__ = "interview_responses"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    interview_test_id: Mapped[int] = mapped_column(
        ForeignKey("interview_tests.id"),
        nullable=False,
        index=True,
    )

    question_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    answer_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Server-computed composite score (0-100) for this one
    # answer — see services/assessment/interview_scoring.py.
    score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # JSON: the validated InterviewAnswerEvaluation fields
    # (technical_correctness, understanding, reasoning,
    # relevance, communication, strengths, gaps, feedback) —
    # candidate-safe; never includes expected_answer_points or
    # any evaluator-internal prompt content.
    evaluation_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # "ai" | "fallback" — mirrors ProgrammingTest.question_source
    # exactly, but per-answer rather than per-test, since Gemini
    # could plausibly succeed for some answers and fail for
    # others within the same interview.
    evaluation_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="fallback",
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

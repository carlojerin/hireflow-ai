from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class AptitudeTest(Base):

    __tablename__ = "aptitude_tests"

    # =========================================================
    # IDENTITY
    # =========================================================

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # =========================================================
    # APPLICATION
    #
    # One aptitude test per JobApplication — enforced at the
    # database level. This is never a user-global test.
    # =========================================================

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # =========================================================
    # QUESTIONS / ANSWERS
    #
    # Stored as JSON text, matching the existing project
    # convention (ResumeAnalysis.skills, JobQuestion.options,
    # JobApplication.ai_matched_skills, etc.).
    #
    # `questions` includes each question's correct_answer —
    # this column is never returned to the candidate as-is;
    # the API layer strips correct_answer before the test is
    # submitted.
    # =========================================================

    questions: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    answers: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # DRAFT ANSWERS (autosave)
    #
    # Selected-but-not-yet-submitted answers, written by the
    # candidate's own periodic autosave calls while status ==
    # "in_progress". Distinct from `answers` above, which is
    # only ever written once, atomically, at final submit.
    # A dropped connection or accidental refresh mid-exam can
    # then resume from here instead of losing every answer
    # while the exam clock keeps running.
    # =========================================================

    draft_answers: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # RESULT
    # =========================================================

    score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    total_questions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    passed: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    # =========================================================
    # STATUS
    #
    # assigned | in_progress | completed | expired | terminated
    #
    # "terminated" (Task 4D-STRICT-1) is set only by
    # services/assessment/integrity_service.py when the strict-
    # mode violation policy reaches its termination limit —
    # never set directly by any candidate-facing endpoint.
    # =========================================================

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="in_progress",
    )

    # =========================================================
    # QUESTION SOURCE
    #
    # "ai" — generated personally by Gemini for this
    #        candidate/job.
    # "fallback" — the fixed deterministic question bank was
    #        used (Gemini unavailable, errored, or returned
    #        invalid data).
    # =========================================================

    question_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="fallback",
    )

    # =========================================================
    # HR ASSIGNMENT / DEADLINE
    #
    # Populated when HR sends the test (POST
    # /api/company-applications/{company_id}/{application_id}/
    # aptitude). NULL for the pre-existing rows created under
    # the old direct-start flow (before this feature existed).
    #
    # expires_at is always assigned_at + 24 hours, computed by
    # the backend — never trusted from the frontend.
    # =========================================================

    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # The 24-hour ASSIGNMENT window — how long the candidate has
    # to actually begin the test after HR sends/generates it.
    # Governs status=="assigned".
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # =========================================================
    # EXAM DURATION (Task 4D-STRICT-1)
    #
    # Deliberately separate from expires_at above: this is the
    # fixed, short exam-taking window (20 minutes), computed as
    # started_at + APTITUDE_EXAM_DURATION_MINUTES the moment the
    # candidate actually starts (status flips assigned ->
    # in_progress in aptitude_tests.py). NULL until then. Once
    # set, this — not expires_at — is what governs whether an
    # in_progress test has run out of time.
    # =========================================================

    exam_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # =========================================================
    # TIMESTAMPS
    # =========================================================

    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

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

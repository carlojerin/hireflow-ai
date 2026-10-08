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


class SituationalJudgmentTest(Base):
    """
    The non-technical equivalent of AptitudeTest, in the exact same
    pipeline slot a technical candidate's ProgrammingAssessmentV2
    occupies — see stage_progression_service.py and
    role_classification.py. Workplace-scenario multiple-choice
    questions (leadership, conflict resolution, time management,
    ethics, customer service, communication) instead of logical/
    quantitative reasoning, matching how real ATS platforms (SHL,
    TestGorilla, Criteria Corp) handle non-technical assessment:
    a Situational Judgment Test, not a coding test.

    Column-for-column mirrors AptitudeTest (same autosave, same
    server-authoritative timers, same status vocabulary) — this is
    deliberate: the candidate-facing UI and the route logic are
    near-identical clones of the aptitude stage's own, so they
    should stay trivially easy to compare/keep in sync.
    """

    __tablename__ = "situational_judgment_tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # One SJT per JobApplication — enforced at the database level.
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Each question's correct_answer lives in here too — never
    # returned to the candidate as-is; the API layer strips it.
    questions: Mapped[str] = mapped_column(Text, nullable=False)

    answers: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Autosave: selected-but-not-yet-submitted answers.
    draft_answers: Mapped[str | None] = mapped_column(Text, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # assigned | in_progress | completed | expired | terminated
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")

    # "ai" — generated personally by Groq for this candidate/job.
    # "fallback" — the curated SJT question bank was used (Groq
    #        unavailable, errored, or returned invalid data).
    question_source: Mapped[str] = mapped_column(String(20), nullable=False, default="fallback")

    # Assignment window (24h to begin) vs. exam duration (fixed,
    # short, starts ticking only once the candidate actually starts)
    # — identical two-clock design to AptitudeTest, see that model's
    # own comments for the full rationale.
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exam_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # "mcq" (the situational-judgment questions above) | "practical" (a work-sample task: see practical_integration notes in
    # sjt_integration.py). A practical test reuses this table so the whole pipeline (assignments, proctoring, recordings, snapshots, HR
    # report) works for it unchanged. For "practical": questions = the tasks (with answer keys / rubrics, never sent to the candidate),
    # payload_json = what the page needs to show them (environment, dataset), result_json = per-task feedback.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="mcq", server_default="mcq")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

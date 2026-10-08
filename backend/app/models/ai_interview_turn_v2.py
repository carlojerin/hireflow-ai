from datetime import datetime

from sqlalchemy import DateTime, Integer, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2 TURN (Task 5H)
#
# One row per question asked (and, once answered, its analysis) --
# an append-only conversational log, mirroring ProgrammingSubmissionV2's
# "one row per real action" pattern. `question`/`question_type`/
# `competency`/`difficulty` are written when the question is
# GENERATED (candidate-safe, shown immediately); `candidate_answer`/
# `answer_analysis_json`/`next_action` are written once the
# candidate answers this turn. The LAST row with no answer yet is
# always the "current question" the candidate is being shown.
#
# `answer_analysis_json` stores the candidate-safe AnswerAnalysisV2
# fields ONLY (strengths/weaknesses/evidence/claims/missing_depth/
# strength_level) -- never a hidden prompt, never Gemini's raw
# response, never chain-of-thought. This is what both the
# candidate's own state view intentionally OMITS (Section 20 — no
# hidden evaluation shown to the candidate) and what the HR review
# endpoint DOES show (Section 25).
# ============================================================


class AIInterviewTurnV2(Base):

    __tablename__ = "ai_interview_turns_v2"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("ai_interview_sessions_v2.id"),
        nullable=False,
        index=True,
    )

    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_type: Mapped[str] = mapped_column(String(30), nullable=False)
    competency: Mapped[str] = mapped_column(String(100), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="intermediate")

    # Which rung of the probe ladder (example / role_or_decision / reasoning_or_tradeoff / result_or_metric /
    # challenge_or_change / clarification) THIS question targets on its competency. Nullable: rows created before this
    # field existed, and the deterministic fallback engine's templated questions, simply have none. Used to stop the
    # interviewer re-asking the same rung of the same competency in reworded words -- a real bug plain question-text
    # similarity checks could not reliably catch, since the wording can differ a great deal for the same underlying ask.
    question_focus: Mapped[str | None] = mapped_column(String(30), nullable=True)

    candidate_answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AnswerAnalysisV2, serialized -- candidate-safe, no hidden
    # rubric/prompt content. Null until this turn is answered.
    answer_analysis_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_action: Mapped[str | None] = mapped_column(String(30), nullable=True)

    generated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="fallback")

    asked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

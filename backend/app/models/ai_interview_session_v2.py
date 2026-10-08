from datetime import datetime

from sqlalchemy import DateTime, Integer, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2 SESSION (Task 5H)
#
# NEW, ADDITIVE table — mirrors ProgrammingAssessmentV2's own
# shape/conventions exactly (one row per application, unique FK,
# server-authoritative assigned/expires/started/completed
# timestamps). Has NO foreign key relationship to, and never
# modifies, the legacy `interview_tests`/`interview_responses`
# tables — the legacy AI Interview flow (interview_tests.py) is
# left completely untouched and keeps working exactly as before.
#
# `blueprint_json` is the STABLE InterviewBlueprintV2 snapshot,
# written once at start and never regenerated — analogous to
# ProgrammingAssessmentV2.question_set_json. `conversation_summary`
# is a COMPACT rolling summary of the conversation so far (never
# the raw candidate resume/profile, never a full transcript re-sent
# to Gemini every turn — see interview_brain_v2.py's own module
# header for why). `final_evaluation_json` is written once, at
# completion, from interview_evidence_v2.compute_final_evaluation_
# v2() — the ONE place a competency-level evidence summary is
# computed, reused unchanged by both the candidate result view and
# the HR review endpoint.
# ============================================================


class AIInterviewSessionV2(Base):

    __tablename__ = "ai_interview_sessions_v2"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    assessment_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_sessions.id"),
        nullable=True,
    )

    # "in_progress" | "completed" | "expired"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")

    current_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    blueprint_json: Mapped[str] = mapped_column(Text, nullable=False)

    # Compact rolling summary (Section 13) -- NOT the full
    # transcript. Rebuilt after every turn by interview_brain_v2.py.
    conversation_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Written once, at completion -- competency-level evidence
    # summary (see interview_evidence_v2.py). Null while in_progress.
    final_evaluation_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="fallback")

    assigned_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

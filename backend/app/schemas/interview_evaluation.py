from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI - AI INTERVIEW ANSWER EVALUATION SCHEMA (Task 4D)
#
# Strict output contract for the Gemini-backed evaluator
# (services/assessment/interview_evaluator.py). Gemini's raw
# output is NEVER trusted directly — every field here is
# range-validated (0-100) before being stored, and the
# authoritative per-question composite score is computed
# server-side from these five sub-scores using fixed, centrally
# -defined weights (see INTERVIEW_SCORE_WEIGHTS in
# services/assessment/interview_scoring.py) — Gemini is never
# asked for (or trusted for) a single top-level "score" itself.
# ============================================================

class InterviewAnswerEvaluation(BaseModel):

    technical_correctness: int = Field(..., ge=0, le=100)
    understanding: int = Field(..., ge=0, le=100)
    reasoning: int = Field(..., ge=0, le=100)
    relevance: int = Field(..., ge=0, le=100)
    communication: int = Field(..., ge=0, le=100)

    strengths: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=6)

    # Short, candidate-safe summary — never the evaluator's
    # internal reasoning or the expected-answer rubric itself.
    feedback: str = Field(default="", max_length=500)

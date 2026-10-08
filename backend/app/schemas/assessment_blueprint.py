from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI - ASSESSMENT BLUEPRINT SCHEMA
#
# Output contract for the deterministic Assessment Blueprint
# Engine (backend/app/services/assessment/blueprint_engine.py).
#
# This is a plan for what a future assessment should cover —
# never the actual questions, and never a score. Every field
# is produced by the deterministic engine only; nothing here
# is written by Gemini.
# ============================================================


class SkillAssessmentItem(BaseModel):

    skill: str

    # critical | high | medium | low
    priority: str

    # strong_match | partial_match | missing
    match_status: str

    question_count: int

    reason: str


class DifficultyDistribution(BaseModel):

    basic: int
    intermediate: int
    advanced: int


class AssessmentBlueprint(BaseModel):

    candidate_id: int
    job_id: int
    job_title: str

    assessment_type: str = "aptitude"
    total_questions: int

    candidate_experience_level: str

    skills_to_assess: list[SkillAssessmentItem] = Field(
        default_factory=list
    )

    difficulty_distribution: DifficultyDistribution

    assessment_categories: list[str] = Field(
        default_factory=list
    )

    candidate_focus_areas: list[str] = Field(
        default_factory=list
    )

    job_focus_areas: list[str] = Field(
        default_factory=list
    )

    skill_gaps: list[str] = Field(
        default_factory=list
    )

    algorithm_explanation: list[str] = Field(
        default_factory=list
    )

    # Data-completeness notes — e.g. "no resume analysis found".
    # Never blocks blueprint generation; only documents that a
    # section of the blueprint had less evidence to work with.
    warnings: list[str] = Field(
        default_factory=list
    )

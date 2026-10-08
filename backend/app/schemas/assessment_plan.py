from typing import Literal

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI - ASSESSMENT PLAN SCHEMA (Task 5C Step 2)
#
# Output contract for the standalone, advisory-only Assessment
# Plan decision engine (backend/app/services/assessment/
# assessment_plan_engine.py). This is a PLAN of what SHOULD be
# assessed for a candidate/job pairing — never the actual
# questions (that remains a separate, later concern), and never
# a score.
#
# Nothing in the live assessment pipeline consumes this schema
# yet — see assessment_plan_engine.py's own module docstring.
# ============================================================


# The controlled assessment-type vocabulary (Task 5C's own
# list). Constrained via Literal specifically so neither Gemini
# nor a bug in the deterministic engine can ever introduce an
# assessment type outside this fixed set — Pydantic validation
# itself is the enforcement mechanism.
AssessmentTypeName = Literal[
    "aptitude",
    "programming",
    "sql",
    "frontend",
    "backend",
    "data_analysis",
    "excel",
    "business_case",
    "finance_case",
    "marketing_case",
    "hr_scenario",
    "communication",
    "written_response",
    "system_design",
    "debugging",
    "technical_knowledge",
    "role_specific_case",
    "ai_interview",
]

# Mirrors the existing AssessmentBlueprint's own documented
# priority/difficulty vocabularies exactly (see
# backend/app/schemas/assessment_blueprint.py) so the two
# schemas stay consistent with each other.
AssessmentPriority = Literal["critical", "high", "medium", "low"]
AssessmentDifficulty = Literal["basic", "intermediate", "advanced"]

PlanSource = Literal["gemini", "deterministic_fallback"]


class AssessmentPlanItem(BaseModel):

    assessment_type: AssessmentTypeName

    # The specific technology/technique this item targets, e.g.
    # "Python", "SQL", "React", "Excel" — None for assessment
    # types that are not technology-specific (e.g. hr_scenario,
    # communication, business_case).
    technology: str | None = None

    # Whether JobPilot's assessment engine can actually EXECUTE
    # candidate code/queries in this technology today — always
    # computed from technology_capability_registry.py, never
    # taken on faith from Gemini or invented here. False does
    # NOT mean "not relevant to the job" — it means "relevant,
    # but no execution runner exists for it yet."
    execution_supported: bool

    # Concise, job-relevant justification — must cite actual
    # job/candidate evidence (e.g. "Python is a required skill
    # in the job description"), never a protected or irrelevant
    # personal characteristic.
    reason: str = Field(min_length=1, max_length=400)

    priority: AssessmentPriority
    difficulty: AssessmentDifficulty

    question_count: int = Field(ge=1, le=10)


class AssessmentPlan(BaseModel):

    # A short, human-readable description of the role this plan
    # was built for (e.g. "Backend Developer", "HR Executive") —
    # free text, not a fixed taxonomy, since job titles are
    # too varied to usefully enumerate.
    role_type: str = Field(min_length=1, max_length=120)

    assessment_types: list[AssessmentPlanItem] = Field(
        min_length=1,
        max_length=8,
    )

    # Whether this specific plan came from a validated Gemini
    # response or the deterministic fallback — surfaced so a
    # future caller/HR view can be transparent about it, never
    # hidden.
    generated_by: PlanSource = "deterministic_fallback"

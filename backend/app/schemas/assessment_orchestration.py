from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.app.schemas.assessment_plan import AssessmentPlanItem
from backend.app.schemas.personalized_aptitude import AptitudeQuestionSet
from backend.app.schemas.personalized_programming import ProgrammingQuestionSet


# ============================================================
# JOBPILOT AI - ASSESSMENT ORCHESTRATION SCHEMA (Task 5C Step 5)
#
# Pure data shapes for the orchestrator (assessment_orchestrator
# .py). Nothing here is persisted to the database by this step —
# see that module's own header for why (the existing
# AptitudeTest.questions / ProgrammingTest.questions columns
# already have the generic Text/JSON capacity to hold a
# CandidateAssessmentSnapshot's per-stage question sets; wiring
# THAT persistence into the live create/advance flow is Step 6's
# job, not this one's).
# ============================================================


# Task 5's own "controlled state" requirement, using this
# project's existing naming convention (AssessmentPlanItem
# already has `execution_supported: bool`) rather than inventing
# a new vocabulary — see assessment_capability_registry.py.
CapabilityState = Literal[
    "execution_available",              # state 1: implemented AND (if applicable) executable
    "implementation_available_no_execution",  # state 2: known/implemented, but no execution runner (e.g. programming in a language with no runner)
    "unavailable",                       # state 3: no implementation exists for this assessment_type at all
]


class ValidatedAssessmentPlanItem(BaseModel):
    """AssessmentPlanItem plus the ONE additional fact the
    orchestrator adds: can this actually run today?"""

    item: AssessmentPlanItem
    capability_state: CapabilityState
    capability_reason: str = Field(min_length=1, max_length=300)


class ValidatedAssessmentPlan(BaseModel):

    role_type: str
    items: list[ValidatedAssessmentPlanItem]

    # The subset of assessment_type values (in plan order,
    # deduplicated) that are genuinely runnable today —
    # dynamically derived, never a hardcoded aptitude->
    # programming->ai_interview sequence (Step 5's own explicit
    # "do not hardcode" rule).
    runnable_stage_sequence: list[str]

    # assessment_type values the plan called for but that have
    # no real implementation/execution today — surfaced
    # honestly, never silently dropped without a trace (HR-
    # facing surfacing of this is a later step's UI concern; this
    # schema just carries the fact).
    unavailable_stage_types: list[str] = Field(default_factory=list)


# ------------------------------------------------------------
# STAGE OUTCOME - the critical platform-vs-candidate distinction
# ------------------------------------------------------------

StageOutcomeStatus = Literal[
    "passed",
    "candidate_failed",
    "platform_failure",
    "unavailable_capability",
]

PlatformFailureReason = Literal[
    "runner_unavailable",
    "generation_failed",
    "malformed_generated_content",
    "sandbox_error",
]


class StageOutcome(BaseModel):

    status: StageOutcomeStatus

    # Populated only when status == "platform_failure".
    platform_failure_reason: Optional[PlatformFailureReason] = None

    # Human-readable, candidate/HR-safe explanation — never a
    # raw exception message, never internal service names, never
    # a Gemini error string (Step 5's own "do not expose internal
    # AI architecture" rule).
    message: str = Field(min_length=1, max_length=300)


# ------------------------------------------------------------
# CANDIDATE ASSESSMENT SNAPSHOT - the stable, one-time-generated
# plan + question sets for a single candidate/application. Built
# once; never regenerated on refresh (Step 5's own "assessment
# stability" rule) - see assessment_orchestrator.build_candidate_
# assessment().
# ------------------------------------------------------------

class CandidateAssessmentSnapshot(BaseModel):

    role_type: str
    ordered_stages: list[str]

    aptitude_questions: Optional[AptitudeQuestionSet] = None
    programming_questions: Optional[ProgrammingQuestionSet] = None

    unavailable_stage_types: list[str] = Field(default_factory=list)


# ------------------------------------------------------------
# SKILL SCORE MAPPING (Step 5's own "skill score mapping" section)
# ------------------------------------------------------------

class SkillScoreEntry(BaseModel):

    skill: str
    earned: float
    max: float

    @property
    def percentage(self) -> float:
        return round((self.earned / self.max) * 100, 2) if self.max else 0.0

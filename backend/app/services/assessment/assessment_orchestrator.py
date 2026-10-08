from typing import Optional

from backend.app.models.job import Job
from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.assessment_orchestration import (
    CandidateAssessmentSnapshot,
    SkillScoreEntry,
    StageOutcome,
    ValidatedAssessmentPlan,
    ValidatedAssessmentPlanItem,
)
from backend.app.schemas.assessment_plan import AssessmentPlan
from backend.app.schemas.execution_result import ExecutionResult
from backend.app.schemas.personalized_programming import ProgrammingQuestionSet
from backend.app.services.assessment.assessment_capability_registry import (
    resolve_capability_state,
)
from backend.app.services.assessment.personalized_aptitude_generator import (
    generate_personalized_aptitude_questions,
)
from backend.app.services.assessment.personalized_programming_generator import (
    generate_personalized_programming_questions,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT ORCHESTRATOR (Task 5C Step 5)
#
# STANDALONE, PURE DECISION LAYER. No function in this module
# writes to the database, calls a live HTTP route, or is called
# by any existing route/module — this step CONNECTS the Step 2
# (AssessmentPlan), Step 3 (personalized generators), and Step 4
# (language execution) capabilities into one coherent decision
# flow, without touching the live Self Intro -> Aptitude ->
# Programming -> AI Interview pipeline at all. Wiring these
# functions into assessment_sessions.py / aptitude_integration.py
# / programming_integration.py / interview_integration.py (so a
# real candidate flow actually calls them) is Step 6's job, not
# this one's — see the module-level docstring precedent set by
# Step 2/3/4's own "standalone, advisory-only" scope.
#
# WHY NO DATABASE PERSISTENCE HERE (Step 5's own explicit
# "inspect existing models before adding new schema" rule):
# AptitudeTest.questions and ProgrammingTest.questions are both
# already generic Text/JSON-blob columns (verified directly
# against the model source — see this step's audit) — a
# CandidateAssessmentSnapshot's question sets fit those existing
# columns with ZERO schema change. This module produces that
# snapshot; a LATER step's job is to actually persist it into
# those existing columns from within aptitude_integration.py /
# programming_integration.py's own creation flow (reusing THEIR
# existing row-creation code, never duplicating it here — Step
# 5's own "do not duplicate" rule).
#
# STAGE SEQUENCE IS NEVER HARDCODED: build_candidate_assessment()
# and determine_next_stage() both read the ordered stage list
# from the AssessmentPlan (via ValidatedAssessmentPlan), not from
# a fixed aptitude->programming->interview literal sequence.
# ============================================================


def validate_plan_capabilities(plan: AssessmentPlan) -> ValidatedAssessmentPlan:
    """
    Attaches a genuine capability_state to every plan item (Step
    5's three-state model, via assessment_capability_registry.py)
    and derives the ORDERED sequence of assessment_type values
    that are actually runnable today. An item the plan called for
    but that has no real implementation is recorded in
    unavailable_stage_types — never silently dropped without a
    trace, and never replaced by an unrelated assessment type
    (Step 5's own "automated fallback policy": no invented
    fallback technology, ever).
    """

    validated_items: list[ValidatedAssessmentPlanItem] = []
    runnable_sequence: list[str] = []
    unavailable_types: list[str] = []

    for item in plan.assessment_types:

        state, reason = resolve_capability_state(item)

        validated_items.append(
            ValidatedAssessmentPlanItem(
                item=item,
                capability_state=state,
                capability_reason=reason,
            )
        )

        if state == "execution_available":

            if item.assessment_type not in runnable_sequence:
                runnable_sequence.append(item.assessment_type)

        else:

            if item.assessment_type not in unavailable_types:
                unavailable_types.append(item.assessment_type)

    return ValidatedAssessmentPlan(
        role_type=plan.role_type,
        items=validated_items,
        runnable_stage_sequence=runnable_sequence,
        unavailable_stage_types=unavailable_types,
    )


def determine_next_stage(
    ordered_stages: list[str],
    current_stage: Optional[str],
    current_stage_passed: bool,
) -> Optional[str]:
    """
    Pure function: given the plan-derived ordered stage sequence
    (NEVER a hardcoded literal sequence — see module header), the
    stage the candidate just completed, and whether they passed
    it, returns the next stage's assessment_type, or None if
    there is no next stage OR the candidate failed (failure stops
    progression — Step 5's own explicit "failure behavior" rule).

    current_stage=None means "the candidate has not started
    yet" — returns the FIRST stage in the sequence.
    """

    if not current_stage_passed:
        return None

    if current_stage is None:
        return ordered_stages[0] if ordered_stages else None

    if current_stage not in ordered_stages:
        return None

    next_index = ordered_stages.index(current_stage) + 1

    if next_index >= len(ordered_stages):
        return None  # candidate has completed every stage in the plan

    return ordered_stages[next_index]


def build_candidate_assessment(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: AssessmentPlan,
    use_gemini: bool = True,
) -> CandidateAssessmentSnapshot:
    """
    Builds the ONE-TIME, STABLE snapshot for a candidate's
    assessment (Step 5's own "assessment stability" rule — this
    function is meant to be called exactly once per candidate/
    application and its result persisted, never regenerated on
    every request). Composes Step 2's validated plan with Step
    3's generators — never duplicates their logic.

    Only generates question sets for stages that are genuinely
    "execution_available" per validate_plan_capabilities() — a
    stage marked unavailable never gets a fake/placeholder
    question set.
    """

    validated_plan = validate_plan_capabilities(plan)

    aptitude_questions = None
    programming_questions = None

    if "aptitude" in validated_plan.runnable_stage_sequence:

        aptitude_questions = generate_personalized_aptitude_questions(
            job, blueprint, use_gemini=use_gemini,
        )

    if "programming" in validated_plan.runnable_stage_sequence:

        programming_questions = generate_personalized_programming_questions(
            job, blueprint, plan, use_gemini=use_gemini,
        )

    return CandidateAssessmentSnapshot(
        role_type=validated_plan.role_type,
        ordered_stages=validated_plan.runnable_stage_sequence,
        aptitude_questions=aptitude_questions,
        programming_questions=programming_questions,
        unavailable_stage_types=validated_plan.unavailable_stage_types,
    )


# ------------------------------------------------------------
# PLATFORM FAILURE VS CANDIDATE FAILURE (Step 5's own critical
# distinction — never let a platform problem become a recorded
# "candidate_failed" outcome).
# ------------------------------------------------------------

def classify_aptitude_outcome(passed: bool) -> StageOutcome:
    """
    Aptitude scoring is always fully objective/deterministic
    once a valid question set exists (Step 3 guarantees a valid
    question set is always produced, even via fallback) — there
    is no platform-failure path for aptitude scoring itself.
    """

    if passed:
        return StageOutcome(status="passed", message="Aptitude stage passed.")

    return StageOutcome(
        status="candidate_failed",
        message="Aptitude stage did not meet the passing threshold.",
    )


def classify_programming_execution_outcome(
    execution_results: list[ExecutionResult],
    passed: bool,
) -> StageOutcome:
    """
    Inspects the underlying ExecutionResults BEFORE trusting a
    pass/fail verdict: a "runner_unavailable" or "sandbox_error"
    among them means the PLATFORM failed to execute the
    candidate's code at all — that must never be recorded as the
    candidate having failed the question. A "runtime_error" or
    "timeout" is the candidate's OWN code misbehaving, which is
    correctly a candidate-side outcome, not a platform failure.
    """

    platform_error_types = {"runner_unavailable", "sandbox_error"}

    for result in execution_results:

        if result.error_type in platform_error_types:

            return StageOutcome(
                status="platform_failure",
                platform_failure_reason=result.error_type,  # type: ignore[arg-type]
                message=(
                    "The execution platform could not run the "
                    "candidate's submission for one or more test "
                    "cases."
                ),
            )

    if passed:
        return StageOutcome(status="passed", message="Programming stage passed.")

    return StageOutcome(
        status="candidate_failed",
        message="Programming stage did not meet the passing threshold.",
    )


def classify_generation_outcome(question_set_is_valid: bool) -> StageOutcome:
    """
    Step 3's generators are designed to NEVER return an invalid/
    empty question set (Gemini failure always falls back to a
    valid deterministic set) — this function exists as an
    explicit, testable safety net for the theoretical case where
    a caller receives something invalid anyway (e.g. a future
    integration bug), so that scenario is correctly classified as
    a platform failure rather than silently letting a candidate
    see a broken assessment.
    """

    if question_set_is_valid:
        return StageOutcome(status="passed", message="Assessment generated successfully.")

    return StageOutcome(
        status="platform_failure",
        platform_failure_reason="malformed_generated_content",
        message="The assessment could not be generated correctly.",
    )


# ------------------------------------------------------------
# SKILL SCORE MAPPING (Step 5's own "skill score mapping" section)
# ------------------------------------------------------------

def map_programming_skill_scores(
    question_set: ProgrammingQuestionSet,
    score_result: dict,
) -> list[SkillScoreEntry]:
    """
    Aggregates a score_programming_set() result (Step 3's
    personalized_scoring.py) by technology/skill, for the future
    HR report's skill breakdown (e.g. "Python: 88%"). Every
    ProgrammingMainQuestion already names its own `technology`
    (Step 3) — this just sums earned/max marks per technology
    across however many main questions share it.
    """

    totals: dict[str, dict[str, float]] = {}

    score_by_question_id = {
        entry["question_id"]: entry for entry in score_result.get("questions", [])
    }

    for question in question_set.main_questions:

        skill = question.technology or "General Problem Solving"

        question_score = score_by_question_id.get(question.question_id)

        if question_score is None:
            continue

        bucket = totals.setdefault(skill, {"earned": 0.0, "max": 0.0})
        bucket["earned"] += question_score["earned"]
        bucket["max"] += question_score["max"]

    return [
        SkillScoreEntry(skill=skill, earned=round(values["earned"], 2), max=round(values["max"], 2))
        for skill, values in totals.items()
    ]

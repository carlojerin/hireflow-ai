from backend.app.schemas.assessment_plan import AssessmentPlanItem
from backend.app.schemas.assessment_orchestration import CapabilityState
from backend.app.services.assessment.language_capability_registry import (
    get_language_capability,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT IMPLEMENTATION CAPABILITY REGISTRY
# (Task 5C Step 5)
#
# ONE authoritative answer to "does JobPilot actually have a
# working IMPLEMENTATION for this assessment_type today?" — a
# DIFFERENT, higher-level question than Step 4's language
# execution registry (which only answers "can we run CODE in
# this language"). Built explicitly on top of / cross-checked
# against the existing registries (Step 2's technology_
# capability_registry.py via language_capability_registry.py,
# Step 4's language_capability_registry.py directly) — never a
# competing source of truth.
#
# Only two assessment types have a real, tested question-
# generation implementation as of Step 3:
#   - "aptitude"    (personalized_aptitude_generator.py)
#   - "programming" (personalized_programming_generator.py,
#                    further gated by language execution support
#                    for the SPECIFIC technology requested)
#   - "ai_interview" is the existing, live, unrelated interview
#     system (interview_question_generator.py /
#     interview_evaluator.py) — already fully implemented, just
#     not part of the Step 2/3 personalization work.
#
# Every other assessment_type in the Step 2 controlled
# vocabulary (sql, frontend, backend, data_analysis, excel,
# business_case, finance_case, marketing_case, hr_scenario,
# communication, written_response, system_design, debugging,
# technical_knowledge, role_specific_case) is a real, named
# CONCEPT in the vocabulary but has NO generator/evaluator built
# yet — implementation_available=False for all of them. This is
# not a gap this step fixes; Step 5's job is to represent that
# gap honestly (see resolve_capability_state below), not close it
# (explicitly out of scope — "IMPORTANT — DO NOT OVERBUILD").
# ============================================================

IMPLEMENTATION_AVAILABLE: dict[str, bool] = {
    "aptitude": True,
    "programming": True,
    "ai_interview": True,

    "sql": False,
    "frontend": False,
    "backend": False,
    "data_analysis": False,
    "excel": False,
    "business_case": False,
    "finance_case": False,
    "marketing_case": False,
    "hr_scenario": False,
    "communication": False,
    "written_response": False,
    "system_design": False,
    "debugging": False,
    "technical_knowledge": False,
    "role_specific_case": False,
}

# assessment_type values whose implementation ADDITIONALLY
# depends on a specific technology's real code-execution support
# (Step 4) — "programming" is implemented as a QUESTION
# GENERATOR for any technology (Step 3), but a candidate can only
# be scored against real executed code if that technology's
# language runner actually exists.
_EXECUTION_GATED_ASSESSMENT_TYPES = {"programming"}


def is_implementation_available(assessment_type: str) -> bool:

    return IMPLEMENTATION_AVAILABLE.get(assessment_type, False)


def resolve_capability_state(
    item: AssessmentPlanItem,
) -> tuple[CapabilityState, str]:
    """
    Resolves ONE AssessmentPlanItem to the three-state model
    (Step 5's own explicit requirement), returning
    (capability_state, human-readable reason).

    NEVER invents a fallback technology and NEVER marks something
    executable it cannot genuinely execute (Step 5's own
    "automated fallback policy" — a Java-required programming
    item that has no Java runner is "implementation_available_
    no_execution", NEVER silently re-labeled as a Python item).
    """

    if not is_implementation_available(item.assessment_type):

        return (
            "unavailable",
            f"No implementation exists yet for assessment type "
            f"'{item.assessment_type}'.",
        )

    if item.assessment_type not in _EXECUTION_GATED_ASSESSMENT_TYPES:

        # aptitude / ai_interview - implemented, and neither one
        # depends on a language runner at all.
        return (
            "execution_available",
            f"'{item.assessment_type}' is fully implemented.",
        )

    # "programming" - implemented as a generator for any
    # technology, but genuinely scoreable only if that specific
    # technology's language runner exists (Step 4).
    language_capability = get_language_capability(item.technology)

    if language_capability.execution_supported:

        return (
            "execution_available",
            f"Programming assessment for '{item.technology}' is "
            "implemented and executable.",
        )

    return (
        "implementation_available_no_execution",
        (
            f"Programming assessment for '{item.technology}' has no "
            "secure execution runner yet — the question structure "
            "can be generated, but candidate code cannot yet be "
            "objectively executed and scored for this language."
        ),
    )

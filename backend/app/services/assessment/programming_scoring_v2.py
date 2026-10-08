from dataclasses import dataclass, field

from backend.app.schemas.execution_result import ExecutionResult
from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
    ProgrammingSubpartV2,
)


# ============================================================
# JOBPILOT AI — PROGRAMMING ENGINE V2 SCORING HIERARCHY
# (Programming Engine V2 task, Step 11)
#
# test case -> subpart -> main question -> entire assessment,
# exactly the hierarchy the task specifies. The SCORE always comes
# from actually comparing execution output to expected_stdout —
# nothing here trusts an AI-asserted verdict (see
# programming_question_validator_v2.py's own module header for
# the matching Step 12 rule: Gemini generates the question,
# execution decides the score, never the other way round).
#
# Every dataclass below is deliberately candidate-safe: none of
# them carry a hidden test case's stdin/expected_stdout — only
# test_case_id, whether it passed, its weight, and the execution
# metadata (timing/error classification) a candidate is normally
# shown. Redaction is enforced by construction, not by a
# downstream "remember to strip this" step.
# ============================================================


@dataclass(frozen=True)
class TestCaseScoreResult:

    test_case_id: str
    is_hidden: bool
    passed: bool
    weight: float

    execution_time_ms: int
    error_type: str  # ExecutionResult.error_type, e.g. "success"/"runtime_error"/"timeout"


@dataclass(frozen=True)
class SubpartScoreResult:

    subpart_id: str
    earned_fraction: float  # 0.0-1.0 of this subpart's own weight
    test_case_results: list[TestCaseScoreResult]
    platform_failure: bool


@dataclass(frozen=True)
class MainQuestionScoreResult:

    question_id: int
    earned_points: float
    max_points: float
    language_used: str
    subpart_results: list[SubpartScoreResult] = field(default_factory=list)
    platform_failure: bool = False


@dataclass(frozen=True)
class AssessmentScoreResult:

    total_earned: float
    total_possible: float
    question_results: list[MainQuestionScoreResult] = field(default_factory=list)


_PLATFORM_ERROR_TYPES = {"runner_unavailable", "sandbox_error"}


def _normalize_output(text: str) -> str:
    """
    Line endings and trailing spaces never decide a test: Windows prints "\r\n" where the expected text has "\n",
    which failed every multi-line answer. Everything else (for example "5" versus "5.0") still has to match exactly.
    """

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    return "\n".join(line.rstrip() for line in lines).rstrip("\n")


def score_test_case(
    test_case: ExecutableTestCase,
    execution_result: ExecutionResult,
) -> TestCaseScoreResult:
    """
    A test case passes only if execution genuinely succeeded AND
    its stdout matches expected_stdout (trailing-whitespace/
    newline differences are normalized away — a candidate is never
    penalized for a missing final newline, but no other
    normalization is applied, so "5" and "5.0" are correctly
    treated as different outputs).
    """

    passed = (
        execution_result.success
        and _normalize_output(execution_result.stdout) == _normalize_output(test_case.expected_stdout)
    )

    return TestCaseScoreResult(
        test_case_id=test_case.test_case_id,
        is_hidden=test_case.is_hidden,
        passed=passed,
        weight=test_case.weight,
        execution_time_ms=execution_result.execution_time_ms,
        error_type=execution_result.error_type,
    )


def score_subpart(
    subpart: ProgrammingSubpartV2,
    test_case_results: list[TestCaseScoreResult],
) -> SubpartScoreResult:

    earned_fraction = sum(
        tc.weight for tc in test_case_results if tc.passed
    )

    platform_failure = any(
        tc.error_type in _PLATFORM_ERROR_TYPES for tc in test_case_results
    )

    return SubpartScoreResult(
        subpart_id=subpart.subpart_id,
        earned_fraction=min(1.0, earned_fraction),
        test_case_results=test_case_results,
        platform_failure=platform_failure,
    )


def score_main_question(
    question: ProgrammingMainQuestionV2,
    subpart_results: list[SubpartScoreResult],
    language_used: str,
) -> MainQuestionScoreResult:

    results_by_id = {r.subpart_id: r for r in subpart_results}

    earned_points = 0.0

    for subpart in question.subparts:

        result = results_by_id.get(subpart.subpart_id)

        if result is None:
            continue

        earned_points += question.points * subpart.weight * result.earned_fraction

    platform_failure = any(r.platform_failure for r in subpart_results)

    return MainQuestionScoreResult(
        question_id=question.question_id,
        earned_points=round(earned_points, 4),
        max_points=question.points,
        language_used=language_used,
        subpart_results=subpart_results,
        platform_failure=platform_failure,
    )


def score_assessment(
    question_set: ProgrammingQuestionSetV2,
    question_results: list[MainQuestionScoreResult],
) -> AssessmentScoreResult:

    total_earned = round(sum(r.earned_points for r in question_results), 4)
    total_possible = sum(q.points for q in question_set.main_questions)

    return AssessmentScoreResult(
        total_earned=total_earned,
        total_possible=total_possible,
        question_results=question_results,
    )

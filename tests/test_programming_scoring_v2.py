from backend.app.schemas.execution_result import ExecutionResult
from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingSubpartV2,
)
from backend.app.services.assessment.programming_scoring_v2 import (
    score_main_question,
    score_subpart,
    score_test_case,
)


def _exec_result(success, stdout="", error_type="success"):
    return ExecutionResult(
        success=success, stdout=stdout, error_type=error_type,
        language="python", execution_time_ms=5,
    )


def test_score_test_case_passes_on_exact_match():
    tc = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    result = score_test_case(tc, _exec_result(True, "5"))
    assert result.passed is True


def test_score_test_case_ignores_trailing_newline_only():
    tc = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    result = score_test_case(tc, _exec_result(True, "5\n"))
    assert result.passed is True


def test_score_test_case_fails_on_wrong_output():
    tc = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    result = score_test_case(tc, _exec_result(True, "6"))
    assert result.passed is False


def test_score_test_case_fails_on_execution_failure_even_with_matching_text():
    tc = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    result = score_test_case(tc, _exec_result(False, "5", error_type="runtime_error"))
    assert result.passed is False


def test_score_subpart_partial_credit():
    tc_pass = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=0.6)
    tc_fail = ExecutableTestCase(test_case_id="b", expected_stdout="6", weight=0.4)
    subpart = ProgrammingSubpartV2(
        subpart_id="sp1", description="d", weight=1.0,
        test_cases=[tc_pass, tc_fail],
    )
    results = [
        score_test_case(tc_pass, _exec_result(True, "5")),
        score_test_case(tc_fail, _exec_result(True, "wrong")),
    ]
    subpart_result = score_subpart(subpart, results)
    assert subpart_result.earned_fraction == 0.6  # only the 0.6-weighted case passed


def test_score_subpart_detects_platform_failure():
    tc = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    subpart = ProgrammingSubpartV2(
        subpart_id="sp1", description="d", weight=1.0, test_cases=[tc],
    )
    results = [score_test_case(tc, _exec_result(False, "", error_type="runner_unavailable"))]
    subpart_result = score_subpart(subpart, results)
    assert subpart_result.platform_failure is True
    assert subpart_result.earned_fraction == 0.0


def test_score_main_question_combines_subparts_by_weight():
    tc1 = ExecutableTestCase(test_case_id="a", expected_stdout="5", weight=1.0)
    tc2 = ExecutableTestCase(test_case_id="b", expected_stdout="6", weight=1.0)

    subpart1 = ProgrammingSubpartV2(subpart_id="sp1", description="d", weight=0.5, test_cases=[tc1])
    subpart2 = ProgrammingSubpartV2(subpart_id="sp2", description="d", weight=0.5, test_cases=[tc2])

    question = ProgrammingMainQuestionV2(
        question_id=1, title="T", description="D",
        input_specification="I", output_specification="O",
        allowed_languages=["python"], points=20.0,
        subparts=[subpart1, subpart2],
    )

    # subpart1 fully passes, subpart2 fully fails
    r1 = score_subpart(subpart1, [score_test_case(tc1, _exec_result(True, "5"))])
    r2 = score_subpart(subpart2, [score_test_case(tc2, _exec_result(True, "wrong"))])

    question_result = score_main_question(question, [r1, r2], language_used="python")

    # 50% of 20 points = 10
    assert question_result.earned_points == 10.0
    assert question_result.max_points == 20.0

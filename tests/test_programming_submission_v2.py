import dataclasses

import pytest

from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
    ProgrammingSubpartV2,
)
from backend.app.services.assessment.programming_fallback_bank_v2 import (
    build_fallback_question_set_v2,
)
from backend.app.services.assessment.programming_submission_v2 import (
    CandidateSubmission,
    LanguageRejectedResult,
    run_public_examples,
    submit_question,
)

_SUM_QUESTION_ID = 1  # "Sum of Two Numbers" -- has 1 public example (tc1)

_SOLUTIONS = {
    "python": "a, b = map(int, input().split())\nprint(a + b)",
    "javascript": (
        "const data = require('fs').readFileSync(0, 'utf-8').trim();"
        "const [a, b] = data.split(' ').map(Number);"
        "console.log(a + b);"
    ),
    "csharp": (
        "using System;\n"
        "class Program { static void Main() {"
        " var parts = Console.ReadLine().Split(' ');"
        " Console.WriteLine(int.Parse(parts[0]) + int.Parse(parts[1])); } }\n"
    ),
}


def _question_set(allowed=None):
    return build_fallback_question_set_v2(allowed or ["python", "javascript", "csharp"])


# I, J, K: candidate selects each supported language, submission succeeds
def test_candidate_can_submit_in_python():
    _assert_full_marks_for_language("python")


def test_candidate_can_submit_in_javascript():
    _assert_full_marks_for_language("javascript")


def test_candidate_can_submit_in_csharp():
    _assert_full_marks_for_language("csharp")


def _assert_full_marks_for_language(language):
    qs = _question_set()
    submission = CandidateSubmission(
        question_id=_SUM_QUESTION_ID, language=language, source_code=_SOLUTIONS[language],
    )
    result = submit_question(qs, submission)
    assert not isinstance(result, LanguageRejectedResult)
    assert result.earned_points == result.max_points
    assert result.language_used == language


# L: an unsupported language (swift -- java is now genuinely
# supported as of the Complete Multi-Language Execution Engine
# task, so it no longer demonstrates this case) is rejected,
# never silently run as something else
def test_unsupported_language_is_rejected():
    qs = _question_set(allowed=["python", "javascript", "csharp", "swift"])
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="swift", source_code="print(1)")
    result = submit_question(qs, submission)
    assert isinstance(result, LanguageRejectedResult)
    assert result.accepted is False


# M: a language not permitted for THIS question (even if executable) is rejected
def test_language_not_allowed_for_this_question_is_rejected():
    qs = _question_set(allowed=["python"])  # only python permitted
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="javascript", source_code=_SOLUTIONS["javascript"])
    result = submit_question(qs, submission)
    assert isinstance(result, LanguageRejectedResult)
    assert "not permitted" in result.reason


# N: the SAME question evaluated identically through different runners
def test_same_question_scored_identically_across_runners():
    qs = _question_set()
    scores = {}
    for language in ["python", "javascript", "csharp"]:
        submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language=language, source_code=_SOLUTIONS[language])
        scores[language] = submit_question(qs, submission).earned_points
    assert scores["python"] == scores["javascript"] == scores["csharp"] == 20.0


# O: running against public examples only, no hidden data leaked
def test_run_public_examples_only_executes_visible_test_cases():
    qs = _question_set()
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="python", source_code=_SOLUTIONS["python"])
    results = run_public_examples(qs, submission)
    assert not isinstance(results, LanguageRejectedResult)
    question = next(q for q in qs.main_questions if q.question_id == _SUM_QUESTION_ID)
    visible_ids = {
        tc.test_case_id for sp in question.subparts for tc in sp.test_cases if not tc.is_hidden
    }
    assert {r.test_case_id for r in results} == visible_ids
    assert len(results) > 0
    for r in results:
        assert r.execution.success is True


# P, Q: submitting executes hidden tests too and awards partial credit
def test_submit_executes_hidden_tests_and_awards_partial_credit():
    qs = _question_set()
    # correct only for POSITIVE inputs -- breaks sp2's hidden negative-
    # number test case, proving hidden tests actually ran and affected
    # the score (not just the public example).
    broken_code = "a, b = map(int, input().split())\nprint(abs(a) + abs(b))"
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="python", source_code=broken_code)
    result = submit_question(qs, submission)
    assert 0 < result.earned_points < result.max_points


# R: compile error (C#)
def test_csharp_compile_error_is_classified_correctly():
    qs = _question_set()
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="csharp", source_code="this is not valid C# {{{")
    result = submit_question(qs, submission)
    assert result.earned_points == 0.0
    error_types = {tc.error_type for sp in result.subpart_results for tc in sp.test_case_results}
    assert "compile_error" in error_types


# S: runtime error (Python)
def test_python_runtime_error_is_classified_correctly():
    qs = _question_set()
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="python", source_code="raise ValueError('boom')")
    result = submit_question(qs, submission)
    assert result.earned_points == 0.0
    error_types = {tc.error_type for sp in result.subpart_results for tc in sp.test_case_results}
    assert "runtime_error" in error_types


# T: timeout (JavaScript)
def test_javascript_timeout_is_classified_correctly():

    infinite_loop_question = ProgrammingMainQuestionV2(
        question_id=1, title="Infinite Loop Trap", description="d",
        input_specification="i", output_specification="o",
        allowed_languages=["javascript"], points=20.0,
        subparts=[
            ProgrammingSubpartV2(subpart_id="sp1", description="d", weight=0.5, test_cases=[
                ExecutableTestCase(test_case_id="tc1", expected_stdout="x", weight=1.0),
            ]),
            ProgrammingSubpartV2(subpart_id="sp2", description="d", weight=0.5, test_cases=[
                ExecutableTestCase(test_case_id="tc2", expected_stdout="y", weight=1.0),
            ]),
        ],
    )
    other_questions = build_fallback_question_set_v2(["javascript"]).main_questions[1:]
    qs = ProgrammingQuestionSetV2(
        main_questions=[infinite_loop_question, *other_questions], generated_by="fallback",
    )

    submission = CandidateSubmission(question_id=1, language="javascript", source_code="while(true) {}")
    result = submit_question(qs, submission)
    error_types = {tc.error_type for sp in result.subpart_results for tc in sp.test_case_results}
    assert "timeout" in error_types


# Y: candidate cannot see hidden test data -- structural guarantee
def test_hidden_test_data_never_present_on_scoring_dataclasses():
    qs = _question_set()
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="python", source_code=_SOLUTIONS["python"])
    result = submit_question(qs, submission)
    field_names = set()
    for sp in result.subpart_results:
        field_names.update(f.name for f in dataclasses.fields(sp))
        for tc in sp.test_case_results:
            field_names.update(f.name for f in dataclasses.fields(tc))
    assert "stdin" not in field_names
    assert "expected_stdout" not in field_names
    assert "stdout" not in field_names


# Z: candidate cannot alter the server-side score -- frozen dataclasses
def test_score_result_is_immutable():
    qs = _question_set()
    submission = CandidateSubmission(question_id=_SUM_QUESTION_ID, language="python", source_code=_SOLUTIONS["python"])
    result = submit_question(qs, submission)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.earned_points = 999.0

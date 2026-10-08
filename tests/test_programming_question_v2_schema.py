import pytest
from pydantic import ValidationError

from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
    ProgrammingSubpartV2,
)


def _test_case(tc_id="tc1", weight=1.0, hidden=True):
    return ExecutableTestCase(
        test_case_id=tc_id,
        stdin="hello world hello",
        expected_stdout="hello 2\nworld 1",
        weight=weight,
        is_hidden=hidden,
    )


def _subpart(subpart_id="sp1", weight=1.0, test_cases=None):
    return ProgrammingSubpartV2(
        subpart_id=subpart_id,
        description="Count word frequency.",
        weight=weight,
        test_cases=test_cases or [_test_case()],
    )


def _question(question_id=1, points=20.0, subparts=None, allowed=None, required=None):
    return ProgrammingMainQuestionV2(
        question_id=question_id,
        title="Word Frequency",
        description="Return the frequency of each word.",
        input_specification="A line of space-separated words.",
        output_specification="Each word and its count, one per line.",
        allowed_languages=allowed or ["python", "java"],
        required_technology=required,
        points=points,
        subparts=subparts or [
            _subpart("sp1", 0.5, [_test_case("tc1", 1.0)]),
            _subpart("sp2", 0.5, [_test_case("tc2", 1.0)]),
        ],
    )


def test_valid_test_case_and_subpart():
    subpart = _subpart()
    assert subpart.test_cases[0].expected_stdout == "hello 2\nworld 1"


def test_subpart_rejects_test_case_weights_not_summing_to_one():
    with pytest.raises(ValidationError):
        _subpart(test_cases=[_test_case("a", 0.5), _test_case("b", 0.3)])


def test_question_rejects_subpart_weights_not_summing_to_one():
    with pytest.raises(ValidationError):
        _question(subparts=[_subpart("sp1", 0.5), _subpart("sp2", 0.6)])


def test_question_requires_2_to_4_subparts():
    with pytest.raises(ValidationError):
        _question(subparts=[_subpart("only-one", 1.0)])


def test_required_technology_must_be_in_allowed_languages():
    with pytest.raises(ValidationError):
        _question(allowed=["python", "java"], required="go")


def test_required_technology_in_allowed_languages_is_fine():
    q = _question(allowed=["python", "java"], required="java")
    assert q.required_technology == "java"


def test_question_set_requires_exactly_5_main_questions():
    with pytest.raises(ValidationError):
        ProgrammingQuestionSetV2(main_questions=[_question(1)], generated_by="fallback")


def test_question_set_points_must_sum_to_100():
    questions = [_question(i, points=15.0) for i in range(1, 6)]  # sums to 75
    with pytest.raises(ValidationError):
        ProgrammingQuestionSetV2(main_questions=questions, generated_by="fallback")


def test_question_set_valid_when_points_sum_to_100():
    questions = [_question(i, points=20.0) for i in range(1, 6)]  # sums to 100
    qs = ProgrammingQuestionSetV2(main_questions=questions, generated_by="fallback")
    assert len(qs.main_questions) == 5


def test_question_set_rejects_duplicate_question_ids():
    questions = [_question(1, points=20.0) for _ in range(5)]  # all id=1
    with pytest.raises(ValidationError):
        ProgrammingQuestionSetV2(main_questions=questions, generated_by="fallback")

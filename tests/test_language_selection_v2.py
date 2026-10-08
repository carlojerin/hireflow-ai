from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingSubpartV2,
)
from backend.app.services.assessment.language_selection import (
    validate_language_selection,
)


def _question(allowed):
    return ProgrammingMainQuestionV2(
        question_id=1,
        title="T",
        description="D",
        input_specification="I",
        output_specification="O",
        allowed_languages=allowed,
        points=20.0,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1",
                description="d",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(
                        test_case_id="tc1", stdin="", expected_stdout="x", weight=1.0,
                    )
                ],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2",
                description="d",
                weight=0.5,
                test_cases=[
                    ExecutableTestCase(
                        test_case_id="tc2", stdin="", expected_stdout="y", weight=1.0,
                    )
                ],
            ),
        ],
    )


def test_accepts_an_allowed_execution_supported_language():
    q = _question(["python", "java"])
    result = validate_language_selection("python", q)
    assert result.accepted is True
    assert result.canonical_language == "python"


def test_rejects_unknown_language_string():
    q = _question(["python"])
    result = validate_language_selection("brainfuck", q)
    assert result.accepted is False
    assert "not a recognized language" in result.reason


def test_rejects_language_not_in_allowed_list():
    q = _question(["python"])
    result = validate_language_selection("java", q)
    assert result.accepted is False
    assert "not permitted for this question" in result.reason


def test_rejects_allowed_but_not_yet_execution_supported_language():
    # Task 5C, Complete Multi-Language Execution Engine: "go" is
    # now fully AVAILABLE (real runner, real toolchain), so it no
    # longer demonstrates this case -- "swift" is the current
    # genuine example (real registered runner architecture, but
    # not execution_supported; see swift_runner.py's docstring
    # for the confirmed root cause).
    q = _question(["python", "swift"])
    result = validate_language_selection("swift", q)
    assert result.accepted is False
    assert "no working execution runner" in result.reason or "runner" in result.reason


def test_never_falls_back_to_a_different_language():
    q = _question(["python"])
    result = validate_language_selection("java", q)
    # rejection must never silently resolve to "python" instead
    assert result.accepted is False
    assert result.canonical_language == "java"

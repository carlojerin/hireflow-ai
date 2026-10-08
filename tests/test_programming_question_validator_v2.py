from backend.app.services.assessment.programming_question_validator_v2 import (
    validate_question_set_v2,
)


def _valid_question(qid, points):
    return {
        "question_id": qid,
        "title": "T",
        "description": "D",
        "input_specification": "I",
        "output_specification": "O",
        "allowed_languages": ["python"],
        "points": points,
        "subparts": [
            {
                "subpart_id": "sp1",
                "description": "d",
                "weight": 0.5,
                "test_cases": [
                    {"test_case_id": "tc1", "expected_stdout": "x", "weight": 1.0},
                ],
            },
            {
                "subpart_id": "sp2",
                "description": "d",
                "weight": 0.5,
                "test_cases": [
                    {"test_case_id": "tc2", "expected_stdout": "y", "weight": 1.0},
                ],
            },
        ],
    }


def test_valid_raw_dict_passes():
    raw = {
        "main_questions": [_valid_question(i, 20.0) for i in range(1, 6)],
        "generated_by": "gemini",
    }
    result = validate_question_set_v2(raw)
    assert result.valid is True
    assert result.question_set is not None
    assert result.errors == []


def test_structurally_broken_dict_fails_with_errors_not_a_crash():
    raw = {"main_questions": [], "generated_by": "gemini"}
    result = validate_question_set_v2(raw)
    assert result.valid is False
    assert result.question_set is None
    assert len(result.errors) > 0


def test_unknown_allowed_language_fails():
    q = _valid_question(1, 20.0)
    q["allowed_languages"] = ["python", "not-a-real-language"]
    raw = {
        "main_questions": [q] + [_valid_question(i, 20.0) for i in range(2, 6)],
        "generated_by": "gemini",
    }
    result = validate_question_set_v2(raw)
    assert result.valid is False
    assert any("not a language JobPilot recognizes" in e for e in result.errors)

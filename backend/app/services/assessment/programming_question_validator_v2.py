from dataclasses import dataclass, field

from pydantic import ValidationError

from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.schemas.sql_question_v2 import SQLQuestionSetV2
from backend.app.services.assessment.language_capability_registry import (
    normalize_language_name,
)


# ============================================================
# JOBPILOT AI — DETERMINISTIC PROGRAMMING QUESTION VALIDATOR
# (Programming Engine V2 task, Step 12)
#
# Gemini generation and execution are kept STRICTLY separate.
# This module is the deterministic gate between them: it takes
# whatever raw dict a generator (AI or fallback) produced and
# either returns a genuinely valid ProgrammingQuestionSetV2 or a
# clear list of reasons it is not — it NEVER asks Gemini (or
# anything else) whether the content "looks right", and it never
# lets a structurally invalid question set through because the
# text inside it seemed plausible. The pydantic model itself
# (programming_question_v2.py) already enforces the load-bearing
# structural rules (weights summing to 1.0, points summing to
# 100, exactly 5 questions, 2-4 subparts) via its own
# model_validators — this module's job is to catch those
# violations without letting them raise all the way up into a
# generator, and to add the one cross-cutting check the schema
# itself cannot perform alone: that every allowed_languages entry
# is at least a NAME JobPilot recognizes (execution support is a
# separate, later concern — see language_selection.py, which is
# what actually enforces execution_supported at submission time).
# ============================================================


@dataclass(frozen=True)
class QuestionSetValidationResult:

    valid: bool
    question_set: ProgrammingQuestionSetV2 | None
    errors: list[str] = field(default_factory=list)


def validate_question_set_v2(raw: dict) -> QuestionSetValidationResult:

    try:
        question_set = ProgrammingQuestionSetV2.model_validate(raw)
    except ValidationError as exc:
        return QuestionSetValidationResult(
            valid=False,
            question_set=None,
            errors=[str(err["msg"]) for err in exc.errors()],
        )

    unknown_language_errors: list[str] = []

    for question in question_set.main_questions:
        for language in question.allowed_languages:
            if normalize_language_name(language) is None:
                unknown_language_errors.append(
                    f"Question {question.question_id}: "
                    f"'{language}' in allowed_languages is not a "
                    "language JobPilot recognizes at all."
                )

    if unknown_language_errors:
        return QuestionSetValidationResult(
            valid=False,
            question_set=None,
            errors=unknown_language_errors,
        )

    return QuestionSetValidationResult(
        valid=True,
        question_set=question_set,
        errors=[],
    )


@dataclass(frozen=True)
class SQLQuestionSetValidationResult:

    valid: bool
    question_set: SQLQuestionSetV2 | None
    errors: list[str] = field(default_factory=list)


_FORBIDDEN_SETUP_KEYWORDS = ("ATTACH", "PRAGMA", "DETACH")


def validate_sql_question_set_v2(raw: dict) -> SQLQuestionSetValidationResult:
    """
    Deterministic validation for a Gemini-generated (or fallback)
    SQL question set. Structural rules (points sum to 100, unique
    question_ids) are enforced by the pydantic model itself; this
    adds the one SQL-specific safety check the schema cannot: a
    question's own setup_sql must never contain a statement that
    could reach outside its isolated in-memory fixture.
    """

    try:
        question_set = SQLQuestionSetV2.model_validate(raw)
    except ValidationError as exc:
        return SQLQuestionSetValidationResult(
            valid=False,
            question_set=None,
            errors=[str(err["msg"]) for err in exc.errors()],
        )

    unsafe_setup_errors: list[str] = []

    for question in question_set.questions:
        upper_sql = question.setup_sql.upper()
        for keyword in _FORBIDDEN_SETUP_KEYWORDS:
            if keyword in upper_sql:
                unsafe_setup_errors.append(
                    f"Question {question.question_id}: setup_sql contains "
                    f"'{keyword}', which is not permitted in a fixture."
                )

    if unsafe_setup_errors:
        return SQLQuestionSetValidationResult(
            valid=False,
            question_set=None,
            errors=unsafe_setup_errors,
        )

    return SQLQuestionSetValidationResult(
        valid=True,
        question_set=question_set,
        errors=[],
    )

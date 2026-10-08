import json
from typing import Optional

from backend.app.models.job import Job
from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.personalized_aptitude import (
    AptitudeCategory,
    AptitudeQuestion,
    AptitudeQuestionSet,
    DEFAULT_MARKS_PER_QUESTION,
    REQUIRED_OPTION_COUNT,
)
from backend.app.services.aptitude.question_bank import APTITUDE_QUESTION_BANK
from backend.app.services.assessment.gemini_client import (
    GeminiUnavailableError,
    generate_json_content,
)


# ============================================================
# JOBPILOT AI - PERSONALIZED APTITUDE QUESTION GENERATOR
# (Task 5C Step 3)
#
# STANDALONE. Does not replace or modify the existing, live
# backend/app/services/aptitude/ai_question_generator.py or its
# route wiring — nothing in the live assessment pipeline calls
# this module yet. The existing static
# services/aptitude/question_bank.py IS reused here as the
# deterministic fallback's question pool (never duplicated into
# a second bank).
#
# Aptitude questions remain individual, independently-answerable
# multiple-choice questions (Step 3's own rule: "APTITUDE MUST
# REMAIN APTITUDE") — this generator's only new behavior beyond
# the existing one is selecting REASONING categories relevant to
# the role/blueprint, never turning a job's technical skill
# requirement into an aptitude question. Job/candidate context
# only ever influences WHICH reasoning categories and WHAT
# difficulty are used — never the fundamental question shape.
# ============================================================

DEFAULT_QUESTION_COUNT = 10

# Maps the static question bank's own category strings (Task 5C
# Step 1's audit) onto the new AptitudeCategory vocabulary.
_BANK_CATEGORY_TO_APTITUDE_CATEGORY: dict[str, AptitudeCategory] = {
    "quantitative_aptitude": "quantitative",
    "logical_reasoning": "logical",
    "verbal_reasoning": "verbal",
    "analytical_ability": "analytical",
}

# The category vocabulary Gemini is constrained to select from —
# deliberately narrower than a technical-skill list (see module
# header).
APTITUDE_CATEGORY_VOCABULARY: list[AptitudeCategory] = [
    "quantitative", "logical", "verbal", "analytical",
    "data_interpretation", "technical_reasoning",
]


def _resolve_question_count(blueprint: AssessmentBlueprint) -> int:

    count = blueprint.total_questions or DEFAULT_QUESTION_COUNT

    return max(1, min(20, count))


def _select_categories(blueprint: AssessmentBlueprint) -> list[AptitudeCategory]:
    """
    Prefers the blueprint's own assessment_categories where they
    already name a reasoning category; falls back to a sensible
    default spread otherwise. Never selects a category derived
    from a candidate's or job's TECHNICAL skill list — aptitude
    categories are a fixed reasoning vocabulary, not a technology
    list (Step 3's own explicit rule).
    """

    candidates = [
        category for category in (blueprint.assessment_categories or [])
        if category in APTITUDE_CATEGORY_VOCABULARY
    ]

    if candidates:
        return candidates

    return ["quantitative", "logical", "verbal", "analytical"]


def _build_prompt(
    job: Job,
    blueprint: AssessmentBlueprint,
    categories: list[str],
    question_count: int,
) -> str:

    return f"""
Generate exactly {question_count} INDEPENDENT multiple-choice
aptitude questions for a candidate applying to this role. Each
question must be fully self-contained — never a multi-part
question, never a case study, never a role-specific scenario.

JOB CONTEXT (for realism/framing only — NEVER turn a technical
skill requirement into an aptitude question; aptitude tests
general reasoning ability, not job-specific technical knowledge)
Job title: {job.title}

CANDIDATE CONTEXT
Experience level: {blueprint.candidate_experience_level}

DIFFICULTY (blueprint is authoritative)
basic={blueprint.difficulty_distribution.basic}, \
intermediate={blueprint.difficulty_distribution.intermediate}, \
advanced={blueprint.difficulty_distribution.advanced}

ALLOWED CATEGORIES (use only these): {", ".join(categories)}

RULES
- Exactly {REQUIRED_OPTION_COUNT} answer options per question.
- correct_answer must be an EXACT copy of one option's text.
- Distribute difficulty roughly matching the basic/intermediate/
  advanced counts above.
- Do not repeat the same underlying question twice.

Return ONLY a JSON object shaped exactly like this (no other text):

{{
  "role_type": "{job.title}",
  "question_count": {question_count},
  "questions": [
    {{
      "question_id": 1,
      "category": "<one of the allowed categories>",
      "difficulty": "<basic|intermediate|advanced>",
      "question": "<question text>",
      "options": ["...", "...", "...", "..."],
      "correct_answer": "<exact copy of the correct option>",
      "explanation": "<one sentence>",
      "marks": {DEFAULT_MARKS_PER_QUESTION}
    }}
  ]
}}
""".strip()


def _build_deterministic_question_set(
    blueprint: AssessmentBlueprint,
    question_count: int,
) -> AptitudeQuestionSet:
    """
    Guaranteed-available fallback — reuses the existing static
    APTITUDE_QUESTION_BANK (10 fixed questions). If more questions
    are requested than the bank has unique entries, cycles through
    the bank again rather than failing — never returns an empty
    set (Task 5C's own explicit requirement, mirrored from Step 2).
    """

    warnings: list[str] = []

    bank_size = len(APTITUDE_QUESTION_BANK)

    if question_count > bank_size:

        warnings.append(
            f"Requested {question_count} questions but only "
            f"{bank_size} unique deterministic questions are "
            "available; some were reused."
        )

    dominant_difficulty = max(
        [
            ("basic", blueprint.difficulty_distribution.basic),
            ("intermediate", blueprint.difficulty_distribution.intermediate),
            ("advanced", blueprint.difficulty_distribution.advanced),
        ],
        key=lambda pair: pair[1],
    )[0]

    questions: list[AptitudeQuestion] = []

    for index in range(question_count):

        bank_entry = APTITUDE_QUESTION_BANK[index % bank_size]

        category = _BANK_CATEGORY_TO_APTITUDE_CATEGORY.get(
            bank_entry["category"], "quantitative"
        )

        questions.append(
            AptitudeQuestion(
                question_id=index + 1,
                category=category,
                difficulty=dominant_difficulty,
                question=bank_entry["question"],
                options=list(bank_entry["options"]),
                correct_answer=bank_entry["correct_answer"],
                explanation="",
                marks=DEFAULT_MARKS_PER_QUESTION,
            )
        )

    return AptitudeQuestionSet(
        role_type=blueprint.job_title or "Unspecified Role",
        question_count=question_count,
        questions=questions,
        generated_by="deterministic_fallback",
        warnings=warnings,
    )


def generate_personalized_aptitude_questions(
    job: Job,
    blueprint: AssessmentBlueprint,
    use_gemini: bool = True,
) -> AptitudeQuestionSet:
    """
    Builds a personalized, individual-question aptitude set.
    Tries Gemini first (constrained to the reasoning-category
    vocabulary and the blueprint's difficulty distribution); on
    ANY failure (unavailable, malformed JSON, wrong question
    count, invalid category/option shape) falls back to the
    deterministic bank, which never depends on Gemini and never
    returns an empty set.
    """

    question_count = _resolve_question_count(blueprint)
    categories = _select_categories(blueprint)

    if use_gemini:

        try:

            prompt = _build_prompt(job, blueprint, categories, question_count)

            raw_json_text = generate_json_content(prompt)

            question_set = AptitudeQuestionSet.model_validate_json(raw_json_text)

            if question_set.question_count != question_count:

                raise ValueError(
                    "Gemini returned a different question_count than "
                    "requested."
                )

            return question_set.model_copy(
                update={"generated_by": "gemini"}
            )

        except (GeminiUnavailableError, ValueError, json.JSONDecodeError) as exc:

            _ = exc  # intentionally not logged with prompt/candidate content

    return _build_deterministic_question_set(blueprint, question_count)

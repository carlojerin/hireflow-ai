from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ============================================================
# JOBPILOT AI - PERSONALIZED APTITUDE QUESTION SCHEMA
# (Task 5C Step 3)
#
# Aptitude questions are and remain NORMAL, INDIVIDUAL,
# independently-answerable multiple-choice questions — never
# sub-parts, never disguised role-specific/case-study
# structures (see Step 3's own explicit rule: "APTITUDE MUST
# REMAIN APTITUDE"). This schema formalizes the exact shape
# already produced by the existing backend/app/services/
# aptitude/ai_question_generator.py (category, difficulty,
# question, options[4], correct_answer, explanation) as a real
# Pydantic contract, adding only a stable question_id and a
# fixed per-question marks value for scoring.
#
# Nothing in the live assessment pipeline consumes this schema
# yet — see personalized_aptitude_generator.py's own docstring.
# ============================================================


# Reasoning/aptitude categories only — deliberately NOT a
# technical-skill vocabulary. A job requiring Python never turns
# into a "Python" aptitude category; Python is assessed by the
# separate programming/role-specific structure instead (Step 3's
# own explicit example).
AptitudeCategory = Literal[
    "quantitative",
    "logical",
    "verbal",
    "analytical",
    "data_interpretation",
    "technical_reasoning",
]

AptitudeDifficulty = Literal["basic", "intermediate", "advanced"]

REQUIRED_OPTION_COUNT = 4

# Fixed, uniform marks per aptitude question (Step 3's own
# scoring example: "10 questions x 2 marks = 20") — aptitude
# questions are never individually weighted differently from one
# another, unlike programming's per-question/per-sub-part
# weights.
DEFAULT_MARKS_PER_QUESTION = 2.0


class AptitudeQuestion(BaseModel):

    question_id: int = Field(ge=1)

    category: AptitudeCategory
    difficulty: AptitudeDifficulty

    question: str = Field(min_length=1, max_length=1000)

    options: list[str] = Field(
        min_length=REQUIRED_OPTION_COUNT,
        max_length=REQUIRED_OPTION_COUNT,
    )

    correct_answer: str = Field(min_length=1)

    # One-sentence reason the correct answer is correct — never
    # shown to the candidate before submission, matching the
    # existing generator's own convention.
    explanation: str = Field(default="", max_length=500)

    marks: float = Field(default=DEFAULT_MARKS_PER_QUESTION, gt=0)

    @model_validator(mode="after")
    def _correct_answer_matches_an_option(self) -> "AptitudeQuestion":

        if self.correct_answer not in self.options:

            raise ValueError(
                f"Question {self.question_id}: correct_answer does "
                "not exactly match one of its options."
            )

        return self


class AptitudeQuestionSet(BaseModel):

    role_type: str = Field(min_length=1, max_length=120)

    question_count: int = Field(ge=1, le=20)

    questions: list[AptitudeQuestion]

    generated_by: Literal["gemini", "deterministic_fallback"] = (
        "deterministic_fallback"
    )

    # Data-completeness notes only (e.g. "fewer unique questions
    # available than requested; some were reused") — never blocks
    # generation, mirrors AssessmentBlueprint's own warnings field.
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _question_count_matches_list(self) -> "AptitudeQuestionSet":

        if len(self.questions) != self.question_count:

            raise ValueError(
                f"question_count ({self.question_count}) does not "
                f"match the number of questions provided "
                f"({len(self.questions)})."
            )

        return self

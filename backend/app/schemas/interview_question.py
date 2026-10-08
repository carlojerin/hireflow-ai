from pydantic import BaseModel, Field, field_validator


# ============================================================
# JOBPILOT AI - AI INTERVIEW QUESTION SCHEMA (Task 4D)
#
# Strict output contract for both the Gemini-backed generator
# (services/assessment/interview_question_generator.py) and the
# deterministic fallback bank (interview_question_bank.py) —
# mirrors schemas/programming_question.py's role exactly.
#
# expected_answer_points and follow_up_focus are server-only —
# api/routes/interview_tests.py's sanitize_interview_question()
# is the single choke point that strips them before any
# candidate-facing response, mirroring
# sanitize_programming_question()'s hidden_tests stripping.
# ============================================================

VALID_DIFFICULTIES = {"easy", "medium", "hard"}

VALID_CATEGORIES = {
    "experience",
    "technical_skill",
    "job_scenario",
    "skill_gap",
    "behavioral",
}

REQUIRED_QUESTION_COUNT = 5


class InterviewQuestion(BaseModel):

    question_id: int

    question: str = Field(..., min_length=10, max_length=1000)

    category: str

    difficulty: str

    primary_skill: str = Field(..., max_length=100)

    # Candidate-safe — shown in the UI ("What it tests:").
    what_it_assesses: str = Field(..., max_length=300)

    # Server-only.
    expected_answer_points: list[str] = Field(
        ..., min_length=1, max_length=8
    )

    # Server-only — informational note on how a later question
    # in this same interview might adapt based on this answer.
    follow_up_focus: str = Field(default="", max_length=300)

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:

        normalized = value.strip().lower()

        if normalized not in VALID_CATEGORIES:
            raise ValueError(f"invalid category: {value!r}")

        return normalized

    @field_validator("difficulty")
    @classmethod
    def _check_difficulty(cls, value: str) -> str:

        normalized = value.strip().lower()

        if normalized not in VALID_DIFFICULTIES:
            raise ValueError(f"invalid difficulty: {value!r}")

        return normalized


class InterviewQuestionSet(BaseModel):

    questions: list[InterviewQuestion] = Field(
        ...,
        min_length=REQUIRED_QUESTION_COUNT,
        max_length=REQUIRED_QUESTION_COUNT,
    )

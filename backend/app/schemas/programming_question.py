from pydantic import BaseModel, Field, field_validator


# ============================================================
# JOBPILOT AI - PROGRAMMING QUESTION SCHEMA (Task 4C)
#
# Strict output contract for both the Gemini-backed generator
# (services/assessment/programming_question_generator.py) and
# the deterministic fallback bank (programming_question_bank.py)
# — both must produce data that validates against this schema
# before a ProgrammingTest row is ever created.
#
# hidden_tests is the one field that must NEVER reach a
# candidate-facing API response — every route in
# api/routes/programming_tests.py strips it (and
# evaluation_notes) before returning a question, mirroring how
# aptitude_test.py's sanitize_questions() strips correct_answer.
# ============================================================

VALID_DIFFICULTIES = {"easy", "medium", "hard"}

# Only Python execution is supported in this task (Part 6 of
# the spec explicitly scopes the sandbox to Python 3 only).
SUPPORTED_LANGUAGES = {"python"}

MIN_HIDDEN_TESTS = 3
MAX_HIDDEN_TESTS = 15
MIN_EXAMPLES = 1
MAX_EXAMPLES = 5
REQUIRED_QUESTION_COUNT = 3


class ProgrammingExample(BaseModel):

    input: str = Field(..., max_length=2000)
    output: str = Field(..., max_length=2000)
    explanation: str | None = Field(default=None, max_length=1000)


class HiddenTestCase(BaseModel):

    input: str = Field(..., max_length=4000)
    expected_output: str = Field(..., max_length=4000)


class ProgrammingQuestion(BaseModel):

    question_id: int

    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=4000)

    difficulty: str
    category: str = Field(..., max_length=100)
    primary_skill: str = Field(..., max_length=100)

    input_description: str = Field(..., max_length=1000)
    output_description: str = Field(..., max_length=1000)
    constraints: str = Field(default="", max_length=1000)

    examples: list[ProgrammingExample] = Field(
        ..., min_length=MIN_EXAMPLES, max_length=MAX_EXAMPLES
    )

    starter_code: str = Field(default="", max_length=3000)
    expected_language: str

    time_limit_seconds: int = Field(default=5, ge=1, le=15)
    memory_limit_mb: int = Field(default=256, ge=32, le=512)

    evaluation_notes: str = Field(default="", max_length=500)

    # Server-only. Never serialized into a candidate-facing
    # response — see sanitize_programming_question() in
    # api/routes/programming_tests.py.
    hidden_tests: list[HiddenTestCase] = Field(
        ..., min_length=MIN_HIDDEN_TESTS, max_length=MAX_HIDDEN_TESTS
    )

    @field_validator("difficulty")
    @classmethod
    def _check_difficulty(cls, value: str) -> str:

        normalized = value.strip().lower()

        if normalized not in VALID_DIFFICULTIES:
            raise ValueError(f"invalid difficulty: {value!r}")

        return normalized

    @field_validator("expected_language")
    @classmethod
    def _check_language(cls, value: str) -> str:

        normalized = value.strip().lower()

        if normalized not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {value!r}")

        return normalized


class ProgrammingQuestionSet(BaseModel):

    questions: list[ProgrammingQuestion] = Field(
        ...,
        min_length=REQUIRED_QUESTION_COUNT,
        max_length=REQUIRED_QUESTION_COUNT,
    )

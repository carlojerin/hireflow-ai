from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================================
# JOBPILOT AI - PERSONALIZED PROGRAMMING QUESTION SCHEMA
# (Task 5C Step 3)
#
# A DIFFERENT structure from aptitude, by design (Step 3's own
# explicit rule: "DO NOT MIX ASSESSMENT TYPES"). Programming is
# EXACTLY 5 main questions, each with 2-4 sub-parts, each
# sub-part independently weighted and independently scoreable
# for partial credit — never a single PASS/FAIL per question.
#
# Language-neutral by design: `technology` names what a
# question is ABOUT; `execution_supported` (always taken from
# technology_capability_registry.py, never invented here) says
# whether JobPilot can currently run candidate code for it. A
# question about Java is still generated and stored even though
# execution_supported=False for Java today — this project never
# fakes execution support or silently substitutes Python for a
# different required language (see technology_capability_
# registry.py's own docstring).
#
# Nothing in the live assessment pipeline consumes this schema
# yet — see personalized_programming_generator.py's own
# docstring. This schema also does not replace
# backend/app/schemas/programming_question.py, which the live,
# unchanged 3-question Python-only pipeline still uses.
# ============================================================


ProgrammingQuestionType = Literal[
    "coding",
    "algorithm",
    "data_structures",
    "debugging",
    "backend_scenario",
    "frontend_task",
    "database_task",
    "performance",
    "real_world_problem",
]

ProgrammingDifficulty = Literal["basic", "intermediate", "advanced"]

MIN_SUB_PARTS = 2
MAX_SUB_PARTS = 4
REQUIRED_MAIN_QUESTION_COUNT = 5


class BehavioralTestCase(BaseModel):
    """
    Describes WHAT behavior a sub-part is tested against and how
    many marks it is worth — never an actual runnable input/
    output pair tied to one specific language (that is a later,
    per-language-runner concern for whenever a real execution
    environment for that language exists). `is_hidden` mirrors
    the existing programming system's convention that hidden
    tests are never shown to the candidate.
    """

    description: str = Field(min_length=1, max_length=300)
    category: Literal["visible", "hidden", "edge", "performance"] = "hidden"
    weight: float = Field(gt=0)


class ProgrammingSubPart(BaseModel):

    part_id: str = Field(min_length=1, max_length=2)  # "A", "B", "C", "D"
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)

    weight: float = Field(gt=0)

    test_cases: list[BehavioralTestCase] = Field(min_length=1)

    # True when this sub-part's problem statement assumes the
    # candidate's solution to an earlier sub-part (Step 3's own
    # "parts may build on each other" rule) — informational only;
    # scoring still awards this sub-part's own weight independently
    # (a candidate who only solves Part A still gets Part A's
    # marks even if they never attempt Part B).
    depends_on_previous_part: bool = False

    @model_validator(mode="after")
    def _test_case_weights_sum_to_part_weight(self) -> "ProgrammingSubPart":

        total = round(sum(tc.weight for tc in self.test_cases), 2)

        if total != round(self.weight, 2):

            raise ValueError(
                f"Sub-part {self.part_id}: test case weights "
                f"({total}) must sum to the sub-part's own weight "
                f"({self.weight})."
            )

        return self


class ProgrammingMainQuestion(BaseModel):

    question_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=150)

    # Language-neutral problem statement — see module header.
    scenario: str = Field(min_length=1, max_length=4000)

    question_type: ProgrammingQuestionType

    # The specific technology this question is ABOUT (e.g.
    # "Python", "SQL", "React") — None only for a genuinely
    # language-agnostic question (rare; most real questions name
    # a technology).
    technology: Optional[str] = None

    # Always recomputed from technology_capability_registry.py by
    # the generator/finalizer — never trusted from Gemini, never
    # set by hand to True for a technology without a real runner.
    execution_supported: bool

    difficulty: ProgrammingDifficulty

    total_marks: float = Field(gt=0)

    sub_parts: list[ProgrammingSubPart] = Field(
        min_length=MIN_SUB_PARTS,
        max_length=MAX_SUB_PARTS,
    )

    @model_validator(mode="after")
    def _sub_part_weights_sum_to_total_marks(self) -> "ProgrammingMainQuestion":

        total = round(sum(part.weight for part in self.sub_parts), 2)

        if total != round(self.total_marks, 2):

            raise ValueError(
                f"Question {self.question_id}: sub-part weights "
                f"({total}) must sum to total_marks "
                f"({self.total_marks})."
            )

        return self


class ProgrammingQuestionSet(BaseModel):

    role_type: str = Field(min_length=1, max_length=120)

    main_questions: list[ProgrammingMainQuestion] = Field(
        min_length=REQUIRED_MAIN_QUESTION_COUNT,
        max_length=REQUIRED_MAIN_QUESTION_COUNT,
    )

    total_marks: float = Field(gt=0)

    generated_by: Literal["gemini", "deterministic_fallback"] = (
        "deterministic_fallback"
    )

    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _question_marks_sum_to_total(self) -> "ProgrammingQuestionSet":

        total = round(
            sum(question.total_marks for question in self.main_questions), 2
        )

        if total != round(self.total_marks, 2):

            raise ValueError(
                f"main_questions total_marks ({total}) do not sum "
                f"to total_marks ({self.total_marks})."
            )

        return self

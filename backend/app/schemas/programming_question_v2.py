from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================================
# JOBPILOT AI — PROGRAMMING ENGINE V2 (Task 5C, Programming
# Engine V2 task)
#
# LANGUAGE-NEUTRAL QUESTION CONTRACT.
#
# This is a NEW, standalone schema module living BESIDE the
# existing Step 3 `personalized_programming.py` — it does not
# replace, import from, or get imported by that module, and
# nothing in the live pipeline references this file yet (see the
# module list in the final report for this task). The existing
# Python-only programming assessment is completely untouched.
#
# THE CORE FIX THIS MODULE MAKES (the task's Step 1 blocker):
# personalized_programming.py's BehavioralTestCase has only
# {description, category, weight} — no concrete input/output data,
# so nothing could ever actually execute and score it. ExecutableTestCase
# below adds a genuinely language-neutral stdin/expected_stdout pair
# (the same concrete test data every language's runner is handed —
# see language_runner.py's ExecutionRequest, which already takes a
# plain `stdin: str`). No language-specific code, syntax, or
# input-parsing convention is baked into this model — "hello world\n"
# as stdin and "hello 2\nworld 1\n" as expected_stdout mean exactly
# the same thing whether the candidate submits Python, Java, or Go.
# ============================================================


ExecutionMode = Literal["console_coding", "frontend_coding", "sql"]

# Which execution modes actually have a real, tested execution path
# TODAY, versus which are merely recognized as a future architecture
# slot. Never treat a mode as usable just because it is a valid
# Literal value — see assessment_capability_registry.py's own
# three-state model, which this constant deliberately mirrors.
IMPLEMENTED_EXECUTION_MODES: frozenset[ExecutionMode] = frozenset({
    "console_coding", "sql",
})
# "frontend_coding" is declared (a recognized value candidates/UI
# code can reference) but has NO execution engine behind it yet —
# see frontend_execution.py's module docstring for why, and what
# building one for real would require. Referencing it anywhere in
# this codebase must always go through IMPLEMENTED_EXECUTION_MODES,
# never assume the Literal's mere existence means it works.


class ExecutableTestCase(BaseModel):
    """
    ONE concrete, language-neutral, executable test case.

    stdin / expected_stdout are the language-neutral contract: every
    LanguageRunner (see language_runner.py) already takes a plain
    stdin string and returns plain stdout — so a single test case
    here is directly executable against ANY language's runner
    without this schema ever encoding a language-specific input
    format, wire protocol, or serialization convention. A candidate
    solving "count word frequency" reads from stdin and writes to
    stdout in whatever way is idiomatic for their chosen language;
    only the RESULT (stdout) is compared, never the mechanism.

    `weight` is this test case's share of ITS OWN subpart's total
    weight — see ProgrammingSubpartV2 for the exact validation rule
    (all sibling test-case weights must sum to 1.0).
    """

    test_case_id: str = Field(min_length=1, max_length=50)

    stdin: str = Field(default="")
    expected_stdout: str = Field(min_length=1)

    weight: float = Field(gt=0, le=1)

    is_hidden: bool = Field(
        default=True,
        description=(
            "True for every genuinely hidden behavioral test case — "
            "the candidate NEVER sees stdin/expected_stdout for a "
            "hidden case (see programming_scoring_v2.py's explicit "
            "redaction rule). False only for the small number of "
            "test cases mirrored from the question's own public "
            "`examples` list, shown to the candidate as worked "
            "examples before they submit."
        ),
    )

    # Optional, human-only context — never read by the execution
    # engine, never shown to the candidate for a hidden case.
    description: Optional[str] = Field(default=None, max_length=300)


class ProgrammingSubpartV2(BaseModel):
    """
    One weighted subpart of a main question (2-4 per main question,
    per the existing Step 3 structure this task explicitly keeps).
    """

    subpart_id: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=2000)

    # This subpart's share of its PARENT main question's points.
    # All sibling subparts' weights must sum to 1.0 (validated by
    # ProgrammingMainQuestionV2 below, not here — a single subpart
    # cannot validate a property of its siblings).
    weight: float = Field(gt=0, le=1)

    test_cases: list[ExecutableTestCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _test_case_weights_sum_to_one(self) -> "ProgrammingSubpartV2":

        total = sum(tc.weight for tc in self.test_cases)

        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Subpart '{self.subpart_id}': test case weights "
                f"must sum to 1.0, got {total!r}."
            )

        return self


class ProgrammingMainQuestionV2(BaseModel):
    """
    One of the 5 main questions. `technology` is deliberately
    NEVER used to pick the candidate's implementation language —
    it records what the QUESTION is fundamentally about (e.g. "REST
    API design", "concurrency", "data structures"), which is a
    property of the problem itself, independent of which language
    ends up solving it. `allowed_languages` is the separate,
    explicit list of languages a candidate may implement THIS
    question in — see the module header's three-concept
    distinction (tested concept vs. job's required technology vs.
    candidate's chosen implementation language).
    """

    question_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)

    constraints: str = Field(default="", max_length=1000)
    examples: list[dict] = Field(default_factory=list)

    input_specification: str = Field(min_length=1, max_length=1000)
    output_specification: str = Field(min_length=1, max_length=1000)

    difficulty: Literal["easy", "medium", "hard"] = "medium"

    # ------------------------------------------------------------
    # V3 additive fields (Task 5G). ALL optional/defaulted so every
    # existing persisted ProgrammingAssessmentV2.question_set_json
    # row (written before this task) still deserializes unchanged,
    # and the deterministic fallback bank (which leaves these at
    # their defaults) remains fully valid. Gemini is asked to fill
    # these in for a real-world-scenario question; a candidate-safe
    # UI section is simply omitted when a field is empty.
    # ------------------------------------------------------------

    # The real-world framing this question is set in (e.g. "You are
    # building a rate limiter for a public API gateway.") — never
    # the solution, only the situation. Optional: a purely abstract
    # question (e.g. a fallback-bank one) leaves this unset.
    scenario: Optional[str] = Field(default=None, max_length=1500)

    # Explicit, itemized functional requirements — rendered as a
    # bullet list in the candidate UI, distinct from the narrative
    # `description`.
    requirements: list[str] = Field(default_factory=list, max_length=12)

    # The concrete skills this question provides evidence for —
    # e.g. ["Java", "concurrency", "error handling"]. Feeds the
    # evidence/intelligence layer (programming_assessment_evidence_
    # v2.py); falls back to `technology` there when empty.
    skills_assessed: list[str] = Field(default_factory=list, max_length=8)

    # Free-text complexity guidance where meaningful (e.g. "O(n)
    # time, O(1) extra space") — advisory only, never enforced by
    # the scorer.
    expected_complexity: Optional[str] = Field(default=None, max_length=200)

    # Any additional candidate-facing clarification that doesn't
    # fit the other structured fields (e.g. an I/O edge-case note).
    notes: Optional[str] = Field(default=None, max_length=800)

    # The concept/skill this question is fundamentally testing —
    # NEVER a source of truth for which language must be used.
    technology: Optional[str] = Field(default=None, max_length=100)

    # The job's own required technology/environment, when the job
    # genuinely constrains this — e.g. a Java/Spring Boot job may
    # require the CANDIDATE to demonstrate Java specifically for
    # this question. None means "no job-level language constraint
    # beyond the assessment's own allowed_languages list".
    required_technology: Optional[str] = Field(default=None, max_length=100)

    # Which languages a candidate may pick for THIS question.
    # Server-side enforced — see language_selection.py's
    # validate_language_selection(). Never empty.
    allowed_languages: list[str] = Field(min_length=1)

    execution_mode: ExecutionMode = "console_coding"

    # This question's absolute point value within the assessment
    # (the 5 main questions' points must sum to 100 — validated at
    # the ProgrammingQuestionSetV2 level, not here).
    points: float = Field(gt=0, le=100)

    subparts: list[ProgrammingSubpartV2] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def _subpart_weights_sum_to_one(self) -> "ProgrammingMainQuestionV2":

        total = sum(sp.weight for sp in self.subparts)

        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Question {self.question_id} ('{self.title}'): "
                f"subpart weights must sum to 1.0, got {total!r}."
            )

        return self

    @model_validator(mode="after")
    def _allowed_languages_include_required_technology(self) -> "ProgrammingMainQuestionV2":

        if self.required_technology is None:
            return self

        normalized_required = self.required_technology.strip().lower()
        normalized_allowed = {lang.strip().lower() for lang in self.allowed_languages}

        if normalized_required not in normalized_allowed:
            raise ValueError(
                f"Question {self.question_id}: required_technology "
                f"'{self.required_technology}' is not present in "
                f"allowed_languages {self.allowed_languages!r}. A "
                "job-mandated language must always be one of the "
                "languages the candidate is actually allowed to pick."
            )

        return self


class ProgrammingQuestionSetV2(BaseModel):
    """
    The full language-neutral assessment definition for one
    candidate/application — exactly 5 main questions (the existing
    Step 3 structure), points summing to 100.
    """

    main_questions: list[ProgrammingMainQuestionV2] = Field(
        min_length=5, max_length=5,
    )

    # Where the questions came from. "gemini"/"fallback" are the legacy values already stored on old rows; new
    # assessments use the accurate four-tier vocabulary:
    #   ai_generated | validated_pool | role_aware_fallback | generic_fallback
    generated_by: Literal[
        "gemini", "fallback", "ai_generated", "validated_pool", "role_aware_fallback", "generic_fallback",
    ] = "fallback"

    # Additive, optional: provider/model, per-question sources, language ranking and the validation report.
    generation_metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _points_sum_to_100(self) -> "ProgrammingQuestionSetV2":

        total = sum(q.points for q in self.main_questions)

        if abs(total - 100.0) > 1e-6:
            raise ValueError(
                f"Main question points must sum to 100, got {total!r}."
            )

        return self

    @model_validator(mode="after")
    def _question_ids_are_unique(self) -> "ProgrammingQuestionSetV2":

        ids = [q.question_id for q in self.main_questions]

        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate question_id values in {ids!r}.")

        return self

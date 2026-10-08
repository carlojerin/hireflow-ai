import json
from typing import Optional

from backend.app.models.job import Job
from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.assessment_plan import AssessmentPlan, AssessmentPlanItem
from backend.app.schemas.personalized_programming import (
    BehavioralTestCase,
    MAX_SUB_PARTS,
    MIN_SUB_PARTS,
    ProgrammingMainQuestion,
    ProgrammingQuestionSet,
    ProgrammingQuestionType,
    ProgrammingSubPart,
    REQUIRED_MAIN_QUESTION_COUNT,
)
from backend.app.services.assessment.gemini_client import (
    GeminiUnavailableError,
    generate_json_content,
)
from backend.app.services.assessment.technology_capability_registry import (
    is_execution_supported,
)


# ============================================================
# JOBPILOT AI - PERSONALIZED PROGRAMMING QUESTION GENERATOR
# (Task 5C Step 3)
#
# STANDALONE. Consumes the Step 2 AssessmentPlan (technology/
# competency selection) + AssessmentBlueprint (difficulty/skill
# evidence) + job/candidate context to produce EXACTLY 5 main
# questions, each with 2-4 independently-weighted sub-parts (see
# backend/app/schemas/personalized_programming.py). Does NOT
# replace or modify the existing, live 3-fixed-question Python-
# only backend/app/services/assessment/
# programming_question_generator.py — nothing in the live
# pipeline calls this module yet.
#
# TECHNOLOGY SELECTION comes entirely from the AssessmentPlan
# (which itself is job-driven, per Step 2) — this module never
# re-derives technology from a job or candidate directly, and
# never invents execution support: every question's
# execution_supported is recomputed from
# technology_capability_registry.py, so a Java/SQL/React
# question is still generated with a real problem statement, but
# is honestly marked as not-yet-executable rather than silently
# becoming a Python question.
# ============================================================

TOTAL_MARKS_PER_QUESTION = 20.0
TOTAL_MARKS = TOTAL_MARKS_PER_QUESTION * REQUIRED_MAIN_QUESTION_COUNT

TECHNICAL_ASSESSMENT_TYPES = {"programming", "sql", "frontend", "backend"}

# assessment_type -> a fixed question_type override. "programming"
# has no fixed override — it cycles through
# _PROGRAMMING_TYPE_SEQUENCE below for variety across the 5 slots,
# matching Step 3's own example (coding / API logic / algorithm /
# debugging / real-world problem).
_ASSESSMENT_TYPE_QUESTION_TYPE_OVERRIDE: dict[str, ProgrammingQuestionType] = {
    "sql": "database_task",
    "frontend": "frontend_task",
    "backend": "backend_scenario",
}

_PROGRAMMING_TYPE_SEQUENCE: list[ProgrammingQuestionType] = [
    "coding", "algorithm", "data_structures", "debugging", "real_world_problem",
]

# Slot-position labels/scenario templates. Each has a distinctly
# DIFFERENT problem for "fresher" vs "experienced" — not merely
# reworded text (Step 3's own explicit requirement: "the
# underlying problem complexity must actually change").
_SCENARIO_TEMPLATES: list[dict] = [
    {
        "title": "Data Processing",
        "fresher": (
            "Given a list of numeric readings, implement logic to "
            "compute their sum, average, and maximum value."
        ),
        "experienced": (
            "Given a large, continuous stream of transaction "
            "records (potentially millions of entries), implement "
            "logic to aggregate totals per account while detecting "
            "and safely skipping malformed records."
        ),
    },
    {
        "title": "API / Backend Logic",
        "fresher": (
            "Implement the logic for an endpoint that accepts a "
            "single record and returns a validated, structured "
            "response."
        ),
        "experienced": (
            "Implement the logic for an endpoint that accepts a "
            "batch of records, validates each independently, "
            "applies business rules that vary per record type, "
            "and returns a structured per-record result set."
        ),
    },
    {
        "title": "Algorithm",
        "fresher": (
            "Given a list of integers, implement logic to find the "
            "two elements that sum closest to a given target value."
        ),
        "experienced": (
            "Given a large list of integers, implement an "
            "efficient algorithm to find all unique pairs summing "
            "to a target value, without using more than linear "
            "extra memory."
        ),
    },
    {
        "title": "Debugging",
        "fresher": (
            "A short function that is supposed to remove duplicate "
            "values from a list contains a logic error. Identify "
            "and correct it."
        ),
        "experienced": (
            "A data-aggregation function produces incorrect totals "
            "only when the input contains concurrent updates or "
            "out-of-order records. Identify the root cause and "
            "correct the implementation."
        ),
    },
    {
        "title": "Real-World Problem",
        "fresher": (
            "Given a small list of orders, implement logic to "
            "group them by customer and compute each customer's "
            "total spend."
        ),
        "experienced": (
            "Given a high-volume order feed for an e-commerce "
            "platform, implement logic to detect duplicate orders, "
            "group valid orders by customer, compute total spend "
            "per customer, and handle records that arrive with "
            "missing or inconsistent fields."
        ),
    },
]


def _is_experienced(blueprint: AssessmentBlueprint) -> bool:

    level = (blueprint.candidate_experience_level or "").strip().lower()

    return level in ("mid", "experienced", "senior", "intermediate")


def _dominant_difficulty(blueprint: AssessmentBlueprint) -> str:

    dist = blueprint.difficulty_distribution

    return max(
        [("basic", dist.basic), ("intermediate", dist.intermediate), ("advanced", dist.advanced)],
        key=lambda pair: pair[1],
    )[0]


def _select_technology_slots(plan: Optional[AssessmentPlan]) -> list[dict]:
    """
    Returns exactly REQUIRED_MAIN_QUESTION_COUNT slots, each
    {assessment_type, technology, difficulty}, drawn from the
    AssessmentPlan's technical items (cycling if fewer than 5
    exist). If no plan is given, or the plan has no technical
    items at all (e.g. an HR/finance/marketing role whose plan
    never selected programming/sql/frontend/backend), returns 5
    generic, technology-agnostic "programming"/algorithm-style
    slots instead — this generator always produces a complete,
    valid structure, but a caller integrating this into the live
    pipeline later would simply never invoke it for a role the
    plan marked as non-technical (Step 3 stays out of that
    routing decision by design).
    """

    technical_items: list[AssessmentPlanItem] = []

    if plan:

        technical_items = [
            item for item in plan.assessment_types
            if item.assessment_type in TECHNICAL_ASSESSMENT_TYPES
        ]

    if not technical_items:

        return [
            {"assessment_type": "programming", "technology": None, "difficulty": "intermediate"}
            for _ in range(REQUIRED_MAIN_QUESTION_COUNT)
        ]

    slots = []

    for index in range(REQUIRED_MAIN_QUESTION_COUNT):

        item = technical_items[index % len(technical_items)]

        slots.append({
            "assessment_type": item.assessment_type,
            "technology": item.technology,
            "difficulty": item.difficulty,
        })

    return slots


def _question_type_for_slot(slot: dict, slot_index: int) -> ProgrammingQuestionType:

    override = _ASSESSMENT_TYPE_QUESTION_TYPE_OVERRIDE.get(slot["assessment_type"])

    if override:
        return override

    return _PROGRAMMING_TYPE_SEQUENCE[slot_index % len(_PROGRAMMING_TYPE_SEQUENCE)]


def _sub_part_count_for(difficulty: str, question_type: str) -> int:
    """
    Decided from difficulty + question type (Step 3's own rule:
    "the engine should decide 2, 3, or 4 based on difficulty,
    experience, job requirements, skill priority, question
    type" — experience/job/skill-priority already fed into
    `difficulty` upstream via the blueprint/plan, so this
    function only needs difficulty + question_type directly).
    """

    base = {"basic": 2, "intermediate": 3, "advanced": 4}.get(difficulty, 3)

    if question_type == "real_world_problem":
        base = max(base, 3)

    return max(MIN_SUB_PARTS, min(MAX_SUB_PARTS, base))


def _distribute_marks(total_marks: float, part_count: int) -> list[float]:
    """
    Increasing weights toward later parts (later parts are
    conventionally the harder edge-case/optimization parts) —
    always sums EXACTLY to total_marks (the schema's validator
    requires this).
    """

    raw_weights = [index + 2 for index in range(part_count)]
    raw_total = sum(raw_weights)

    marks = [round(total_marks * weight / raw_total, 2) for weight in raw_weights[:-1]]

    marks.append(round(total_marks - sum(marks), 2))

    return marks


def _build_test_cases(part_weight: float, part_index: int, part_count: int) -> list[BehavioralTestCase]:
    """
    Splits one sub-part's weight across 2 behavioral test
    categories, weighted differently depending on the part's
    position (first part = mostly basic correctness; middle
    parts = hidden + edge; last part = edge + performance) —
    always sums EXACTLY to part_weight.
    """

    if part_index == 0:
        splits = [("visible", 0.5, "Basic correctness on a typical input"),
                  ("hidden", 0.5, "Basic correctness on an additional hidden input")]
    elif part_index == part_count - 1:
        splits = [("edge", 0.5, "Edge-case input (empty/boundary values)"),
                  ("performance", 0.5, "Large-input performance check")]
    else:
        splits = [("hidden", 0.6, "Hidden correctness check"),
                  ("edge", 0.4, "Edge-case input")]

    weights = [round(part_weight * fraction, 2) for _category, fraction, _desc in splits[:-1]]
    weights.append(round(part_weight - sum(weights), 2))

    return [
        BehavioralTestCase(description=description, category=category, weight=max(weight, 0.01))
        for (category, _fraction, description), weight in zip(splits, weights)
    ]


def _build_sub_parts(
    scenario_key: str,
    part_count: int,
    total_marks: float,
) -> list[ProgrammingSubPart]:

    part_labels = ["A", "B", "C", "D"][:part_count]
    marks = _distribute_marks(total_marks, part_count)

    generic_descriptions_by_position = {
        0: "Implement the basic solution described in the scenario.",
        1: "Handle an important edge case (e.g. empty input, duplicate values, or invalid records).",
        2: "Handle an additional edge case or validation requirement.",
        3: "Optimize the solution for large input volumes or improve error handling under load.",
    }

    sub_parts = []

    for index, part_id in enumerate(part_labels):

        sub_parts.append(
            ProgrammingSubPart(
                part_id=part_id,
                label=f"Part {part_id}",
                description=generic_descriptions_by_position.get(
                    index, "Extend the solution further."
                ),
                weight=marks[index],
                test_cases=_build_test_cases(marks[index], index, part_count),
                depends_on_previous_part=index > 0,
            )
        )

    return sub_parts


def _build_deterministic_question_set(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: Optional[AssessmentPlan],
) -> ProgrammingQuestionSet:
    """
    Guaranteed-available fallback. Never depends on Gemini, never
    returns fewer than 5 questions. Scenario text genuinely
    differs between a fresher and an experienced candidate (not
    just reworded — see _SCENARIO_TEMPLATES), per Step 3's own
    explicit requirement.
    """

    slots = _select_technology_slots(plan)
    experienced = _is_experienced(blueprint)

    main_questions = []

    for index, slot in enumerate(slots):

        template = _SCENARIO_TEMPLATES[index % len(_SCENARIO_TEMPLATES)]
        scenario_text = template["experienced"] if experienced else template["fresher"]

        question_type = _question_type_for_slot(slot, index)
        difficulty = slot["difficulty"]
        part_count = _sub_part_count_for(difficulty, question_type)

        technology = slot["technology"]

        title = (
            f"{template['title']} ({technology})" if technology
            else template["title"]
        )

        main_questions.append(
            ProgrammingMainQuestion(
                question_id=index + 1,
                title=title,
                scenario=scenario_text,
                question_type=question_type,
                technology=technology,
                execution_supported=is_execution_supported(technology),
                difficulty=difficulty,
                total_marks=TOTAL_MARKS_PER_QUESTION,
                sub_parts=_build_sub_parts(template["title"], part_count, TOTAL_MARKS_PER_QUESTION),
            )
        )

    return ProgrammingQuestionSet(
        role_type=job.title or "Unspecified Role",
        main_questions=main_questions,
        total_marks=TOTAL_MARKS,
        generated_by="deterministic_fallback",
    )


def _build_gemini_prompt(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: Optional[AssessmentPlan],
) -> str:

    slots = _select_technology_slots(plan)

    slot_lines = "\n".join(
        f"- Question {i + 1}: technology={slot['technology'] or 'general'}, "
        f"difficulty={slot['difficulty']}"
        for i, slot in enumerate(slots)
    )

    return f"""
Generate EXACTLY {REQUIRED_MAIN_QUESTION_COUNT} main technical
assessment questions for this job/candidate. Each main question
must have between {MIN_SUB_PARTS} and {MAX_SUB_PARTS} sub-parts,
each independently weighted, and all sub-part weights within a
question must sum EXACTLY to that question's total_marks
(each question is worth {TOTAL_MARKS_PER_QUESTION} marks).

JOB
Title: {job.title}
Required skills: {getattr(job, "required_skills", "") or "(not specified)"}

CANDIDATE
Experience level: {blueprint.candidate_experience_level}

REQUIRED TECHNOLOGY/DIFFICULTY PER QUESTION (in order):
{slot_lines}

RULES
- Problem statements must be language-neutral where the
  technology allows it — describe WHAT to implement, not
  language-specific syntax.
- The underlying problem complexity must genuinely differ for an
  experienced candidate vs a fresher — not just wording.
- Do not invent test case input/output pairs tied to one
  language; describe test cases as behavioral checks
  (category: visible|hidden|edge|performance) with a weight.
- question_type must be one of: coding, algorithm,
  data_structures, debugging, backend_scenario, frontend_task,
  database_task, performance, real_world_problem.
- Never claim a technology can be executed — execution_supported
  will be overridden server-side regardless of what you return.

Return ONLY a JSON object shaped exactly like this (no other text):

{{
  "role_type": "{job.title}",
  "total_marks": {TOTAL_MARKS},
  "main_questions": [
    {{
      "question_id": 1,
      "title": "...",
      "scenario": "...",
      "question_type": "...",
      "technology": "...",
      "execution_supported": false,
      "difficulty": "basic|intermediate|advanced",
      "total_marks": {TOTAL_MARKS_PER_QUESTION},
      "sub_parts": [
        {{
          "part_id": "A",
          "label": "Part A",
          "description": "...",
          "weight": 8,
          "depends_on_previous_part": false,
          "test_cases": [
            {{"description": "...", "category": "visible", "weight": 4}},
            {{"description": "...", "category": "hidden", "weight": 4}}
          ]
        }}
      ]
    }}
  ]
}}
""".strip()


def generate_personalized_programming_questions(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: Optional[AssessmentPlan] = None,
    use_gemini: bool = True,
) -> ProgrammingQuestionSet:
    """
    Builds a personalized, 5-main-question x 2-4-sub-part
    programming/technical question set. Tries Gemini first
    (constrained to the exact schema shape via Pydantic
    validation); on ANY failure falls back to the deterministic
    generator, which never depends on Gemini and always produces
    a complete, valid 5-question structure.

    execution_supported is ALWAYS recomputed server-side from
    technology_capability_registry.py for every question,
    regardless of which path produced it — never trusted from
    Gemini, and never faked for a technology without a real
    runner.
    """

    if use_gemini:

        try:

            prompt = _build_gemini_prompt(job, blueprint, plan)

            raw_json_text = generate_json_content(prompt)

            question_set = ProgrammingQuestionSet.model_validate_json(raw_json_text)

            finalized_questions = [
                question.model_copy(
                    update={"execution_supported": is_execution_supported(question.technology)}
                )
                for question in question_set.main_questions
            ]

            return question_set.model_copy(
                update={
                    "main_questions": finalized_questions,
                    "generated_by": "gemini",
                }
            )

        except (GeminiUnavailableError, ValueError, json.JSONDecodeError) as exc:

            _ = exc  # intentionally not logged with prompt/candidate content

    return _build_deterministic_question_set(job, blueprint, plan)

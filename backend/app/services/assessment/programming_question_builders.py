from collections import OrderedDict

from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingSubpartV2,
)

# ============================================================
# JOBPILOT AI - PROGRAMMING QUESTION BUILDERS (shared)
#
# Small helpers every role generator (coding / SQL / web) uses to turn a list of
# concrete test cases into the weighted subparts ProgrammingMainQuestionV2 requires.
# Nothing here generates content or scores anything.
# ============================================================

QUESTIONS_PER_ASSESSMENT = 5
POINTS_PER_QUESTION = 20.0


GROUP_DESCRIPTIONS = {
    "typical": "Typical inputs.",
    "edge": "Edge cases.",
    "stress": "Larger inputs and performance.",
}


def _split_groups(cases: list[dict]) -> list[tuple[str, list[dict]]]:
    """Groups by each case's own "group" label (typical/edge/stress) when there are 2-4 of them, else two halves."""

    labelled = OrderedDict()

    for case in cases:
        labelled.setdefault(case.get("group") or "", []).append(case)

    if "" not in labelled and 2 <= len(labelled) <= 4:
        return [(GROUP_DESCRIPTIONS.get(name, name.capitalize() + "."), group) for name, group in labelled.items()]

    split = (len(cases) + 1) // 2
    return [("Typical inputs.", cases[:split]), ("Edge cases and harder inputs.", cases[split:])]


def build_subparts(cases: list[dict]) -> list[ProgrammingSubpartV2]:
    """
    `cases` is a list of {stdin, expected_stdout, is_hidden, description, group?}. Cases with a `group` label
    (typical / edge / stress) become one subpart per label (2-4 subparts); otherwise they are split in two halves.
    Subparts weigh the same; cases weigh the same inside a subpart; every sum is exactly 1.0.
    """

    if len(cases) < 2:
        raise ValueError("a question needs at least two test cases")

    groups = _split_groups(cases)

    subpart_weight = round(1.0 / len(groups), 6)
    subpart_weights = [subpart_weight] * len(groups)
    subpart_weights[-1] = round(1.0 - sum(subpart_weights[:-1]), 6)

    subparts = []
    counter = 0

    for index, ((description, group), sp_weight) in enumerate(zip(groups, subpart_weights), start=1):

        weight = round(1.0 / len(group), 6)
        weights = [weight] * len(group)
        weights[-1] = round(1.0 - sum(weights[:-1]), 6)                   # exact sum of 1.0

        test_cases = []

        for case, case_weight in zip(group, weights):
            counter += 1
            test_cases.append(ExecutableTestCase(
                test_case_id=f"tc{counter}",
                stdin=case["stdin"],
                expected_stdout=case["expected_stdout"],
                weight=case_weight,
                is_hidden=bool(case.get("is_hidden", True)),
                description=case.get("description"),
            ))

        subparts.append(ProgrammingSubpartV2(
            subpart_id=f"sp{index}", description=description, weight=sp_weight, test_cases=test_cases,
        ))

    return subparts


def build_question(
    *,
    title: str,
    description: str,
    input_specification: str,
    output_specification: str,
    difficulty: str,
    allowed_languages: list[str],
    cases: list[dict],
    examples: list[dict] | None = None,
    constraints: str = "",
    scenario: str | None = None,
    requirements: list[str] | None = None,
    skills_assessed: list[str] | None = None,
    technology: str | None = None,
    execution_mode: str = "console_coding",
    notes: str | None = None,
    expected_complexity: str | None = None,
) -> ProgrammingMainQuestionV2:
    """One main question; question_id and points are assigned later by assemble_question_set()."""

    return ProgrammingMainQuestionV2(
        question_id=1,
        title=title[:200],
        description=description[:5000],
        constraints=constraints[:1000],
        examples=examples or [],
        input_specification=input_specification[:1000],
        output_specification=output_specification[:1000],
        difficulty=difficulty if difficulty in ("easy", "medium", "hard") else "medium",
        scenario=scenario[:1500] if scenario else None,
        requirements=(requirements or [])[:12],
        skills_assessed=(skills_assessed or [])[:8],
        expected_complexity=expected_complexity,
        notes=notes[:800] if notes else None,
        technology=technology,
        allowed_languages=allowed_languages,
        execution_mode=execution_mode,
        points=POINTS_PER_QUESTION,
        subparts=build_subparts(cases),
    )


def assemble_question_set(questions: list[ProgrammingMainQuestionV2], generated_by: str, metadata: dict | None = None):
    """Numbers the questions 1..5 (20 points each) and wraps them into a ProgrammingQuestionSetV2."""

    from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2

    if len(questions) != QUESTIONS_PER_ASSESSMENT:
        raise ValueError(f"expected {QUESTIONS_PER_ASSESSMENT} questions, got {len(questions)}")

    numbered = []

    for number, question in enumerate(questions, start=1):
        data = question.model_dump()
        data["question_id"] = number
        data["points"] = POINTS_PER_QUESTION
        numbered.append(ProgrammingMainQuestionV2(**data))

    return ProgrammingQuestionSetV2(main_questions=numbered, generated_by=generated_by, generation_metadata=metadata or {})

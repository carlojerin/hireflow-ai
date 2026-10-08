from backend.app.schemas.personalized_aptitude import AptitudeQuestionSet
from backend.app.schemas.personalized_programming import (
    ProgrammingMainQuestion,
    ProgrammingQuestionSet,
    ProgrammingSubPart,
)


# ============================================================
# JOBPILOT AI - PERSONALIZED ASSESSMENT SCORING (Task 5C Step 3)
#
# Pure, deterministic scoring functions — no database access, no
# code execution. These describe HOW a set of test-case results
# (once a real execution/grading step exists — a later, separate
# concern) would be converted into marks. Nothing here is wired
# into the live pipeline.
#
# APTITUDE: unchanged simple model — each question is either
# fully correct or not (Step 3's own rule: "Wrong = 0 unless the
# company assessment policy explicitly enables negative
# marking" — negative marking is never enabled by default here).
#
# PROGRAMMING: weighted, partial-credit scoring at THREE levels
# (main question -> sub-part -> individual test case) — never a
# single PASS/FAIL per question (Step 3's own explicit rule).
# ============================================================


def score_aptitude_set(
    question_set: AptitudeQuestionSet,
    candidate_answers: dict[int, str],
    negative_marking_enabled: bool = False,
    negative_marks_per_wrong: float = 0.0,
) -> dict:
    """
    candidate_answers: {question_id: selected_option_text}.
    Negative marking is OFF by default and must be explicitly
    opted into by the caller — this function never enables it on
    its own.
    """

    breakdown = []
    total_earned = 0.0
    total_max = 0.0

    for question in question_set.questions:

        total_max += question.marks

        selected = candidate_answers.get(question.question_id)
        is_correct = selected is not None and selected == question.correct_answer

        if is_correct:
            earned = question.marks
        elif negative_marking_enabled:
            earned = -abs(negative_marks_per_wrong)
        else:
            earned = 0.0

        total_earned += earned

        breakdown.append({
            "question_id": question.question_id,
            "correct": is_correct,
            "earned": round(earned, 2),
            "max": question.marks,
        })

    return {
        "earned": round(total_earned, 2),
        "max": round(total_max, 2),
        "questions": breakdown,
    }


def score_sub_part(
    sub_part: ProgrammingSubPart,
    passed_test_case_indices: set[int],
) -> float:
    """
    Sums the weight of every test case whose index is in
    `passed_test_case_indices` — each test case contributes its
    own weight independently, so a sub-part that passes 2 of its
    3 test cases earns exactly those 2 test cases' combined
    weight, never all-or-nothing (Step 3's own explicit rule).
    """

    earned = sum(
        test_case.weight
        for index, test_case in enumerate(sub_part.test_cases)
        if index in passed_test_case_indices
    )

    return round(min(earned, sub_part.weight), 2)


def score_main_question(
    question: ProgrammingMainQuestion,
    sub_part_passed_indices: dict[str, set[int]],
) -> dict:
    """
    sub_part_passed_indices: {part_id: {passed test case indices}}.
    A candidate who only attempts Part A still receives Part A's
    earned marks even if every other part is entirely unattempted
    (an empty/missing entry for a part_id scores that part 0,
    never negatively, and never blocks scoring the parts that
    WERE attempted).
    """

    breakdown = {}
    total_earned = 0.0

    for sub_part in question.sub_parts:

        passed_indices = sub_part_passed_indices.get(sub_part.part_id, set())
        earned = score_sub_part(sub_part, passed_indices)

        breakdown[sub_part.part_id] = {
            "earned": earned,
            "max": sub_part.weight,
        }

        total_earned += earned

    return {
        "question_id": question.question_id,
        "earned": round(total_earned, 2),
        "max": question.total_marks,
        "sub_parts": breakdown,
    }


def score_programming_set(
    question_set: ProgrammingQuestionSet,
    results: dict[int, dict[str, set[int]]],
) -> dict:
    """
    results: {question_id: {part_id: {passed test case indices}}}.
    A question_id missing from `results` entirely is scored as
    fully unattempted (0 marks for every sub-part) rather than
    raising — a candidate who never opened a question must still
    receive a defined, zero score for it, not an error.
    """

    breakdown = []
    total_earned = 0.0

    for question in question_set.main_questions:

        question_result = score_main_question(
            question, results.get(question.question_id, {})
        )

        breakdown.append(question_result)
        total_earned += question_result["earned"]

    return {
        "earned": round(total_earned, 2),
        "max": question_set.total_marks,
        "questions": breakdown,
    }

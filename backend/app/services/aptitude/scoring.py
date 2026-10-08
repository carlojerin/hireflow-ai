# ============================================================
# JOBPILOT AI - APTITUDE TEST SCORING
#
# Pure, backend-only scoring logic. The frontend never
# supplies a score — only raw selected answers, which this
# module grades against the stored correct answers.
#
# This threshold is intentionally independent from
# MINIMUM_AI_MATCH_TO_APPLY (backend/app/api/routes/
# applications.py) and AI_SHORTLIST_THRESHOLD
# (backend/app/services/ai_candidate_matching.py). All three
# are separate 70%-style business rules.
# ============================================================

APTITUDE_PASS_THRESHOLD = 70.0


def score_aptitude_test(
    questions: list[dict],
    submitted_answers: list[dict],
) -> dict:
    """
    Grade a submitted aptitude attempt.

    questions: the test's own stored question snapshot,
        each with "id" and "correct_answer".

    submitted_answers: candidate-submitted list of
        {"question_id": int, "selected_answer": str}.

    Returns a dict with:
        correct_count, total_questions, percentage, passed
    """

    correct_answer_by_question_id = {
        question["id"]: question["correct_answer"]
        for question in questions
    }

    selected_answer_by_question_id = {}

    for answer in submitted_answers:

        question_id = answer.get("question_id")

        if question_id is None:
            continue

        selected_answer_by_question_id[question_id] = (
            answer.get("selected_answer")
        )

    correct_count = 0

    for question_id, correct_answer in (
        correct_answer_by_question_id.items()
    ):

        selected_answer = (
            selected_answer_by_question_id.get(
                question_id
            )
        )

        if (
            selected_answer is not None
            and str(selected_answer).strip()
            == str(correct_answer).strip()
        ):

            correct_count += 1

    total_questions = len(
        correct_answer_by_question_id
    )

    percentage = (
        round(
            (correct_count / total_questions) * 100,
            2,
        )
        if total_questions > 0
        else 0.0
    )

    passed = percentage >= APTITUDE_PASS_THRESHOLD

    return {
        "correct_count": correct_count,
        "total_questions": total_questions,
        "percentage": percentage,
        "passed": passed,
    }

from backend.app.schemas.interview_evaluation import InterviewAnswerEvaluation


# ============================================================
# JOBPILOT AI - AI INTERVIEW SCORING (Task 4D)
#
# Deterministic, backend-only scoring — mirrors services/
# aptitude/scoring.py's and services/assessment/
# programming_scoring.py's role exactly. The frontend never
# supplies or computes a score; it only ever displays what this
# module produces.
#
# Weights and the pass threshold are centralized here, in
# exactly one place, per Part 11 of the task spec.
# ============================================================

INTERVIEW_SCORE_WEIGHTS = {
    "technical_correctness": 0.30,
    "understanding": 0.20,
    "reasoning": 0.20,
    "relevance": 0.20,
    "communication": 0.10,
}

# Independent, same-value business-rule constant by design —
# matches this project's own established convention (see
# APTITUDE_PASS_THRESHOLD, PROGRAMMING_PASS_THRESHOLD).
INTERVIEW_PASS_THRESHOLD = 70.0


def compute_question_score(evaluation: InterviewAnswerEvaluation) -> int:
    """
    The one authoritative place a per-question interview score
    is computed — a fixed weighted average of the five validated
    sub-scores. Never trusts a top-level "score" field from
    Gemini (none is even requested).
    """

    weighted_sum = sum(
        getattr(evaluation, dimension) * weight
        for dimension, weight in INTERVIEW_SCORE_WEIGHTS.items()
    )

    return round(weighted_sum)


def _recommendation_for_score(overall_score: float) -> str:
    """
    A deterministic, descriptive score-band label — explicitly
    NOT a hiring verdict (Part 12 / Part 34: this system provides
    additional evidence, it does not make hiring decisions).
    """

    if overall_score >= 85:
        return "Strong Match"

    if overall_score >= INTERVIEW_PASS_THRESHOLD:
        return "Good Match"

    if overall_score >= 50:
        return "Partial Match"

    return "Needs Improvement"


def aggregate_interview_result(
    question_scores: list[int],
    evaluations: list[InterviewAnswerEvaluation],
) -> dict:
    """
    Combines all five per-question scores/evaluations into the
    final InterviewTest-level result. Returns:
        {
            "overall_score": int,
            "passed": bool,
            "recommendation": str,
            "evaluation_summary": {
                "technical_correctness": int, ... (averaged),
                "strengths": [...] (deduplicated, capped),
                "gaps": [...] (deduplicated, capped),
            },
        }
    """

    overall_score = (
        round(sum(question_scores) / len(question_scores))
        if question_scores
        else 0
    )

    passed = overall_score >= INTERVIEW_PASS_THRESHOLD

    dimension_averages = {}

    for dimension in INTERVIEW_SCORE_WEIGHTS:

        values = [getattr(evaluation, dimension) for evaluation in evaluations]

        dimension_averages[dimension] = (
            round(sum(values) / len(values)) if values else 0
        )

    strengths: list[str] = []
    gaps: list[str] = []

    for evaluation in evaluations:

        for item in evaluation.strengths:
            if item and item not in strengths:
                strengths.append(item)

        for item in evaluation.gaps:
            if item and item not in gaps:
                gaps.append(item)

    return {
        "overall_score": overall_score,
        "passed": passed,
        "recommendation": _recommendation_for_score(overall_score),
        "evaluation_summary": {
            **dimension_averages,
            "strengths": strengths[:6],
            "gaps": gaps[:6],
        },
    }

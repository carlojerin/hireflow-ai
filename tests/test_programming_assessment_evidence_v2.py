"""
Deterministic unit tests for the Task 5G evidence foundation
(programming_assessment_evidence_v2.py). No database, no Gemini,
no execution -- built entirely from in-memory schema/dataclass
objects, matching this project's existing "pure function, pure
test" style for programming_scoring_v2.py itself.
"""

from backend.app.schemas.programming_question_v2 import (
    ExecutableTestCase,
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
    ProgrammingSubpartV2,
)
from backend.app.services.assessment.programming_assessment_evidence_v2 import (
    build_programming_evidence_v2,
)
from backend.app.services.assessment.programming_scoring_v2 import (
    AssessmentScoreResult,
    MainQuestionScoreResult,
)


def _question(question_id, skills, difficulty="medium", technology="general", points=20.0):
    return ProgrammingMainQuestionV2(
        question_id=question_id,
        title=f"Question {question_id}",
        description="A task.",
        input_specification="input", output_specification="output",
        difficulty=difficulty, technology=technology,
        skills_assessed=skills,
        allowed_languages=["python"], points=points,
        subparts=[
            ProgrammingSubpartV2(
                subpart_id="sp1", description="basic", weight=0.5,
                test_cases=[ExecutableTestCase(
                    test_case_id="tc1", stdin="x", expected_stdout="y", weight=1.0, is_hidden=False,
                )],
            ),
            ProgrammingSubpartV2(
                subpart_id="sp2", description="edge case", weight=0.5,
                test_cases=[ExecutableTestCase(
                    test_case_id="tc2", stdin="z", expected_stdout="w", weight=1.0, is_hidden=True,
                )],
            ),
        ],
    )


def _filler_question(question_id):
    # ProgrammingQuestionSetV2 requires EXACTLY 5 main_questions
    # summing to 100 points -- these pad out a test's real
    # question(s) to satisfy that structural rule without being
    # referenced by any score_result, so they contribute nothing
    # to the evidence being tested (never attempted -> "insufficient
    # evidence" for their own placeholder skill, which no assertion
    # below checks).
    return _question(question_id, [f"filler_skill_{question_id}"], points=20.0)


def _question_set(questions):
    all_questions = list(questions)
    used_points = sum(q.points for q in all_questions)
    next_id = max((q.question_id for q in all_questions), default=0) + 1

    remaining_slots = 5 - len(all_questions)
    remaining_points = 100.0 - used_points

    for i in range(remaining_slots):
        # spread any remaining points evenly across the filler slots
        filler_points = round(remaining_points / remaining_slots, 4) if remaining_slots else 0.0
        all_questions.append(_filler_question(next_id))
        all_questions[-1] = all_questions[-1].model_copy(update={"points": filler_points})
        next_id += 1

    return ProgrammingQuestionSetV2(main_questions=all_questions, generated_by="fallback")


def _result(question_id, earned, max_points, language="python"):
    return MainQuestionScoreResult(
        question_id=question_id, earned_points=earned, max_points=max_points,
        language_used=language, subpart_results=[], platform_failure=False,
    )


# A candidate who fully passed every question for a skill -> that
# skill is a strength, never simultaneously a weakness.
def test_fully_passed_skill_is_a_strength():
    qs = _question_set([_question(1, ["Java"], difficulty="medium")])
    score = AssessmentScoreResult(
        total_earned=20.0, total_possible=20.0,
        question_results=[_result(1, 20.0, 20.0)],
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "Java" in evidence.strengths
    assert "Java" not in evidence.weaknesses
    assert "Java" not in evidence.insufficient_evidence
    assert evidence.highest_difficulty_passed == "medium"


# A skill the candidate failed outright is a weakness, never a
# strength -- and never silently dropped.
def test_failed_skill_is_a_weakness_not_a_strength():
    qs = _question_set([_question(1, ["SQL"])])
    score = AssessmentScoreResult(
        total_earned=0.0, total_possible=20.0,
        question_results=[_result(1, 0.0, 20.0)],
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "SQL" in evidence.weaknesses
    assert "SQL" not in evidence.strengths
    assert evidence.highest_difficulty_passed is None


# A question the candidate never submitted at all contributes
# "not_attempted" evidence, distinct from a genuine failure --
# never guessed at, never silently counted as a pass or fail.
def test_unattempted_question_is_insufficient_evidence_not_a_failure():
    qs = _question_set([_question(1, ["Go"])])
    score = AssessmentScoreResult(
        total_earned=0.0, total_possible=20.0,
        question_results=[],  # candidate never submitted question 1
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "Go" in evidence.insufficient_evidence
    assert "Go" not in evidence.strengths
    assert "Go" not in evidence.weaknesses

    go_items = [i for i in evidence.skill_evidence if i.skill == "Go"]
    assert len(go_items) == 1
    assert go_items[0].outcome == "not_attempted"
    assert go_items[0].earned_fraction == 0.0


# Partial credit (some but not all subparts) is neither a strength
# nor silently ignored -- it is a weakness signal (there IS real
# evidence of difficulty with this skill).
def test_partial_credit_counts_as_a_weakness():
    qs = _question_set([_question(1, ["Concurrency"])])
    score = AssessmentScoreResult(
        total_earned=8.0, total_possible=20.0,
        question_results=[_result(1, 8.0, 20.0)],
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "Concurrency" in evidence.weaknesses
    assert "Concurrency" not in evidence.strengths
    assert evidence.skill_evidence[0].outcome == "partial"


# A skill with mixed evidence across two questions (one passed, one
# failed) must appear as a weakness (real signal of trouble exists)
# -- never a strength, even though one instance passed.
def test_mixed_evidence_for_same_skill_is_a_weakness():
    qs = _question_set([
        _question(1, ["Java"], difficulty="easy"),
        _question(2, ["Java"], difficulty="hard"),
    ])
    score = AssessmentScoreResult(
        total_earned=20.0, total_possible=40.0,
        question_results=[_result(1, 20.0, 20.0), _result(2, 0.0, 20.0)],
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "Java" in evidence.weaknesses
    assert "Java" not in evidence.strengths
    assert evidence.highest_difficulty_passed == "easy"


# A question with no skills_assessed falls back to its `technology`
# field -- never silently dropped from the evidence entirely.
def test_missing_skills_assessed_falls_back_to_technology():
    qs = _question_set([_question(1, [], technology="data structures")])
    score = AssessmentScoreResult(
        total_earned=20.0, total_possible=20.0,
        question_results=[_result(1, 20.0, 20.0)],
    )

    evidence = build_programming_evidence_v2(qs, score)

    assert "data structures" in evidence.strengths
    assert "python" in evidence.languages_used

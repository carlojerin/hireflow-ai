from dataclasses import dataclass, field

from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.services.assessment.programming_scoring_v2 import AssessmentScoreResult


# ============================================================
# JOBPILOT AI — PROGRAMMING ASSESSMENT EVIDENCE (Task 5G,
# Section 10/11: Cross-Question Intelligence foundation)
#
# THIS IS A FOUNDATION, NOT AN AI INTERVIEW. It builds a
# deterministic, evidence-based summary of what a candidate's
# programming attempt actually demonstrated — derived ONLY from
# real, already-computed execution results (programming_scoring_
# v2.AssessmentScoreResult) and the question metadata that was
# already presented to the candidate (skills_assessed/technology/
# difficulty on ProgrammingQuestionSetV2). It never invents a
# score, never calls Gemini, and never asserts a skill was
# demonstrated without a real passing/failing test result behind
# it.
#
# WHY THIS SHAPE: a future AI Interview (Task 5H, explicitly NOT
# built here) needs to know "what does this candidate's
# programming performance actually tell us" without re-deriving it
# from raw submission rows itself. This module is that single,
# reusable answer — HR's review UI and a future interview-question
# selector can both consume the SAME ProgrammingAssessmentEvidenceV2
# object, so the two can never drift into disagreeing about what
# the candidate showed.
#
# CANDIDATE-SAFE BY CONSTRUCTION: built entirely from
# AssessmentScoreResult, which (see programming_scoring_v2.py's own
# module header) never carries a hidden test's stdin/expected_
# stdout — this module inherits that guarantee automatically,
# nothing here re-adds hidden data.
# ============================================================


EvidenceOutcome = str  # "passed" | "partial" | "failed" | "not_attempted"

_PASS_THRESHOLD = 0.8  # earned_fraction >= this counts as "passed" evidence


@dataclass(frozen=True)
class SkillEvidenceItem:

    skill: str
    question_id: int
    difficulty: str
    outcome: EvidenceOutcome
    earned_fraction: float  # 0.0-1.0 for this question; 0.0 for not_attempted
    language_used: str | None


@dataclass(frozen=True)
class ProgrammingAssessmentEvidenceV2:

    skill_evidence: list[SkillEvidenceItem] = field(default_factory=list)

    # Skills where EVERY piece of evidence collected was "passed".
    strengths: list[str] = field(default_factory=list)

    # Skills with at least one "failed" or "partial" outcome.
    weaknesses: list[str] = field(default_factory=list)

    # Skills that were named on a question (skills_assessed/
    # technology) but the candidate never attempted that question
    # at all — real "we don't know" territory, never guessed at.
    insufficient_evidence: list[str] = field(default_factory=list)

    technologies_demonstrated: list[str] = field(default_factory=list)
    languages_used: list[str] = field(default_factory=list)

    highest_difficulty_passed: str | None = None

    # Deliberately NEVER set by build_programming_evidence_v2() below -- that
    # function only ever sees ProgrammingQuestionSetV2 + AssessmentScoreResult,
    # neither of which carries the candidate's actual submitted code. This
    # field is instead populated by interview_integration_v2.py's
    # _get_programming_evidence(), which already loads the raw
    # ProgrammingSubmissionV2 rows for a different reason (scoring) and reuses
    # that same query -- one short excerpt of the candidate's OWN code for
    # their highest-scoring answered question, so the AI interview can ask a
    # specific, hard-to-fake follow-up ("why a TreeMap here instead of X?")
    # instead of only a generic "you showed strong Java skills".
    representative_snippet: dict | None = None


_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2}


def _outcome_for(earned_points: float, max_points: float, attempted: bool) -> tuple[EvidenceOutcome, float]:

    if not attempted:
        return "not_attempted", 0.0

    fraction = (earned_points / max_points) if max_points else 0.0

    if fraction >= _PASS_THRESHOLD:
        return "passed", fraction

    if fraction > 0.0:
        return "partial", fraction

    return "failed", fraction


def build_programming_evidence_v2(
    question_set: ProgrammingQuestionSetV2,
    score_result: AssessmentScoreResult,
) -> ProgrammingAssessmentEvidenceV2:

    results_by_question_id = {r.question_id: r for r in score_result.question_results}

    skill_evidence: list[SkillEvidenceItem] = []
    technologies: set[str] = set()
    languages: set[str] = set()
    highest_passed_rank = -1

    for question in question_set.main_questions:

        result = results_by_question_id.get(question.question_id)
        attempted = result is not None

        earned = result.earned_points if result else 0.0
        outcome, fraction = _outcome_for(earned, question.points, attempted)

        skills = list(question.skills_assessed) or (
            [question.technology] if question.technology else []
        )

        if not skills:
            skills = [f"question_{question.question_id}"]

        language_used = result.language_used if result else None

        for skill in skills:

            skill_evidence.append(SkillEvidenceItem(
                skill=skill,
                question_id=question.question_id,
                difficulty=question.difficulty,
                outcome=outcome,
                earned_fraction=round(fraction, 4),
                language_used=language_used,
            ))

        if attempted:

            if question.technology:
                technologies.add(question.technology)

            if language_used:
                languages.add(language_used)

            if outcome == "passed":
                highest_passed_rank = max(
                    highest_passed_rank, _DIFFICULTY_RANK.get(question.difficulty, 0),
                )

    evidence_by_skill: dict[str, list[SkillEvidenceItem]] = {}

    for item in skill_evidence:
        evidence_by_skill.setdefault(item.skill, []).append(item)

    strengths = sorted(
        skill for skill, items in evidence_by_skill.items()
        if items and all(i.outcome == "passed" for i in items)
    )

    weaknesses = sorted(
        skill for skill, items in evidence_by_skill.items()
        if any(i.outcome in ("failed", "partial") for i in items)
    )

    insufficient_evidence = sorted(
        skill for skill, items in evidence_by_skill.items()
        if items and all(i.outcome == "not_attempted" for i in items)
    )

    highest_difficulty_passed = None
    for name, rank in _DIFFICULTY_RANK.items():
        if rank == highest_passed_rank:
            highest_difficulty_passed = name

    return ProgrammingAssessmentEvidenceV2(
        skill_evidence=skill_evidence,
        strengths=strengths,
        weaknesses=weaknesses,
        insufficient_evidence=insufficient_evidence,
        technologies_demonstrated=sorted(technologies),
        languages_used=sorted(languages),
        highest_difficulty_passed=highest_difficulty_passed,
    )

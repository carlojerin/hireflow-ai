from dataclasses import dataclass, field

from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: FINAL EVIDENCE AGGREGATION
# (Task 5H)
#
# Turns a list of per-turn AnswerAnalysisV2 records (one per
# answered question, already candidate-safe) into a competency-
# level evaluation HR can actually read — Section 17's explicit
# requirement: "Do not create one magic AI score... create
# competency-level evaluation" with a deterministic, inspectable
# rule, not a fabricated composite number.
#
# NO HIRING DECISION IS EVER PRODUCED HERE (Section 38) — only
# evidence labels per competency, exactly like programming_
# assessment_evidence_v2.py's strengths/weaknesses/insufficient_
# evidence shape (Task 5G), kept deliberately consistent so HR
# reads the same vocabulary across both assessment stages.
# ============================================================


_STRENGTH_RANK = {"strong": 3, "moderate": 2, "weak": 1, "none": 0}


@dataclass(frozen=True)
class CompetencyEvaluationV2:

    competency: str
    label: str  # "Strong evidence" | "Moderate evidence" | "Weak evidence" | "Insufficient evidence"
    turns_covering: int
    key_evidence: list[str] = field(default_factory=list)
    key_weaknesses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InterviewFinalEvaluationV2:

    competency_evaluations: list[CompetencyEvaluationV2] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    insufficient_evidence: list[str] = field(default_factory=list)
    total_turns: int = 0


def _label_for(levels: list[str]) -> str:

    if not levels:
        return "Insufficient evidence"

    # A competency probed across multiple turns is judged by its
    # BEST demonstrated level, not an average -- one strong,
    # well-evidenced answer is real proof of competence even if an
    # earlier follow-up on the same topic was only moderate; this
    # mirrors how a human interviewer credits a candidate who
    # eventually demonstrates real understanding.
    best = max(levels, key=lambda lv: _STRENGTH_RANK.get(lv, 0))

    return {
        "strong": "Strong evidence",
        "moderate": "Moderate evidence",
        "weak": "Weak evidence",
        "none": "Insufficient evidence",
    }.get(best, "Insufficient evidence")


def compute_final_evaluation_v2(
    analyses: list[AnswerAnalysisV2],
    all_competency_names: list[str],
) -> InterviewFinalEvaluationV2:
    """
    all_competency_names: every competency named in the interview's
    blueprint, so a competency that was NEVER actually asked about
    (interview ended early) still appears as "Insufficient evidence"
    rather than being silently omitted.
    """

    by_competency: dict[str, list[AnswerAnalysisV2]] = {}

    for analysis in analyses:
        by_competency.setdefault(analysis.competency, []).append(analysis)

    evaluations: list[CompetencyEvaluationV2] = []

    for name in all_competency_names:

        turns = by_competency.get(name, [])
        levels = [t.strength_level for t in turns]

        key_evidence: list[str] = []
        key_weaknesses: list[str] = []

        for t in turns:
            key_evidence.extend(t.evidence)
            key_weaknesses.extend(t.weaknesses)

        evaluations.append(CompetencyEvaluationV2(
            competency=name,
            label=_label_for(levels),
            turns_covering=len(turns),
            key_evidence=key_evidence[:5],
            key_weaknesses=key_weaknesses[:5],
        ))

    strengths = [e.competency for e in evaluations if e.label == "Strong evidence"]
    weaknesses = [e.competency for e in evaluations if e.label in ("Weak evidence", "Moderate evidence")]
    insufficient = [e.competency for e in evaluations if e.label == "Insufficient evidence"]

    return InterviewFinalEvaluationV2(
        competency_evaluations=evaluations,
        strengths=strengths,
        weaknesses=weaknesses,
        insufficient_evidence=insufficient,
        total_turns=len(analyses),
    )

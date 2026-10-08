from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: INDEPENDENT FINAL EVALUATOR
#
# The per-turn AnswerAnalysisV2.strength_level judgment (interview_turn_v2.py)
# is made by the SAME model, in the SAME call, under the SAME time pressure
# that generated the next question -- nothing ever re-reads the finished
# transcript with fresh eyes. This schema is that second, independent pass
# (interview_final_evaluator_v2.py): it re-grades each competency against a
# fixed rubric with quoted evidence from the candidate's OWN answers, and
# flags anything that looks internally inconsistent or technically
# implausible (a lightweight self-consistency check -- there is no external
# ground truth to verify against, so this is honestly scoped as "does this
# hold together", never "is this objectively true").
#
# Field names/vocabulary deliberately mirror interview_evidence_v2.
# CompetencyEvaluationV2 (competency/label/turns_covering/key_evidence/
# key_weaknesses) so interview_integration_v2.py's existing pass/fail
# derivation (checks e.label against _PASS_LABELS) keeps working unchanged
# against this richer object -- this is a strict superset, not a redesign.
#
# NO HIRING DECISION IS EVER PRODUCED HERE (same rule as interview_evidence_v2.py) --
# only competency-level evidence labels plus quoted transcript lines.
# ============================================================

LabelV2 = Literal["Strong evidence", "Moderate evidence", "Weak evidence", "Insufficient evidence"]


class CompetencyRubricScoreV2(BaseModel):

    competency: str
    label: LabelV2 = "Insufficient evidence"
    turns_covering: int = 0

    # Short verbatim quotes from the CANDIDATE's own answers only (never the
    # interviewer's questions) -- interview_final_evaluator_v2.py verifies
    # each one is an actual substring of a real answer before trusting it,
    # dropping any the model invented rather than failing the whole result.
    key_evidence: list[str] = Field(default_factory=list)
    key_weaknesses: list[str] = Field(default_factory=list)

    # None when nothing looked off. A short, specific reason otherwise (e.g.
    # "claimed reducing latency by 90% with no numbers given earlier in the
    # same answer") -- never fired merely for a terse or hard-to-verify
    # answer, only for an actual internal inconsistency or implausible claim.
    veracity_flag: Optional[str] = None


class InterviewFinalEvaluationV2(BaseModel):

    competency_evaluations: list[CompetencyRubricScoreV2] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)

    # One or two sentences, grounded only in what was actually said --
    # never a hire/reject recommendation (Section 38 of the original task
    # spec, unchanged here).
    overall_summary: str = ""

    # Flattened, HR-scannable view of every competency_evaluations[].veracity_flag
    # that isn't None -- so a reviewer doesn't have to open every competency to
    # notice something was flagged.
    veracity_concerns: list[str] = Field(default_factory=list)

    total_turns: int = 0

    # "llm_rubric": the independent evaluator pass ran successfully.
    # "fallback_aggregation": it failed/timed out and interview_evidence_v2.
    # compute_final_evaluation_v2()'s plain majority-vote aggregation was used
    # instead -- HR-visible so a fallback result is never silently presented
    # as if it had the same scrutiny as a real rubric pass.
    evaluated_by: Literal["llm_rubric", "fallback_aggregation"] = "fallback_aggregation"

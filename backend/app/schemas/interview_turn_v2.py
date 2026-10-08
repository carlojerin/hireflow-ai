from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: TURN CONTRACT (Task 5H)
#
# The ONE strict JSON shape Gemini must return after analyzing a
# candidate's answer and deciding what happens next (Section 15 of
# the task spec). Validated with pydantic before anything touches
# the database or the candidate's screen — a response that fails
# validation is treated exactly like a Gemini failure (falls back
# to the deterministic engine in interview_brain_v2.py), never
# shown to the candidate malformed.
# ============================================================

NextActionV2 = Literal[
    "DIRECT_NEXT_TOPIC",
    "FOLLOW_UP",
    "DEEPER_PROBE",
    "CLARIFICATION",
    "CHALLENGE",
    "SCENARIO",
    "COUNTER_EXAMPLE",
    "RESUME_VERIFICATION",
    "MOVE_ON",
    "END_INTERVIEW",
]

DifficultyV2 = Literal["basic", "intermediate", "advanced"]

# The probe ladder (Section: HOW A GOOD INTERVIEWER THINKS in interview_brain_v2.INTERVIEWER_SYSTEM_PROMPT). One rung per
# turn on a given competency; jobpilot rejects a repeated rung for the SAME competency instead of trusting the model to
# remember on its own -- real transcripts showed it re-asking the "example" rung twice in different words (a genuine
# near-duplicate question that plain text-similarity checks could not reliably catch, since the wording differs a lot).
ProbeRungV2 = Literal["example", "role_or_decision", "reasoning_or_tradeoff", "result_or_metric", "challenge_or_change", "clarification"]

QuestionTypeV2 = Literal[
    "INTRODUCTION",
    "EXPERIENCE",
    "TECHNICAL",
    "SCENARIO",
    "PROBLEM_SOLVING",
    "RESUME_VERIFICATION",
    "DEEP_DIVE",
    "CHALLENGE",
    "CLARIFICATION",
]


class AnswerAnalysisV2(BaseModel):
    """
    What the interviewer extracted from the candidate's LAST
    answer. Every list is allowed to be empty — an empty
    `evidence` list is itself real information (the answer gave
    none), never padded to look more thorough than it was.
    """

    competency: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    missing_depth: list[str] = Field(default_factory=list)

    # Qualitative only, per Section 6's explicit instruction not to
    # fabricate numerical confidence the model cannot justify.
    strength_level: Literal["strong", "moderate", "weak", "none"] = "none"


class InterviewTurnDecisionV2(BaseModel):
    """
    The full structured decision for one turn: analysis of the
    answer just given, PLUS what to do/ask next. next_question is
    required unless next_action == "END_INTERVIEW".
    """

    answer_analysis: AnswerAnalysisV2

    next_action: NextActionV2
    reason: str = ""

    next_competency: Optional[str] = None
    next_difficulty: DifficultyV2 = "intermediate"
    next_question: Optional[str] = None
    next_question_type: QuestionTypeV2 = "TECHNICAL"

    # Which rung of the probe ladder this question targets, for the SAME competency it stays on (irrelevant, and left at
    # its default, when next_action moves to a different competency or ends the interview).
    next_question_focus: ProbeRungV2 = "example"

    def validate_question_present(self) -> bool:
        return self.next_action == "END_INTERVIEW" or bool(
            self.next_question and self.next_question.strip()
        )


class InterviewOpeningV2(BaseModel):
    """
    The contract for the very first turn — a natural greeting plus
    a candidate-specific opening question (Section 7). Kept
    separate from InterviewTurnDecisionV2 since there is no prior
    answer to analyze yet.
    """

    opening_message: str
    first_question: str
    competency: str
    difficulty: DifficultyV2 = "basic"
    question_type: QuestionTypeV2 = "INTRODUCTION"

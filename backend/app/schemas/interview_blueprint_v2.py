from typing import Literal

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: INTERVIEW BLUEPRINT (Task 5H)
#
# The interview's PLAN, built once (deterministically — never by
# Gemini) from InterviewContextV2 and held stable for the whole
# attempt, exactly mirroring how ProgrammingQuestionSetV2 is a
# stable snapshot for a programming attempt. Gemini receives this
# blueprint as CONTEXT on every turn; it never invents the
# competency list itself.
#
# EvidenceStatus distinguishes three states per competency,
# matching Section 5 of the task spec exactly:
#   KNOWN            — real evidence already exists (programming/
#                       aptitude) and is strong enough that the
#                       interview should VERIFY depth, not re-prove
#                       basic competence from zero.
#   NEEDS_VERIFICATION — some signal exists but is weak/partial/
#                       resume-only; the interview should gather
#                       real evidence.
#   UNKNOWN          — no signal at all; full exploration needed.
# ============================================================

EvidenceStatus = Literal["KNOWN", "NEEDS_VERIFICATION", "UNKNOWN"]


class InterviewCompetencyV2(BaseModel):

    name: str
    priority: Literal["high", "medium", "low"] = "medium"

    # What kind of evidence would satisfy this competency —
    # human-readable, used only as prompt guidance, never scored.
    target_evidence: str = ""

    known_strength: bool = False
    known_weakness: bool = False
    evidence_status: EvidenceStatus = "UNKNOWN"

    # Where the known_strength/known_weakness signal came from, if
    # any — "programming" | "aptitude" | "resume" | None. Purely
    # for HR/debug transparency, never shown to the candidate.
    source: str | None = None


class InterviewBlueprintV2(BaseModel):

    competencies: list[InterviewCompetencyV2] = Field(min_length=1)

    # Deterministic guidance derived from the job/candidate, e.g.
    # "technical" vs "non_technical" role shape — informs whether
    # DEEP_DIVE/CHALLENGE turns lean technical or situational.
    role_shape: Literal["technical", "non_technical"] = "technical"

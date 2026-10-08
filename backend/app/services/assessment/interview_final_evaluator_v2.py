import json
import logging
import os

from pydantic import ValidationError

from backend.app.schemas.interview_final_evaluation_v2 import (
    CompetencyRubricScoreV2,
    InterviewFinalEvaluationV2,
)
from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2
from backend.app.services.assessment.interview_evidence_v2 import compute_final_evaluation_v2
from backend.app.services.assessment.interview_llm_provider import (
    HostedChatProvider,
    InterviewLLMUnavailableError,
    get_evaluator_llm_provider,
)


logger = logging.getLogger("jobpilot.assessment.interview_final_evaluator_v2")


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: INDEPENDENT FINAL EVALUATOR
#
# interview_evidence_v2.compute_final_evaluation_v2() only aggregates the SAME
# per-turn strength_level judgments interview_brain_v2.py's turn-decision call
# already made, in the heat of the conversation, under a tight token/time
# budget. Nothing ever re-reads the finished transcript with fresh eyes. This
# module is that second, independent pass: one extra LLM call, made ONCE after
# the interview ends, that re-grades every competency against a fixed rubric
# with quoted evidence, and flags anything that looks internally inconsistent.
#
# This is a genuine second opinion, not a rename of the same computation --
# it sees the FULL transcript at once (the in-conversation model only ever
# saw a flattened running summary) and is explicitly told it may disagree
# with the earlier in-the-moment labels.
#
# Never raises: any failure (LLM unavailable, malformed reply after one
# retry) falls back to compute_final_evaluation_v2()'s plain aggregation,
# clearly marked evaluated_by="fallback_aggregation" so HR never mistakes a
# degraded result for a real rubric pass.
# ============================================================


_EVALUATOR_SYSTEM_PROMPT = """You are an independent, second-opinion evaluator reviewing a COMPLETED technical interview \
transcript for a hiring team. You did not conduct this interview -- another AI interviewer asked these questions in \
real time, under time pressure, and its in-the-moment judgments can be wrong. Re-read the whole transcript with fresh \
eyes and grade it properly.

STRICT RULES:
- Never invent a fact, skill, or claim the candidate did not actually say. Every "key_evidence" entry MUST be copied \
verbatim (word-for-word) from the candidate's own answer text -- never paraphrased, never taken from the \
interviewer's question.
- Use ONLY these four labels: "Strong evidence", "Moderate evidence", "Weak evidence", "Insufficient evidence". \
"Insufficient evidence" means the competency was never really probed, not merely that the candidate did badly.
- veracity_flag: leave it null for almost every competency. Only fill it in when an answer is genuinely internally \
inconsistent (contradicts something the SAME candidate said elsewhere in this transcript) or makes a technically \
implausible claim. Never flag an answer merely for being short, vague, hard to verify, or not matching your own \
preferred approach -- that is normal interview variation, not a red flag.
- Never produce a hiring recommendation, an overall pass/fail verdict, or a single overall score. Only \
per-competency evidence labels and a short overall_summary describing what was actually covered.
- Output ONLY one JSON object. No markdown fences, no commentary before or after it.
"""

_REPAIR_NOTE = (
    "\n\nYour previous reply was rejected: {reason}. Reply again with ONLY the JSON object in the exact shape "
    "requested, keeping every key_evidence entry verbatim from the candidate's own answers."
)

_RESPONSE_SHAPE = """Return ONLY this JSON shape:
{
  "competency_evaluations": [
    {"competency": "<exact competency name from the list above>",
     "label": "Strong evidence|Moderate evidence|Weak evidence|Insufficient evidence",
     "turns_covering": 0,
     "key_evidence": ["verbatim quote copied from a candidate answer", "..."],
     "key_weaknesses": ["..."],
     "veracity_flag": null}
  ],
  "strengths": ["competency name", "..."],
  "weaknesses": ["competency name", "..."],
  "insufficient_evidence": ["competency name", "..."],
  "overall_summary": "1-2 sentences describing what was actually covered -- no recommendation, no pass/fail verdict"
}"""


def _transcript_lines(answered_turns: list) -> str:

    lines = []

    for t in answered_turns:
        lines.append(
            f'Turn {t.turn_number} [{t.competency}]\n'
            f'Question: {t.question}\n'
            f'Candidate answered: "{t.candidate_answer}"'
        )

    return "\n\n".join(lines)


def _prompt(answered_turns: list, all_competency_names: list[str], job_title: str, required_skills: str) -> str:

    return (
        f"JOB: {job_title or 'Not specified'}\n"
        f"REQUIRED SKILLS: {required_skills or 'Not specified'}\n"
        f"COMPETENCIES TO GRADE (grade every one of these, even if never asked about): {', '.join(all_competency_names)}\n\n"
        f"FULL INTERVIEW TRANSCRIPT (the candidate's own words only ever appear as \"Candidate answered\"):\n"
        f"{_transcript_lines(answered_turns)}\n\n"
        f"{_RESPONSE_SHAPE}"
    )


def _ask_llm(prompt: str) -> str:

    provider = get_evaluator_llm_provider()

    kwargs = {}

    # a full-transcript rubric pass needs more room than a single turn decision does
    if isinstance(provider, HostedChatProvider):
        kwargs["max_tokens"] = int(os.getenv("AI_INTERVIEW_EVALUATOR_MAX_TOKENS") or "2000")

    return provider.generate_json(prompt, system=_EVALUATOR_SYSTEM_PROMPT, **kwargs)


def _verify_quotes(
    evaluation: InterviewFinalEvaluationV2,
    answers_by_competency: dict[str, list[str]],
) -> InterviewFinalEvaluationV2:
    """
    Drops any key_evidence entry that is not an actual substring of a real
    candidate answer for that competency -- defense-in-depth against a
    fabricated quote, rather than trusting the model's claim that it copied
    verbatim (Section 3 of the original task spec: never fabricate evidence).
    """

    cleaned = []

    for comp_eval in evaluation.competency_evaluations:

        real_answers = answers_by_competency.get(comp_eval.competency, [])
        kept = [q for q in comp_eval.key_evidence if any(q in answer for answer in real_answers)]

        cleaned.append(comp_eval.model_copy(update={"key_evidence": kept}))

    return evaluation.model_copy(update={"competency_evaluations": cleaned})


def _fallback(analyses: list[AnswerAnalysisV2], all_competency_names: list[str]) -> InterviewFinalEvaluationV2:

    agg = compute_final_evaluation_v2(analyses, all_competency_names)

    return InterviewFinalEvaluationV2(
        competency_evaluations=[
            CompetencyRubricScoreV2(
                competency=e.competency,
                label=e.label,
                turns_covering=e.turns_covering,
                key_evidence=e.key_evidence,
                key_weaknesses=e.key_weaknesses,
                veracity_flag=None,
            )
            for e in agg.competency_evaluations
        ],
        strengths=agg.strengths,
        weaknesses=agg.weaknesses,
        insufficient_evidence=agg.insufficient_evidence,
        overall_summary="",
        veracity_concerns=[],
        total_turns=agg.total_turns,
        evaluated_by="fallback_aggregation",
    )


def evaluate_interview_final_v2(
    answered_turns: list,
    analyses: list[AnswerAnalysisV2],
    all_competency_names: list[str],
    job_title: str,
    required_skills: str,
) -> InterviewFinalEvaluationV2:
    """
    answered_turns: AIInterviewTurnV2 rows with candidate_answer is not None,
    ordered by turn_number. analyses: the SAME turns' answer_analysis_json
    already parsed into AnswerAnalysisV2 (used only for the fallback path, to
    match compute_final_evaluation_v2()'s existing signature). Never raises.
    """

    if not answered_turns:
        return _fallback(analyses, all_competency_names)

    answers_by_competency: dict[str, list[str]] = {}

    for t in answered_turns:
        answers_by_competency.setdefault(t.competency, []).append(t.candidate_answer or "")

    prompt = _prompt(answered_turns, all_competency_names, job_title, required_skills)

    reason = ""

    for attempt in range(2):

        try:

            raw_text = _ask_llm(prompt + (_REPAIR_NOTE.format(reason=reason) if reason else ""))
            evaluation = InterviewFinalEvaluationV2.model_validate(json.loads(raw_text))

            unknown = [c.competency for c in evaluation.competency_evaluations if c.competency not in all_competency_names]

            if unknown:
                raise ValueError(f"Unknown competency name(s), not in this interview's blueprint: {unknown}")

            evaluation = _verify_quotes(evaluation, answers_by_competency)

            return evaluation.model_copy(update={
                "evaluated_by": "llm_rubric",
                "total_turns": len(answered_turns),
                "veracity_concerns": [c.veracity_flag for c in evaluation.competency_evaluations if c.veracity_flag],
            })

        except InterviewLLMUnavailableError as exc:

            logger.warning("Interview final evaluator unavailable (%s) -- using deterministic fallback.", exc)
            return _fallback(analyses, all_competency_names)

        except (json.JSONDecodeError, ValidationError, ValueError) as exc:

            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("Interview final evaluator reply rejected on attempt %d (%s).", attempt + 1, type(exc).__name__)

    logger.warning("Interview final evaluator failed validation after retry -- using deterministic fallback.")
    return _fallback(analyses, all_competency_names)

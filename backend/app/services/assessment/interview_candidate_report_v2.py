"""
Detailed HR report for a finished AI interview (one LLM call, made ONCE when the interview ends).

It reads the whole transcript, the job, the candidate's resume and how they did in the earlier stages, and writes what a
recruiter wants to know: a candidate overview, a review of EVERY answer, strengths and concerns with reasons, how well the
answers match the resume, communication, the AI tools they use, their expected package, their closing answer on why they fit the
role, and what to ask next. It never recommends hiring or rejecting: HR decides.

The candidate's own words on the closing questions (AI tools, expected package, how they would work) are copied verbatim by
code (never by the model), so what HR reads there is exactly what was said. Never raises: any failure falls back to a plain
report built from the transcript, marked generated_by="fallback".
"""

import json
import logging

from backend.app.services.assessment.interview_llm_provider import (
    HostedChatProvider, InterviewLLMUnavailableError, get_evaluator_llm_provider,
)

logger = logging.getLogger("jobpilot.assessment.interview_candidate_report")

AI_TOOL = "ai tool fluency"
PACKAGE = "salary expectations"
WORK_FIT = "closing thoughts & fit"

_QUALITIES = ("strong", "adequate", "weak", "off_topic")

_SYSTEM = """You write the detailed interview report a recruiter reads before deciding on a candidate. You are given the \
job, the candidate's resume summary, their results in earlier assessment stages, and the full interview transcript.

RULES:
- Be specific and detailed. Refer to what the candidate actually said; name the technologies, projects, numbers and \
examples they gave. Never write generic filler such as "good communication skills" without saying what shows it.
- Never invent anything the candidate did not say, and never state a fact that is not in the material you were given.
- Never recommend hiring or rejecting, and never give an overall pass/fail verdict: describe the evidence, HR decides.
- Judge each answer on its own merit: depth, correctness, concrete examples, relevance to the question, and consistency \
with the resume and with the candidate's other answers.
- If the candidate failed or did poorly in an earlier stage, say so plainly in the overview; the interview continued \
regardless and HR needs the whole picture.
- Output ONLY one JSON object. No markdown fences, no text before or after it."""

_SHAPE = """Return ONLY this JSON shape:
{
  "candidate_overview": "5-8 sentences: who the candidate appears to be for this role, the level of experience shown, how the interview went overall, and how it fits with their resume and earlier stages",
  "answer_reviews": [
    {"turn": 1,
     "answer_summary": "2-3 sentences of what they actually said, with the specifics",
     "assessment": "2-3 sentences on the quality: depth, correctness, examples, relevance, anything vague or contradictory",
     "quality": "strong|adequate|weak|off_topic"}
  ],
  "strengths": ["a full sentence each, with the evidence from the answers"],
  "concerns": ["a full sentence each, with the evidence: gaps, vague answers, inconsistencies, anything to verify"],
  "communication": "3-4 sentences: clarity, structure, confidence, how well they explain things",
  "resume_consistency": "3-4 sentences: do the answers back up the resume claims, what was not demonstrated",
  "ai_tools": {"tools": ["tool names they said"], "assessment": "2-3 sentences: how real and how deep their AI-tool use sounds"},
  "compensation": "1-2 sentences about the expectation they stated and anything notable (flexibility, fit for the role level)",
  "work_style_and_fit": "2-4 sentences on their final answer (anything they added, why they say they fit the role) and how convincing it is against their earlier answers",
  "suggested_follow_ups": ["specific questions HR could still ask in a later round, based on the gaps above"],
  "overall_impression": "4-6 sentences summarising the candidate for HR, balanced, no hire/reject verdict"
}
Include one answer_reviews entry for EVERY turn in the transcript, in order."""


def _closing_answers(answered_turns: list) -> dict:
    """The candidate's own words on the closing questions, copied verbatim (never produced by the model)."""

    tools, package, work = [], None, None

    for t in answered_turns:
        key = (t.competency or "").lower()
        if key == AI_TOOL:
            tools.append({"question": t.question, "answer": t.candidate_answer or ""})
        elif key == PACKAGE:
            package = {"question": t.question, "answer": t.candidate_answer or ""}
        elif key == WORK_FIT:
            work = {"question": t.question, "answer": t.candidate_answer or ""}

    return {"ai_tools": tools, "expected_package": package, "work_in_company": work}


def _transcript(answered_turns: list) -> str:
    return "\n\n".join(
        f"Turn {t.turn_number} [{t.competency}]\nQuestion: {t.question}\nCandidate answered: \"{t.candidate_answer}\""
        for t in answered_turns
    )


def _prompt(answered_turns: list, job, resume_summary: str, stage_facts: dict) -> str:

    stages = "\n".join(f"- {name}: {fact}" for name, fact in stage_facts.items() if fact) or "- no earlier stage results"

    return (
        f"JOB: {getattr(job, 'title', '') or 'Not specified'} at {getattr(job, 'company', '') or 'the company'}\n"
        f"REQUIRED SKILLS: {getattr(job, 'required_skills', '') or 'Not specified'}\n"
        f"JOB DESCRIPTION: {(getattr(job, 'description', '') or '')[:700]}\n\n"
        f"CANDIDATE RESUME SUMMARY: {resume_summary or 'Not available'}\n\n"
        f"EARLIER STAGE RESULTS:\n{stages}\n\n"
        f"FULL INTERVIEW TRANSCRIPT:\n{_transcript(answered_turns)}\n\n{_SHAPE}"
    )


def _clean(report: dict, answered_turns: list) -> dict:
    """Keeps only expected fields in the expected shapes; one review per real turn, in order."""

    def _text(value, limit=1500):
        return str(value or "").strip()[:limit]

    def _list(value, limit=8):
        return [_text(v, 600) for v in (value or []) if str(v or "").strip()][:limit]

    by_turn = {}
    for review in report.get("answer_reviews") or []:
        try:
            by_turn[int(review.get("turn"))] = review
        except (TypeError, ValueError):
            continue

    reviews = []
    for t in answered_turns:
        r = by_turn.get(t.turn_number) or {}
        quality = _text(r.get("quality"), 20).lower()
        reviews.append({
            "turn": t.turn_number,
            "competency": t.competency,
            "question": t.question,
            "answer": t.candidate_answer or "",
            "answer_summary": _text(r.get("answer_summary")),
            "assessment": _text(r.get("assessment")),
            "quality": quality if quality in _QUALITIES else "adequate",
        })

    ai = report.get("ai_tools") if isinstance(report.get("ai_tools"), dict) else {}

    return {
        "candidate_overview": _text(report.get("candidate_overview"), 2500),
        "answer_reviews": reviews,
        "strengths": _list(report.get("strengths")),
        "concerns": _list(report.get("concerns")),
        "communication": _text(report.get("communication")),
        "resume_consistency": _text(report.get("resume_consistency")),
        "ai_tools": {"tools": _list(ai.get("tools"), 10), "assessment": _text(ai.get("assessment"))},
        "compensation": _text(report.get("compensation")),
        "work_style_and_fit": _text(report.get("work_style_and_fit")),
        "suggested_follow_ups": _list(report.get("suggested_follow_ups"), 6),
        "overall_impression": _text(report.get("overall_impression"), 2500),
    }


def _fallback(answered_turns: list) -> dict:

    return {
        "candidate_overview": (
            f"The candidate answered {len(answered_turns)} interview question(s). The detailed written analysis could not be "
            "generated, so each answer is shown below in the candidate's own words."
        ),
        "answer_reviews": [
            {"turn": t.turn_number, "competency": t.competency, "question": t.question, "answer": t.candidate_answer or "",
             "answer_summary": "", "assessment": "", "quality": "adequate"}
            for t in answered_turns
        ],
        "strengths": [], "concerns": [], "communication": "", "resume_consistency": "",
        "ai_tools": {"tools": [], "assessment": ""}, "compensation": "", "work_style_and_fit": "",
        "suggested_follow_ups": [], "overall_impression": "",
    }


def generate_interview_candidate_report(answered_turns: list, job, resume_summary: str = "", stage_facts: dict | None = None) -> dict:
    """Returns the report dict (see _SHAPE) plus the verbatim closing answers. Never raises."""

    closing = _closing_answers(answered_turns)

    if not answered_turns:
        return {**_fallback(answered_turns), "stated_by_candidate": closing, "generated_by": "fallback"}

    prompt = _prompt(answered_turns, job, resume_summary, stage_facts or {})

    try:

        provider = get_evaluator_llm_provider()
        kwargs = {"max_tokens": 3500} if isinstance(provider, HostedChatProvider) else {}
        raw = provider.generate_json(prompt, system=_SYSTEM, **kwargs)
        report = _clean(json.loads(raw), answered_turns)

        if report["candidate_overview"]:
            return {**report, "stated_by_candidate": closing, "generated_by": "ai"}

    except InterviewLLMUnavailableError as exc:
        logger.warning("Detailed interview report unavailable (%s): using the plain fallback.", exc)
    except Exception as exc:  # noqa: BLE001 - the report must never break finishing an interview
        logger.warning("Detailed interview report failed (%s): using the plain fallback.", type(exc).__name__)

    return {**_fallback(answered_turns), "stated_by_candidate": closing, "generated_by": "fallback"}

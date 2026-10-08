import logging

from backend.app.services.assessment.interview_llm_provider import (
    HostedChatProvider,
    InterviewLLMUnavailableError,
)


# ============================================================
# JOBPILOT AI - CANDIDATE NARRATIVE SUMMARY (HR Intelligence Report)
#
# A plain-language paragraph synthesizing a candidate's performance
# across every stage they completed — self intro, aptitude,
# programming/SJT/technical-domain, AI interview. Deliberately
# NEVER produces a hire/reject verdict or any pass/fail-style
# recommendation of its own — same rule interview_final_evaluator_
# v2.py already follows ("NO HIRING DECISION IS EVER PRODUCED
# HERE"), confirmed with the user before this module was built.
# It only describes what the evidence already shows (scores,
# strengths, gaps) in readable prose instead of a dashboard — never
# a judgment the system isn't entitled to make. HR reads this
# alongside the raw scores/evidence already in the report and
# decides for themselves.
#
# Groq (same low-stakes-generation provider aptitude/SJT/TDA
# question generation already use) — this is a convenience summary,
# never on a scoring-critical path, so a failure here degrades to a
# simple templated fallback rather than breaking the report.
# ============================================================

logger = logging.getLogger("jobpilot.assessment.candidate_narrative_summary")

_SYSTEM_PROMPT = (
    "You write detailed, factual hiring-assessment summaries for a recruiter. "
    "You are given ONLY the candidate's actual scores, strengths, gaps and interview findings across "
    "whichever assessment stages they completed. Write THREE paragraphs of plain, professional prose "
    "separated by a blank line: (1) an overall picture of the candidate for this role; (2) how they did "
    "stage by stage, with the specific numbers you were given, saying plainly where a stage was not passed "
    "(candidates continue to the next stage whether or not they pass, so the recruiter needs to see this); "
    "(3) what the interview showed: their strengths, concerns, the AI tools they use, their expected package "
    "and their final answer on why they fit the role, using the specifics you were given. "
    "ABSOLUTE RULES: "
    "1) Never say the words 'hire', 'reject', 'recommend', 'should be hired', or any "
    "synonym of a hiring decision — you summarize evidence, you never decide. "
    "2) Never invent a score, skill, claim or stage that wasn't given to you. "
    "3) If a stage is missing/not completed, simply don't mention it — never say "
    "'did not complete' speculatively. "
    "4) No bullet points, no headers, just prose."
)

_cache: dict = {}


def _format_stage_facts(stage_facts: dict) -> str:

    lines = []

    for label, data in stage_facts.items():

        if not data:
            continue

        lines.append(f"{label}: {data}")

    return "\n".join(lines) or "No completed assessment stages yet."


def generate_candidate_narrative_summary(stage_facts: dict) -> dict:
    """
    stage_facts: a plain dict of {stage_label: short_fact_string}, e.g.
        {"Aptitude": "90% (9/10), passed",
         "Programming": "85%, strengths: React, Node.js; gaps: system design",
         "AI Interview": "Strong evidence on Communication, Problem Solving"}
    Only include stages that actually happened — the prompt is told
    never to speculate about missing ones.

    Returns {"summary": str, "generated_by": "ai" | "fallback"}.
    """

    facts_text = _format_stage_facts(stage_facts)

    # The report page asks again on every open: the same facts get the same summary without another LLM call.
    cache_key = hash(facts_text)

    if cache_key in _cache:
        return dict(_cache[cache_key])

    if not stage_facts:
        return {
            "summary": "This candidate has not completed any assessment stages yet.",
            "generated_by": "fallback",
        }

    try:

        provider = HostedChatProvider("groq")

        summary = provider.generate_json(
            prompt=f"Candidate's actual results:\n{facts_text}\n\nWrite the summary paragraph now.",
            system=_SYSTEM_PROMPT,
            raw_text=True,
            max_tokens=1100,
            temperature=0.4,
        ).strip()

        if summary:
            result = {"summary": summary, "generated_by": "ai"}
            if len(_cache) > 200:
                _cache.clear()
            _cache[cache_key] = result
            return dict(result)

    except InterviewLLMUnavailableError as exc:
        logger.warning("Candidate narrative summary generation unavailable: %s", exc)
    except Exception as exc:
        logger.warning("Candidate narrative summary generation failed: %s", exc)

    # Honest, simple fallback — never blocks the report from loading.
    fallback = "This candidate's results so far: " + "; ".join(
        f"{label} — {data}" for label, data in stage_facts.items() if data
    ) + "."

    return {"summary": fallback, "generated_by": "fallback"}

import re
import time
import os
import json
from difflib import SequenceMatcher
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2
from backend.app.schemas.interview_context_v2 import InterviewContextV2
from backend.app.schemas.interview_turn_v2 import (
    AnswerAnalysisV2,
    InterviewOpeningV2,
    InterviewTurnDecisionV2,
)
from backend.app.services.assessment.interview_llm_provider import (
    HostedChatProvider,
    InterviewLLMUnavailableError,
    get_interview_llm_provider,
)


logger = logging.getLogger("jobpilot.assessment.interview_brain_v2")


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: CONVERSATION BRAIN (Task 5H)
#
# THE core adaptive component (Section 8). Since Task 5O-A the
# model is reached ONLY through interview_llm_provider (default: a
# Gemini by default; a hosted provider (Groq) only if explicitly configured, and
# never as an automatic fallback). Every LLM call asks for
# ONE strict JSON contract (InterviewOpeningV2 / InterviewTurnDecisionV2,
# both in interview_turn_v2.py) and the response is ALWAYS
# validated with pydantic (plus JobPilot's own semantic guards) before
# it can reach the database or the candidate's screen. A malformed
# response gets ONE constrained retry; any remaining failure (provider
# down, malformed JSON, schema violation) falls through to the
# deterministic fallback path below, which is a genuinely different
# code path -- not "the model pretending" -- clearly marked
# generated_by="fallback"
# wherever it is persisted, exactly like every other AI generator
# in this project.
#
# CONVERSATION MEMORY (Section 13): The model is never sent the raw
# full transcript. `build_conversation_summary()` produces a
# compact, bullet-style summary of what has been established so
# far (competency: evidence-so-far), which is what actually gets
# passed on every subsequent call -- keeping prompts small and
# giving the model exactly the "what do we already know" signal it
# needs to avoid repeating itself.
# ============================================================


INTERVIEWER_SYSTEM_PROMPT = """
You are the interviewing engine of HireFlow, an AI recruitment assessment agent.
You conduct a professional, human-like, adaptive interview for ONE specific job.

Rules you must always follow:
- Ask exactly ONE question at a time. Be concise, professional and neutral.
- SPOKEN INTERVIEW: every question is read aloud, so it must be ONE short conversational sentence of at most 25 words
  (no lists, no "including ... and ..." sub-parts). You may start with a 3-6 word acknowledgement of the answer.
  Ask for one detail at a time; the candidate can always be asked a follow-up next turn.
- Keep the analysis fields terse: at most 2 items per list, each under 12 words. Speed matters.
- The JOB REQUIREMENTS are the boundary. Only ask about the listed competencies and
  technologies. Never introduce a technology or topic that is not part of the job
  requirements or the candidate's own stated background, even if you know it.
- The candidate's data personalizes questions. Use only facts that are given; never
  invent experience, projects, employers or company requirements.
- Distinguish a CLAIM ("I used X") from DEMONSTRATED EVIDENCE (specific decisions,
  trade-offs, details, results). A bare claim is never "strong".
- Vague or very short answer -> ask a specific clarification.
  Claim without detail -> ask a follow-up for concrete detail.
  Detailed, correct answer -> move on to a different, not-yet-covered competency.
  Technically questionable answer -> ask a deeper verification/challenge question.
  Claim not supported by the resume -> ask a neutral evidence-verification question.
  A different but technically valid answer is valid; do not penalise unexpected wording.
- Never repeat a question that was already asked. Respect the turn limits.

HOW A GOOD INTERVIEWER THINKS (apply on every turn):
1. LISTEN FIRST. Quote or paraphrase the candidate's OWN words in your next question ("You mentioned Redis caching - ...").
   A generic question that ignores what was just said is a failure.
2. CLASSIFY THE LAST ANSWER, then react accordingly:
   - greeting / filler / "hello" / a question back ("can you tell me?") / "repeat the question" -> not an answer. Reply warmly, and
     re-ask the SAME topic in simpler, different, shorter words (next_action CLARIFICATION). Never re-send the identical wording.
   - "I don't know" / no experience / "can we skip" -> react like a kind human: one short warm line in your own words ("That's perfectly
     okay, no worries", "Fair enough, not everyone has touched that"), then MOVE ON to a DIFFERENT topic, ideally from their own resume where they can
     do well. Never press, re-ask or rephrase the same question after an "I don't know".
   - vague or a bare claim ("I used Spring Boot") -> ask for ONE concrete example: what exactly did YOU build or decide?
   - concrete but incomplete -> probe the missing piece: the reason (why this way), a trade-off, a failure/edge case, or a measurable result.
   - strong and specific -> acknowledge the specific detail, then go deeper (scale, testing, alternatives, what they would change) OR move on.
   - technically wrong or contradicting the resume -> a neutral, curious verification question, never an accusation.
3. PROBE LADDER per topic: example -> your role/decision -> why/trade-off -> result/metric -> what you would change.
   At most 2 follow-ups on a topic, then move to a competency that has no evidence yet. Never linger.
4. ADAPT DIFFICULTY: strong answers -> harder next question; weak answers -> easier, more concrete question.
5. GROUND EVERYTHING in the resume, the job requirements and the earlier answers; use the candidate's real project/employer names.
6. SOUND HUMAN: short natural acknowledgement, then the question. No lectures, no praise inflation, no repeating the whole answer back.
- Never ask about age, gender, religion, ethnicity, marital status, health, disability,
  nationality, politics or other protected personal characteristics.
- Never decide to hire or reject. Only report evidence for the human recruiter.
- The reason field is internal for HR and is never shown to the candidate.
- Output ONE JSON object in exactly the requested shape and nothing else.
""".strip()


def generate_llm_json(prompt: str) -> str:
    """The single seam between the brain and the model provider (mockable in tests)."""

    provider = get_interview_llm_provider()

    kwargs = {}

    # Groq counts the REQUESTED reply size against its per-minute token budget, so a turn asks for no more than it needs
    if isinstance(provider, HostedChatProvider):
        kwargs["max_tokens"] = int(os.getenv("AI_INTERVIEW_TURN_MAX_TOKENS") or "700")

    try:
        return provider.generate_json(prompt, system=INTERVIEWER_SYSTEM_PROMPT, **kwargs)
    except InterviewLLMUnavailableError as exc:
        # a per-minute rate limit clears within seconds: wait for it once (the message says "try again in 3.7s") instead of
        # dropping to the generic fallback question in the middle of a conversation
        text = str(exc)
        if isinstance(provider, HostedChatProvider) and ("429" in text or "rate_limit" in text.lower()):
            match = re.search(r"try again in ([0-9.]+)\s*(ms|s)", text, re.I)
            wait = 4.0
            if match:
                wait = float(match.group(1)) / (1000.0 if match.group(2).lower() == "ms" else 1.0)
            time.sleep(min(max(wait, 0.5) + 0.5, 9.0))
            return provider.generate_json(prompt, system=INTERVIEWER_SYSTEM_PROMPT, **kwargs)
        raise


def _llm_label() -> str:
    return get_interview_llm_provider().name


_REPAIR_NOTE = (
    "\n\nYour previous reply was rejected: {reason}. "
    "Reply again with ONLY the JSON object in the exact shape requested, "
    "using competency names exactly as listed. Allowed difficulty values: basic, intermediate, advanced. "
    "Allowed question types: INTRODUCTION, EXPERIENCE, TECHNICAL, SCENARIO, PROBLEM_SOLVING, "
    "RESUME_VERIFICATION, DEEP_DIVE, CHALLENGE, CLARIFICATION."
)


def _max_llm_attempts() -> int:
    """A malformed reply gets ONE constrained repair-note retry -- it demonstrably helps a capable model that made
    a one-off mistake."""

    return 2


def _ask_and_validate(prompt: str, parse, what: str):
    """
    Calls the model and validates with `parse` (which must raise
    ValueError/ValidationError on any structural or semantic problem).
    A malformed reply gets ONE constrained retry. An unreachable provider
    is NOT retried. Returns the validated object, or None so the
    caller falls back to the deterministic engine.
    """

    reason = ""

    for attempt in range(_max_llm_attempts()):

        try:
            raw_text = generate_llm_json(prompt + (_REPAIR_NOTE.format(reason=reason) if reason else ""))
            return parse(json.loads(raw_text))

        except InterviewLLMUnavailableError as exc:
            logger.warning("Interview LLM unavailable for %s (%s) -- using deterministic fallback.", what, exc)
            return None

        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("Interview LLM %s reply rejected on attempt %d (%s).", what, attempt + 1, type(exc).__name__)

    logger.warning("Interview LLM %s failed validation after retry -- using deterministic fallback.", what)
    return None


def _norm_question(text):
    return " ".join((text or "").lower().split()).rstrip("?. ")


_MAX_QUESTION_WORDS = 40      # asked at 25 in the prompt; a small margin before a reply is rejected as too long to say aloud


def _asked_questions(summary: str) -> list[str]:
    return re.findall(r'you asked "([^"]+)"', summary or "")


def _too_similar(a: str, b: str) -> bool:
    """A reworded copy of an earlier question (the interviewer looping) - not merely the same topic."""

    a, b = _norm_question(a), _norm_question(b)

    if not a or not b:
        return False

    return SequenceMatcher(None, a[:110], b[:110]).ratio() >= 0.82


_MIN_WORDS_FOR_STRONG = 15

_KIND_LINES = (
    "That's perfectly okay, no worries.",
    "Fair enough, not everyone has touched that.",
    "No problem at all, let's move on.",
    "That's alright, we'll try something different.",
    "Totally fine. Let's look at another area.",
)
_KIND_MARKERS = ("okay", "ok,", "fine", "no worries", "no problem", "that's alright", "that's ok", "fair enough", "all good", "understood", "no rush", "not a problem")
_PRESSING_ACTIONS = {"FOLLOW_UP", "CLARIFICATION", "DEEPER_PROBE", "CHALLENGE", "COUNTER_EXAMPLE"}


def _candidate_does_not_know(answer_text: str) -> bool:

    from backend.app.services.assessment.interview_delivery_v2 import analyze_delivery

    return "dont_know" in analyze_delivery(answer_text)["tags"]


def _presses_same_topic(decision, current_competency, blueprint, covered_competencies) -> bool:
    """True when, after an "I don't know", the next question stays on the same competency although others still need coverage."""

    if decision.next_action not in _PRESSING_ACTIONS:
        return False

    stays = (decision.next_competency or current_competency).lower() == current_competency.lower()
    others = [c for c in blueprint.competencies
              if c.name.lower() != current_competency.lower() and c.name.lower() not in covered_competencies]

    return stays and bool(others)


def _with_kind_line(decision, answer_text: str, turn_number: int):
    """After "I don't know" the next spoken line always starts with a short human reassurance (varied, never the same twice in a row)."""

    if not decision.next_question or not _candidate_does_not_know(answer_text):
        return decision

    lead = decision.next_question.lower()[:70]

    if any(marker in lead for marker in _KIND_MARKERS):
        return decision

    return decision.model_copy(update={"next_question": f"{_KIND_LINES[turn_number % len(_KIND_LINES)]} {decision.next_question}"})


_ACKNOWLEDGMENT_LINES = (
    "I see.",
    "Okay, understood.",
    "Alright, thanks for sharing that.",
    "Got it.",
    "I hear you.",
)
_ACKNOWLEDGMENT_MARKERS = ("i see", "okay,", "ok,", "understood", "got it", "alright", "i hear you", "thanks for sharing")


def _with_acknowledgment_line(decision, answer_text: str, turn_number: int):
    """
    The candidate DID attempt an answer, but it came out weak/unconvincing (strength_level "weak" or "none") --
    distinct from "I don't know" (_with_kind_line above already gives THAT case its own warmer reassurance). The
    next spoken line opens with one short, strictly NEUTRAL acknowledgment before the follow-up/next question --
    never a judgmental or corrective phrase ("that's wrong", "actually no", "not quite") -- matching this
    project's existing rule that the interviewer never sounds disappointed or evaluative toward the candidate.
    Varied per turn, never the same phrase twice in a row, same mechanism as _with_kind_line.
    """

    if not decision.next_question or _candidate_does_not_know(answer_text):
        return decision

    if decision.answer_analysis.strength_level not in ("weak", "none"):
        return decision

    lead = decision.next_question.lower()[:70]

    if any(marker in lead for marker in _ACKNOWLEDGMENT_MARKERS) or any(marker in lead for marker in _KIND_MARKERS):
        return decision

    return decision.model_copy(update={
        "next_question": f"{_ACKNOWLEDGMENT_LINES[turn_number % len(_ACKNOWLEDGMENT_LINES)]} {decision.next_question}",
    })
_MAX_TURNS_PER_COMPETENCY = 3

_DIFFICULTY_SYNONYMS = {
    "easy": "basic", "beginner": "basic", "simple": "basic", "low": "basic",
    "medium": "intermediate", "moderate": "intermediate", "mid": "intermediate",
    "hard": "advanced", "difficult": "advanced", "expert": "advanced", "high": "advanced",
}
_QUESTION_TYPES = {
    "INTRODUCTION", "EXPERIENCE", "TECHNICAL", "SCENARIO", "PROBLEM_SOLVING",
    "RESUME_VERIFICATION", "DEEP_DIVE", "CHALLENGE", "CLARIFICATION",
}

_PROBE_RUNGS = {"example", "role_or_decision", "reasoning_or_tradeoff", "result_or_metric", "challenge_or_change", "clarification"}
_PROBE_RUNG_SYNONYMS = {
    "role": "role_or_decision", "decision": "role_or_decision", "your_role": "role_or_decision",
    "reasoning": "reasoning_or_tradeoff", "tradeoff": "reasoning_or_tradeoff", "trade_off": "reasoning_or_tradeoff", "why": "reasoning_or_tradeoff",
    "result": "result_or_metric", "metric": "result_or_metric", "outcome": "result_or_metric",
    "challenge": "challenge_or_change", "change": "challenge_or_change", "counter_example": "challenge_or_change",
}


def _normalize_probe_focus(raw: dict) -> dict:
    """Same harmless-drift cleanup as _normalize_model_enums, kept separate: next_question_focus has its own value set
    and must never be run through the difficulty/question-type normalizer (that would corrupt it into "intermediate")."""

    if not isinstance(raw, dict):
        return raw

    raw = dict(raw)
    value = raw.get("next_question_focus")

    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "_").replace("-", "_")
        raw["next_question_focus"] = key if key in _PROBE_RUNGS else _PROBE_RUNG_SYNONYMS.get(key, "example")

    return raw


def _normalize_model_enums(raw: dict, difficulty_key: str, type_key: str) -> dict:
    """
    Deterministic clean-up of harmless enum drift ("medium" for
    "intermediate", an unlisted question type) BEFORE strict validation.
    It never touches next_action or competency names: those stay strict.
    """

    if not isinstance(raw, dict):
        return raw

    raw = dict(raw)

    d = raw.get(difficulty_key)
    if isinstance(d, str):
        d = d.strip().lower()
        raw[difficulty_key] = _DIFFICULTY_SYNONYMS.get(d, d if d in ("basic", "intermediate", "advanced") else "intermediate")

    t = raw.get(type_key)
    if isinstance(t, str):
        t = t.strip().upper().replace(" ", "_").replace("-", "_")
        raw[type_key] = t if t in _QUESTION_TYPES else "TECHNICAL"

    return raw


def _sanitize_analysis(analysis: AnswerAnalysisV2, answer_text: str) -> AnswerAnalysisV2:
    """
    JobPilot, not the model, decides what counts as demonstrated
    evidence: a short answer, or one with no concrete evidence,
    cannot be rated "strong".
    """

    if analysis.strength_level == "strong" and (
        len((answer_text or "").split()) < _MIN_WORDS_FOR_STRONG or not analysis.evidence
    ):
        return analysis.model_copy(update={"strength_level": "moderate"})

    return analysis


_MAX_SUMMARY_ENTRIES = 12
_MAX_STRUCTURED_ITEMS_PER_COMPETENCY = 5
_MEMORY_STRENGTH_RANK = {"strong": 3, "moderate": 2, "weak": 1, "none": 0}


def build_structured_memory_v2(prior_analyses: tuple[tuple[str, AnswerAnalysisV2], ...], current_competency: str) -> str:
    """
    build_conversation_summary() below already gives the model a readable
    prose recap, but it truncates every answer to 2 evidence items and 160
    characters, and has no separate concept of "unresolved gaps" at all --
    every prior turn's FULL, already-persisted AnswerAnalysisV2 (evidence/
    claims/missing_depth) is real structured data, not something that needs
    re-deriving, so this renders it directly instead of relying on a lossy
    paragraph. Kept ALONGSIDE conversation_summary, never a replacement for
    it -- the prose still carries conversational flow, this carries the
    precise gaps a follow-up should actually target.

    prior_analyses: (competency, AnswerAnalysisV2) for every ANSWERED turn so
    far, in turn order. current_competency gets full detail (evidence/claims/
    missing_depth); every other already-covered competency gets one compact
    line, so this never balloons the size of the summary the way a raw dump
    of every turn's full analysis would.
    """

    if not prior_analyses:
        return "(no prior turns yet)"

    by_competency: dict[str, list[AnswerAnalysisV2]] = {}

    for competency, analysis in prior_analyses:
        by_competency.setdefault(competency, []).append(analysis)

    lines: list[str] = []

    current = by_competency.pop(current_competency, None)

    if current:

        evidence, claims, missing_depth = [], [], []

        for analysis in current:
            evidence.extend(analysis.evidence)
            claims.extend(analysis.claims)
            missing_depth.extend(analysis.missing_depth)

        lines.append(f"Current competency '{current_competency}' -- accumulated so far:")
        lines.append(f"  Evidence given: {'; '.join(evidence[:_MAX_STRUCTURED_ITEMS_PER_COMPETENCY]) or 'none yet'}")
        lines.append(f"  Claims made: {'; '.join(claims[:_MAX_STRUCTURED_ITEMS_PER_COMPETENCY]) or 'none yet'}")
        lines.append(
            f"  UNRESOLVED GAPS (target these specifically, don't just ask something generic): "
            f"{'; '.join(missing_depth[:_MAX_STRUCTURED_ITEMS_PER_COMPETENCY]) or 'none recorded'}"
        )

    if by_competency:

        lines.append("Other competencies already covered this interview:")

        for competency, analyses in by_competency.items():

            best = max(analyses, key=lambda a: _MEMORY_STRENGTH_RANK.get(a.strength_level, 0))
            top_evidence = []

            for analysis in analyses:
                top_evidence.extend(analysis.evidence)

            lines.append(
                f"  {competency} [{best.strength_level}]: "
                f"{'; '.join(top_evidence[:2]) or 'no concrete evidence given'}"
            )

    return "\n".join(lines)


def build_conversation_summary(previous_summary: str, turn_number: int, question: str, answer: str, analysis: AnswerAnalysisV2) -> str:
    """
    Appends one compact line for the turn just completed. Never
    stores the full question/answer text verbatim beyond a short
    excerpt -- the summary is meant to be small and reusable, not
    a transcript archive (the full transcript already lives in
    AIInterviewTurnV2 rows for HR review).
    """

    answer_excerpt = (answer or "").strip().replace("\n", " ")
    if len(answer_excerpt) > 160:
        answer_excerpt = answer_excerpt[:157] + "..."

    evidence_bits = "; ".join(analysis.evidence[:2]) or "no concrete evidence given"

    question_excerpt = (question or "").strip().replace("\n", " ")
    if len(question_excerpt) > 110:
        question_excerpt = question_excerpt[:107] + "..."

    line = (
        f"Turn {turn_number} [{analysis.competency}, {analysis.strength_level}]: "
        f"you asked \"{question_excerpt}\"; candidate said \"{answer_excerpt}\" -> {evidence_bits}."
    )

    existing_lines = [ln for ln in previous_summary.split("\n") if ln.strip()]
    existing_lines.append(line)

    # Keep only the most recent entries -- old, already-superseded
    # turns don't need to keep costing prompt tokens.
    return "\n".join(existing_lines[-_MAX_SUMMARY_ENTRIES:])


def _question_focus(blueprint: InterviewBlueprintV2) -> str:
    """Where the model's own questions (the first four of the interview) must come from."""

    if getattr(blueprint, "role_shape", "technical") == "non_technical":
        subject = ("the projects, portfolio or past work on the candidate's resume or profile (campaigns, content, reports, client or "
                   "team work, results they delivered)")
    else:
        subject = ("the specific projects on the candidate's resume or profile (what they built, their own role, the technologies and "
                   "decisions they made)")

    return (
        "\nQUESTION FOCUS (your own questions, the first four of the interview)\n"
        f"Ask mainly about {subject}, and tie EVERY question to a requirement of THIS job (its required skills, responsibilities or "
        "technologies). Name the real project or piece of work from the resume, then ask how they handled something this job needs, for "
        "example: \"In your <project>, how did you handle <requirement from the job>?\". Do not ask generic textbook questions, and "
        "never invent a project the resume does not list: if none is listed, ask about the most relevant work they have done for this job."
    )


def _competency_lines(blueprint: InterviewBlueprintV2) -> str:

    lines = []

    for c in blueprint.competencies:

        status_bits = []
        if c.known_strength:
            status_bits.append(f"already shows strength (source: {c.source})")
        if c.known_weakness:
            status_bits.append(f"already shows weakness (source: {c.source})")
        if not status_bits:
            status_bits.append("no prior evidence")

        lines.append(f"- {c.name} (priority: {c.priority}, evidence_status: {c.evidence_status}): {', '.join(status_bits)}. Target: {c.target_evidence}")

    return "\n".join(lines) + "\n" + _question_focus(blueprint)


def _context_block(context: InterviewContextV2) -> str:

    programming_lines = "(no programming assessment evidence available)"
    if context.programming.available:
        programming_lines = (
            f"Strengths: {', '.join(context.programming.strengths) or 'none'}. "
            f"Weaknesses: {', '.join(context.programming.weaknesses) or 'none'}. "
            f"Insufficient evidence: {', '.join(context.programming.insufficient_evidence) or 'none'}. "
            f"Languages used: {', '.join(context.programming.languages_used) or 'none'}. "
            f"Highest difficulty passed: {context.programming.highest_difficulty_passed or 'none'}."
        )

    aptitude_lines = "(no aptitude evidence available)"
    if context.aptitude.available:
        aptitude_lines = (
            f"Score: {context.aptitude.percentage}%, passed: {context.aptitude.passed}. "
            f"Category results: {context.aptitude.category_results or 'none recorded'}."
        )

    job_extra = "".join(
        f"{label}: {value[:500]}\n" for label, value in (
            ("Responsibilities", context.job.responsibilities),
            ("Requirements", context.job.requirements),
            ("Preferred skills", context.job.preferred_skills),
            ("Technologies", context.job.technologies),
            ("Experience wanted", context.job.experience_required or ""),
            ("Employment / work mode", " / ".join(v for v in (context.job.employment_type, context.job.work_mode) if v)),
        ) if value
    )

    candidate_extra = "".join(
        f"{label}: {value[:500]}\n" for label, value in (
            ("Headline", context.candidate.headline or ""),
            ("About", context.candidate.about or ""),
            ("Profile experience", context.candidate.profile_experience or ""),
            ("Profile projects", context.candidate.profile_projects or ""),
            ("Certifications", context.candidate.certifications or ""),
            ("Achievements", context.candidate.achievements or ""),
            ("Preferred role", context.candidate.preferred_role or ""),
        ) if value
    )

    return f"""
JOB
Title: {context.job.title}
Company: {context.job.company}
Required skills: {context.job.required_skills}
Description: {context.job.description[:600]}
{job_extra}
CANDIDATE
Name: {context.candidate.full_name or '(not provided)'}
Experience level: {context.candidate.experience_level or '(not specified)'}
Education: {context.candidate.education or '(not specified)'}
Skills (resume + profile): {', '.join(context.candidate.skills) or '(none extracted)'}
Resume summary: {context.candidate.resume_summary or '(not available)'}
Resume experience/projects: {'; '.join(context.candidate.resume_projects_or_experience) or '(none extracted)'}
{candidate_extra}
APTITUDE EVIDENCE
{aptitude_lines}

PROGRAMMING EVIDENCE
{programming_lines}
""".strip()


# ------------------------------------------------------------
# OPENING
# ------------------------------------------------------------

def _opening_prompt(context: InterviewContextV2, blueprint: InterviewBlueprintV2) -> str:

    return f"""
You are an experienced, courteous technical/recruitment interviewer
conducting a real interview for the role below. Generate a natural,
human-sounding OPENING for this interview: a brief greeting that
shows you have reviewed the candidate's real background (only using
facts given below -- never invent a project, employer, or skill not
listed), followed by ONE specific first question.

Ground the first question in something REAL from the candidate's
resume/skills if available (e.g. a named project or technology from
their resume experience). If no usable resume detail exists, ask a
general but role-relevant opening question instead -- do not invent
a project to ask about.

{_context_block(context)}

INTERVIEW COMPETENCIES TO EVENTUALLY COVER
{_competency_lines(blueprint)}

Return STRICT JSON only, no markdown fences, in exactly this shape:
{{
  "opening_message": "1-3 sentence natural greeting",
  "first_question": "one specific, real question, ONE sentence of at most 25 words (it is spoken aloud)",
  "competency": "the name of ONE competency from the list above this question targets",
  "difficulty": "basic|intermediate|advanced",
  "question_type": "INTRODUCTION|EXPERIENCE|TECHNICAL"
}}
""".strip()


def _fallback_opening(context: InterviewContextV2, blueprint: InterviewBlueprintV2) -> InterviewOpeningV2:

    first_competency = blueprint.competencies[0]

    if context.candidate.resume_projects_or_experience:

        project_hint = context.candidate.resume_projects_or_experience[0]

        question = (
            f"I see from your background: \"{project_hint}\". "
            "Can you walk me through what you personally worked on there?"
        )
        question_type = "EXPERIENCE"

    else:

        if blueprint.role_shape == "non_technical":
            question = (
                f"To start, could you walk me through a project or piece of work from your portfolio that is most relevant "
                f"to this {context.job.title} role?"
            )
        else:
            question = (
                f"To start, can you tell me about your experience relevant to "
                f"{first_competency.name} for this {context.job.title} role?"
            )
        question_type = "INTRODUCTION"

    greeting = (
        f"Hi, thanks for joining. I've reviewed your application for the "
        f"{context.job.title} role at {context.job.company}. I'd like to "
        "understand your background and then explore a few relevant "
        "situations for this role."
    )

    return InterviewOpeningV2(
        opening_message=greeting,
        first_question=question,
        competency=first_competency.name,
        difficulty="basic",
        question_type=question_type,
    )


def generate_opening_v2(context: InterviewContextV2, blueprint: InterviewBlueprintV2, use_llm: bool = True) -> tuple[InterviewOpeningV2, str]:
    """Returns (opening, generated_by) -- generated_by is the provider name or "fallback"."""

    if use_llm:

        valid_competency_names = {c.name.lower() for c in blueprint.competencies}

        def _parse(raw):
            opening = InterviewOpeningV2.model_validate(_normalize_model_enums(raw, "difficulty", "question_type"))
            if opening.competency.lower() not in valid_competency_names:
                raise ValueError(f"competency {opening.competency!r} is not in the interview blueprint")
            if not opening.first_question.strip():
                raise ValueError("first_question is empty")
            return opening

        opening = _ask_and_validate(_opening_prompt(context, blueprint), _parse, "opening")

        if opening is not None:
            return opening, _llm_label()

    return _fallback_opening(context, blueprint), "fallback"


# ------------------------------------------------------------
# TURN DECISION (analyze answer -> decide next action/question)
# ------------------------------------------------------------

_TURN_ACTIONS_DOC = """
DIRECT_NEXT_TOPIC - move straight to a new, unrelated competency.
FOLLOW_UP - ask a natural, direct follow-up on the SAME topic to get more detail.
DEEPER_PROBE - the candidate showed strength; go deeper (architecture/edge cases/why).
CLARIFICATION - the answer was ambiguous or too short; ask them to clarify.
CHALLENGE - present a harder variant or edge case to test depth (never trick questions).
SCENARIO - pose a realistic job-relevant scenario for reasoning.
COUNTER_EXAMPLE - ask what they would do differently / what could go wrong.
RESUME_VERIFICATION - probe a specific resume claim for supporting detail.
MOVE_ON - this competency has enough evidence; move to the next unassessed competency.
END_INTERVIEW - enough evidence has been gathered across required competencies, or bounds reached.
""".strip()


def _representative_code_note(context: InterviewContextV2, current_competency: str) -> str:
    """
    Surfaces the candidate's OWN submitted-code excerpt from the programming
    assessment (context.programming.representative_snippet) ONLY when its
    language actually matches the competency being probed right now -- a
    real, specific, hard-to-fake follow-up ("why a TreeMap here?") instead of
    a generic "you showed strong Java skills", but never forced into a turn
    it has nothing to do with.
    """

    snippet = context.programming.representative_snippet

    if not snippet or not isinstance(snippet, dict):
        return ""

    language = (snippet.get("language") or "").strip().lower()

    if not language or language not in current_competency.lower():
        return ""

    return (
        f'\nTHE CANDIDATE\'S OWN SUBMITTED CODE (programming assessment, question "{snippet.get("question_title", "")}", '
        f'{snippet.get("language", "")}) -- you may reference a SPECIFIC detail from it for a sharp, hard-to-fake '
        f'follow-up (e.g. why a particular structure/approach was chosen), but never quote it back verbatim at length:\n'
        f'{(snippet.get("code_excerpt") or "")[:800]}\n'
    )


def _turn_prompt(
    context: InterviewContextV2,
    blueprint: InterviewBlueprintV2,
    conversation_summary: str,
    current_competency: str,
    current_difficulty: str,
    last_question: str,
    answer_text: str,
    turn_number: int,
    min_turns: int,
    max_turns: int,
    used_probe_rungs: frozenset[str] = frozenset(),
    prior_analyses: tuple[tuple[str, AnswerAnalysisV2], ...] = (),
) -> str:

    return f"""
You are continuing a real, ongoing technical/recruitment interview.
Analyze the candidate's LAST answer, then decide what happens next.
Be a good interviewer: listen, remember what was already established
(see CONVERSATION SO FAR), avoid repeating a question already asked,
and probe for real depth rather than accepting a shallow answer at
face value. Never invent evidence the candidate did not actually
provide. Never be confrontational -- use neutral, professional
language even when challenging a claim.

{_context_block(context)}

INTERVIEW COMPETENCIES
{_competency_lines(blueprint)}

CONVERSATION SO FAR (compact summary, most recent last)
{conversation_summary or '(this is the first answer)'}

STRUCTURED EVIDENCE SO FAR (the real, already-recorded evidence/claims/gaps behind the summary above -- prefer
targeting an UNRESOLVED GAP for the current competency over a generic follow-up when one is listed)
{build_structured_memory_v2(prior_analyses, current_competency)}

CURRENT TURN
Turn number: {turn_number} (minimum {min_turns}, maximum {max_turns} turns total for this interview)
Competency being assessed: {current_competency}
Current difficulty: {current_difficulty}
Question just asked: {last_question}
Candidate's answer: {answer_text}
{_representative_code_note(context, current_competency)}
Probe-ladder rungs ALREADY used for '{current_competency}' in this conversation: {', '.join(sorted(used_probe_rungs)) or 'none yet'}.
If you stay on '{current_competency}', next_question_focus MUST be a rung not in that list (a rewording of an already-used
rung is a real repeated question, even in very different words) -- otherwise move to a different competency instead.

POSSIBLE next_action VALUES AND WHEN TO USE THEM
{_TURN_ACTIONS_DOC}

Choose END_INTERVIEW only if turn_number >= {min_turns} AND either
every high-priority competency has real evidence, or turn_number is
approaching {max_turns}.

Return STRICT JSON only, no markdown fences, in exactly this shape:
{{
  "answer_analysis": {{
    "competency": "{current_competency}",
    "strengths": ["..."],
    "weaknesses": ["..."],
    "evidence": ["..."],
    "claims": ["..."],
    "missing_depth": ["..."],
    "strength_level": "strong|moderate|weak|none"
  }},
  "next_action": "one of the values above",
  "reason": "one short sentence, HR-internal only, never shown to the candidate",
  "next_competency": "competency name for the next question (may be the same one)",
  "next_difficulty": "basic|intermediate|advanced",
  "next_question": "the exact next question to ask, or null if next_action is END_INTERVIEW",
  "next_question_type": "INTRODUCTION|EXPERIENCE|TECHNICAL|SCENARIO|PROBLEM_SOLVING|RESUME_VERIFICATION|DEEP_DIVE|CHALLENGE|CLARIFICATION",
  "next_question_focus": "example|role_or_decision|reasoning_or_tradeoff|result_or_metric|challenge_or_change|clarification"
}}

next_question_focus is which rung of the probe ladder this question targets (only meaningful when next_competency stays
the SAME as the current one -- pick "example" for a new competency). NEVER pick a rung you already used for THIS SAME
competency earlier in the conversation (see CONVERSATION SO FAR): asking for "an example" a second time in different
words is a real repeated question even though the wording differs, and JobPilot will reject it. Move to the next unused
rung, or to "clarification" only when the last answer was genuinely unclear.
""".strip()


@dataclass(frozen=True)
class _FallbackDecisionInputs:

    blueprint: InterviewBlueprintV2
    current_competency: str
    current_difficulty: str
    answer_text: str
    turn_number: int
    min_turns: int
    max_turns: int
    follow_ups_on_current: int
    mentioned_terms: tuple = ()          # skills/technologies/projects from the context that the candidate's answer actually names


def _mentioned_terms(context: InterviewContextV2, answer_text: str) -> tuple:
    """
    Deterministic grounding for the no-LLM path: which of the candidate's/job's KNOWN
    skills, technologies or project names does this answer literally mention? (Only names
    already present in the context -- nothing is ever invented.)
    """

    text = (answer_text or "").lower()

    if not text.strip():
        return ()

    candidates: list[str] = list(context.candidate.skills)

    for source in (context.job.required_skills, context.job.technologies, context.job.preferred_skills):
        candidates += [s.strip() for s in (source or "").split(",") if s.strip()]

    for item in context.candidate.resume_projects_or_experience:
        head = item.split(":")[0].split(" - ")[0].split("(")[0].strip()
        if 3 <= len(head) <= 60:
            candidates.append(head)

    found: list[str] = []
    seen: set[str] = set()

    for name in candidates:
        key = name.strip().lower()
        if len(key) >= 2 and key not in seen and re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text):
            seen.add(key)
            found.append(name.strip())

    return tuple(found[:3])


def _classify_answer_fallback(answer_text: str) -> tuple[str, list[str], list[str]]:
    """
    A deliberately simple, honest heuristic used ONLY when the model is
    unavailable: length/content is not a real competence judgment,
    so the fallback never claims "strong"/"weak" — only "moderate"
    (some real content given) or "none" (effectively no answer) —
    and every list is genuinely derived from the text, never
    invented.
    """

    stripped = (answer_text or "").strip()

    if len(stripped) < 15:
        return "none", [], ["answer was too short to assess"]

    return "moderate", [f"candidate provided a {len(stripped.split())}-word response"], []


def _grounded_follow_up_question(mentioned: tuple, competency: str) -> str:
    """A follow-up that points at what the candidate literally just said (no model involved)."""

    if not mentioned:
        return f"Can you tell me more about that -- specifically, how did you approach {competency.lower()} in practice?"

    names = " and ".join(mentioned[:2])

    return (
        f"You mentioned {names}. Can you walk me through a specific decision you made there, "
        "the hardest problem you hit, and how you solved it?"
    )


def _next_unassessed_competency(blueprint: InterviewBlueprintV2, covered: set[str]) -> str | None:

    for c in sorted(blueprint.competencies, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.priority]):
        if c.name.lower() not in covered:
            return c.name

    return None


def _fallback_turn_decision(
    inputs: _FallbackDecisionInputs,
    covered_competencies: set[str],
) -> InterviewTurnDecisionV2:

    strength_level, evidence, missing_depth = _classify_answer_fallback(inputs.answer_text)

    if inputs.mentioned_terms:
        evidence = evidence + [f"candidate named: {', '.join(inputs.mentioned_terms)}"]

    analysis = AnswerAnalysisV2(
        competency=inputs.current_competency,
        strengths=[],
        weaknesses=[],
        evidence=evidence,
        claims=[],
        missing_depth=missing_depth,
        strength_level=strength_level,
    )

    at_bounds = inputs.turn_number >= inputs.max_turns
    enough_on_current = inputs.follow_ups_on_current >= 1 or strength_level == "none"

    # Coverage is checked BEFORE deciding to follow up -- if this
    # turn's own competency is the LAST unassessed one and we've
    # already met the minimum length, ending here (once every
    # competency has at least one turn of evidence) takes priority
    # over squeezing in one more follow-up on the current topic.
    covered_after_this_turn = set(covered_competencies) | {inputs.current_competency.lower()}
    next_competency = _next_unassessed_competency(inputs.blueprint, covered_after_this_turn)

    if at_bounds:

        return InterviewTurnDecisionV2(
            answer_analysis=analysis,
            next_action="END_INTERVIEW",
            reason="Maximum interview length reached.",
            next_question=None,
        )

    if inputs.turn_number >= inputs.min_turns and next_competency is None:

        return InterviewTurnDecisionV2(
            answer_analysis=analysis,
            next_action="END_INTERVIEW",
            reason="All required competencies have at least one turn of evidence.",
            next_question=None,
        )

    if not enough_on_current and strength_level != "none":

        return InterviewTurnDecisionV2(
            answer_analysis=analysis,
            next_action="FOLLOW_UP",
            reason="Gathering one more detail on the current competency (fallback mode).",
            next_competency=inputs.current_competency,
            next_difficulty=inputs.current_difficulty,
            next_question=_grounded_follow_up_question(inputs.mentioned_terms, inputs.current_competency),
            next_question_type="CLARIFICATION" if not inputs.mentioned_terms else "DEEP_DIVE",
        )

    if next_competency is None:

        return InterviewTurnDecisionV2(
            answer_analysis=analysis,
            next_action="END_INTERVIEW",
            reason="No further unassessed competencies remain.",
            next_question=None,
        )

    return InterviewTurnDecisionV2(
        answer_analysis=analysis,
        next_action="MOVE_ON",
        reason="Moving to the next unassessed competency (fallback mode).",
        next_competency=next_competency,
        next_difficulty="basic",
        next_question=f"Let's talk about {next_competency}. Can you describe your practical experience with it for a role like this?",
        next_question_type="TECHNICAL",
    )


def analyze_and_decide_next_v2(
    context: InterviewContextV2,
    blueprint: InterviewBlueprintV2,
    conversation_summary: str,
    current_competency: str,
    current_difficulty: str,
    last_question: str,
    answer_text: str,
    turn_number: int,
    min_turns: int,
    max_turns: int,
    covered_competencies: set[str],
    follow_ups_on_current: int,
    use_llm: bool = True,
    used_probe_rungs: frozenset[str] = frozenset(),
    prior_analyses: tuple[tuple[str, AnswerAnalysisV2], ...] = (),
) -> tuple[InterviewTurnDecisionV2, str]:
    """Returns (decision, generated_by). used_probe_rungs: the probe-ladder rungs already targeted for
    current_competency earlier in this conversation (see ProbeRungV2) -- reusing one is a real repeated question even
    when the model rewords it enough that plain text-similarity checks (_too_similar) do not catch it.
    prior_analyses: (competency, AnswerAnalysisV2) for every answered turn so far -- see build_structured_memory_v2()."""

    if use_llm:

        valid_competency_names = {c.name.lower() for c in blueprint.competencies}

        def _parse(raw):
            raw = dict(raw)
            if isinstance(raw.get("next_action"), str):
                raw["next_action"] = raw["next_action"].strip().upper()
            decision = InterviewTurnDecisionV2.model_validate(
                _normalize_probe_focus(_normalize_model_enums(raw, "next_difficulty", "next_question_type"))
            )

            if decision.next_action != "END_INTERVIEW":
                if decision.next_competency and decision.next_competency.lower() not in valid_competency_names:
                    raise ValueError(f"next_competency {decision.next_competency!r} is not in the interview blueprint")
                if not decision.validate_question_present():
                    raise ValueError("next_question is required unless next_action is END_INTERVIEW")
                if _norm_question(decision.next_question) == _norm_question(last_question):
                    raise ValueError("next_question repeats the question that was just asked")
                if _candidate_does_not_know(answer_text) and _presses_same_topic(decision, current_competency, blueprint, covered_competencies):
                    raise ValueError(
                        "The candidate said they do not know: do not press or re-ask. Say one short kind line and move to a DIFFERENT competency/topic "
                        "(next_action DIRECT_NEXT_TOPIC or MOVE_ON)."
                    )
                if len(decision.next_question.split()) > _MAX_QUESTION_WORDS:
                    raise ValueError(f"next_question is too long to say aloud: use at most 25 words, one sentence")
                for asked in _asked_questions(conversation_summary) + [last_question]:
                    if _too_similar(decision.next_question, asked):
                        raise ValueError("next_question is nearly the same as a question already asked: ask about something different or in clearly different words")

                stays_on_competency = (decision.next_competency or current_competency).lower() == current_competency.lower()
                if (
                    stays_on_competency
                    and decision.next_question_focus != "clarification"
                    and decision.next_question_focus in used_probe_rungs
                ):
                    raise ValueError(
                        f"next_question_focus '{decision.next_question_focus}' was already used for '{current_competency}' earlier in this "
                        f"conversation (a rewording of the same underlying question, not a real new question). Pick a different, unused rung "
                        f"of the probe ladder for this competency, or set next_competency to a different one."
                    )

            if decision.next_action == "END_INTERVIEW" and turn_number < min_turns:
                raise ValueError(f"END_INTERVIEW is not allowed before turn {min_turns}")

            # Coverage is JobPilot's job, not the model's: one competency may
            # take at most _MAX_TURNS_PER_COMPETENCY turns while others are
            # still uncovered.
            if decision.next_action != "END_INTERVIEW":
                uncovered = [
                    c.name for c in blueprint.competencies
                    if c.name.lower() not in covered_competencies and c.name.lower() != current_competency.lower()
                ]
                stays = (decision.next_competency or current_competency).lower() == current_competency.lower()
                if uncovered and stays and follow_ups_on_current + 1 >= _MAX_TURNS_PER_COMPETENCY:
                    raise ValueError(
                        f"'{current_competency}' has already had {follow_ups_on_current + 1} turns; "
                        f"set next_competency to one of these not-yet-covered competencies: {', '.join(uncovered)}"
                    )

            return decision.model_copy(update={
                "answer_analysis": _sanitize_analysis(decision.answer_analysis, answer_text),
            })

        decision = _ask_and_validate(
            _turn_prompt(
                context, blueprint, conversation_summary, current_competency,
                current_difficulty, last_question, answer_text, turn_number,
                min_turns, max_turns, used_probe_rungs, prior_analyses,
            ),
            _parse,
            "turn decision",
        )

        if decision is not None:
            decision = _with_kind_line(decision, answer_text, turn_number)
            decision = _with_acknowledgment_line(decision, answer_text, turn_number)
            return decision, _llm_label()

    inputs = _FallbackDecisionInputs(
        blueprint=blueprint,
        current_competency=current_competency,
        current_difficulty=current_difficulty,
        answer_text=answer_text,
        turn_number=turn_number,
        min_turns=min_turns,
        max_turns=max_turns,
        follow_ups_on_current=follow_ups_on_current,
        mentioned_terms=_mentioned_terms(context, answer_text),
    )

    fallback = _fallback_turn_decision(inputs, covered_competencies)

    # the deterministic engine also answers an "I don't know" kindly and moves on (it never presses)
    if _candidate_does_not_know(answer_text) and _presses_same_topic(fallback, current_competency, blueprint, covered_competencies):
        others = [c for c in blueprint.competencies
                  if c.name.lower() != current_competency.lower() and c.name.lower() not in covered_competencies]
        fallback = fallback.model_copy(update={
            "next_action": "DIRECT_NEXT_TOPIC", "next_competency": others[0].name,
            "next_question": f"Let's talk about {others[0].name}. Can you tell me about your practical experience with it for a role like this?",
        })

    fallback = _with_kind_line(fallback, answer_text, turn_number)
    fallback = _with_acknowledgment_line(fallback, answer_text, turn_number)
    return fallback, "fallback"

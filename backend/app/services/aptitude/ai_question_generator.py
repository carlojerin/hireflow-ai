import json
import logging
import os
import random
import re
import time
import uuid

from backend.app.services.assessment.role_classification import job_role_shape

logger = logging.getLogger("jobpilot.aptitude")


# ============================================================
# JOBPILOT AI - AI-GENERATED APTITUDE QUESTIONS (Groq)
#
# An IT-company style aptitude test with three core areas --
# quantitative aptitude, logical reasoning, verbal ability --
# generated per candidate by Groq (openai/gpt-oss-120b, through the
# existing HostedChatProvider) so no two candidates get the same set.
#
# Difficulty and format follow the candidate's EXPERIENCE LEVEL:
#   fresher (0-1 yr)   medium-high, general aptitude only
#   junior  (1-3 yrs)  easier general aptitude + pseudo-code /
#                      debugging / basic system-logic questions
#   senior  (3+ yrs)   general aptitude reduced to a few basic
#                      questions; mostly advanced technical logic
#
# This module NEVER scores anything and NEVER decides pass/fail; it only produces a
# question set in the shape scoring.py grades. Any failure raises RuntimeError so the
# caller can fall back to the fixed question bank. There is NO fallback to another
# AI provider.
# ============================================================

REQUIRED_OPTION_COUNT = 4
REQUIRED_QUESTION_COUNT = 10

GENERATION_ATTEMPTS = 3                      # only the still-missing questions are regenerated on a retry
GENERATION_TIMEOUT_SECONDS = 45
GENERATION_DEADLINE_SECONDS = 50              # total budget for one candidate's generation (HR / candidate waits for it)
GENERATION_MAX_TOKENS = 5000                 # reasoning models spend part of this on hidden reasoning
RATE_LIMIT_BACKOFF_SECONDS = 8.0             # Groq free tier limits tokens per minute: wait before the next attempt
VERIFY_ANSWER_KEYS = True                    # a second, independent pass solves every question; disagreements are regenerated
                                             # (APTITUDE_VERIFY_KEYS=true|false overrides; default: on for groq, off for the small local model,
                                             #  which disputed 8 of 10 questions and made generation take minutes)
GENERATION_TEMPERATURE = 0.9                 # variety between candidates
FALLBACK_MODEL = "openai/gpt-oss-120b"

CATEGORIES = (
    "quantitative_aptitude",
    "logical_reasoning",
    "verbal_reasoning",
    "technical_logic",
)

VALID_DIFFICULTIES = {"basic", "intermediate", "advanced"}


# ------------------------------------------------------------
# Experience band -> question plan
# ------------------------------------------------------------

BAND_PLANS = {
    "fresher": {
        "quantitative_aptitude": 4, "logical_reasoning": 3, "verbal_reasoning": 3,
    },
    "junior": {
        "quantitative_aptitude": 2, "logical_reasoning": 2, "verbal_reasoning": 2, "technical_logic": 4,
    },
    "senior": {
        "quantitative_aptitude": 1, "logical_reasoning": 1, "verbal_reasoning": 1, "technical_logic": 7,
    },
}

# For a NON-technical role (role_classification.job_role_shape(job) == "non_technical" --
# HR, Sales, Marketing, Finance, Recruitment Coordinator, etc.) -- "technical_logic" means
# pseudo-code/debugging/Python questions, which have no place in their aptitude test at all
# (this was a real bug: a Talent Acquisition Coordinator candidate was shown Python recursion,
# conditional logic, time complexity and "find the bug" questions purely because BAND_PLANS
# above keys technical_logic off EXPERIENCE level, never off whether the role is technical).
# The technical_logic slots are redistributed into the three general-aptitude categories
# instead, same total question count, same experience-based difficulty progression.
NON_TECHNICAL_BAND_PLANS = {
    "fresher": {
        "quantitative_aptitude": 4, "logical_reasoning": 3, "verbal_reasoning": 3,
    },
    "junior": {
        "quantitative_aptitude": 3, "logical_reasoning": 4, "verbal_reasoning": 3,
    },
    "senior": {
        "quantitative_aptitude": 3, "logical_reasoning": 4, "verbal_reasoning": 3,
    },
}

BAND_DIFFICULTY_TEXT = {
    "fresher": (
        "FRESHER (0-1 year). Difficulty is MEDIUM TO HIGH: about 60% 'intermediate' and 40% 'advanced'. "
        "Focus strictly on speed and accuracy in maths, logic and English. No programming questions."
    ),
    "junior": (
        "JUNIOR PROFESSIONAL (1-3 years). Difficulty is MEDIUM. The general aptitude questions "
        "(quantitative_aptitude, logical_reasoning, verbal_reasoning) are EASIER: mostly 'basic', "
        "a few 'intermediate'. The technical_logic questions are 'intermediate': pseudo-code output "
        "tracing, spotting the bug in a short code snippet, and basic system logic."
    ),
    "senior": (
        "SENIOR PROFESSIONAL (3+ years). General aptitude is largely waived: the few "
        "quantitative_aptitude, logical_reasoning and verbal_reasoning questions are all 'basic'. "
        "The technical_logic questions are 'advanced': tracing tricky pseudo-code, finding subtle bugs, "
        "complexity reasoning and system-design style logic (caching, queues, consistency, API behaviour)."
    ),
}

SUBTOPICS = {
    "quantitative_aptitude": [
        "percentages", "profit and loss", "ratio and proportion", "time and work", "speed, distance and time",
        "simple and compound interest", "averages", "mixtures and alligation", "number systems and remainders",
        "permutations and combinations", "basic probability", "ages", "pipes and cisterns",
        "boats and streams", "data interpretation from a small table given in the question text",
    ],
    "logical_reasoning": [
        "number series", "letter series", "coding-decoding", "blood relations", "syllogisms", "ranking and order by comparison",
        "direction sense", "analogies", "statement and conclusion", "odd one out", "calendar and clocks",
    ],
    "verbal_reasoning": [
        "synonyms", "antonyms", "sentence correction", "reading comprehension with a short passage in the question",
        "fill in the blank", "one-word substitution", "idioms and phrases", "spotting the grammatical error",
        "sentence completion",
    ],
    "technical_logic": [
        "pseudo-code output tracing with loops", "finding the bug in a short function", "time-complexity reasoning",
        "basic system logic (queue/stack/cache behaviour)", "boolean and conditional logic", "SQL query reasoning",
        "recursion tracing", "choosing the right data structure", "HTTP/API behaviour reasoning",
        "off-by-one and edge-case debugging",
    ],
}


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_DATE = r"(?:(?P<{m}>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+)?(?P<{y}>(?:19|20)\d\d)"
_RANGE = re.compile(
    _DATE.format(m="m1", y="y1") + r"\s*(?:-|\u2013|\u2014|to)\s*(?:" + _DATE.format(m="m2", y="y2") + r"|(?P<now>present|current|now|till date|ongoing))",
    re.I,
)


def years_from_experience_text(text, today=None) -> float:
    """
    Years of work experience from the date ranges of the EXPERIENCE section ("June 2024 - Present", "2021 - 2023").
    Overlapping ranges are merged; education is never passed in. 0.0 when no range is found.
    """

    from datetime import date

    today = today or date.today()
    spans = []

    for match in _RANGE.finditer(str(text or "")):

        start = int(match.group("y1")) * 12 + (MONTHS[match.group("m1").lower()[:3]] if match.group("m1") else 1)

        if match.group("now"):
            end = today.year * 12 + today.month
        else:
            end = int(match.group("y2")) * 12 + (MONTHS[match.group("m2").lower()[:3]] if match.group("m2") else 12)

        if end >= start:
            spans.append((start, end))

    spans.sort()
    merged = []

    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    return sum(end - start for start, end in merged) / 12.0


def resolve_experience_band(*hints, experience_text=None) -> str:
    """
    Map the candidate's experience information to fresher / junior / senior.

    Each hint is free text (resume-analysis level such as "Fresher / Entry Level / Mid Level /
    Senior Level", or a summary containing "2 years of experience"). A number of years found in
    the text wins over a label; with no information the candidate is treated as a fresher.
    """

    text = " ".join(str(h) for h in hints if h).lower()

    years = [
        float(match)
        for match in re.findall(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", text)
    ]

    # the experience section's own dates count too (an analysis label like "Entry Level" can hide 2 years of work)
    from_dates = years_from_experience_text(experience_text)

    if from_dates > 0:
        years.append(from_dates)

    if years:
        top = max(years)
        if top < 1:
            return "fresher"
        if top < 3:
            return "junior"
        return "senior"

    if re.search(r"senior|lead|principal|architect|staff", text):
        return "senior"

    if re.search(r"mid|junior|intermediate|associate", text):
        return "junior"

    return "fresher"


def _scaled_plan(band: str, total: int, is_technical: bool = True) -> dict:
    """The band's category counts scaled (largest remainder) to `total` questions."""

    base = BAND_PLANS[band] if is_technical else NON_TECHNICAL_BAND_PLANS[band]
    base_total = sum(base.values())

    if total == base_total:
        return dict(base)

    raw = {k: v * total / base_total for k, v in base.items()}
    plan = {k: int(v) for k, v in raw.items()}
    leftover = total - sum(plan.values())

    for key in sorted(raw, key=lambda k: raw[k] - plan[k], reverse=True)[:leftover]:
        plan[key] += 1

    return plan


def _safe_json_list(raw_value) -> list:

    if not raw_value:
        return []

    if isinstance(raw_value, list):
        return raw_value

    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


# ------------------------------------------------------------
# Prompt
# ------------------------------------------------------------

def _pick_subtopics(plan: dict, rng: random.Random) -> list[tuple[str, str]]:
    """One (category, subtopic) per question, sampled per candidate so every set differs."""

    slots = []

    for category, count in plan.items():
        pool = SUBTOPICS[category]
        chosen = rng.sample(pool, count) if count <= len(pool) else [rng.choice(pool) for _ in range(count)]
        slots.extend((category, topic) for topic in chosen)

    rng.shuffle(slots)
    return slots


def _build_prompt(job, user, profile, resume_analysis, band: str, slots: list, variation: str) -> str:

    plan = {}
    for category, _topic in slots:
        plan[category] = plan.get(category, 0) + 1

    job_title = getattr(job, "title", "") or ""
    required_skills = getattr(job, "required_skills", "") or ""
    technologies = getattr(job, "technologies", "") or ""

    candidate_skills = []

    if resume_analysis:
        candidate_skills.extend(_safe_json_list(resume_analysis.skills))

    if profile and profile.skills:
        candidate_skills.extend(s.strip() for s in str(profile.skills).split(",") if s.strip())

    slot_lines = "\n".join(
        f"{index}. category={category}; subtopic={topic}"
        for index, (category, topic) in enumerate(slots, start=1)
    )

    job_requirements = getattr(job, "requirements", "") or getattr(job, "description", "") or ""

    role_context = f"""
ROLE CONTEXT (use this to make quantitative/logical/verbal scenarios feel grounded in this
candidate's actual role -- e.g. word-problem settings, vocabulary, and verbal-reasoning passages
drawn from this field -- WITHOUT changing what skill each question actually tests; a quantitative
question stays a quantitative question, just set in a scenario this candidate would recognize):
JOB TITLE: {job_title or "Not specified"}
JOB REQUIREMENT / DESCRIPTION: {(job_requirements or "Not specified")[:600]}
CANDIDATE SKILLS (from their resume): {", ".join(dict.fromkeys(candidate_skills)) or "Not specified"}
"""

    technical_context = ""

    if "technical_logic" in plan:
        technical_context = f"""
For technical_logic questions use the role's real stack so the questions are relevant:
REQUIRED SKILLS: {required_skills or "Not specified"}
TECHNOLOGIES: {technologies or "Not specified"}
Write short code / pseudo-code (at most 8 lines, use \\n for line breaks) in Python or the job's main
language. Every technical_logic question must have exactly one defensible answer.
"""

    total = sum(plan.values())

    return f"""
You are an expert IT-company recruiter writing a pre-employment APTITUDE test.

Write EXACTLY {total} multiple-choice questions in the three core aptitude areas used by IT companies:
quantitative aptitude, logical reasoning and verbal ability{" plus technical logic" if "technical_logic" in plan else ""}.

CANDIDATE LEVEL AND DIFFICULTY:
{BAND_DIFFICULTY_TEXT[band]}

QUESTION SLOTS (write exactly one question per slot, in this order; use the slot's category and subtopic):
{slot_lines}

VARIATION CODE: {variation}
This test is generated separately for every candidate. Use the variation code to choose fresh numbers, names,
scenarios and wording so the questions differ from any standard question bank. Do not reuse textbook
examples such as "15% of 200" or "5 machines 5 widgets".
{role_context}{technical_context}
RULES:
- Each question has EXACTLY {REQUIRED_OPTION_COUNT} options, all different, and exactly one correct option.
- correct_answer must be an EXACT copy of the correct option's text. Work the answer out carefully before
  writing it: a wrong answer key is a serious defect.
- Questions are self-contained, unambiguous and answerable without a calculator or the internet.
- No two questions may test the same idea or share near-identical wording.
- Never write seating/ordering/arrangement puzzles or syllogisms whose options are "valid / invalid but possible / undetermined": their
  answers are ambiguous. Every question must have exactly one objectively correct option that a solver can prove.
- Set "difficulty" to exactly one of "basic", "intermediate", "advanced", following the level rules above.
- "explanation" is one sentence showing why the correct answer is correct.
- "category" must equal the slot's category exactly.
- Never use markdown, backticks or language labels; write code as plain text lines inside the question.

Return ONLY valid JSON in exactly this structure, with no markdown and nothing outside the JSON:
{{"questions": [{{"category": "quantitative_aptitude", "difficulty": "intermediate", "question": "...", "options": ["a", "b", "c", "d"], "correct_answer": "a", "explanation": "..."}}]}}
"""


def _response_schema() -> dict:

    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(CATEGORIES)},
                        "difficulty": {"type": "string", "enum": sorted(VALID_DIFFICULTIES)},
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "correct_answer": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["category", "difficulty", "question", "options", "correct_answer", "explanation"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------
# Validation
# ------------------------------------------------------------

def _match_answer(correct_answer: str, options: list[str]) -> str | None:
    """The option the model's answer key points to (exact, then tolerant of case/spacing/letter keys)."""

    if correct_answer in options:
        return correct_answer

    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", str(text)).strip().rstrip(".").casefold()

    hits = [option for option in options if norm(option) == norm(correct_answer)]

    if len(hits) == 1:
        return hits[0]

    letter = re.fullmatch(r"\(?([A-Da-d])[\).:]?", correct_answer.strip())

    if letter:
        return options[ord(letter.group(1).upper()) - ord("A")]

    return None


_LANGUAGE_LABEL_LINE = re.compile(r"^\s*(python|sql|javascript|java|pseudo-?code|text|code)\s*$", re.I)


def _clean_question_text(text: str) -> str:
    """Drop markdown fences and stray language-label lines the model puts around code snippets."""

    text = re.sub(r"```[A-Za-z]*[ \t]*\n?", "", text)
    lines = [line for line in text.splitlines() if not _LANGUAGE_LABEL_LINE.match(line)]
    return "\n".join(lines).strip()


AMBIGUOUS_PATTERNS = (
    # seating / ordering puzzles very often have several valid arrangements
    (re.compile(r"\b(seated|seating|sits?|sitting|circular table|row of (?:\w+ )?(?:chairs|seats)|standing in a (?:row|line|queue))\b", re.I),
     "seating/ordering puzzles are not accepted (their answers are often ambiguous)"),
    # 'valid / invalid but possible / undetermined' style syllogism keys are subjective
    (re.compile(r"\b(invalid but possible|invalid and impossible|undetermined)\b", re.I),
     "subjective validity options are not accepted"),
)


def _reject_ambiguous(question_text: str, options: list[str]) -> None:

    blob = question_text + " " + " ".join(options)

    for pattern, reason in AMBIGUOUS_PATTERNS:
        if pattern.search(blob):
            raise RuntimeError(reason)


def _normalize_question(raw_question, index: int) -> dict:
    """One validated question, or RuntimeError."""

    if not isinstance(raw_question, dict):
        raise RuntimeError(f"Malformed question at position {index}.")

    question_text = _clean_question_text(str(raw_question.get("question", "")))
    options = raw_question.get("options")

    if not question_text:
        raise RuntimeError(f"Question {index} has empty text.")

    if not isinstance(options, list) or len(options) != REQUIRED_OPTION_COUNT:
        raise RuntimeError(f"Question {index} does not have exactly {REQUIRED_OPTION_COUNT} options.")

    clean_options = [str(option).strip() for option in options]

    if any(not option for option in clean_options):
        raise RuntimeError(f"Question {index} has an empty option.")

    if len({option.lower() for option in clean_options}) != REQUIRED_OPTION_COUNT:
        raise RuntimeError(f"Question {index} has duplicate options.")

    _reject_ambiguous(question_text, clean_options)

    correct_answer = _match_answer(str(raw_question.get("correct_answer", "")).strip(), clean_options)

    if correct_answer is None:
        raise RuntimeError(f"Question {index}'s correct_answer does not match any of its options.")

    raw_difficulty = str(raw_question.get("difficulty") or "").strip().lower()

    return {
        "id": index + 1,
        "category": str(raw_question.get("category") or "general").strip(),
        "difficulty": raw_difficulty if raw_difficulty in VALID_DIFFICULTIES else "intermediate",
        "question": question_text,
        "options": clean_options,
        "correct_answer": correct_answer,
        "explanation": str(raw_question.get("explanation") or "").strip(),
    }


def _question_key(question_text: str) -> str:
    return re.sub(r"\s+", " ", question_text.lower()).strip()


def _validate_and_normalize(
    parsed: dict,
    expected_count: int = REQUIRED_QUESTION_COUNT,
    expected_plan: dict | None = None,
) -> list[dict]:
    """Strict whole-set validation (used for complete sets)."""

    questions_raw = parsed.get("questions")

    if not isinstance(questions_raw, list):
        raise RuntimeError("The AI response did not contain a 'questions' array.")

    if len(questions_raw) != expected_count:
        raise RuntimeError(f"The AI returned {len(questions_raw)} questions, expected {expected_count}.")

    normalized_questions = []
    seen_questions = set()

    for index, raw_question in enumerate(questions_raw):

        item = _normalize_question(raw_question, index)

        key = _question_key(item["question"])

        if key in seen_questions:
            raise RuntimeError(f"Question {index} repeats an earlier question.")

        seen_questions.add(key)
        normalized_questions.append(item)

    if expected_plan:

        counts = {}
        for item in normalized_questions:
            counts[item["category"]] = counts.get(item["category"], 0) + 1

        if counts != expected_plan:
            raise RuntimeError(f"Category mix {counts} does not match the required {expected_plan}.")

    return normalized_questions



def _verification_schema() -> dict:

    return {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "choice": {"type": "string"}},
                    "required": ["id", "choice"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["answers"],
        "additionalProperties": False,
    }


def _disputed_slots(provider, items: dict[int, dict]):
    """
    Independent answer-key check: a second Groq call solves each new question WITHOUT seeing the key.
    Returns the slot indices where its answer differs from the key (wrong key, or ambiguous question).
    A failing verification call never blocks the test (the questions are then used unverified).
    """

    if not _verify_enabled() or not items:
        return set(), False

    ordered = sorted(items)

    listing = "\n\n".join(
        f"ID {slot}\n{items[slot]['question']}\nOptions:\n" + "\n".join(f"- {option}" for option in items[slot]["options"])
        for slot in ordered
    )

    prompt = (
        "Solve each multiple-choice question independently and carefully. For every question choose the single "
        "best option and return its EXACT text. If a question has no single correct option, or more than one "
        "option is correct, return the exact text of the first option but prefix nothing else.\n\n"
        f"{listing}\n\n"
        'Return ONLY JSON: {"answers": [{"id": <ID>, "choice": "<exact option text>"}]}'
    )

    try:

        try:
            text = provider.generate_json(
                prompt, json_schema=_verification_schema(), max_tokens=_token_budget(), temperature=0.0,
            )
        except Exception as first_error:
            if "429" not in str(first_error) and "rate_limit" not in str(first_error):
                raise
            time.sleep(RATE_LIMIT_BACKOFF_SECONDS)              # the free tier limits tokens per minute: one retry
            text = provider.generate_json(
                prompt, json_schema=_verification_schema(), max_tokens=_token_budget(), temperature=0.0,
            )

        parsed = json.loads(text)

        chosen = {
            int(entry["id"]): _match_answer(str(entry["choice"]).strip(), items[int(entry["id"])]["options"])
            for entry in parsed.get("answers", [])
            if int(entry["id"]) in items
        }

    except Exception as exc:
        logger.warning("Aptitude answer-key verification skipped: %s", exc)
        return set(), False

    disputed = {slot for slot in ordered if slot in chosen and chosen[slot] != items[slot]["correct_answer"]}

    if disputed:
        logger.warning("Aptitude answer-key check disputed %s question(s); regenerating them.", len(disputed))

    return disputed, True


# ------------------------------------------------------------
# Generation
# ------------------------------------------------------------

def _verify_enabled() -> bool:

    setting = (os.getenv("APTITUDE_VERIFY_KEYS") or "").strip().lower()

    if setting in ("true", "1", "yes"):
        return VERIFY_ANSWER_KEYS
    if setting in ("false", "0", "no"):
        return False

    return VERIFY_ANSWER_KEYS and get_aptitude_provider_name() == "groq"


def _token_budget() -> int:
    return GENERATION_MAX_TOKENS


def get_aptitude_provider_name() -> str:
    """APTITUDE_PROVIDER = groq. Chosen explicitly; there is only one supported value."""

    choice = (os.getenv("APTITUDE_PROVIDER") or "groq").strip().lower()

    if choice != "groq":
        raise RuntimeError(f"APTITUDE_PROVIDER must be 'groq', not '{choice}'.")

    return choice


def _make_provider():

    from backend.app.services.assessment.interview_llm_provider import HostedChatProvider

    provider = HostedChatProvider(
        "groq",
        model=os.getenv("APTITUDE_GENERATION_MODEL") or FALLBACK_MODEL,
        timeout=GENERATION_TIMEOUT_SECONDS,
    )

    # correct answer keys matter here: give the reasoning model more thinking than the interview default
    if provider.model.startswith("openai/gpt-oss"):
        provider.reasoning_effort = "medium"

    return provider


def _experience_text(resume_analysis, profile) -> str:
    """The experience section as text (resume analysis first, then the profile's own experience field)."""

    parts = []

    for value in (getattr(resume_analysis, "experience", None) if resume_analysis else None,
                  getattr(profile, "experience", None) if profile else None):
        if value:
            parts.append(value if isinstance(value, str) else json.dumps(value))

    return " ".join(parts)


def plan_for_candidate(user, profile, resume_analysis, blueprint=None, job=None):
    """(band, plan) for this candidate: fresher | junior | senior and the category counts.

    `job` decides whether "technical_logic" (pseudo-code/debugging/Python) slots are even
    offered -- they only make sense for a technical role (see NON_TECHNICAL_BAND_PLANS above).
    `job=None` is treated as technical (the old, pre-fix behaviour) so any caller that genuinely
    cannot supply a job never silently loses a category it used to get.
    """

    total = blueprint.total_questions if blueprint else REQUIRED_QUESTION_COUNT

    band = resolve_experience_band(
        blueprint.candidate_experience_level if blueprint else None,
        resume_analysis.experience_level if resume_analysis else None,
        resume_analysis.summary if resume_analysis else None,
        profile.experience_level if profile else None,
        getattr(user, "experience_level", None),
        experience_text=_experience_text(resume_analysis, profile),
    )

    is_technical = job is None or job_role_shape(job) == "technical"

    return band, _scaled_plan(band, total, is_technical=is_technical)


def generate_ai_aptitude_questions(
    job,
    user,
    profile,
    resume_analysis,
    blueprint=None,
) -> list[dict]:
    """
    Generate a per-candidate aptitude test with Groq: quantitative, logical and verbal questions
    (10 by default, blueprint.total_questions when a blueprint is supplied), with difficulty and
    format set by the candidate's experience level.

    Raises RuntimeError on any failure -- callers must catch it and fall back to the deterministic
    question bank (backend/app/services/aptitude/question_bank.py).
    """

    band, plan = plan_for_candidate(user, profile, resume_analysis, blueprint, job=job)

    provider = _make_provider()
    last_error = None

    variation = uuid.uuid4().hex[:12]
    all_slots = _pick_subtopics(plan, random.Random(variation))
    accepted: dict[int, dict] = {}          # slot index -> validated question
    seen_questions: set[str] = set()

    started = time.monotonic()

    for attempt in range(1, GENERATION_ATTEMPTS + 1):

        missing = [i for i in range(len(all_slots)) if i not in accepted]

        if not missing:
            break

        if time.monotonic() - started >= GENERATION_DEADLINE_SECONDS:
            last_error = RuntimeError("generation time budget used up")
            break

        prompt = _build_prompt(
            job, user, profile, resume_analysis, band,
            [all_slots[i] for i in missing], uuid.uuid4().hex[:12],
        )

        try:

            text = provider.generate_json(
                prompt,
                json_schema=_response_schema(),
                max_tokens=_token_budget(),
                temperature=GENERATION_TEMPERATURE,
            )

            parsed = json.loads(text)
            raw_questions = parsed.get("questions") if isinstance(parsed, dict) else None

            if not isinstance(raw_questions, list):
                raise RuntimeError("The AI response did not contain a 'questions' array.")

            fresh: dict[int, dict] = {}

            for position, raw_question in enumerate(raw_questions[:len(missing)]):

                slot_index = missing[position]

                try:
                    item = _normalize_question(raw_question, slot_index)
                except RuntimeError as bad:
                    last_error = bad
                    logger.warning("Aptitude question for slot %s rejected: %s", slot_index, bad)
                    continue

                if item["category"] != all_slots[slot_index][0]:
                    last_error = RuntimeError(f"Question {slot_index} has category {item['category']}.")
                    continue

                key = _question_key(item["question"])

                if key in seen_questions:
                    last_error = RuntimeError(f"Question {slot_index} repeats an earlier question.")
                    continue

                seen_questions.add(key)
                fresh[slot_index] = item

            disputed, verified_ran = _disputed_slots(provider, fresh)

            for slot_index in disputed:
                seen_questions.discard(_question_key(fresh[slot_index]["question"]))
                del fresh[slot_index]

            for item in fresh.values():
                item["_verified"] = verified_ran

            accepted.update(fresh)

        except Exception as exc:

            last_error = exc
            logger.warning("Aptitude generation attempt %s/%s failed: %s", attempt, GENERATION_ATTEMPTS, exc)

            if "429" in str(exc) or "rate_limit" in str(exc):
                if time.monotonic() - started + RATE_LIMIT_BACKOFF_SECONDS >= GENERATION_DEADLINE_SECONDS:
                    break
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS)

    if len(accepted) != len(all_slots):
        raise RuntimeError(
            f"Aptitude question generation produced {len(accepted)} of {len(all_slots)} valid questions: {last_error}"
        )

    questions = []

    for number in range(len(all_slots)):
        item = dict(accepted[number])
        item["id"] = number + 1
        questions.append(item)

    logger.info("Aptitude questions generated by %s (band=%s plan=%s).", get_aptitude_provider_name(), band, plan)

    return questions

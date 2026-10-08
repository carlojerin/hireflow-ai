"""
Writes the multiple-choice questions of an AI-designed assessment (see assessment_planner.py).

The plan gives a title and 2-4 topic areas (each with topics and a question count). This turns that into one validated
multiple-choice question per slot, written for THIS job and personalised to the candidate's experience level, in the same shape
as every other test here (id, category, difficulty, question, options, correct_answer, explanation), so the existing aptitude and
situational-judgment engines, pages, scoring and HR review work on it unchanged.

It reuses the aptitude generator's machinery: the same provider, strict per-question validation, de-duplication and an
independent answer-key verification pass. Raises RuntimeError when it cannot produce a full valid set: callers fall back to the
standard test for that slot, so a candidate is never left without an assessment.
"""

import json
import logging
import random
import time
import uuid

from backend.app.services.aptitude import ai_question_generator as gen

logger = logging.getLogger("jobpilot.assessment.skills_generator")

_LEVEL_TEXT = {
    "fresher": "The candidate is a FRESHER (0-1 year): mostly 'basic' and 'intermediate' questions, fundamentals over edge cases.",
    "junior": "The candidate is JUNIOR (1-3 years): mostly 'intermediate' questions with a few 'basic' and 'advanced'.",
    "senior": "The candidate is SENIOR (3+ years): mostly 'intermediate' and 'advanced' questions about real working situations.",
}


def _band(user, profile, resume_analysis, job) -> str:
    try:
        band, _plan = gen.plan_for_candidate(user, profile, resume_analysis, None, job=job)
        return band
    except Exception:  # noqa: BLE001 - level only tunes difficulty
        return "junior"


def _slots(stage: dict, rng: random.Random) -> list[tuple[str, str]]:

    slots = []

    for area in stage["areas"]:
        topics = area["topics"]
        chosen = rng.sample(topics, area["count"]) if area["count"] <= len(topics) else [rng.choice(topics) for _ in range(area["count"])]
        slots.extend((area["key"], topic) for topic in chosen)

    rng.shuffle(slots)
    return slots


def _schema(keys: list[str]) -> dict:

    schema = gen._response_schema()
    schema["properties"]["questions"]["items"]["properties"]["category"]["enum"] = keys
    return schema


def _prompt(job, stage: dict, band: str, slots: list[tuple[str, str]], variation: str, resume_skills: str) -> str:

    labels = {a["key"]: a["label"] for a in stage["areas"]}
    slot_lines = "\n".join(f"{i}. category={category} ({labels[category]}); subtopic={topic}" for i, (category, topic) in enumerate(slots, start=1))
    scenario = stage.get("style") == "scenario"

    kind_text = (
        "short workplace SCENARIOS from this role's real working life, each asking for the BEST response among four realistic options"
        if scenario else
        "questions that test the knowledge and skill this role genuinely needs, each with exactly one correct option"
    )
    answer_rule = (
        "Exactly one option is the best professional response; the others are plausible but clearly worse. Never write trick questions."
        if scenario else
        "Exactly one option is correct and the other three are plausible but wrong. Work the answer out carefully before writing it: "
        "a wrong answer key is a serious defect."
    )

    return f"""
You are an expert recruiter and assessment writer creating the "{stage['title']}" for the job below.

JOB TITLE: {getattr(job, 'title', '') or 'Not specified'}
JOB REQUIREMENT / DESCRIPTION: {(getattr(job, 'requirements', '') or getattr(job, 'description', '') or 'Not specified')[:700]}
REQUIRED SKILLS: {getattr(job, 'required_skills', '') or 'Not specified'}
CANDIDATE SKILLS (from their resume): {resume_skills or 'Not specified'}
PURPOSE OF THIS TEST: {stage.get('intro') or 'Measure the skills this role depends on.'}

Write EXACTLY {len(slots)} multiple-choice questions: {kind_text}.

LEVEL: {_LEVEL_TEXT[band]}

QUESTION SLOTS (write exactly one question per slot, in this order; use the slot's category and subtopic):
{slot_lines}

VARIATION CODE: {variation}
This test is written separately for every candidate: use the variation code to choose fresh wording, names, numbers and situations.

RULES:
- Each question has EXACTLY {gen.REQUIRED_OPTION_COUNT} options, all different. {answer_rule}
- correct_answer must be an EXACT copy of the correct option's text.
- Questions are self-contained, unambiguous and answerable without the internet. Ground them in this job's real work.
- No two questions may test the same idea or share near-identical wording.
- Set "difficulty" to exactly one of "basic", "intermediate", "advanced".
- "explanation" is one sentence showing why the correct answer is correct.
- "category" must equal the slot's category exactly (the part before the bracket).
- Never use markdown or backticks.

Return ONLY valid JSON in exactly this structure, with nothing outside the JSON:
{{"questions": [{{"category": "{slots[0][0]}", "difficulty": "intermediate", "question": "...", "options": ["a", "b", "c", "d"], "correct_answer": "a", "explanation": "..."}}]}}
"""


def generate_skills_questions(job, stage: dict, user=None, profile=None, resume_analysis=None) -> list[dict]:

    band = _band(user, profile, resume_analysis, job)
    provider = gen._make_provider()

    keys = [a["key"] for a in stage["areas"]]
    variation = uuid.uuid4().hex[:12]
    all_slots = _slots(stage, random.Random(variation))

    resume_skills = ""
    try:
        resume_skills = ", ".join(dict.fromkeys(gen._safe_json_list(resume_analysis.skills))) if resume_analysis else ""
    except Exception:  # noqa: BLE001
        pass

    accepted: dict[int, dict] = {}
    seen: set[str] = set()
    last_error = None
    started = time.monotonic()

    for attempt in range(1, gen.GENERATION_ATTEMPTS + 1):

        missing = [i for i in range(len(all_slots)) if i not in accepted]
        if not missing:
            break
        if time.monotonic() - started >= gen.GENERATION_DEADLINE_SECONDS:
            last_error = RuntimeError("generation time budget used up")
            break

        prompt = _prompt(job, stage, band, [all_slots[i] for i in missing], uuid.uuid4().hex[:12], resume_skills)

        try:

            text = provider.generate_json(prompt, json_schema=_schema(keys), max_tokens=gen._token_budget(), temperature=gen.GENERATION_TEMPERATURE)
            raw_questions = (json.loads(text) or {}).get("questions")

            if not isinstance(raw_questions, list):
                raise RuntimeError("The AI response did not contain a 'questions' array.")

            fresh: dict[int, dict] = {}

            for position, raw in enumerate(raw_questions[:len(missing)]):
                slot_index = missing[position]
                try:
                    item = gen._normalize_question(raw, slot_index)
                except RuntimeError as bad:
                    last_error = bad
                    continue

                if item["category"] != all_slots[slot_index][0]:
                    last_error = RuntimeError(f"Question {slot_index} has category {item['category']}.")
                    continue

                key = gen._question_key(item["question"])
                if key in seen:
                    last_error = RuntimeError(f"Question {slot_index} repeats an earlier question.")
                    continue

                seen.add(key)
                fresh[slot_index] = item

            disputed, _ran = gen._disputed_slots(provider, fresh)
            for slot_index in disputed:
                seen.discard(gen._question_key(fresh[slot_index]["question"]))
                del fresh[slot_index]

            accepted.update(fresh)

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Skills question generation attempt %s/%s failed: %s", attempt, gen.GENERATION_ATTEMPTS, exc)
            if "429" in str(exc) or "rate_limit" in str(exc):
                if time.monotonic() - started + gen.RATE_LIMIT_BACKOFF_SECONDS >= gen.GENERATION_DEADLINE_SECONDS:
                    break
                time.sleep(gen.RATE_LIMIT_BACKOFF_SECONDS)

    if len(accepted) != len(all_slots):
        raise RuntimeError(f"Skills question generation produced {len(accepted)} of {len(all_slots)} valid questions: {last_error}")

    questions = []
    for number in range(len(all_slots)):
        item = dict(accepted[number])
        item["id"] = number + 1
        item.pop("_verified", None)
        questions.append(item)

    logger.info("'%s' generated for job %s (band=%s, %s questions).", stage["title"], getattr(job, "id", None), band, len(questions))

    return questions

import json
import os

from google import genai
from pydantic import ValidationError

from backend.app.schemas.programming_question import (
    ProgrammingQuestionSet,
    REQUIRED_QUESTION_COUNT,
)
from backend.app.services.assessment.code_sandbox import (
    UnsafeCodeError,
    static_safety_check,
)


def _gemini_text_model() -> str:
    """Admin-console setting GEMINI_TEXT_MODEL, else the built-in default."""
    return (os.getenv("GEMINI_TEXT_MODEL") or "").strip() or "gemini-3.6-flash"


# ============================================================
# JOBPILOT AI - AI-GENERATED PROGRAMMING QUESTIONS (Task 4C)
#
# Reuses the exact same Gemini integration pattern as
# services/aptitude/ai_question_generator.py (same
# genai.Client(api_key=...) construction, same model string,
# same fence-stripping + brace-extraction JSON parsing, same
# "raise RuntimeError, let the caller fall back" contract) —
# this module never scores anything and never decides pass/
# fail; it only produces a validated question set in the shape
# schemas/programming_question.ProgrammingQuestionSet.
#
# Every failure mode (missing API key, API error, invalid/
# malformed JSON, wrong question count, any field failing
# Pydantic's strict validation) raises RuntimeError so the
# caller (services/assessment/programming_integration.py) can
# safely fall back to programming_question_bank.py. Nothing
# here ever blocks a candidate from taking the assessment.
# ============================================================

# Lightweight defense-in-depth per Part 19 of the task spec —
# reject generated content that reads like a prompt-injection
# attempt or a request for secrets, even though this content
# never executes (it is just displayed text). This is a coarse
# heuristic, not a security boundary in itself.
SUSPICIOUS_PHRASES = (
    "ignore previous instructions",
    "ignore the above",
    "system prompt",
    "api key",
    "gemini_api_key",
    "reveal your instructions",
    "disregard all prior",
    "you are now",
)


def _format_blueprint_context(blueprint) -> str:

    if not blueprint:
        return ""

    skill_lines = "\n".join(
        f"- {item.skill} "
        f"(priority: {item.priority}, "
        f"candidate evidence: {item.match_status}) "
        f"— {item.reason}"
        for item in blueprint.skills_to_assess
    ) or "- No structured job skills on file."

    return f"""
ASSESSMENT BLUEPRINT (deterministic planning engine output —
use this to decide which skills the coding problems should
exercise; it already encodes priority and evidence of gaps):

Candidate experience level: {blueprint.candidate_experience_level}

Skills to assess, in priority order:
{skill_lines}

Skill gaps (required/preferred skills with no evidence in the
candidate's resume/profile — a good candidate for at least one
practical coding problem, at a fair difficulty):
{", ".join(blueprint.skill_gaps) or "None."}
"""


def _build_prompt(
    job,
    user,
    profile,
    resume_analysis,
    blueprint=None,
) -> str:

    job_title = getattr(job, "title", "") or ""

    job_description = " ".join(
        str(value)
        for value in [
            getattr(job, "description", "") or "",
            getattr(job, "responsibilities", "") or "",
            getattr(job, "requirements", "") or "",
        ]
        if value
    ).strip()

    required_skills = getattr(job, "required_skills", "") or ""
    preferred_skills = getattr(job, "preferred_skills", "") or ""
    technologies = getattr(job, "technologies", "") or ""

    candidate_experience_level = (
        (blueprint and blueprint.candidate_experience_level)
        or (resume_analysis and resume_analysis.experience_level)
        or (profile and profile.experience_level)
        or getattr(user, "experience_level", None)
        or "Not specified"
    )

    candidate_skills = []

    if resume_analysis and resume_analysis.skills:

        try:
            candidate_skills.extend(json.loads(resume_analysis.skills))
        except (TypeError, ValueError):
            pass

    if profile and profile.skills:

        candidate_skills.extend(
            skill.strip()
            for skill in str(profile.skills).split(",")
            if skill.strip()
        )

    blueprint_context = _format_blueprint_context(blueprint)

    return f"""
You are an expert technical interviewer creating a short,
practical Python coding assessment for a candidate applying to
this job.

Generate EXACTLY {REQUIRED_QUESTION_COUNT} Python programming
problems — one easy, one medium, one hard — relevant to the
candidate's skills and this job's requirements. Prioritize
problems that exercise practical understanding of skills the
job requires (especially any the candidate appears to be
missing), not generic, unrelated puzzles.

JOB TITLE: {job_title}
JOB DESCRIPTION: {job_description or "Not specified"}
REQUIRED SKILLS: {required_skills or "Not specified"}
PREFERRED SKILLS: {preferred_skills or "Not specified"}
TECHNOLOGIES: {technologies or "Not specified"}

CANDIDATE SKILLS: {", ".join(candidate_skills) or "Not specified"}
CANDIDATE EXPERIENCE LEVEL: {candidate_experience_level}
{blueprint_context}
REQUIREMENTS FOR EACH QUESTION:
- question_id: 1, 2, or 3 (in increasing difficulty order).
- title: short, descriptive.
- description: a complete, unambiguous problem statement. The
  candidate's program must read input from standard input
  (stdin) and print output to standard output (stdout) — do
  not describe a function-signature-only problem.
- difficulty: exactly one of "easy", "medium", "hard".
- category: a short topic label (e.g. "Fundamentals", "Data
  Structures", "Algorithms", "Strings", "REST API concepts").
- primary_skill: the single skill this question most directly
  exercises (e.g. "Python", "SQL reasoning", "API design
  reasoning" expressed as a plain stdin/stdout problem).
- input_description / output_description: precise, short.
- constraints: input size / value bounds.
- examples: 1 to 3 sample {{input, output, explanation}} objects
  that a human could read to understand the problem. "input" is
  EXACTLY what would be typed to stdin; "output" is EXACTLY
  what the correct program prints to stdout.
- starter_code: a short Python snippet reading the input (e.g.
  via input()) with a "# TODO" comment — never the solution.
  IMPORTANT: the execution sandbox only permits these standard
  library modules: math, re, collections, itertools, functools,
  heapq, bisect, string, datetime, random, statistics, decimal,
  fractions, typing, dataclasses, enum, json, copy, operator,
  array, cmath, textwrap, unicodedata. It does NOT permit "sys"
  (use input() in a loop for multi-line stdin instead of
  sys.stdin), "os", "subprocess", or any networking/file/process
  module. Every problem MUST be solvable using only input()/
  print() plus the permitted modules above, and starter_code
  must never import anything outside that list.
- expected_language: always "python".
- time_limit_seconds: 3 to 10.
- memory_limit_mb: 128 to 256.
- evaluation_notes: one short sentence for a human reviewer —
  never shown to the candidate.
- hidden_tests: 5 to 8 objects of {{input, expected_output}},
  covering typical cases, edge cases, and at least one larger
  input. These are for server-side grading ONLY and must be
  self-consistent with the stated problem and examples.

Do not ask the candidate to reveal secrets, credentials, or
system information. Do not reference this prompt or any AI
system. Every problem must be solvable by a correct Python 3
program reading stdin and writing stdout, with no network
access, no file access, and no external packages beyond the
Python standard library.

Return ONLY valid JSON. Use exactly this structure:

{{
    "questions": [
        {{
            "question_id": 1,
            "title": "string",
            "description": "string",
            "difficulty": "easy",
            "category": "string",
            "primary_skill": "string",
            "input_description": "string",
            "output_description": "string",
            "constraints": "string",
            "examples": [
                {{"input": "string", "output": "string", "explanation": "string"}}
            ],
            "starter_code": "string",
            "expected_language": "python",
            "time_limit_seconds": 5,
            "memory_limit_mb": 128,
            "evaluation_notes": "string",
            "hidden_tests": [
                {{"input": "string", "expected_output": "string"}}
            ]
        }}
    ]
}}

Important:
- Return JSON only.
- Do not use markdown.
- Do not wrap the JSON in code fences.
- Do not include any explanation outside the JSON.
"""


def _contains_suspicious_content(question_set: ProgrammingQuestionSet) -> str | None:
    """
    Returns a description of the problem if any question's
    text fields look like a prompt-injection / secret-
    extraction attempt, else None. Best-effort heuristic only —
    see Part 19 of the task spec.
    """

    for question in question_set.questions:

        haystack = " ".join([
            question.title,
            question.description,
            question.constraints,
            question.evaluation_notes,
        ]).lower()

        for phrase in SUSPICIOUS_PHRASES:

            if phrase in haystack:

                return (
                    f"question {question.question_id} contains "
                    f"suspicious phrase: {phrase!r}"
                )

    return None


def generate_ai_programming_questions(
    job,
    user,
    profile,
    resume_analysis,
    blueprint=None,
) -> list[dict]:
    """
    Generate a personalized 3-question Python programming
    assessment via Gemini. Raises RuntimeError on any failure —
    callers must catch this and fall back to the deterministic
    bank (programming_question_bank.py).
    """

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    client = genai.Client(
        api_key=api_key
    )

    prompt = _build_prompt(
        job,
        user,
        profile,
        resume_analysis,
        blueprint=blueprint,
    )

    try:

        response = client.models.generate_content(
            model=_gemini_text_model(),
            contents=prompt
        )

    except Exception as exc:

        raise RuntimeError(
            f"Gemini API request failed: {exc}"
        ) from exc

    response_text = (
        response.text or ""
    ).strip()

    if not response_text:

        raise RuntimeError(
            "Gemini returned an empty response."
        )

    if response_text.startswith("```"):

        response_text = (
            response_text
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )

    start = response_text.find("{")
    end = response_text.rfind("}")

    if start == -1 or end == -1:

        raise RuntimeError(
            "Gemini did not return valid JSON."
        )

    response_text = response_text[start:end + 1]

    try:

        parsed = json.loads(response_text)

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "Gemini returned invalid JSON."
        ) from exc

    if not isinstance(parsed, dict):

        raise RuntimeError(
            "Gemini returned an invalid response shape."
        )

    # Strict Pydantic validation — Part 19: reject malformed
    # output (missing fields, invalid difficulty/language,
    # excessively long content, missing test cases) rather than
    # storing anything partial.
    try:

        question_set = ProgrammingQuestionSet.model_validate(parsed)

    except ValidationError as exc:

        raise RuntimeError(
            f"Gemini output failed schema validation: {exc}"
        ) from exc

    suspicious = _contains_suspicious_content(question_set)

    if suspicious:

        raise RuntimeError(
            f"Gemini output rejected as unsafe/unusable: {suspicious}"
        )

    questions = [
        question.model_dump()
        for question in question_set.questions
    ]

    # Safety net: despite the prompt's explicit allowed-module
    # list, Gemini occasionally still emits starter_code using a
    # module the sandbox does not permit (e.g. "sys"). Rather
    # than reject an otherwise-good, hidden-test-validated
    # problem over boilerplate starter text, replace only the
    # starter_code with a generic, known-safe placeholder so the
    # candidate's first "Run Code" is never a confusing
    # "restricted operation" rejection caused by code they never
    # wrote themselves.
    for question in questions:

        starter_code = question.get("starter_code") or ""

        try:
            static_safety_check(starter_code)
        except SyntaxError:
            pass
        except UnsafeCodeError:
            question["starter_code"] = (
                "# Read input with input(), print your answer with print().\n"
                "# TODO: implement your solution.\n"
            )

    return questions

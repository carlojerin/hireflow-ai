import json
import os
import re

from google import genai
from pydantic import ValidationError

from backend.app.schemas.interview_evaluation import InterviewAnswerEvaluation


def _gemini_text_model() -> str:
    """Admin-console setting GEMINI_TEXT_MODEL, else the built-in default."""
    return (os.getenv("GEMINI_TEXT_MODEL") or "").strip() or "gemini-3.6-flash"


# ============================================================
# JOBPILOT AI - AI INTERVIEW ANSWER EVALUATOR (Task 4D)
#
# Evaluates ONE candidate answer at a time, using the same
# Gemini client/model pattern as every other AI generator in
# this project. The evaluator NEVER sees or evaluates protected
# characteristics — it receives only the question, the
# candidate's answer text, the expected answer points, the
# skill being assessed, minimal job context, and the candidate's
# experience level. No name, email, mobile, resume file, photo,
# or any other personal identifier is included in the prompt.
#
# Gemini's output is never trusted directly: it is parsed,
# range-validated against InterviewAnswerEvaluation (0-100 per
# dimension), and the CALLER (interview_scoring.py) computes the
# authoritative composite score server-side from the validated
# sub-scores — Gemini is never asked for, or trusted for, a
# single top-level score.
#
# Any failure (missing API key, API error, invalid JSON, output
# failing validation) raises RuntimeError so the caller falls
# back to evaluate_answer_fallback() below. Fallback evaluation
# is clearly, permanently marked via evaluation_source="fallback"
# wherever it is stored — it is never presented as an AI
# evaluation.
# ============================================================


def _build_prompt(
    question: dict,
    answer_text: str,
    experience_level: str,
    job_title: str,
) -> str:

    expected_points = "\n".join(
        f"- {point}" for point in question.get("expected_answer_points", [])
    ) or "- (none specified)"

    return f"""
You are evaluating one answer from a structured technical
interview. Evaluate ONLY the content of the answer against the
question and expected points below. Base your evaluation
strictly on job-relevant technical/communication content.

Do NOT evaluate or infer anything about the candidate's race,
religion, gender, disability, age, appearance, accent,
nationality, or political views. Those topics are irrelevant
and must never influence your scores.

JOB TITLE: {job_title}
CANDIDATE EXPERIENCE LEVEL: {experience_level}
SKILL BEING ASSESSED: {question.get("primary_skill", "Not specified")}

QUESTION: {question.get("question", "")}

WHAT A STRONG ANSWER COVERS:
{expected_points}

CANDIDATE'S ANSWER:
\"\"\"
{answer_text.strip() or "(no answer provided)"}
\"\"\"

Score the answer on these five dimensions, each 0-100:
- technical_correctness: factual/technical accuracy.
- understanding: depth of genuine understanding versus surface
  -level or memorized phrasing.
- reasoning: quality of the reasoning/justification shown.
- relevance: how directly the answer addresses the actual
  question asked.
- communication: clarity and structure of the explanation.

If the answer is empty, off-topic, or nonsensical, score all
five dimensions low (0-20) rather than guessing generously.

Also provide:
- strengths: 0-4 short bullet points of what the answer did well
  (empty list if none).
- gaps: 0-4 short bullet points of what was missing or weak
  (empty list if none).
- feedback: one or two sentences of constructive, candidate-safe
  feedback (never reveal the expected_answer_points verbatim).

Return ONLY valid JSON. Use exactly this structure:

{{
    "technical_correctness": 0,
    "understanding": 0,
    "reasoning": 0,
    "relevance": 0,
    "communication": 0,
    "strengths": ["string"],
    "gaps": ["string"],
    "feedback": "string"
}}

Important:
- Return JSON only.
- Do not use markdown.
- Do not wrap the JSON in code fences.
- Do not include any explanation outside the JSON.
"""


def evaluate_answer_ai(
    question: dict,
    answer_text: str,
    experience_level: str,
    job_title: str,
) -> InterviewAnswerEvaluation:
    """
    Evaluate one answer via Gemini. Raises RuntimeError on any
    failure — the caller must catch this and fall back to
    evaluate_answer_fallback().
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
        question,
        answer_text,
        experience_level,
        job_title,
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

    try:

        return InterviewAnswerEvaluation.model_validate(parsed)

    except ValidationError as exc:

        raise RuntimeError(
            f"Gemini evaluation failed schema validation: {exc}"
        ) from exc


# ============================================================
# DETERMINISTIC FALLBACK EVALUATOR
#
# Used only when Gemini is unavailable or fails. This is a
# crude, honestly-limited heuristic — it cannot judge genuine
# technical understanding the way an LLM can, and must never be
# presented as equivalent. It scores every dimension from the
# SAME keyword-coverage ratio (it has no way to tell
# "correctness" apart from "relevance" without real language
# understanding), except communication, which uses a simple
# answer-length heuristic. evaluation_source="fallback" is
# always set by the caller alongside this result.
# ============================================================

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def _significant_words(text: str) -> set[str]:

    words = _WORD_RE.findall(text.lower())

    return {word for word in words if len(word) >= 4}


def evaluate_answer_fallback(
    question: dict,
    answer_text: str,
) -> InterviewAnswerEvaluation:

    answer_text = (answer_text or "").strip()

    if not answer_text:

        return InterviewAnswerEvaluation(
            technical_correctness=0,
            understanding=0,
            reasoning=0,
            relevance=0,
            communication=0,
            strengths=[],
            gaps=["No answer was provided."],
            feedback=(
                "No answer was provided. (Automated fallback "
                "evaluation — AI evaluation was unavailable.)"
            ),
        )

    expected_points = question.get("expected_answer_points", [])

    expected_words: set[str] = set()

    for point in expected_points:
        expected_words |= _significant_words(point)

    answer_words = _significant_words(answer_text)

    if expected_words:

        matched = expected_words & answer_words
        coverage_ratio = len(matched) / len(expected_words)

    else:

        coverage_ratio = 0.5

    coverage_score = round(min(1.0, coverage_ratio) * 100)

    # Communication: a crude length-based proxy only — long
    # enough to be substantive, not so long it looks like
    # padding.
    length = len(answer_text)

    if length < 20:
        communication_score = 20
    elif length < 400:
        communication_score = 80
    elif length < 2000:
        communication_score = 60
    else:
        communication_score = 40

    feedback = (
        "Automated fallback evaluation (AI evaluation was "
        "unavailable) — based on keyword overlap with expected "
        "concepts, not genuine language understanding."
    )

    return InterviewAnswerEvaluation(
        technical_correctness=coverage_score,
        understanding=coverage_score,
        reasoning=coverage_score,
        relevance=coverage_score,
        communication=communication_score,
        strengths=[],
        gaps=[],
        feedback=feedback,
    )

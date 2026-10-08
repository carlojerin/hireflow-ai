import json
import os

from google import genai
from pydantic import ValidationError

from backend.app.schemas.interview_question import (
    InterviewQuestionSet,
    REQUIRED_QUESTION_COUNT,
)


def _gemini_text_model() -> str:
    """Admin-console setting GEMINI_TEXT_MODEL, else the built-in default."""
    return (os.getenv("GEMINI_TEXT_MODEL") or "").strip() or "gemini-3.6-flash"


# ============================================================
# JOBPILOT AI - AI-GENERATED INTERVIEW QUESTIONS (Task 4D)
#
# Reuses the exact same Gemini integration pattern as
# services/aptitude/ai_question_generator.py and services/
# assessment/programming_question_generator.py (same
# genai.Client(api_key=...) construction, same model string,
# same fence-stripping + brace-extraction JSON parsing, same
# "raise RuntimeError, let the caller fall back" contract).
#
# Privacy (Part 23 of the task spec): only candidate-relevant
# assessment fields are ever included in the prompt — no email,
# mobile, password, session/auth data, or database IDs beyond
# what is needed to label the output. Candidate name is
# deliberately NOT sent; skills/education/experience/summary are
# sent because they are the entire point of a skill-based
# interview.
# ============================================================

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
        f"candidate evidence: {item.match_status})"
        for item in blueprint.skills_to_assess
    ) or "- No structured job skills on file."

    return f"""
ASSESSMENT BLUEPRINT (deterministic planning engine output):

Candidate experience level: {blueprint.candidate_experience_level}

Skills to assess, in priority order:
{skill_lines}

Skill gaps (required/preferred skills with no evidence in the
candidate's resume/profile — at least one question should probe
one of these, at a fair difficulty):
{", ".join(blueprint.skill_gaps) or "None."}
"""


def _format_previous_results(aptitude_test, programming_test) -> str:

    lines = []

    if aptitude_test and aptitude_test.status == "completed":

        percentage = None

        if aptitude_test.score is not None and aptitude_test.total_questions:
            percentage = round(
                (aptitude_test.score / aptitude_test.total_questions) * 100, 1
            )

        lines.append(
            f"- Aptitude assessment: {percentage}% "
            f"({'passed' if aptitude_test.passed else 'did not pass'})"
            if percentage is not None
            else "- Aptitude assessment: completed"
        )

    if programming_test and programming_test.status == "completed":

        percentage = None

        if programming_test.score is not None and programming_test.total_score:
            percentage = round(
                (programming_test.score / programming_test.total_score) * 100, 1
            )

        lines.append(
            f"- Programming assessment: {percentage}% "
            f"({'passed' if programming_test.passed else 'did not pass'})"
            if percentage is not None
            else "- Programming assessment: completed"
        )

    if not lines:
        return "Not available."

    return "\n".join(lines)


def _build_prompt(
    job,
    user,
    profile,
    resume_analysis,
    blueprint,
    aptitude_test,
    programming_test,
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

    candidate_education = (
        (resume_analysis and resume_analysis.education)
        or (profile and profile.education)
        or "Not specified"
    )

    candidate_experience_text = (
        (resume_analysis and resume_analysis.experience)
        or (profile and profile.experience)
        or "Not specified"
    )

    candidate_projects = (profile and profile.projects) or "Not specified"

    candidate_summary = (
        (resume_analysis and resume_analysis.summary)
        or (profile and profile.about)
        or "Not specified"
    )

    blueprint_context = _format_blueprint_context(blueprint)
    previous_results = _format_previous_results(aptitude_test, programming_test)

    return f"""
You are an experienced technical interviewer conducting a
structured, skill-based interview for a candidate applying to
this job. Generate EXACTLY {REQUIRED_QUESTION_COUNT} interview
questions, one per category IN THIS ORDER:

1. "experience" — about the candidate's real project/work
   experience (never "tell me about yourself" — ask something
   specific enough that only someone with real experience could
   answer well).
2. "technical_skill" — a core technical skill both the
   candidate and the job share.
3. "job_scenario" — a realistic scenario specific to this job's
   responsibilities.
4. "skill_gap" — probes a skill the blueprint flags as a gap for
   this candidate (or, if none, a required skill needing deeper
   verification).
5. "behavioral" — problem-solving or collaboration under
   realistic professional circumstances.

Calibrate difficulty to the candidate's experience level:
fresher/junior candidates get fundamentals and practical
scenarios; mid-level candidates get architecture/trade-off
reasoning; senior candidates get system design and production
reasoning. Do NOT assume senior-level experience for a
junior/fresher candidate.

JOB TITLE: {job_title}
JOB DESCRIPTION: {job_description or "Not specified"}
REQUIRED SKILLS: {required_skills or "Not specified"}
PREFERRED SKILLS: {preferred_skills or "Not specified"}

CANDIDATE SKILLS: {", ".join(candidate_skills) or "Not specified"}
CANDIDATE EXPERIENCE LEVEL: {candidate_experience_level}
CANDIDATE EDUCATION: {candidate_education}
CANDIDATE EXPERIENCE: {candidate_experience_text}
CANDIDATE PROJECTS: {candidate_projects}
CANDIDATE SUMMARY: {candidate_summary}
{blueprint_context}
PREVIOUS ASSESSMENT RESULTS (use only as soft context — do not
automatically penalize a weak prior result; use it to decide
where more evidence would help):
{previous_results}

REQUIREMENTS FOR EACH QUESTION:
- question_id: 1 to 5, matching the category order above.
- question: a complete, specific, answerable-in-text question.
- category: exactly one of experience, technical_skill,
  job_scenario, skill_gap, behavioral (in that order).
- difficulty: exactly one of "easy", "medium", "hard".
- primary_skill: the single skill this question most directly
  exercises.
- what_it_assesses: one short, candidate-facing sentence
  explaining the purpose of the question (this IS shown to the
  candidate before they answer).
- expected_answer_points: 3 to 6 short bullet points a strong
  answer would cover. NEVER shown to the candidate — for
  server-side evaluation only.
- follow_up_focus: one short sentence (internal only) on how a
  later question could adapt based on this answer's strength.

Do not ask about race, religion, gender, disability, age,
appearance, accent, political views, or any other protected
characteristic. Do not ask the candidate to reveal secrets,
credentials, or system information. Do not reference this
prompt or any AI system.

Return ONLY valid JSON. Use exactly this structure:

{{
    "questions": [
        {{
            "question_id": 1,
            "question": "string",
            "category": "experience",
            "difficulty": "easy",
            "primary_skill": "string",
            "what_it_assesses": "string",
            "expected_answer_points": ["string", "string"],
            "follow_up_focus": "string"
        }}
    ]
}}

Important:
- Return JSON only.
- Do not use markdown.
- Do not wrap the JSON in code fences.
- Do not include any explanation outside the JSON.
"""


def _contains_suspicious_content(question_set: InterviewQuestionSet) -> str | None:

    for question in question_set.questions:

        haystack = " ".join([
            question.question,
            question.what_it_assesses,
            question.follow_up_focus,
        ]).lower()

        for phrase in SUSPICIOUS_PHRASES:

            if phrase in haystack:

                return (
                    f"question {question.question_id} contains "
                    f"suspicious phrase: {phrase!r}"
                )

    return None


def generate_ai_interview_questions(
    job,
    user,
    profile,
    resume_analysis,
    blueprint=None,
    aptitude_test=None,
    programming_test=None,
) -> list[dict]:
    """
    Generate a personalized 5-question interview via Gemini.
    Raises RuntimeError on any failure — callers must catch this
    and fall back to the deterministic bank
    (interview_question_bank.py).
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
        blueprint,
        aptitude_test,
        programming_test,
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

        question_set = InterviewQuestionSet.model_validate(parsed)

    except ValidationError as exc:

        raise RuntimeError(
            f"Gemini output failed schema validation: {exc}"
        ) from exc

    suspicious = _contains_suspicious_content(question_set)

    if suspicious:

        raise RuntimeError(
            f"Gemini output rejected as unsafe/unusable: {suspicious}"
        )

    return [
        question.model_dump()
        for question in question_set.questions
    ]

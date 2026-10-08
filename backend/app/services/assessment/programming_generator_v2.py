import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

from backend.app.models.job import Job
from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.assessment_plan import AssessmentPlan, AssessmentPlanItem
from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.services.assessment.gemini_client import (
    GeminiUnavailableError,
    generate_json_content,
)
from backend.app.services.assessment.language_capability_registry import (
    get_language_capability,
    list_all_known_languages,
    normalize_language_name,
)
from backend.app.services.assessment.coding_question_generator import apply_reference_solutions
from backend.app.services.assessment.programming_question_validator_v2 import (
    validate_question_set_v2,
)

logger = logging.getLogger("jobpilot.assessment.programming")


# ============================================================
# JOBPILOT AI — PROGRAMMING ASSESSMENT V2 GENERATOR
# (Programming Engine V2, AI generation task)
#
# STANDALONE. Mirrors personalized_programming_generator.py's
# (Step 3) own Gemini-then-fallback pattern exactly, but targets
# the NEW language-neutral ProgrammingQuestionSetV2 schema
# (executable stdin/expected_stdout test cases) instead of
# Step 3's descriptive-only BehavioralTestCase. Nothing in the
# live pipeline calls this module yet.
#
# THE ONE POLICY THIS MODULE ENFORCES, MATCHING programming_
# integration.py's EXISTING, LIVE Step 6 capability guard
# EXACTLY: if the job's assessment plan explicitly requires a
# programming language JobPilot cannot execute (Java, Go, C++,
# etc.), this generator NEVER silently produces Python questions
# instead. It returns status="capability_unavailable" with the
# real required technology and its actual capability_state — the
# caller decides what to show HR/the candidate, but the "fake
# Python substitute" outcome is impossible to reach.
#
# GEMINI NEVER DECIDES SCORING. This module's only output is a
# QUESTION DEFINITION (problem text, constraints, examples,
# hidden test stdin/expected_stdout, weights) — nothing it
# produces is ever treated as a verdict. Scoring happens later,
# entirely from execution results, in programming_scoring_v2.py.
# ============================================================

GeneratedBy = Literal["gemini", "fallback"]
GenerationStatus = Literal["generated", "capability_unavailable", "not_applicable"]

def _general_purpose_languages() -> set[str]:
    """
    The set of languages a job MAY leave unpinned — a plan item
    naming one of these explicitly still narrows allowed_languages
    to just that one (job-anchored, per the task's own "must not
    automatically allow every language" rule); a plan item naming
    anything else, or naming nothing at all, is handled by
    _classify_programming_items below.

    Computed LIVE from language_capability_registry.py's own
    execution_supported flag — never a hardcoded literal set.
    Task 5C's Complete Multi-Language Execution Engine work
    exposed exactly why a hardcoded {"python", "javascript",
    "csharp"} set is a bug waiting to happen: the moment
    java_runner.py (etc.) went live, that stale literal set
    caused a job explicitly requiring Java to be wrongly reported
    as "capability_unavailable" even though Java was fully
    working — the SAME "unsupported technology" rejection path a
    genuinely unsupported language uses, indistinguishable to a
    caller without reading the message text. Deriving this list
    from the registry means every future language addition
    updates this behavior automatically, with no second file to
    remember to edit.
    """

    return {
        capability.canonical_name
        for capability in list_all_known_languages()
        if capability.execution_supported and capability.canonical_name not in ("sql", "html")
    }


@dataclass(frozen=True)
class ProgrammingAssessmentGenerationResult:

    status: GenerationStatus
    question_set: Optional[ProgrammingQuestionSetV2]
    required_technology: Optional[str]
    capability_state: Optional[str]
    message: str
    generated_by: Optional[GeneratedBy] = None


def _classify_programming_items(
    plan: AssessmentPlan,
) -> tuple[list[AssessmentPlanItem], list[str]]:
    """
    Returns (programming_items, unsupported_specific_languages).
    unsupported_specific_languages is the exact same computation
    programming_integration.py's Step 6 guard already performs —
    duplicated here deliberately in the SAME narrow shape (rather
    than imported) because that module is the LIVE, already-
    tested guard and must not gain a new import dependency on
    this standalone generator; the two are kept independently
    correct against the same rule, not coupled.
    """

    programming_items = [
        item for item in plan.assessment_types if item.assessment_type == "programming"
    ]

    general_purpose = _general_purpose_languages()

    unsupported = sorted({
        item.technology.strip().lower()
        for item in programming_items
        if item.technology
        and item.technology.strip().lower() not in general_purpose
        and normalize_language_name(item.technology) not in general_purpose
    })

    return programming_items, unsupported


def _determine_allowed_languages(
    programming_items: list[AssessmentPlanItem],
    candidate_languages: list[str] | None = None,
) -> list[str]:
    """
    A job that names a language pins it. When the job leaves the language open, the candidate is offered the
    languages their own resume shows (best first), so they solve the problems in a language they actually know;
    only a candidate with no usable language on the resume gets the default set.
    """

    general_purpose = _general_purpose_languages()

    pinned = {
        normalize_language_name(item.technology)
        for item in programming_items
        if item.technology and normalize_language_name(item.technology) in general_purpose
    }

    if pinned:
        return sorted(pinned)

    known = [
        language for language in dict.fromkeys(candidate_languages or [])
        if language in general_purpose
    ]

    if known:
        return known                                    # order kept: the candidate's strongest language first

    return sorted(general_purpose)


def _dominant_difficulty(blueprint: AssessmentBlueprint) -> str:

    dist = blueprint.difficulty_distribution

    return max(
        [("basic", dist.basic), ("intermediate", dist.intermediate), ("advanced", dist.advanced)],
        key=lambda pair: pair[1],
    )[0]


# Junior/mid/senior progression guidance (Task 5G, Section 9) — a
# textual instruction to Gemini, never a hardcoded question. The
# actual difficulty distribution per candidate still comes from the
# deterministic blueprint_engine.py; this only tells Gemini HOW to
# spread 5 questions across that candidate's dominant difficulty
# band so the 5 are not all identical.
_DIFFICULTY_PROGRESSION_HINT = {
    "basic": "mostly basic, with 1-2 intermediate questions to differentiate stronger candidates",
    "intermediate": "a mix of intermediate questions with 1-2 advanced questions for the strongest candidates",
    "advanced": "mostly intermediate-to-advanced questions, including at least one advanced, reasoning-heavy scenario",
}


def _build_gemini_prompt(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: AssessmentPlan,
    allowed_languages: list[str],
    resume_skills: list[str] | None = None,
    variation: str = "",
) -> str:

    difficulty = _dominant_difficulty(blueprint)
    required_skills = getattr(job, "required_skills", "") or "(not specified)"
    job_description = getattr(job, "description", "") or "(not specified)"
    progression_hint = _DIFFICULTY_PROGRESSION_HINT.get(
        difficulty, _DIFFICULTY_PROGRESSION_HINT["intermediate"],
    )

    # Reuse the ALREADY-COMPUTED, deterministic blueprint signal
    # (blueprint_engine.py derives this from the candidate's real
    # resume/profile) instead of asking Gemini to re-derive it —
    # Gemini only ever receives facts already established elsewhere
    # in the pipeline, never guesses at candidate skill from scratch.
    priority_skills = [
        f"{item.skill} (priority: {item.priority}, candidate match: {item.match_status})"
        for item in blueprint.skills_to_assess
    ] or ["(no specific skill breakdown available)"]

    focus_lines = []
    if blueprint.job_focus_areas:
        focus_lines.append(
            "Job-critical focus areas: " + ", ".join(blueprint.job_focus_areas)
        )
    if blueprint.candidate_focus_areas:
        focus_lines.append(
            "Candidate's own demonstrated focus areas: " + ", ".join(blueprint.candidate_focus_areas)
        )
    if blueprint.skill_gaps:
        focus_lines.append(
            "Skill gaps between candidate and job (do not assume the "
            "candidate already knows these): " + ", ".join(blueprint.skill_gaps)
        )
    focus_context = "\n".join(focus_lines) or "(no additional focus-area signal available)"

    return f"""
You are writing a professional, role-relevant technical assessment
for a real hiring pipeline — the kind of question a senior engineer
at this company would actually ask a candidate, not a generic
programming-101 exercise. Ground every question in a concrete,
realistic scenario tied to the job below (e.g. for a backend/API
role: request validation, rate limiting, response aggregation,
caching, retry/error handling, data transformation; for a data
role: aggregation, joins/window-function-style reasoning, duplicate
handling; for a frontend/JS role: data/state transformation,
filtering/sorting, event/API-response handling). Only use a
classic algorithmic exercise (e.g. string reversal, palindrome
check) if the job genuinely calls for that exact competency — do
not default to it out of convenience, and do not repeat the same
underlying task (e.g. two "count the frequency of X" questions)
across the 5 questions.

Generate a language-neutral programming assessment as a JSON
object with EXACTLY this shape (no extra text, no markdown fences):

{{
  "main_questions": [
    {{
      "question_id": 1,
      "title": "...",
      "scenario": "1-3 sentences: the real-world situation this question is set in",
      "description": "the concrete task the candidate must implement",
      "requirements": ["explicit functional requirement 1", "requirement 2"],
      "constraints": "...",
      "examples": [{{"input": "...", "output": "..."}}],
      "input_specification": "...",
      "output_specification": "...",
      "expected_complexity": "e.g. O(n) time, O(1) extra space, or null if not meaningful",
      "notes": "any clarifying edge-case note, or null",
      "difficulty": "easy|medium|hard",
      "technology": "the concept/skill this question is fundamentally about, e.g. 'concurrency' or 'data aggregation'",
      "skills_assessed": ["skill 1", "skill 2"],
      "required_technology": null,
      "allowed_languages": {json.dumps(allowed_languages)},
      "execution_mode": "console_coding",
      "points": 20.0,
      "subparts": [
        {{
          "subpart_id": "sp1",
          "description": "...",
          "weight": 0.5,
          "test_cases": [
            {{"test_case_id": "tc1", "stdin": "...", "expected_stdout": "...", "weight": 0.6, "is_hidden": true, "description": "..."}},
            {{"test_case_id": "tc2", "stdin": "...", "expected_stdout": "...", "weight": 0.4, "is_hidden": true, "description": "..."}}
          ]
        }}
      ]
    }}
  ],
  "generated_by": "gemini"
}}

RULES (all are validated server-side and enforced regardless of
what you return):
- EXACTLY 5 entries in "main_questions", covering 5 MEANINGFULLY
  DIFFERENT scenarios/core tasks — never near-duplicates of each
  other (server-side duplicate detection will reject and discard
  the whole set if two questions are too similar).
- Each main question has 2 to 4 "subparts".
- Each subpart's test_cases' "weight" values sum to EXACTLY 1.0.
- Each question's subparts' "weight" values sum to EXACTLY 1.0.
- All 5 questions' "points" sum to EXACTLY 100.
- Every "expected_stdout" must be the ACTUAL, CORRECT output for
  its "stdin" — you are defining ground truth for automated
  grading, not a hint. Do not invent a plausible-looking but
  wrong expected output. Every requirement/constraint you state
  must be objectively checkable by an automated stdin/stdout test —
  never write a requirement that cannot be mechanically verified.
- Test coverage per question should include, where meaningful: a
  normal case, a boundary/edge case, and (for medium/hard
  questions) a larger or repeated/duplicate-value input.
- "stdin"/"expected_stdout" must be plain text, never tied to any
  one programming language's syntax or input-parsing convention —
  the same test case must be answerable in any of: {allowed_languages}.
- "allowed_languages" on every question must be exactly {allowed_languages}
  — never add a language outside this list.
- "required_technology" must be null unless the job STRICTLY
  requires one specific one of {allowed_languages} for this
  question; if set, it must already be one of allowed_languages.
- Do not write Python-specific (or any language-specific) code
  into "description"/"constraints"/"examples"/"requirements" —
  describe the problem, never the solution's syntax.
- "skills_assessed" must only name skills genuinely exercised by
  that question's own task — never pad it with unrelated skills.
- question_id values 1-5, no duplicates.

EXTRA RULES FOR THIS CANDIDATE
- VARIATION CODE: {variation}. This test is generated separately for every candidate: use the code to pick a fresh scenario, fresh
  names and fresh numbers so the questions differ from every other candidate's test for the same job.
- Every question MUST also include "reference_solution_python": a complete, correct Python 3 program that reads stdin and prints
  stdout for that question (standard library only). The server RUNS it on every test input and uses ITS output as the expected output,
  so it must be right and must print something for every test case.
- Ground the 5 questions in the job's real work AND the candidate's own skills below; do not ask about tools the job never uses.
- Candidate skills from the resume: {", ".join(resume_skills or []) or "not specified"}

JOB CONTEXT
Title: {job.title}
Description: {job_description}
Required skills: {required_skills}

CANDIDATE CONTEXT
Experience level: {blueprint.candidate_experience_level}
Dominant difficulty for this candidate: {difficulty}
Difficulty progression across the 5 questions: {progression_hint}
Skills to assess, in priority order: {"; ".join(priority_skills)}
{focus_context}

ASSESSMENT PLAN CONTEXT
Role type: {plan.role_type}
""".strip()


# ------------------------------------------------------------
# DUPLICATE AVOIDANCE (Task 5G, Section 13)
#
# A deterministic, content-based fingerprint — never an exact
# string match (two questions can be worded differently but still
# be the same underlying task) and never an embedding/AI call
# (would make "is this a duplicate" itself an unverified AI
# judgment). Normalizes on the STABLE, structural signal already
# present on every question: its core task words (from the title),
# the concept it tests (`technology`), and its difficulty — the
# same fields a human reviewer would use to say "these two are
# basically the same question".
# ------------------------------------------------------------

_FINGERPRINT_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on",
    "with", "your", "you", "given", "write", "implement", "build",
    "create", "design", "system", "function", "program",
})


def _question_fingerprint(question) -> tuple[str, str]:

    title_words = {
        word for word in "".join(
            ch.lower() if ch.isalnum() else " " for ch in question.title
        ).split()
        if word not in _FINGERPRINT_STOPWORDS
    }

    technology = (question.technology or "").strip().lower()

    return (technology, frozenset(title_words))


def _has_near_duplicate_questions(question_set: ProgrammingQuestionSetV2) -> bool:
    """
    Two questions are treated as near-duplicates when they target
    the SAME technology/concept AND share more than half of their
    (stopword-stripped) title vocabulary — e.g. "Word Frequency
    Counter" vs "Most Frequent Word Finder" under the same
    technology would collide; "Word Frequency Counter" vs "API
    Rate Limiter" would not, even under a loosely-matching
    technology label.
    """

    fingerprints = [_question_fingerprint(q) for q in question_set.main_questions]

    for i in range(len(fingerprints)):
        for j in range(i + 1, len(fingerprints)):

            tech_i, words_i = fingerprints[i]
            tech_j, words_j = fingerprints[j]

            if tech_i != tech_j or not tech_i:
                continue

            if not words_i or not words_j:
                continue

            overlap = len(words_i & words_j)
            smaller = min(len(words_i), len(words_j))

            if smaller > 0 and (overlap / smaller) > 0.5:
                return True

    return False


def _fallback_bank(allowed_languages: list[str]) -> ProgrammingQuestionSetV2:

    from backend.app.services.assessment.programming_fallback_bank_v2 import (
        build_fallback_question_set_v2,
    )

    return build_fallback_question_set_v2(allowed_languages)


def _run_reference(source: str, stdin: str) -> tuple[bool, str]:

    from backend.app.services.assessment.language_runner import execute_code

    result = execute_code("python", source, stdin)

    return bool(result.success), result.stdout or ""


def _parametric_set(allowed_languages: list[str], blueprint, job, rng=None) -> ProgrammingQuestionSetV2:

    from backend.app.services.assessment.coding_question_generator import parametric_coding_questions
    from backend.app.services.assessment.programming_question_builders import assemble_question_set

    band = "junior"

    experience = (blueprint.candidate_experience_level or "").lower()

    if "fresh" in experience or "entry" in experience or "intern" in experience:
        band = "fresher"
    elif "senior" in experience or "lead" in experience:
        band = "senior"

    job_text = " ".join(str(getattr(job, name, "") or "") for name in ("title", "description", "required_skills"))

    return assemble_question_set(
        parametric_coding_questions(5, band, allowed_languages, job_text, rng), generated_by="fallback",
    )


def _validated_gemini_set(raw: dict, allowed_languages: list[str], blueprint, job) -> ProgrammingQuestionSetV2 | None:
    """Every question keeps only what the server could verify by running its reference solution; others are replaced."""

    from backend.app.services.assessment.coding_question_generator import parametric_coding_question

    questions = raw.get("main_questions")

    if not isinstance(questions, list) or len(questions) != 5:
        return None

    used: set = set()
    replaced = 0

    for index, question in enumerate(questions):

        if not isinstance(question, dict):
            return None

        if not apply_reference_solutions(question, _run_reference):

            replaced += 1

            substitute = parametric_coding_question("medium", allowed_languages, __import__("random").Random(), "general", used)
            data = substitute.model_dump()
            data["question_id"] = question.get("question_id", index + 1)
            data["points"] = question.get("points", 20.0)
            questions[index] = data

    if replaced == 5:
        return None

    validation = validate_question_set_v2(raw)

    if not validation.valid or validation.question_set is None or _has_near_duplicate_questions(validation.question_set):
        return None

    logger.info("Programming questions: %s from Gemini (reference-checked), %s replaced.", 5 - replaced, replaced)

    return validation.question_set


def generate_programming_assessment_v2(
    job: Job,
    blueprint: AssessmentBlueprint,
    plan: AssessmentPlan,
    use_gemini: bool = True,
    candidate_languages: list[str] | None = None,
    resume_skills: list[str] | None = None,
) -> ProgrammingAssessmentGenerationResult:

    programming_items, unsupported_languages = _classify_programming_items(plan)

    if not programming_items:

        return ProgrammingAssessmentGenerationResult(
            status="not_applicable",
            question_set=None,
            required_technology=None,
            capability_state=None,
            message=(
                "This role's assessment plan does not include a "
                "programming assessment — no programming question "
                "set was generated."
            ),
        )

    if unsupported_languages:

        required_technology = unsupported_languages[0]
        capability = get_language_capability(required_technology)

        return ProgrammingAssessmentGenerationResult(
            status="capability_unavailable",
            question_set=None,
            required_technology=required_technology,
            capability_state=capability.implementation_status,
            message=(
                f"This job's programming assessment requires "
                f"{required_technology}, which JobPilot cannot yet "
                f"execute securely (capability state: "
                f"{capability.implementation_status}). No Python "
                f"substitute question was generated."
            ),
        )

    allowed_languages = _determine_allowed_languages(programming_items, candidate_languages)

    if use_gemini:

        try:

            prompt = _build_gemini_prompt(
                job, blueprint, plan, allowed_languages, resume_skills, uuid.uuid4().hex[:10],
            )
            raw_json_text = generate_json_content(prompt, retries=2)
            raw = json.loads(raw_json_text)

            question_set = _validated_gemini_set(raw, allowed_languages, blueprint, job)

            if question_set is not None:

                return ProgrammingAssessmentGenerationResult(
                    status="generated",
                    question_set=question_set,
                    required_technology=None,
                    capability_state=None,
                    message="Generated via Gemini; expected outputs verified by running reference solutions.",
                    generated_by="gemini",
                )

        except (GeminiUnavailableError, json.JSONDecodeError, ValueError) as exc:

            logger.warning("Gemini programming generation unavailable or invalid: %s", type(exc).__name__)

    # Never the old identical bank: random, role-flavoured problems whose expected outputs are computed
    fallback_set = _parametric_set(allowed_languages, blueprint, job)

    return ProgrammingAssessmentGenerationResult(
        status="generated",
        question_set=fallback_set,
        required_technology=None,
        capability_state=None,
        message="Generated by the parametric generator (Gemini unavailable).",
        generated_by="fallback",
    )

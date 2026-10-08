import logging
import random
import re

from backend.app.services.assessment import programming_groq_generator as groq_generator
from backend.app.services.assessment import programming_question_pool as question_pool
from backend.app.services.assessment.candidate_language_ranker import rank_languages
from backend.app.services.assessment.language_capability_registry import normalize_language_name
from backend.app.services.assessment.programming_allocation import allocate_questions, role_family
from backend.app.services.assessment.programming_generator_v2 import (
    ProgrammingAssessmentGenerationResult,
    _classify_programming_items,
    _determine_allowed_languages,
    _general_purpose_languages,
)
from backend.app.services.assessment.programming_question_builders import assemble_question_set

logger = logging.getLogger("jobpilot.assessment.orchestrator")

# ============================================================
# JOBPILOT AI - ROLE-BASED PROGRAMMING ASSESSMENT (backend / general phase)
#
#   1. The Assessment Blueprint decides how the 5 questions are shared between coding / SQL / web
#   2. The languages come from the JOB (never widened) and are ranked by the candidate's resume
#   3. Coding questions come from a four-tier hierarchy, and the source is reported accurately:
#
#        ai_generated          Groq (openai/gpt-oss-120b), every question validated (reference solution run,
#                              independent cross-check, duplicates, relevance, difficulty, language)
#        validated_pool        earlier validated Groq questions (their reference solutions are re-run)
#        role_aware_fallback   parametric problems from the role's own domain (computed answers)
#        generic_fallback      only when nothing role-specific is possible; never presented as personalized
#
#   A set is labelled with its WEAKEST tier. Gemini and no other provider is ever used here.
#   (SQL and web questions are still the earlier parametric builders until their own phases.)
# ============================================================

SOURCE_AI = "ai_generated"
SOURCE_POOL = "validated_pool"
SOURCE_ROLE_AWARE = "role_aware_fallback"
SOURCE_GENERIC = "generic_fallback"

_TIER_RANK = {SOURCE_AI: 0, SOURCE_POOL: 1, SOURCE_ROLE_AWARE: 2, SOURCE_GENERIC: 3}
PERSONALIZED_SOURCES = {SOURCE_AI, SOURCE_POOL, SOURCE_ROLE_AWARE}

DATA_TITLE = re.compile(r"\b(data analyst|data engineer|data scientist|bi developer|analytics|reporting)\b", re.I)
BACKEND_TEXT = re.compile(
    r"backend|back-end|(?<![a-z])api(?![a-z])|rest|server|django|flask|fastapi|spring|node|microservice|python|golang|\.net|php|laravel|"
    r"(?<![a-z])java(?![a-z])", re.I,
)
DATA_TEXT = re.compile(r"\b(sql|etl|pandas|analytics|reporting|data)\b", re.I)


def job_domain(job) -> str:
    """backend | data | general, from the job's title first, then its description and skills."""

    title = str(getattr(job, "title", "") or "")

    if DATA_TITLE.search(title):
        return "data"

    text = " ".join(str(getattr(job, name, "") or "") for name in ("title", "description", "required_skills", "technologies"))

    if BACKEND_TEXT.search(text):
        return "backend"

    if DATA_TEXT.search(text):
        return "data"

    return "general"


def weakest_source(sources: list[str]) -> str:
    return max(sources, key=lambda s: _TIER_RANK[s]) if sources else SOURCE_GENERIC


def _experience_band(blueprint, resume_analysis, profile) -> str:

    from backend.app.services.aptitude.ai_question_generator import resolve_experience_band

    return resolve_experience_band(
        getattr(blueprint, "candidate_experience_level", None),
        getattr(resume_analysis, "experience_level", None) if resume_analysis else None,
        getattr(resume_analysis, "summary", None) if resume_analysis else None,
        getattr(profile, "experience_level", None) if profile else None,
    )


def _candidate_skill_list(resume_analysis, profile) -> list[str]:

    from backend.app.services.assessment.candidate_language_ranker import _as_list

    skills = _as_list(getattr(resume_analysis, "skills", None) if resume_analysis else None)
    skills += _as_list(getattr(profile, "skills", None) if profile else None)
    skills += _as_list(getattr(profile, "preferred_technologies", None) if profile else None)

    return list(dict.fromkeys(skills))


def select_languages(programming_items, resume_analysis, profile) -> tuple[list[str], dict]:
    """
    The job decides which languages are allowed; the resume decides the order (strongest first).
    Returns (allowed_languages ranked, language_selection report).
    """

    general = _general_purpose_languages()
    pinned = sorted({
        normalize_language_name(item.technology)
        for item in programming_items
        if item.technology and normalize_language_name(item.technology) in general
    })

    job_allowed = pinned or sorted(general)
    ranked, reasons = rank_languages(job_allowed, resume_analysis, profile)

    if pinned:
        allowed = ranked                                                  # the job's languages, strongest first
        basis = "job-pinned languages ranked by resume evidence"
    else:
        evidenced = [r["language"] for r in reasons if r["score"] > 0]
        allowed = evidenced or ranked                                     # an open job: the candidate's own languages
        basis = "job names no language: the languages the resume shows, strongest first" if evidenced \
            else "job names no language and the resume shows none: default set"

    return allowed, {
        "selected": allowed[0], "allowed": allowed, "job_allowed": job_allowed, "job_pinned": bool(pinned),
        "basis": basis, "ranking": reasons,
    }


# ------------------------------------------------------------ the coding hierarchy

def _from_pool(slot_targets: dict[int, str], job, blueprint, allowed_languages, candidate_skills, exclude: set[str],
               rng, executor=None):
    """Pooled specs that still validate (their reference solutions are re-run). Returns {slot: (question, spec)}."""

    found = {}
    terms = sorted(groq_generator.job_terms(job, blueprint, candidate_skills))
    used_keys = []

    for slot, difficulty in slot_targets.items():

        for entry in question_pool.candidates(terms, difficulty, exclude | {s[1]["fingerprint"] for s in found.values()}, rng):

            spec = entry["spec"]

            try:
                question, _outputs = groq_generator.materialize(spec, allowed_languages, executor)
            except groq_generator.Rejected:
                question_pool.remove(entry["fingerprint"])                  # a pooled question that no longer validates is dropped
                continue

            found[slot] = (question, {**spec, "fingerprint": entry["fingerprint"]})
            used_keys.append(entry["fingerprint"])
            break

    if used_keys:
        question_pool.mark_used(used_keys)

    return found


def generate_coding_questions(count, job, blueprint, band, allowed_languages, candidate_skills, use_ai, rng, provider=None):
    """Returns (questions, sources, metadata) for `count` coding questions, one source per question."""

    from concurrent.futures import ThreadPoolExecutor

    from backend.app.services.assessment.coding_question_generator import parametric_coding_question

    targets = groq_generator.difficulty_slots(band, blueprint, count)
    slots = {}                                                              # slot -> (question, source, spec or None)
    metadata = {"provider": groq_generator.PROVIDER_NAME, "model": groq_generator.MODEL, "tiers_tried": []}
    seen: set[str] = set()

    if use_ai:

        metadata["tiers_tried"].append("groq")

        try:
            ai_items, report = groq_generator.generate_ai_questions(
                count, job, blueprint, band, allowed_languages, candidate_skills, seen, targets, provider=provider,
            )
            metadata["validation"] = report

            for slot, (question, spec) in zip(report["slots"], ai_items):
                slots[slot] = (question, SOURCE_AI, spec)

        except Exception as exc:                                            # Groq down / unconfigured: the next tier takes over
            metadata["validation"] = {"provider": groq_generator.PROVIDER_NAME, "errors": [f"{type(exc).__name__}"]}
            logger.warning("Groq programming generation unavailable: %s", exc)

    missing = {i: targets[i] for i in range(count) if i not in slots}

    if missing:

        metadata["tiers_tried"].append("validated_pool")

        with ThreadPoolExecutor(max_workers=groq_generator.MAX_WORKERS) as executor:
            pooled = _from_pool(
                missing, job, blueprint, allowed_languages, candidate_skills,
                {s[2]["fingerprint"] for s in slots.values() if s[2]}, rng, executor,
            )

        for slot, (question, spec) in pooled.items():
            slots[slot] = (question, SOURCE_POOL, spec)

    missing = {i: targets[i] for i in range(count) if i not in slots}

    if missing:

        metadata["tiers_tried"].append("parametric")
        domain = job_domain(job)
        used: set = set()

        for slot, difficulty in missing.items():

            level = {"easy": "easy", "medium": "medium", "hard": "hard"}[difficulty]
            question, template_domain = parametric_coding_question(level, allowed_languages, rng, domain, used, return_domain=True)

            # role-aware only when the problem really comes from the job's own domain
            source = SOURCE_ROLE_AWARE if domain != "general" and template_domain == domain else SOURCE_GENERIC
            slots[slot] = (question, source, None)

    ordered = [slots[i] for i in range(count)]

    if use_ai:
        fresh = [s[2] for s in ordered if s[1] == SOURCE_AI and s[2]]
        if fresh:
            question_pool.add(fresh, sorted(groq_generator.job_terms(job, blueprint, candidate_skills)))

    return [s[0] for s in ordered], [s[1] for s in ordered], metadata


# ------------------------------------------------------------ the assessment

def generate_role_based_assessment(
    job,
    blueprint,
    plan,
    user=None,
    profile=None,
    resume_analysis=None,
    use_ai: bool = True,
    rng: random.Random | None = None,
    provider=None,
) -> ProgrammingAssessmentGenerationResult:

    rng = rng or random.Random()

    allocation, explanation = allocate_questions(blueprint, plan, job)

    if not allocation:

        return ProgrammingAssessmentGenerationResult(
            status="not_applicable", question_set=None, required_technology=None, capability_state=None,
            message="This role's assessment plan does not include a programming, SQL or web assessment.",
        )

    programming_items, unsupported = _classify_programming_items(plan)

    if "coding" in allocation and unsupported:

        from backend.app.services.assessment.language_capability_registry import get_language_capability

        technology = unsupported[0]
        capability = get_language_capability(technology)

        return ProgrammingAssessmentGenerationResult(
            status="capability_unavailable", question_set=None, required_technology=technology,
            capability_state=capability.implementation_status,
            message=(f"This job's programming assessment requires {technology}, which JobPilot cannot yet execute securely "
                     f"(capability state: {capability.implementation_status}). No substitute question was generated."),
        )

    band = _experience_band(blueprint, resume_analysis, profile)
    skills = _candidate_skill_list(resume_analysis, profile)
    questions, sources = [], []
    metadata = {
        "provider": groq_generator.PROVIDER_NAME, "model": groq_generator.MODEL, "band": band,
        "allocation": allocation, "allocation_basis": explanation,
    }

    if "coding" in allocation:

        allowed_languages, selection = select_languages(programming_items, resume_analysis, profile)

        if role_family(job) == "frontend":
            # a frontend role's coding questions are JavaScript logic questions, whatever the plan pinned
            allowed_languages = ["javascript"]
            selection = {**selection, "selected": "javascript", "allowed": allowed_languages, "job_pinned": True,
                         "basis": "frontend role: JavaScript"}

        metadata["language_selection"] = selection

        coding, coding_sources, coding_meta = generate_coding_questions(
            allocation["coding"], job, blueprint, band, allowed_languages, skills, use_ai, rng, provider,
        )
        questions.extend(coding)
        sources.extend(coding_sources)
        metadata.update({k: v for k, v in coding_meta.items() if k not in ("provider", "model")})

    if "sql" in allocation:

        from backend.app.services.assessment.sql_question_generator import generate_sql_questions

        sql_questions, _ = generate_sql_questions(allocation["sql"], job, band, skills, use_gemini=False, rng=rng)
        questions.extend(sql_questions)
        sources.extend([SOURCE_ROLE_AWARE] * len(sql_questions))              # parametric until the SQL phase

    if "web" in allocation:

        from backend.app.services.assessment.frontend_question_generator import generate_frontend_questions

        web_questions, _ = generate_frontend_questions(allocation["web"], job, band, skills, use_gemini=False, rng=rng)
        questions.extend(web_questions)
        sources.extend([SOURCE_ROLE_AWARE] * len(web_questions))              # parametric until the frontend phase

    generated_by = weakest_source(sources)
    metadata["question_sources"] = sources
    metadata["personalized"] = generated_by in PERSONALIZED_SOURCES

    logger.info("Programming assessment: allocation=%s source=%s sources=%s", allocation, generated_by, sources)

    return ProgrammingAssessmentGenerationResult(
        status="generated",
        question_set=assemble_question_set(questions, generated_by=generated_by, metadata=metadata),
        required_technology=None,
        capability_state=None,
        message=f"Role-based assessment: {allocation}.",
        generated_by=generated_by,
    )

import json
import re
from typing import Optional

from backend.app.models.company import Company
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User

from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.assessment_plan import (
    AssessmentDifficulty,
    AssessmentPlan,
    AssessmentPlanItem,
    AssessmentPriority,
    AssessmentTypeName,
)

from backend.app.services.assessment.gemini_client import (
    GeminiUnavailableError,
    generate_json_content,
)
from backend.app.services.assessment.technology_capability_registry import (
    is_execution_supported,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT PLAN DECISION ENGINE (Task 5C Step 2)
#
# STANDALONE AND ADVISORY-ONLY. Nothing in the live assessment
# pipeline (assessment_sessions.py, aptitude_integration.py,
# programming_integration.py, interview_integration.py, or any
# frontend page) calls this module yet. It only answers "what
# SHOULD be assessed for this candidate/job" — it never creates
# questions, never mutates AssessmentSession / AssessmentAssignment
# / JobApplication, and never touches the database. Wiring this
# into the live pipeline is an explicitly separate, later,
# approved step.
#
# DECISION PRIORITY (highest to lowest — see build_assessment_plan
# and _scan_job_technology_signals):
#   1. Job.required_skills
#   2. Job.responsibilities
#   3. Job.technologies
#   4. Job.preferred_skills
#   5. Candidate skills relevant to the job
#   6. Candidate education
#   7. Candidate experience
#   8. Existing AssessmentBlueprint
#   9. Candidate skill_gaps
#
# The candidate profile personalizes DIFFICULTY and identifies
# strengths/gaps. It never determines WHICH technology/category
# gets assessed on its own — that decision is driven only by the
# job's own fields (see _scan_job_technology_signals, which reads
# exclusively from `job`, never from candidate skills, for
# technology SELECTION). A technology on the candidate's resume
# that the job does not call for is never added to the plan.
# ============================================================


# ------------------------------------------------------------
# Controlled keyword vocabularies. These are heuristics for the
# DETERMINISTIC fallback path only — Gemini, when used, is asked
# to reason more flexibly but is still constrained to the exact
# same AssessmentTypeName/technology-capability vocabulary by
# Pydantic validation + the post-validation execution_supported
# override (see _finalize_plan).
# ------------------------------------------------------------

# keyword (lowercase) -> canonical technology name
PROGRAMMING_LANGUAGE_KEYWORDS: dict[str, str] = {
    "python": "Python",
    "java": "Java",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "golang": "Go",
    "rust": "Rust",
    "php": "PHP",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "ruby": "Ruby",
    # "c" alone is deliberately excluded from keyword scanning —
    # far too many false-positive substring matches in ordinary
    # English text ("a", "c" as an abbreviation, etc.) for a
    # single-character keyword to be scanned safely.
    #
    # "javascript"/"typescript" are deliberately NOT scanned here
    # — they are ambiguous between frontend and backend (Node.js)
    # and are handled by their own dedicated disambiguation block
    # below (see JAVASCRIPT_FAMILY_KEYWORDS), which decides
    # "frontend" vs "backend" instead of defaulting both to
    # "programming". Scanning them here too would register the
    # SAME technology under two different assessment_type values.
}

# Canonical names for the JS-family keywords, used only by the
# dedicated disambiguation block below - kept separate from
# PROGRAMMING_LANGUAGE_KEYWORDS for the reason explained above.
JAVASCRIPT_FAMILY_KEYWORDS: dict[str, str] = {
    "javascript": "JavaScript",
    "typescript": "TypeScript",
}

DATABASE_KEYWORDS: dict[str, str] = {
    "postgresql": "SQL",
    "postgres": "SQL",
    "mysql": "SQL",
    "sql server": "SQL",
    "sql": "SQL",
}

FRONTEND_FRAMEWORK_KEYWORDS: dict[str, str] = {
    "react": "React",
    "angular": "Angular",
    "vue": "Vue",
}

FRONTEND_BASE_KEYWORDS: dict[str, str] = {
    "html": "HTML/CSS",
    "css": "HTML/CSS",
}

BACKEND_FRAMEWORK_KEYWORDS: dict[str, str] = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring boot": "Spring Boot",
    "spring": "Spring Boot",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    ".net": ".NET",
    "dotnet": ".NET",
}

# category -> keywords that justify adding it. Checked against
# job text only (title/required/preferred/responsibilities/
# requirements/description) — never against candidate data, for
# the same reason technology selection never reads candidate
# data (Step 15 fairness rule: the JOB determines what is
# assessed).
NON_TECHNICAL_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "hr_scenario": [
        "recruitment", "employee relations", "hr operations",
        "onboarding", "talent acquisition", "human resources",
        "hr executive", "hr generalist",
    ],
    "finance_case": [
        "financial analysis", "financial modelling",
        "financial modeling", "accounting", "budgeting",
        "forecasting", "financial analyst",
    ],
    "marketing_case": [
        "seo", "digital advertising", "content strategy",
        "campaign", "digital marketing", "brand strategy",
        "social media marketing",
    ],
    "data_analysis": [
        "data analysis", "data interpretation", "power bi",
        "tableau", "data analyst", "business intelligence",
    ],
    "excel": [
        "excel", "spreadsheet", "pivot table",
    ],
    "business_case": [
        "business case", "market research", "consulting",
        "go-to-market",
    ],
    "communication": [
        "communication skills", "stakeholder management",
        "presentation skills", "written communication",
    ],
}


def _parse_text_list(value) -> list[str]:
    """
    Best-effort parse of a free-text DB column into a list of
    individual items. Several columns across Job/CandidateProfile
    /ResumeAnalysis store the same kind of information in
    different formats (JSON-array string vs comma-separated
    text, per Task 5C Step 1's audit) — this handles both without
    assuming either, and never raises on malformed input.
    """

    if not value:
        return []

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    text = str(value).strip()

    if not text:
        return []

    try:

        parsed = json.loads(text)

        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]

    except (json.JSONDecodeError, TypeError):
        pass

    return [part.strip() for part in text.split(",") if part.strip()]


def _dominant_difficulty(blueprint: AssessmentBlueprint) -> AssessmentDifficulty:
    """
    The blueprint's difficulty_distribution is the authoritative,
    already-computed source (Task 5C's own instruction: "do not
    invent a completely separate difficulty system if the
    blueprint already provides the information") — this just
    picks whichever bucket the blueprint weighted most heavily.
    """

    dist = blueprint.difficulty_distribution

    buckets: list[tuple[AssessmentDifficulty, int]] = [
        ("basic", dist.basic),
        ("intermediate", dist.intermediate),
        ("advanced", dist.advanced),
    ]

    return max(buckets, key=lambda pair: pair[1])[0]


def _difficulty_for_technology(
    blueprint: AssessmentBlueprint,
    technology_or_topic: Optional[str],
) -> AssessmentDifficulty:
    """
    Refines the blueprint's dominant difficulty using the
    specific skill entry (if any) matching this technology/topic
    — a skill the blueprint marked "missing" is never jumped
    straight to advanced difficulty just because the overall
    distribution skews hard, and a "strong_match" skill is given
    at least some stretch rather than staying at the easiest
    bucket. This still treats the blueprint as the primary,
    authoritative source (Step 4's own rule) — it only narrows
    within what the blueprint already computed.
    """

    dominant = _dominant_difficulty(blueprint)

    if not technology_or_topic:
        return dominant

    needle = technology_or_topic.lower()

    matched_skill = next(
        (
            item for item in blueprint.skills_to_assess
            if needle in item.skill.lower() or item.skill.lower() in needle
        ),
        None,
    )

    if matched_skill is None:
        return dominant

    if matched_skill.match_status == "missing":
        return "basic" if dominant != "basic" else "basic"

    if matched_skill.match_status == "strong_match" and dominant == "basic":
        return "intermediate"

    return dominant


def _find_matching_source_text(
    keyword: str,
    required_skills: list[str],
    responsibilities_text: str,
    technologies: list[str],
    preferred_skills: list[str],
) -> tuple[int, str]:
    """
    Returns (priority_rank, source_field_label) for the HIGHEST-
    priority job field this keyword actually appears in, per
    Task 5C's decision hierarchy (required_skills=1,
    responsibilities=2, technologies=3, preferred_skills=4).
    Assumes the caller already confirmed the keyword matches
    somewhere in at least one of these — only used to attribute
    the winning source for the reason string / priority level.
    Uses the same word-boundary matching as the caller (never a
    plain substring check — see keyword_present in
    _scan_job_technology_signals for why: "java" is a literal
    substring of "javascript").
    """

    def contains(text: str) -> bool:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        return bool(re.search(pattern, text.lower()))

    if any(contains(skill) for skill in required_skills):
        return 1, "required skill"

    if contains(responsibilities_text):
        return 2, "job responsibility"

    if any(contains(tech) for tech in technologies):
        return 3, "required technology"

    if any(contains(skill) for skill in preferred_skills):
        return 4, "preferred skill"

    return 5, "job description"


ASSESSMENT_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "hr_scenario": "HR scenario",
    "finance_case": "Finance case",
    "marketing_case": "Marketing case",
    "data_analysis": "Data analysis",
    "excel": "Excel",
    "business_case": "Business case",
    "communication": "Communication",
    "role_specific_case": "Role-specific case",
}


_SOURCE_PRIORITY_TO_ASSESSMENT_PRIORITY: dict[int, AssessmentPriority] = {
    1: "critical",
    2: "high",
    3: "high",
    4: "medium",
    5: "low",
}


def _scan_job_technology_signals(job: Job) -> list[dict]:
    """
    Reads ONLY job fields (never candidate data) to decide WHICH
    technologies/technical categories are actually relevant —
    this is the "no random technology" / "job requirement
    overrides resume" rule enforced structurally: there is no
    code path in this function that can see candidate skills at
    all.

    Returns a list of dicts: {assessment_type, technology,
    source_priority_rank, source_label} — one per DISTINCT
    (assessment_type, technology) pair found, already deduped and
    ordered by source_priority_rank (best evidence first).
    """

    required_skills = _parse_text_list(job.required_skills)
    preferred_skills = _parse_text_list(job.preferred_skills)
    technologies = _parse_text_list(job.technologies)

    responsibilities_text = " ".join(
        filter(None, [job.responsibilities, job.requirements, job.description])
    ).lower()

    # The full combined text this function is allowed to search
    # across, for keyword presence only (job fields exclusively).
    haystacks = (
        [s.lower() for s in required_skills]
        + [responsibilities_text]
        + [t.lower() for t in technologies]
        + [s.lower() for s in preferred_skills]
    )

    combined_text = " ".join(haystacks)

    def keyword_present(keyword: str) -> bool:
        # Word-boundary-aware match, NOT a plain substring check —
        # "java" is a literal substring of "javascript", "sql" of
        # "postgresql" (already given its own dedicated keyword),
        # etc. \b doesn't reliably bound tokens containing "+"/"#"
        # /"." (e.g. "c++", "c#", "node.js"), so this uses explicit
        # alphanumeric lookaround instead.
        pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
        return bool(re.search(pattern, combined_text))

    found: dict[tuple[str, str], dict] = {}

    def register(assessment_type: AssessmentTypeName, technology: str, keyword: str):

        key = (assessment_type, technology)

        rank, label = _find_matching_source_text(
            keyword, required_skills, responsibilities_text,
            technologies, preferred_skills,
        )

        existing = found.get(key)

        if existing is None or rank < existing["source_priority_rank"]:

            found[key] = {
                "assessment_type": assessment_type,
                "technology": technology,
                "source_priority_rank": rank,
                "source_label": label,
                "matched_keyword": keyword,
            }

    for keyword, canonical in PROGRAMMING_LANGUAGE_KEYWORDS.items():

        if keyword_present(keyword):
            register("programming", canonical, keyword)

    for keyword, canonical in DATABASE_KEYWORDS.items():

        if keyword_present(keyword):
            register("sql", canonical, keyword)

    frontend_technology = None

    for keyword, canonical in FRONTEND_FRAMEWORK_KEYWORDS.items():

        if keyword_present(keyword):
            register("frontend", canonical, keyword)
            frontend_technology = canonical

    for keyword, canonical in FRONTEND_BASE_KEYWORDS.items():

        if keyword_present(keyword) and frontend_technology is None:
            register("frontend", canonical, keyword)

    # "javascript"/"typescript" alone (no react/angular/vue,
    # no node.js) reads as frontend; alongside node.js/nodejs it
    # is attributed to backend instead, since the language itself
    # is ambiguous without a framework signal.
    has_node = keyword_present("node.js") or keyword_present("nodejs")

    for keyword in ("javascript", "typescript"):

        if keyword_present(keyword):

            canonical = JAVASCRIPT_FAMILY_KEYWORDS[keyword]

            if has_node:
                register("backend", canonical, keyword)
            else:
                register("frontend", canonical, keyword)

    for keyword, canonical in BACKEND_FRAMEWORK_KEYWORDS.items():

        if keyword_present(keyword):
            register("backend", canonical, keyword)

    for category, keywords in NON_TECHNICAL_CATEGORY_KEYWORDS.items():

        for keyword in keywords:

            if keyword_present(keyword):
                register(category, None, keyword)  # type: ignore[arg-type]
                break  # one match is enough evidence for this category

    return sorted(found.values(), key=lambda item: item["source_priority_rank"])


def _question_count_for_type(assessment_type: AssessmentTypeName, blueprint: AssessmentBlueprint) -> int:

    if assessment_type == "aptitude":
        return max(1, min(10, blueprint.total_questions or 10))

    if assessment_type == "ai_interview":
        return 5  # matches the existing, unchanged interview generator's fixed structure

    if assessment_type == "programming":
        return 3  # matches the existing, unchanged programming generator's fixed structure

    if assessment_type in ("sql", "frontend", "backend"):
        return 2

    return 1


def _build_deterministic_plan(
    job: Job,
    blueprint: AssessmentBlueprint,
) -> AssessmentPlan:
    """
    The guaranteed-available fallback path — never depends on
    Gemini, never returns an empty plan (Task 5C's own explicit
    requirement), and never invents candidate information: every
    technology/category signal comes from _scan_job_technology_
    signals(), which reads only job fields.
    """

    items: list[AssessmentPlanItem] = []

    # Aptitude always first, matching the existing, unchanged
    # Self Intro -> Aptitude -> ... -> AI Interview pipeline.
    items.append(
        AssessmentPlanItem(
            assessment_type="aptitude",
            technology=None,
            execution_supported=False,
            reason=(
                "Aptitude is assessed for every role as part of "
                "JobPilot's standard assessment pipeline."
            ),
            priority="high",
            difficulty=_dominant_difficulty(blueprint),
            question_count=_question_count_for_type("aptitude", blueprint),
        )
    )

    signals = _scan_job_technology_signals(job)

    for signal in signals[:6]:  # keep the schema's max_length=8 headroom for aptitude+ai_interview

        assessment_type: AssessmentTypeName = signal["assessment_type"]
        technology = signal["technology"]
        source_label = signal["source_label"]
        matched_keyword = signal["matched_keyword"]

        priority = _SOURCE_PRIORITY_TO_ASSESSMENT_PRIORITY[
            signal["source_priority_rank"]
        ]

        if technology:

            reason = (
                f"{technology} is identified as a {source_label} "
                f"for this job (matched \"{matched_keyword}\")."
            )

        else:

            reason = (
                f"{ASSESSMENT_TYPE_DISPLAY_NAMES.get(assessment_type, assessment_type)} assessment "
                f"selected because \"{matched_keyword}\" appears as a "
                f"{source_label} for this job."
            )

        items.append(
            AssessmentPlanItem(
                assessment_type=assessment_type,
                technology=technology,
                execution_supported=is_execution_supported(technology),
                reason=reason,
                priority=priority,
                difficulty=_difficulty_for_technology(blueprint, technology),
                question_count=_question_count_for_type(assessment_type, blueprint),
            )
        )

    # No specific technology or competency area was found at all
    # (Task 5C test scenario: "job with no explicit technology")
    # — never leave the plan with only aptitude+interview and no
    # explanation of what the middle stage would be.
    if not signals:

        items.append(
            AssessmentPlanItem(
                assessment_type="role_specific_case",
                technology=None,
                execution_supported=False,
                reason=(
                    "No specific technology or competency area was "
                    "explicitly identified in the job description; a "
                    "general role-specific case assessment is used "
                    "instead."
                ),
                priority="medium",
                difficulty=_dominant_difficulty(blueprint),
                question_count=1,
            )
        )

    items.append(
        AssessmentPlanItem(
            assessment_type="ai_interview",
            technology=None,
            execution_supported=False,
            reason=(
                "An AI interview is conducted for every role as part "
                "of JobPilot's standard assessment pipeline."
            ),
            priority="high",
            difficulty=_dominant_difficulty(blueprint),
            question_count=_question_count_for_type("ai_interview", blueprint),
        )
    )

    return AssessmentPlan(
        role_type=job.title or "Unspecified Role",
        assessment_types=items,
        generated_by="deterministic_fallback",
    )


def _finalize_plan(plan: AssessmentPlan, source: "PlanSourceLiteral") -> AssessmentPlan:
    """
    Applied to EVERY plan regardless of origin (Gemini or
    deterministic) — recomputes execution_supported for every
    item from the one centralized registry, overriding whatever
    a Gemini response may have claimed. This is the single
    enforcement point for "never invent execution support."
    """

    finalized_items = [
        item.model_copy(
            update={
                "execution_supported": is_execution_supported(item.technology),
            }
        )
        for item in plan.assessment_types
    ]

    return plan.model_copy(
        update={
            "assessment_types": finalized_items,
            "generated_by": source,
        }
    )


PlanSourceLiteral = str  # see assessment_plan.PlanSource; avoids a circular import of the Literal alias here


ALLOWED_ASSESSMENT_TYPES_TEXT = ", ".join(
    AssessmentTypeName.__args__  # type: ignore[attr-defined]
)


def _build_gemini_prompt(
    job: Job,
    blueprint: AssessmentBlueprint,
    profile: Optional[CandidateProfile],
    resume_analysis: Optional[ResumeAnalysis],
    company: Optional[Company],
) -> str:

    candidate_skills = []

    if profile and profile.skills:
        candidate_skills.extend(_parse_text_list(profile.skills))

    if resume_analysis and resume_analysis.skills:
        candidate_skills.extend(_parse_text_list(resume_analysis.skills))

    skills_to_assess_lines = "\n".join(
        f"- {item.skill} (priority: {item.priority}, "
        f"candidate match: {item.match_status})"
        for item in blueprint.skills_to_assess
    ) or "(none provided)"

    return f"""
You are deciding WHAT KINDS of assessments are appropriate for a
job candidate — not writing any questions yet.

JOB
Title: {job.title}
Company: {company.company_name if company else job.company}
Required skills: {job.required_skills or "(not specified)"}
Preferred skills: {job.preferred_skills or "(not specified)"}
Technologies: {job.technologies or "(not specified)"}
Responsibilities: {job.responsibilities or "(not specified)"}
Requirements: {job.requirements or "(not specified)"}

CANDIDATE CONTEXT (use only to personalize difficulty/priority —
never to introduce a technology the job itself does not require)
Experience level: {blueprint.candidate_experience_level}
Candidate skills: {", ".join(candidate_skills) or "(not specified)"}
Skill gaps: {", ".join(blueprint.skill_gaps) or "(none identified)"}

ASSESSMENT BLUEPRINT (already computed — treat as authoritative)
Skills to assess:
{skills_to_assess_lines}
Difficulty distribution: basic={blueprint.difficulty_distribution.basic}, \
intermediate={blueprint.difficulty_distribution.intermediate}, \
advanced={blueprint.difficulty_distribution.advanced}

RULES
- Only select assessment types genuinely justified by the JOB's
  own requirements/responsibilities/technologies — never add a
  technology merely because it appears on the candidate's resume.
- Do not use the candidate's name, gender, age, or any personal
  characteristic unrelated to job-relevant skills.
- assessment_type must be one of exactly: {ALLOWED_ASSESSMENT_TYPES_TEXT}
- priority must be one of: critical, high, medium, low
- difficulty must be one of: basic, intermediate, advanced
- Always include "aptitude" first and "ai_interview" last.
- Return 2 to 8 total assessment_types entries.

Return ONLY a JSON object shaped exactly like this (no other text):

{{
  "role_type": "<short description of this role>",
  "assessment_types": [
    {{
      "assessment_type": "<one of the allowed values>",
      "technology": "<technology name or null>",
      "execution_supported": false,
      "reason": "<concise, job-relevant justification>",
      "priority": "<critical|high|medium|low>",
      "difficulty": "<basic|intermediate|advanced>",
      "question_count": <integer 1-10>
    }}
  ]
}}
""".strip()


def build_assessment_plan(
    job: Job,
    blueprint: AssessmentBlueprint,
    user: Optional[User] = None,
    profile: Optional[CandidateProfile] = None,
    resume_analysis: Optional[ResumeAnalysis] = None,
    company: Optional[Company] = None,
    use_gemini: bool = True,
) -> AssessmentPlan:
    """
    Builds an advisory AssessmentPlan for a job/candidate pairing.

    Tries Gemini first (if use_gemini and configured), constrained
    to the exact AssessmentTypeName vocabulary via Pydantic
    validation. On ANY failure — Gemini unavailable, malformed
    JSON, a value outside the allowed vocabulary, an invalid
    question_count, etc. — falls back to the deterministic engine,
    which never depends on Gemini and never returns an empty plan.

    execution_supported is ALWAYS recomputed server-side from
    technology_capability_registry.py for every item, regardless
    of which path produced the plan — never trusted from Gemini.
    """

    if use_gemini:

        try:

            prompt = _build_gemini_prompt(
                job, blueprint, profile, resume_analysis, company,
            )

            raw_json_text = generate_json_content(prompt)

            plan = AssessmentPlan.model_validate_json(raw_json_text)

            return _finalize_plan(plan, "gemini")

        except (GeminiUnavailableError, ValueError) as exc:

            # ValueError covers Pydantic's ValidationError (a
            # subclass of ValueError) — malformed JSON, an
            # assessment_type outside the allowed Literal set, an
            # out-of-range question_count, etc. all land here and
            # fall through to the deterministic path below.
            _ = exc  # intentionally not logged with prompt/candidate content

    deterministic_plan = _build_deterministic_plan(job, blueprint)

    return _finalize_plan(deterministic_plan, "deterministic_fallback")

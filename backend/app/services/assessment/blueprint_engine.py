from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User

from backend.app.schemas.assessment_blueprint import (
    AssessmentBlueprint,
    DifficultyDistribution,
    SkillAssessmentItem,
)

from backend.app.services.ai_candidate_matching import (
    normalize_text,
    parse_json_list,
    token_set,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT BLUEPRINT ENGINE
#
# Deterministic, explainable planning step that sits between
# the existing candidate-job match (ai_candidate_matching.py,
# left completely unmodified) and future Gemini question
# generation. This module decides WHAT should be assessed and
# WHY — it never writes question text and it never produces a
# score.
#
# Every skill-matching primitive here (normalize_text,
# parse_json_list, token_set) is imported from the existing
# matching engine rather than reimplemented, so this engine's
# notion of "does the candidate have this skill" is always
# consistent with calculate_candidate_match()'s own notion of
# it — this module only adds the extra strong/partial/missing
# granularity that a single aggregate score doesn't expose.
# ============================================================


DEFAULT_TOTAL_QUESTIONS = 10
MIN_TOTAL_QUESTIONS = 5
MAX_TOTAL_QUESTIONS = 30

# Share of the total reserved for general problem-solving
# questions that aren't tied to any one skill, matching the
# existing aptitude bank's own quantitative/logical/verbal/
# analytical categories.
GENERAL_QUESTION_SHARE = 0.2

# Question-allocation weight per assessment priority tier.
PRIORITY_WEIGHTS = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}

# Difficulty split per normalized candidate experience level.
# Values are proportions of the total question count and are
# the same fresher/junior/mid/senior vocabulary already used
# by services/aptitude/ai_question_generator.py.
EXPERIENCE_DIFFICULTY_BANDS = {
    "fresher": {"basic": 0.60, "intermediate": 0.35, "advanced": 0.05},
    "junior": {"basic": 0.40, "intermediate": 0.45, "advanced": 0.15},
    "mid": {"basic": 0.20, "intermediate": 0.50, "advanced": 0.30},
    "senior": {"basic": 0.10, "intermediate": 0.40, "advanced": 0.50},
}

GENERAL_CATEGORY_NAME = "Problem Solving"


# ============================================================
# ALLOCATION HELPER
# ============================================================

def _largest_remainder_allocation(
    total: int,
    weights: list[float],
) -> list[int]:
    """
    Split `total` whole units across `weights` proportionally,
    guaranteeing the result always sums to exactly `total` —
    plain integer division/rounding does not guarantee that,
    which would silently break "allocations must add up to the
    total" for the candidate/HR-facing blueprint.
    """

    count = len(weights)

    if total <= 0 or count == 0:
        return [0 for _ in range(count)]

    weight_sum = sum(weights)

    if weight_sum <= 0:
        weights = [1.0 for _ in range(count)]
        weight_sum = float(count)

    raw_shares = [
        total * weight / weight_sum
        for weight in weights
    ]

    allocations = [int(share) for share in raw_shares]

    remaining = total - sum(allocations)

    remainder_order = sorted(
        range(count),
        key=lambda index: raw_shares[index] - allocations[index],
        reverse=True,
    )

    for position in range(remaining):
        allocations[remainder_order[position % count]] += 1

    return allocations


# ============================================================
# EXPERIENCE LEVEL NORMALIZATION
# ============================================================

def _normalize_experience_level(
    resume_analysis: ResumeAnalysis | None,
    profile: CandidateProfile | None,
    user: User,
) -> tuple[str, str | None]:
    """
    Resolve one of fresher/junior/mid/senior from whichever
    source actually has a value, using the same fallback
    order already used by
    services/aptitude/ai_question_generator.py's prompt
    builder (resume analysis, then profile, then user).

    Returns (level, warning) — warning is None unless no
    source had a usable value, in which case a safe default
    is used and the caller is told so explicitly.
    """

    candidates = [
        resume_analysis.experience_level if resume_analysis else None,
        profile.experience_level if profile else None,
        getattr(user, "experience_level", None),
    ]

    for raw_value in candidates:

        text = normalize_text(raw_value)

        if not text:
            continue

        if "senior" in text:
            return "senior", None

        if "mid" in text:
            return "mid", None

        if "fresher" in text or "entry" in text:
            return "fresher", None

        if "junior" in text:
            return "junior", None

    return (
        "junior",
        (
            "Candidate experience level was not specified anywhere "
            "(resume analysis, profile, or account) — defaulted to "
            "'junior' for the difficulty distribution."
        ),
    )


# ============================================================
# SKILL MATCH TIER
#
# Mirrors ai_candidate_matching.contains_match()'s own two
# conditions (exact substring, then token overlap) but reports
# which one fired instead of collapsing both into one boolean —
# that extra granularity is what lets this engine tell strong
# evidence apart from weak evidence, which the aggregate skill
# score in calculate_candidate_match() intentionally does not.
# ============================================================

def _skill_match_tier(
    candidate_text: str,
    skill: str,
) -> str:

    skill_text = normalize_text(skill)

    if not candidate_text or not skill_text:
        return "none"

    if skill_text in candidate_text:
        return "exact"

    if token_set(candidate_text) & token_set(skill_text):
        return "fuzzy"

    return "none"


def _classify_skill(
    skill: str,
    resume_skill_text: str,
    profile_skill_text: str,
) -> str:

    resume_tier = _skill_match_tier(resume_skill_text, skill)
    profile_tier = _skill_match_tier(profile_skill_text, skill)

    if resume_tier == "exact" or profile_tier == "exact":
        return "strong_match"

    if resume_tier == "fuzzy" or profile_tier == "fuzzy":
        return "partial_match"

    return "missing"


# ============================================================
# PRIORITY + EXPLANATION
# ============================================================

def _priority_and_reason(
    skill: str,
    is_required: bool,
    match_status: str,
) -> tuple[str, str]:

    if is_required:

        if match_status == "missing":
            return (
                "critical",
                f"{skill} was prioritized because it is required by "
                "the selected job and was not found in the "
                "candidate's demonstrated skills.",
            )

        if match_status == "partial_match":
            return (
                "high",
                f"{skill} was prioritized because it is required by "
                "the job but the candidate's evidence for it is "
                "limited.",
            )

        return (
            "medium",
            f"{skill} is required by the job and is already "
            "demonstrated by the candidate — assessed to confirm "
            "depth.",
        )

    if match_status == "missing":
        return (
            "medium",
            f"{skill} is a preferred skill for this job and was not "
            "found in the candidate's demonstrated skills.",
        )

    if match_status == "partial_match":
        return (
            "low",
            f"{skill} is a preferred skill with limited evidence in "
            "the candidate's profile.",
        )

    return (
        "low",
        f"{skill} is a preferred skill and is already demonstrated "
        "by the candidate.",
    )


# ============================================================
# SKILL COLLECTION (JOB SIDE)
# ============================================================

def _collect_job_skills(
    job: Job,
) -> tuple[list[str], list[str]]:
    """
    Returns (required_skills, preferred_skills) as
    display-name lists, deduplicated case-insensitively while
    keeping the first-seen casing, with any skill already
    counted as required removed from the preferred list so
    every skill is assessed exactly once.
    """

    required = parse_json_list(job.required_skills)
    preferred = parse_json_list(job.preferred_skills)

    seen_required: dict[str, str] = {}

    for skill in required:

        key = normalize_text(skill)

        if key and key not in seen_required:
            seen_required[key] = skill

    seen_preferred: dict[str, str] = {}

    for skill in preferred:

        key = normalize_text(skill)

        if key and key not in seen_required and key not in seen_preferred:
            seen_preferred[key] = skill

    return list(seen_required.values()), list(seen_preferred.values())


# ============================================================
# MAIN ENGINE
# ============================================================

def build_assessment_blueprint(
    user: User,
    profile: CandidateProfile | None,
    resume_analysis: ResumeAnalysis | None,
    job: Job,
    total_questions: int = DEFAULT_TOTAL_QUESTIONS,
    assessment_type: str = "aptitude",
) -> AssessmentBlueprint:

    warnings: list[str] = []

    if resume_analysis is None:
        warnings.append(
            "No resume analysis found for this candidate — skill "
            "matching relies on the candidate profile only."
        )

    if profile is None:
        warnings.append(
            "No candidate profile found — skill matching relies on "
            "the resume analysis only."
        )

    # --------------------------------------------------------
    # STEP 1 + 2 — COLLECT & NORMALIZE CANDIDATE SKILLS
    # --------------------------------------------------------

    resume_skills = (
        parse_json_list(resume_analysis.skills)
        if resume_analysis
        else []
    )

    profile_skills = (
        parse_json_list(profile.skills)
        if profile
        else []
    )

    resume_skill_text = " | ".join(
        normalize_text(skill)
        for skill in resume_skills
        if normalize_text(skill)
    )

    profile_skill_text = " | ".join(
        normalize_text(skill)
        for skill in profile_skills
        if normalize_text(skill)
    )

    # --------------------------------------------------------
    # STEP 1 — COLLECT JOB SKILLS
    # --------------------------------------------------------

    required_skills, preferred_skills = _collect_job_skills(job)

    if not required_skills and not preferred_skills:
        warnings.append(
            "This job has no structured required/preferred skills "
            "on file — the assessment defaults entirely to general "
            "problem solving."
        )

    # --------------------------------------------------------
    # STEP 3 + 4 — CLASSIFY & PRIORITIZE EACH SKILL
    # --------------------------------------------------------

    skill_rows: list[dict] = []

    for skill in required_skills:

        match_status = _classify_skill(
            skill,
            resume_skill_text,
            profile_skill_text,
        )

        priority, reason = _priority_and_reason(
            skill,
            is_required=True,
            match_status=match_status,
        )

        skill_rows.append({
            "skill": skill,
            "priority": priority,
            "match_status": match_status,
            "reason": reason,
        })

    for skill in preferred_skills:

        match_status = _classify_skill(
            skill,
            resume_skill_text,
            profile_skill_text,
        )

        priority, reason = _priority_and_reason(
            skill,
            is_required=False,
            match_status=match_status,
        )

        skill_rows.append({
            "skill": skill,
            "priority": priority,
            "match_status": match_status,
            "reason": reason,
        })

    # --------------------------------------------------------
    # STEP 5 — QUESTION ALLOCATION
    # --------------------------------------------------------

    general_question_count = (
        max(1, round(total_questions * GENERAL_QUESTION_SHARE))
        if skill_rows
        else total_questions
    )

    general_question_count = min(
        general_question_count,
        total_questions,
    )

    skill_question_budget = total_questions - general_question_count

    if skill_rows and skill_question_budget > 0:

        weights = [
            PRIORITY_WEIGHTS[row["priority"]]
            for row in skill_rows
        ]

        allocations = _largest_remainder_allocation(
            skill_question_budget,
            weights,
        )

        for row, count in zip(skill_rows, allocations):
            row["question_count"] = count

    else:

        for row in skill_rows:
            row["question_count"] = 0

        general_question_count = total_questions

    skills_to_assess = [
        SkillAssessmentItem(
            skill=row["skill"],
            priority=row["priority"],
            match_status=row["match_status"],
            question_count=row["question_count"],
            reason=row["reason"],
        )
        for row in skill_rows
    ]

    # --------------------------------------------------------
    # STEP 6 — DIFFICULTY DISTRIBUTION
    # --------------------------------------------------------

    experience_level, experience_warning = _normalize_experience_level(
        resume_analysis,
        profile,
        user,
    )

    if experience_warning:
        warnings.append(experience_warning)

    difficulty_shares = EXPERIENCE_DIFFICULTY_BANDS[experience_level]

    basic_count, intermediate_count, advanced_count = (
        _largest_remainder_allocation(
            total_questions,
            [
                difficulty_shares["basic"],
                difficulty_shares["intermediate"],
                difficulty_shares["advanced"],
            ],
        )
    )

    difficulty_distribution = DifficultyDistribution(
        basic=basic_count,
        intermediate=intermediate_count,
        advanced=advanced_count,
    )

    # --------------------------------------------------------
    # STEP 7 — ASSESSMENT CATEGORIES
    # --------------------------------------------------------

    assessment_categories = [
        row["skill"]
        for row in skill_rows
    ]

    assessment_categories.append(GENERAL_CATEGORY_NAME)

    # --------------------------------------------------------
    # STEP 8 — CANDIDATE FOCUS AREAS
    # --------------------------------------------------------

    candidate_focus_areas = [
        row["reason"]
        for row in skill_rows
        if row["priority"] in ("critical", "high")
    ]

    # --------------------------------------------------------
    # STEP 9 — JOB FOCUS AREAS
    # --------------------------------------------------------

    job_focus_skills = required_skills or preferred_skills

    job_focus_areas = (
        [
            "Primary assessment focus: "
            + ", ".join(job_focus_skills) + "."
        ]
        if job_focus_skills
        else [
            "This job has no structured skill requirements on file; "
            "assessment focus defaults to general problem solving."
        ]
    )

    # --------------------------------------------------------
    # SKILL GAPS (flat list, for quick reference)
    # --------------------------------------------------------

    skill_gaps = [
        row["skill"]
        for row in skill_rows
        if row["match_status"] == "missing"
    ]

    # --------------------------------------------------------
    # ALGORITHM EXPLANATION (overall, not per-skill)
    # --------------------------------------------------------

    algorithm_explanation = [
        f"Experience level resolved to '{experience_level}', used "
        "for the difficulty distribution below.",

        f"{total_questions} total questions: "
        f"{general_question_count} reserved for general "
        f"'{GENERAL_CATEGORY_NAME}', "
        f"{total_questions - general_question_count} distributed "
        f"across {len(skill_rows)} job skill(s) by priority "
        "(critical > high > medium > low).",

        "Difficulty distribution for "
        f"'{experience_level}': "
        f"{int(difficulty_shares['basic'] * 100)}% basic, "
        f"{int(difficulty_shares['intermediate'] * 100)}% "
        "intermediate, "
        f"{int(difficulty_shares['advanced'] * 100)}% advanced.",

        f"{len(skill_gaps)} required/preferred skill(s) had no "
        "evidence in the candidate's resume analysis or profile.",
    ]

    return AssessmentBlueprint(
        candidate_id=user.id,
        job_id=job.id,
        job_title=job.title,
        assessment_type=assessment_type,
        total_questions=total_questions,
        candidate_experience_level=experience_level,
        skills_to_assess=skills_to_assess,
        difficulty_distribution=difficulty_distribution,
        assessment_categories=assessment_categories,
        candidate_focus_areas=candidate_focus_areas,
        job_focus_areas=job_focus_areas,
        skill_gaps=skill_gaps,
        algorithm_explanation=algorithm_explanation,
        warnings=warnings,
    )

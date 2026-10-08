import json
import re
from typing import Any


# ============================================================
# JOBPILOT AI - CANDIDATE MATCHING ENGINE
# ============================================================

AI_SHORTLIST_THRESHOLD = 75.0


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip().lower()


def parse_json_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    text = str(value).strip()

    if not text:
        return []

    try:
        parsed = json.loads(text)

        if isinstance(parsed, list):
            return [
                str(item).strip()
                for item in parsed
                if str(item).strip()
            ]

        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]

    except (json.JSONDecodeError, TypeError):
        pass

    parts = re.split(r"[,;\n|]+", text)

    return [
        part.strip()
        for part in parts
        if part.strip()
    ]


def token_set(value: Any) -> set[str]:
    text = normalize_text(value)

    if not text:
        return set()

    tokens = re.findall(
        r"[a-zA-Z0-9+#.]+",
        text
    )

    return {
        token
        for token in tokens
        if len(token) > 1
    }


def contains_match(
    source: str,
    target: str
) -> bool:

    source_text = normalize_text(source)
    target_text = normalize_text(target)

    if not source_text or not target_text:
        return False

    if target_text in source_text:
        return True

    source_tokens = token_set(source_text)
    target_tokens = token_set(target_text)

    return bool(
        source_tokens.intersection(
            target_tokens
        )
    )


# ============================================================
# SKILL MATCH
# ============================================================

def calculate_skill_match(
    candidate_skills: list[str],
    required_skills: list[str],
    preferred_skills: list[str]
) -> float:

    candidate_text = " | ".join(
        normalize_text(skill)
        for skill in candidate_skills
        if normalize_text(skill)
    )

    required = [
        skill.strip()
        for skill in required_skills
        if str(skill).strip()
    ]

    preferred = [
        skill.strip()
        for skill in preferred_skills
        if str(skill).strip()
    ]

    if not required and not preferred:
        return 100.0

    required_score = 100.0

    if required:

        matched_required = sum(
            1
            for skill in required
            if contains_match(
                candidate_text,
                skill
            )
        )

        required_score = (
            matched_required / len(required)
        ) * 100.0

    preferred_score = 100.0

    if preferred:

        matched_preferred = sum(
            1
            for skill in preferred
            if contains_match(
                candidate_text,
                skill
            )
        )

        preferred_score = (
            matched_preferred / len(preferred)
        ) * 100.0

    if required and preferred:

        score = (
            required_score * 0.80
            + preferred_score * 0.20
        )

    elif required:

        score = required_score

    else:

        score = preferred_score

    return round(
        max(0.0, min(100.0, score)),
        2
    )


# ============================================================
# ROLE MATCH
# ============================================================

def calculate_role_match(
    candidate_roles: list[str],
    current_role: str | None,
    job_title: str | None,
    job_description: str | None
) -> float:

    roles = [
        normalize_text(role)
        for role in candidate_roles
        if normalize_text(role)
    ]

    if current_role:
        roles.append(
            normalize_text(current_role)
        )

    roles = list(dict.fromkeys(roles))

    if not roles:
        return 50.0

    job_text = " ".join(
        [
            normalize_text(job_title),
            normalize_text(job_description)
        ]
    )

    best_score = 0.0

    for role in roles:

        if not role:
            continue

        if role in job_text:
            best_score = max(
                best_score,
                100.0
            )
            continue

        role_tokens = token_set(role)
        job_tokens = token_set(job_text)

        if not role_tokens:
            continue

        overlap = len(
            role_tokens.intersection(
                job_tokens
            )
        )

        score = (
            overlap / len(role_tokens)
        ) * 100.0

        best_score = max(
            best_score,
            score
        )

    return round(
        min(100.0, best_score),
        2
    )


# ============================================================
# EXPERIENCE MATCH
# ============================================================

def extract_years(
    value: Any
) -> float | None:

    text = normalize_text(value)

    if not text:
        return None

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*years?",
        text
    )

    if range_match:

        try:

            low = float(
                range_match.group(1)
            )

            high = float(
                range_match.group(2)
            )

            return (low + high) / 2.0

        except ValueError:
            return None

    plus_match = re.search(
        r"(\d+(?:\.\d+)?)\s*\+?\s*years?",
        text
    )

    if plus_match:

        try:
            return float(
                plus_match.group(1)
            )
        except ValueError:
            return None

    if "fresher" in text:
        return 0.0

    return None


def extract_min_required_years(
    value: Any
) -> float:

    text = normalize_text(value)

    if not text:
        return 0.0

    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)",
        text
    )

    if range_match:

        try:
            return float(
                range_match.group(1)
            )
        except ValueError:
            pass

    plus_match = re.search(
        r"(\d+(?:\.\d+)?)\s*\+",
        text
    )

    if plus_match:

        try:
            return float(
                plus_match.group(1)
            )
        except ValueError:
            pass

    single_match = re.search(
        r"(\d+(?:\.\d+)?)",
        text
    )

    if single_match:

        try:
            return float(
                single_match.group(1)
            )
        except ValueError:
            pass

    return 0.0


def calculate_experience_match(
    candidate_experience: Any,
    candidate_level: str | None,
    job_experience: str | None
) -> float:

    required_years = (
        extract_min_required_years(
            job_experience
        )
    )

    candidate_years = (
        extract_years(
            candidate_experience
        )
    )

    if candidate_years is None:

        level = normalize_text(
            candidate_level
        )

        if "fresher" in level:
            candidate_years = 0.0

        elif "junior" in level:
            candidate_years = 1.0

        elif "mid" in level:
            candidate_years = 3.0

        elif "senior" in level:
            candidate_years = 6.0

        else:
            return 50.0

    if required_years <= 0:
        return 100.0

    if candidate_years >= required_years:
        return 100.0

    score = (
        candidate_years /
        required_years
    ) * 100.0

    return round(
        max(
            0.0,
            min(
                100.0,
                score
            )
        ),
        2
    )


# ============================================================
# LOCATION MATCH
# ============================================================

def calculate_location_match(
    candidate_location: str | None,
    preferred_locations: Any,
    job_location: str | None,
    work_mode: str | None
) -> float:

    candidate = normalize_text(
        candidate_location
    )

    preferred = [
        normalize_text(item)
        for item in parse_json_list(
            preferred_locations
        )
        if normalize_text(item)
    ]

    job = normalize_text(
        job_location
    )

    mode = normalize_text(
        work_mode
    )

    if "remote" in mode:
        return 100.0

    if not job:
        return 70.0

    locations = [candidate] + preferred

    for location in locations:

        if not location:
            continue

        if (
            location in job
            or job in location
        ):
            return 100.0

        location_tokens = token_set(
            location
        )

        job_tokens = token_set(
            job
        )

        if (
            location_tokens
            and job_tokens
            and location_tokens.intersection(
                job_tokens
            )
        ):
            return 85.0

    return 40.0


# ============================================================
# TECHNOLOGY MATCH
# ============================================================

def calculate_technology_match(
    candidate_skills: list[str],
    technologies: Any
) -> float:

    required = [
        item.strip()
        for item in parse_json_list(
            technologies
        )
        if item.strip()
    ]

    if not required:
        return 100.0

    candidate_text = " | ".join(
        normalize_text(skill)
        for skill in candidate_skills
        if normalize_text(skill)
    )

    matched = sum(
        1
        for technology in required
        if contains_match(
            candidate_text,
            technology
        )
    )

    return round(
        (
            matched /
            len(required)
        ) * 100.0,
        2
    )


# ============================================================
# EDUCATION MATCH
# ============================================================

def calculate_education_match(
    candidate_education: Any,
    job_education: str | None
) -> float:

    required = normalize_text(
        job_education
    )

    candidate = normalize_text(
        candidate_education
    )

    if not required:
        return 100.0

    if not candidate:
        return 40.0

    if required in candidate:
        return 100.0

    required_tokens = token_set(
        required
    )

    candidate_tokens = token_set(
        candidate
    )

    if not required_tokens:
        return 70.0

    overlap = len(
        required_tokens.intersection(
            candidate_tokens
        )
    )

    ratio = (
        overlap /
        len(required_tokens)
    )

    if ratio >= 0.75:
        return 100.0

    if ratio >= 0.50:
        return 80.0

    if ratio >= 0.25:
        return 60.0

    return 40.0


# ============================================================
# MAIN MATCHING ENGINE
# ============================================================

def calculate_candidate_match(
    candidate_analysis: Any,
    candidate_profile: Any,
    candidate_user: Any,
    job: Any
) -> dict:

    if candidate_analysis is None:

        return {
            "score": 0.0,
            "ai_shortlist": False,
            "skill_score": 0.0,
            "experience_score": 0.0,
            "role_score": 0.0,
            "location_score": 0.0,
            "technology_score": 0.0,
            "education_score": 0.0,
            "matched_skills": [],
            "missing_skills": [],
            "recommendation": "Resume analysis required"
        }

    # --------------------------------------------------------
    # CANDIDATE DATA
    # --------------------------------------------------------

    candidate_skills = parse_json_list(
        getattr(
            candidate_analysis,
            "skills",
            None
        )
    )

    candidate_roles = parse_json_list(
        getattr(
            candidate_analysis,
            "job_roles",
            None
        )
    )

    candidate_education = getattr(
        candidate_analysis,
        "education",
        ""
    )

    candidate_experience = getattr(
        candidate_analysis,
        "experience",
        ""
    )

    candidate_level = getattr(
        candidate_analysis,
        "experience_level",
        ""
    )

    if candidate_profile:

        candidate_skills.extend(
            parse_json_list(
                getattr(
                    candidate_profile,
                    "skills",
                    None
                )
            )
        )

        candidate_roles.extend(
            parse_json_list(
                getattr(
                    candidate_profile,
                    "preferred_roles",
                    None
                )
            )
        )

        current_role = getattr(
            candidate_profile,
            "headline",
            None
        )

        if current_role:
            candidate_roles.append(
                current_role
            )

    candidate_skills = list(
        dict.fromkeys(
            skill.strip()
            for skill in candidate_skills
            if skill.strip()
        )
    )

    candidate_roles = list(
        dict.fromkeys(
            role.strip()
            for role in candidate_roles
            if role.strip()
        )
    )

    # --------------------------------------------------------
    # JOB DATA
    # --------------------------------------------------------

    required_skills = parse_json_list(
        getattr(
            job,
            "required_skills",
            None
        )
    )

    preferred_skills = parse_json_list(
        getattr(
            job,
            "preferred_skills",
            None
        )
    )

    technologies = parse_json_list(
        getattr(
            job,
            "technologies",
            None
        )
    )

    job_title = getattr(
        job,
        "title",
        ""
    )

    job_description = " ".join(
        [
            str(
                getattr(
                    job,
                    "description",
                    ""
                ) or ""
            ),
            str(
                getattr(
                    job,
                    "responsibilities",
                    ""
                ) or ""
            ),
            str(
                getattr(
                    job,
                    "requirements",
                    ""
                ) or ""
            )
        ]
    )

    # --------------------------------------------------------
    # COMPONENT SCORES
    # --------------------------------------------------------

    skill_score = (
        calculate_skill_match(
            candidate_skills,
            required_skills,
            preferred_skills
        )
    )

    role_score = (
        calculate_role_match(
            candidate_roles,
            getattr(
                candidate_user,
                "current_role",
                None
            ),
            job_title,
            job_description
        )
    )

    experience_score = (
        calculate_experience_match(
            candidate_experience,
            candidate_level,
            getattr(
                job,
                "experience",
                None
            )
        )
    )

    location_score = (
        calculate_location_match(
            getattr(
                candidate_user,
                "location",
                None
            ),
            (
                getattr(
                    candidate_profile,
                    "preferred_locations",
                    None
                )
                if candidate_profile
                else None
            ),
            getattr(
                job,
                "location",
                None
            ),
            getattr(
                job,
                "work_mode",
                None
            )
        )
    )

    technology_score = (
        calculate_technology_match(
            candidate_skills,
            technologies
        )
    )

    education_score = (
        calculate_education_match(
            candidate_education,
            getattr(
                job,
                "education",
                None
            )
        )
    )

    # --------------------------------------------------------
    # OVERALL SCORE
    # --------------------------------------------------------

    overall_score = (
        skill_score * 0.35
        + experience_score * 0.20
        + role_score * 0.15
        + location_score * 0.10
        + technology_score * 0.10
        + education_score * 0.10
    )

    overall_score = round(
        max(
            0.0,
            min(
                100.0,
                overall_score
            )
        ),
        2
    )

    # --------------------------------------------------------
    # MATCHED / MISSING SKILLS
    # --------------------------------------------------------

    candidate_skill_text = " | ".join(
        candidate_skills
    )

    matched_skills = []

    missing_skills = []

    for skill in required_skills:

        clean_skill = skill.strip()

        if not clean_skill:
            continue

        if contains_match(
            candidate_skill_text,
            clean_skill
        ):

            matched_skills.append(
                clean_skill
            )

        else:

            missing_skills.append(
                clean_skill
            )

    # --------------------------------------------------------
    # RECOMMENDATION
    # --------------------------------------------------------

    if overall_score >= 85:

        recommendation = (
            "Strongly Recommended"
        )

    elif overall_score >= AI_SHORTLIST_THRESHOLD:

        recommendation = (
            "Recommended"
        )

    elif overall_score >= 60:

        recommendation = (
            "Review"
        )

    else:

        recommendation = (
            "Not Recommended"
        )

    # --------------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------------

    return {
        "score": overall_score,

        "ai_shortlist": (
            overall_score >=
            AI_SHORTLIST_THRESHOLD
        ),

        "skill_score": skill_score,

        "experience_score":
            experience_score,

        "role_score":
            role_score,

        "location_score":
            location_score,

        "technology_score":
            technology_score,

        "education_score":
            education_score,

        "matched_skills":
            matched_skills,

        "missing_skills":
            missing_skills,

        "recommendation":
            recommendation
    }
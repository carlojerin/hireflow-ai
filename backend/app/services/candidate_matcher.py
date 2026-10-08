import re


# ==================================================
# TEXT HELPERS
# ==================================================

def normalize_text(value):
    if value is None:
        return ""

    return str(value).strip().lower()


def normalize_skill(skill):
    skill = normalize_text(skill)

    skill = skill.replace(".js", "js")
    skill = skill.replace(" ", "")

    return skill


# ==================================================
# EXPERIENCE
# ==================================================

def extract_experience_years(resume_analysis):
    explicit_years = resume_analysis.get(
        "experience_years"
    )

    if explicit_years is not None:
        try:
            return float(explicit_years)
        except (ValueError, TypeError):
            pass

    experience_level = normalize_text(
        resume_analysis.get(
            "experience_level",
            ""
        )
    )

    if "fresher" in experience_level:
        return 0

    if "entry" in experience_level:
        return 0

    if "intern" in experience_level:
        return 0

    experience = resume_analysis.get(
        "experience",
        []
    )

    if not isinstance(experience, list):
        experience = [experience]

    text = " ".join(
        str(item)
        for item in experience
    ).lower()

    patterns = [
        r"(\d+(?:\.\d+)?)\s*\+?\s*years?",
        r"(\d+(?:\.\d+)?)\s*yrs?"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text
        )

        if match:
            try:
                return float(
                    match.group(1)
                )
            except (
                ValueError,
                TypeError
            ):
                pass

    return 0


# ==================================================
# REQUIRED EXPERIENCE
# ==================================================

def get_required_experience(job):
    value = job.get(
        "minimum_experience_years"
    )

    if value is None:
        return None

    try:
        return float(value)
    except (
        ValueError,
        TypeError
    ):
        return None


# ==================================================
# SENIOR ROLE
# ==================================================

def is_senior_role(job):
    title = normalize_text(
        job.get(
            "title",
            ""
        )
    )

    senior_keywords = [
        "senior",
        "sr.",
        "sr ",
        "lead",
        "principal",
        "architect",
        "manager",
        "director",
        "head of",
        "staff engineer",
        "tech lead",
        "team lead"
    ]

    return any(
        keyword in title
        for keyword in senior_keywords
    )


# ==================================================
# FRESHER FRIENDLY
# ==================================================

def is_fresher_friendly(job):
    if job.get(
        "is_fresher_friendly",
        False
    ):
        return True

    title = normalize_text(
        job.get(
            "title",
            ""
        )
    )

    description = normalize_text(
        job.get(
            "description",
            ""
        )
    )

    text = (
        f"{title} {description}"
    )

    fresher_keywords = [
        "fresher",
        "freshers",
        "entry level",
        "entry-level",
        "graduate",
        "trainee",
        "intern",
        "internship",
        "junior",
        "0 years",
        "0-1 years",
        "0 - 1 years",
        "no experience",
        "recent graduate"
    ]

    return any(
        keyword in text
        for keyword in fresher_keywords
    )


# ==================================================
# ROLE MATCH
# ==================================================

def calculate_role_match(
    resume_analysis,
    job
):
    resume_roles = [
        normalize_text(role)
        for role in resume_analysis.get(
            "job_roles",
            []
        )
    ]

    job_title = normalize_text(
        job.get(
            "title",
            ""
        )
    )

    if not resume_roles:
        return 0

    for role in resume_roles:
        if role == job_title:
            return 100

    for role in resume_roles:
        role_words = set(
            role.split()
        )

        title_words = set(
            job_title.split()
        )

        if not role_words:
            continue

        overlap = len(
            role_words.intersection(
                title_words
            )
        )

        percentage = (
            overlap /
            len(role_words)
        ) * 100

        if percentage >= 50:
            return round(
                percentage
            )

    return 0


# ==================================================
# SKILL MATCH
# ==================================================

def calculate_skill_match(
    resume_analysis,
    job
):
    resume_skills = {
        normalize_skill(skill)
        for skill in resume_analysis.get(
            "skills",
            []
        )
        if skill
    }

    job_skills = {
        normalize_skill(skill)
        for skill in job.get(
            "required_skills",
            []
        )
        if skill
    }

    matched_skills = (
        resume_skills.intersection(
            job_skills
        )
    )

    missing_skills = (
        job_skills.difference(
            resume_skills
        )
    )

    if job_skills:
        skill_score = (
            len(matched_skills)
            /
            len(job_skills)
        ) * 100
    else:
        skill_score = 0

    return (
        matched_skills,
        missing_skills,
        skill_score
    )


# ==================================================
# EXPERIENCE SCORE
# ==================================================

def calculate_experience_score(
    resume_analysis,
    job
):
    candidate_experience = (
        extract_experience_years(
            resume_analysis
        )
    )

    required_experience = (
        get_required_experience(
            job
        )
    )

    senior_role = is_senior_role(
        job
    )

    fresher_friendly = (
        is_fresher_friendly(
            job
        )
    )

    if required_experience is None:

        if (
            candidate_experience == 0
            and senior_role
        ):
            return 0

        if (
            candidate_experience == 0
            and fresher_friendly
        ):
            return 100

        if candidate_experience > 0:
            return 100

        return 70

    if (
        candidate_experience
        >= required_experience
    ):
        return 100

    if (
        candidate_experience == 0
        and required_experience <= 1
    ):
        return 90

    if (
        candidate_experience + 1
        >= required_experience
    ):
        return 70

    if (
        candidate_experience + 2
        >= required_experience
    ):
        return 40

    return 0


# ==================================================
# PREFERENCE SCORE
# ==================================================

def calculate_preference_score(
    resume_analysis,
    job
):
    score = 0

    preferred_role = normalize_text(
        resume_analysis.get(
            "preferred_role",
            ""
        )
    )

    job_title = normalize_text(
        job.get(
            "title",
            ""
        )
    )

    if preferred_role:

        preferred_words = set(
            preferred_role.split()
        )

        title_words = set(
            job_title.split()
        )

        if preferred_words.intersection(
            title_words
        ):
            score += 50

    preferred_mode = normalize_text(
        resume_analysis.get(
            "preferred_work_mode",
            ""
        )
    )

    job_mode = normalize_text(
        job.get(
            "work_mode",
            ""
        )
    )

    if (
        preferred_mode
        and job_mode
    ):

        if (
            preferred_mode in job_mode
            or
            job_mode in preferred_mode
        ):
            score += 25

    preferred_location = normalize_text(
        resume_analysis.get(
            "location",
            ""
        )
    )

    job_location = normalize_text(
        job.get(
            "location",
            ""
        )
    )

    if (
        preferred_location
        and job_location
    ):

        if (
            preferred_location in job_location
            or
            job_location in preferred_location
        ):
            score += 25

    return min(
        score,
        100
    )


# ==================================================
# MAIN MATCH
# ==================================================

def calculate_candidate_match(
    resume_analysis,
    job
):

    (
        matched_skills,
        missing_skills,
        skill_score
    ) = calculate_skill_match(
        resume_analysis,
        job
    )

    matched_skills = sorted(
        matched_skills
    )

    missing_skills = sorted(
        missing_skills
    )

    experience_score = (
        calculate_experience_score(
            resume_analysis,
            job
        )
    )

    role_score = calculate_role_match(
        resume_analysis,
        job
    )

    preference_score = (
        calculate_preference_score(
            resume_analysis,
            job
        )
    )

    senior_role = is_senior_role(
        job
    )

    fresher_friendly = (
        is_fresher_friendly(
            job
        )
    )

    candidate_experience = (
        extract_experience_years(
            resume_analysis
        )
    )

    required_experience = (
        get_required_experience(
            job
        )
    )

    # ------------------------------------------
    # FINAL SCORE
    # ------------------------------------------

    final_score = (
        (skill_score * 0.50)
        +
        (role_score * 0.20)
        +
        (experience_score * 0.20)
        +
        (preference_score * 0.10)
    )

    # ------------------------------------------
    # FRESHER PROTECTION
    # ------------------------------------------

    if (
        candidate_experience == 0
        and senior_role
    ):
        final_score = min(
            final_score,
            25
        )

    final_score = round(
        final_score
    )

    # ------------------------------------------
    # RECOMMENDATION
    # ------------------------------------------

    if (
        candidate_experience == 0
        and senior_role
    ):
        recommendation = (
            "Not Recommended"
        )

    elif final_score >= 80:
        recommendation = (
            "Strong Match"
        )

    elif final_score >= 65:
        recommendation = (
            "Good Match"
        )

    elif final_score >= 45:
        recommendation = (
            "Possible Match"
        )

    else:
        recommendation = (
            "Low Match"
        )

    # ------------------------------------------
    # EXPERIENCE REJECTION
    # ------------------------------------------

    if (
        required_experience is not None
        and
        candidate_experience
        < required_experience - 2
    ):
        recommendation = (
            "Not Recommended"
        )

    return {

        "match_score":
            final_score,

        "skill_score":
            round(skill_score),

        "role_score":
            round(role_score),

        "experience_score":
            round(experience_score),

        "preference_score":
            round(preference_score),

        "matched_skills":
            matched_skills,

        "missing_skills":
            missing_skills,

        "candidate_experience_years":
            candidate_experience,

        "required_experience_years":
            required_experience,

        "fresher_friendly":
            fresher_friendly,

        "recommendation":
            recommendation
    }


# ==================================================
# RANK JOBS
# ==================================================

def rank_candidate_jobs(
    resume_analysis,
    jobs
):

    ranked_jobs = []

    for job in jobs:

        match = (
            calculate_candidate_match(
                resume_analysis,
                job
            )
        )

        job_copy = job.copy()

        job_copy.update(
            match
        )

        ranked_jobs.append(
            job_copy
        )

    ranked_jobs.sort(
        key=lambda job:
            job.get(
                "match_score",
                0
            ),
        reverse=True
    )

    return ranked_jobs
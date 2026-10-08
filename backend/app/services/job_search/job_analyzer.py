import re


def analyze_job(job: dict) -> dict:

    title = job.get("title") or ""
    description = job.get("description") or ""

    # Some job APIs may already provide work mode
    existing_work_mode = (
        job.get("work_mode")
        or job.get("workMode")
        or job.get("work_type")
        or ""
    )

    text = f"{title} {description}".lower()

    # -----------------------------
    # Experience
    # -----------------------------

    experience_years = None
    experience_text = None

    patterns = [
        r"(\d+)\s*-\s*(\d+)\s*years?",
        r"(\d+)\s*\+\s*years?",
        r"minimum\s*(?:of\s*)?(\d+)\s*years?",
        r"(\d+)\s*years?\s*(?:of\s*)?experience",
    ]

    for pattern in patterns:

        matches = re.findall(pattern, text)

        if not matches:
            continue

        match = matches[0]

        if isinstance(match, tuple):

            numbers = [
                int(value)
                for value in match
                if value.isdigit()
            ]

            if numbers:

                experience_years = min(numbers)

                experience_text = (
                    f"{match[0]}-{match[1]} years"
                )

        elif match.isdigit():

            experience_years = int(match)

            experience_text = (
                f"{match} years"
            )

        if experience_years is not None:
            break

    # -----------------------------
    # Skills
    # -----------------------------

    known_skills = [
        "python",
        "java",
        "javascript",
        "typescript",
        "html",
        "css",
        "sql",
        "flask",
        "fastapi",
        "django",
        "machine learning",
        "deep learning",
        "artificial intelligence",
        "generative ai",
        "gemini api",
        "openai api",
        "sqlite",
        "mysql",
        "postgresql",
        "git",
        "github",
        "gitlab",
        "docker",
        "aws",
        "react",
        "node.js",
        "pandas",
        "numpy",
        "scikit-learn",
        "tensorflow",
        "pytorch",
        "pyspark",
        "devops",
    ]

    required_skills = [
        skill
        for skill in known_skills
        if skill in text
    ]

    # -----------------------------
    # Fresher / entry-level signals
    # -----------------------------

    fresher_keywords = [
        "fresher",
        "freshers",
        "entry level",
        "entry-level",
        "0-1 years",
        "0 - 1 years",
        "0-2 years",
        "0 - 2 years",
        "graduate",
        "trainee",
        "junior",
    ]

    fresher_signal = any(
        keyword in text
        for keyword in fresher_keywords
    )

    if experience_years is not None:

        is_fresher_friendly = (
            experience_years <= 1
        )

    else:

        is_fresher_friendly = (
            fresher_signal
        )

    # -----------------------------
    # Job type
    # -----------------------------

    job_type = "unknown"

    if (
        "full-time" in text
        or "full time" in text
    ):

        job_type = "full-time"

    elif (
        "part-time" in text
        or "part time" in text
    ):

        job_type = "part-time"

    elif (
        "internship" in text
        or "intern" in text
    ):

        job_type = "internship"

    elif "contract" in text:

        job_type = "contract"

    # -----------------------------
    # Work mode
    # -----------------------------
    #
    # Strong detection:
    #
    # 1. Existing structured work mode
    # 2. Hybrid detection
    # 3. Remote detection
    # 4. On-site detection
    # 5. Unknown if nothing is certain
    #
    # Priority:
    #
    # HYBRID > REMOTE > ON-SITE
    #
    # because some descriptions contain
    # both "remote" and "office".
    # -----------------------------

    work_mode = detect_work_mode(
        text=text,
        existing_work_mode=existing_work_mode
    )

    return {
        "minimum_experience_years": experience_years,
        "experience_text": experience_text,
        "required_skills": sorted(
            set(required_skills)
        ),
        "fresher_signal": fresher_signal,
        "is_fresher_friendly": is_fresher_friendly,
        "job_type": job_type,
        "work_mode": work_mode,
    }


# ==================================================
# WORK MODE DETECTION
# ==================================================

def detect_work_mode(
    text: str,
    existing_work_mode: str = ""
) -> str:

    text = (
        text or ""
    ).lower()

    structured = (
        existing_work_mode or ""
    ).strip().lower()


    # ==================================================
    # 1. TRUST STRUCTURED API VALUE
    # ==================================================

    structured_mode = normalize_work_mode(
        structured
    )

    if structured_mode != "unknown":

        return structured_mode


    # ==================================================
    # 2. HYBRID
    # ==================================================
    #
    # Hybrid must be checked first.
    #
    # Example:
    #
    # "Hybrid role with remote work"
    #
    # should be HYBRID, not REMOTE.
    # ==================================================

    hybrid_patterns = [

        r"\bhybrid\b",

        r"\bhybrid\s+work\b",

        r"\bhybrid\s+working\b",

        r"\bhybrid\s+role\b",

        r"\bhybrid\s+position\b",

        r"\bpartially\s+remote\b",

        r"\bpartly\s+remote\b",

        r"\bremote\s+and\s+office\b",

        r"\bremote\s+\+\s+office\b",

        r"\boffice\s+and\s+remote\b",

        r"\bwork\s+from\s+home\s+and\s+office\b",

        r"\bhome\s+and\s+office\b",

        r"\bremote\s+\/\s*office\b",

        r"\boffice\s+\/\s*remote\b",

        r"\b\d+\s+days?\s+(?:in|at)\s+(?:the\s+)?office\b",

        r"\b\d+\s+days?\s+remote\b",

        r"\bflexible\s+work\s+location\b",

        r"\bflexible\s+working\s+arrangement\b",

    ]


    if matches_any(
        text,
        hybrid_patterns
    ):

        return "hybrid"


    # ==================================================
    # 3. REMOTE
    # ==================================================

    remote_patterns = [

        r"\bremote\b",

        r"\bfully\s+remote\b",

        r"\b100%\s+remote\b",

        r"\bremote[-\s]?first\b",

        r"\bremote[-\s]?only\b",

        r"\bwork\s+from\s+home\b",

        r"\bwork[-\s]?from[-\s]?home\b",

        r"\bwfh\b",

        r"\bhome[-\s]?based\b",

        r"\bhome\s+based\s+role\b",

        r"\bwork\s+from\s+anywhere\b",

        r"\bwork[-\s]?from[-\s]?anywhere\b",

        r"\banywhere\s+in\s+the\s+world\b",

        r"\bdistributed\s+team\b",

        r"\bdistributed\s+workforce\b",

        r"\bremote\s+position\b",

        r"\bremote\s+role\b",

        r"\bremote\s+opportunity\b",

        r"\btelecommute\b",

        r"\btelecommuting\b",

        r"\bvirtual\s+position\b",

        r"\bvirtual\s+role\b",

    ]


    if matches_any(
        text,
        remote_patterns
    ):

        return "remote"


    # ==================================================
    # 4. ON-SITE
    # ==================================================

    onsite_patterns = [

        r"\bon[-\s]?site\b",

        r"\bonsite\b",

        r"\bon\s+site\b",

        r"\bin[-\s]?office\b",

        r"\bin\s+the\s+office\b",

        r"\boffice[-\s]?based\b",

        r"\boffice\s+based\s+role\b",

        r"\bwork\s+from\s+office\b",

        r"\bwork[-\s]?from[-\s]?office\b",

        r"\bmust\s+work\s+from\s+office\b",

        r"\brequired\s+to\s+work\s+from\s+office\b",

        r"\bphysically\s+located\b",

        r"\bin[-\s]?person\b",

        r"\bin\s+person\s+role\b",

        r"\bin\s+person\s+position\b",

        r"\bworkplace\s+based\b",

    ]


    if matches_any(
        text,
        onsite_patterns
    ):

        return "on-site"


    # ==================================================
    # 5. UNKNOWN
    # ==================================================

    return "unknown"


# ==================================================
# MATCH PATTERNS
# ==================================================

def matches_any(
    text: str,
    patterns: list[str]
) -> bool:

    for pattern in patterns:

        if re.search(
            pattern,
            text,
            re.IGNORECASE
        ):

            return True

    return False


# ==================================================
# NORMALIZE EXISTING WORK MODE
# ==================================================

def normalize_work_mode(
    value: str
) -> str:

    if not value:

        return "unknown"


    value = (
        value
        .strip()
        .lower()
    )


    # -----------------------------
    # REMOTE
    # -----------------------------

    if value in {
        "remote",
        "fully remote",
        "100% remote",
        "wfh",
        "work from home",
        "work-from-home",
        "home based",
        "home-based",
    }:

        return "remote"


    # -----------------------------
    # HYBRID
    # -----------------------------

    if value in {
        "hybrid",
        "partially remote",
        "partly remote",
        "remote/hybrid",
    }:

        return "hybrid"


    # -----------------------------
    # ON-SITE
    # -----------------------------

    if value in {
        "on-site",
        "onsite",
        "on site",
        "in office",
        "office",
        "office-based",
        "office based",
    }:

        return "on-site"


    return "unknown"


# ==================================================
# ANALYZE ALL JOBS
# ==================================================

def analyze_jobs(
    jobs: list[dict]
) -> list[dict]:

    analyzed_jobs = []

    for job in jobs:

        analysis = analyze_job(
            job
        )

        job_copy = job.copy()

        job_copy.update({

            "minimum_experience_years":
                analysis[
                    "minimum_experience_years"
                ],

            "experience_text":
                analysis[
                    "experience_text"
                ],

            "required_skills":
                analysis[
                    "required_skills"
                ],

            "fresher_signal":
                analysis[
                    "fresher_signal"
                ],

            "is_fresher_friendly":
                analysis[
                    "is_fresher_friendly"
                ],

            "detected_job_type":
                analysis[
                    "job_type"
                ],

            "detected_work_mode":
                analysis[
                    "work_mode"
                ],

        })

        analyzed_jobs.append(
            job_copy
        )

    return analyzed_jobs
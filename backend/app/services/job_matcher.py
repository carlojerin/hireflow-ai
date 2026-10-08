import re


def normalize_text(text: str | None) -> str:
    if not text:
        return ""

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9+#.\s]",
        " ",
        text
    )

    return " ".join(text.split())


def extract_job_skills(job: dict) -> set[str]:
    job_text = " ".join([
        job.get("title") or "",
        job.get("description") or "",
        job.get("required_skills") or ""
    ])

    job_text = normalize_text(job_text)

    known_skills = {
        "python",
        "java",
        "javascript",
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
        "gitlab"
    }

    found = set()

    for skill in known_skills:
        if skill in job_text:
            found.add(skill)

    return found


def calculate_match_score(
    resume_skills: list[str],
    job: dict
) -> dict:

    resume_skill_set = {
        normalize_text(skill)
        for skill in resume_skills
    }

    job_skills = extract_job_skills(job)

    if not job_skills:
        return {
            "match_score": 0,
            "matched_skills": [],
            "missing_skills": []
        }

    matched_skills = sorted(
        resume_skill_set.intersection(job_skills)
    )

    missing_skills = sorted(
        job_skills.difference(resume_skill_set)
    )

    score = round(
        (len(matched_skills) / len(job_skills)) * 100
    )

    return {
        "match_score": score,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills
    }


def rank_jobs(
    resume_skills: list[str],
    jobs: list[dict]
) -> list[dict]:

    ranked_jobs = []

    for job in jobs:

        match = calculate_match_score(
            resume_skills,
            job
        )

        job_copy = job.copy()

        job_copy["match_score"] = match["match_score"]
        job_copy["matched_skills"] = match["matched_skills"]
        job_copy["missing_skills"] = match["missing_skills"]

        ranked_jobs.append(job_copy)

    ranked_jobs.sort(
        key=lambda job: job["match_score"],
        reverse=True
    )

    return ranked_jobs
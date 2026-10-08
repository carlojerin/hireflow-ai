from datetime import datetime


def normalize_job(job: dict) -> dict:
    """
    Convert a job returned by a source such as Jooble
    into JobPilot's standard job format.
    """

    return {
        "external_id": str(job.get("external_id") or ""),
        "title": (job.get("title") or "").strip(),
        "company": (job.get("company") or "").strip(),
        "location": (job.get("location") or "").strip(),
        "description": (job.get("description") or "").strip(),
        "required_skills": (
            job.get("required_skills") or ""
        ).strip(),
        "salary": (job.get("salary") or "").strip(),
        "job_type": (job.get("job_type") or "").strip(),
        "work_mode": (job.get("work_mode") or "").strip(),
        "source": (job.get("source") or "").strip(),
        "job_url": (job.get("job_url") or "").strip(),
        "application_url": (
            job.get("application_url")
            or job.get("job_url")
            or ""
        ).strip(),
        "updated": job.get("updated"),
        "url_verified": bool(
            job.get("url_verified", False)
        ),
    }


def normalize_jobs(jobs: list[dict]) -> list[dict]:
    """
    Normalize multiple jobs.
    """

    normalized_jobs = []

    for job in jobs:
        normalized_job = normalize_job(job)

        # Do not keep jobs without a title or URL.
        if not normalized_job["title"]:
            continue

        if not normalized_job["job_url"]:
            continue

        normalized_jobs.append(normalized_job)

    return normalized_jobs
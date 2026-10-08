import re


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    value = value.lower().strip()

    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    unique_jobs = []
    seen = set()

    for job in jobs:

        job_url = job.get("job_url")

        if job_url:
            key = f"url:{job_url.lower().strip()}"
        else:
            title = normalize_text(job.get("title"))
            company = normalize_text(job.get("company"))
            location = normalize_text(job.get("location"))

            key = f"{title}|{company}|{location}"

        if key in seen:
            continue

        seen.add(key)
        unique_jobs.append(job)

    return unique_jobs
import os

import requests
from dotenv import load_dotenv


load_dotenv()


JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY")

JOOBLE_URL = f"https://in.jooble.org/api/{JOOBLE_API_KEY}"


def search_jooble_jobs(
    keywords: str,
    location: str,
    page: int = 1,
    results_per_page: int = 20
) -> list[dict]:

    if not JOOBLE_API_KEY:
        raise RuntimeError(
            "JOOBLE_API_KEY is missing from .env"
        )

    payload = {
        "keywords": keywords,
        "location": location,
        "page": page,
        "ResultOnPage": results_per_page,
        "companysearch": False
    }

    response = requests.post(
        JOOBLE_URL,
        json=payload,
        headers={
            "Content-Type": "application/json"
        },
        timeout=20
    )

    response.raise_for_status()

    data = response.json()

    jobs = []

    for job in data.get("jobs", []):
        jobs.append({
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "description": job.get("snippet"),
            "salary": job.get("salary"),
            "job_type": job.get("type"),
            "source": job.get("source"),
            "job_url": job.get("link"),
            "updated": job.get("updated"),
            "external_id": str(job.get("id"))
        })

    return jobs
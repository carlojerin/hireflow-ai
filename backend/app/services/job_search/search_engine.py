import requests
from bs4 import BeautifulSoup
from urllib.parse import quote


def search_web_jobs(
    role: str,
    location: str,
    experience: str = ""
) -> list[dict]:

    query = f"{role} jobs {location} {experience}"

    url = (
        "https://www.google.com/search?"
        f"q={quote(query)}&num=10"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=10
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    jobs = []

    for result in soup.select("div.MjjYud"):

        title_element = result.select_one("h3")

        link_element = result.select_one("a")

        if not title_element or not link_element:
            continue

        title = title_element.get_text(
            " ",
            strip=True
        )

        job_url = link_element.get("href")

        if not job_url:
            continue

        jobs.append({
            "title": title,
            "company": "Unknown",
            "location": location,
            "description": "",
            "required_skills": "",
            "salary": "",
            "job_type": "",
            "work_mode": "",
            "source": "Web Search",
            "job_url": job_url,
            "application_url": job_url
        })

    return jobs
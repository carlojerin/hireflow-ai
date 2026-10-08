from urllib.parse import urlparse


def verify_job_url(url: str) -> bool:
    """
    Basic URL validation.

    We do not make an HTTP request here because many
    job websites block automated requests.
    """

    if not url:
        return False

    try:
        parsed = urlparse(url)

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


def filter_valid_job_urls(
    jobs: list[dict]
) -> list[dict]:

    valid_jobs = []

    for job in jobs:

        url = job.get("job_url")

        if verify_job_url(url):

            job["url_verified"] = True

            valid_jobs.append(job)

    return valid_jobs
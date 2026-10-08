import re


def detect_required_years(job: dict) -> int | None:
    """
    Extract the minimum apparent experience requirement
    from the job title and description.
    """

    text = " ".join([
        job.get("title") or "",
        job.get("description") or "",
        job.get("required_skills") or ""
    ]).lower()

    patterns = [
        r"(\d+)\s*-\s*(\d+)\s*years?",
        r"(\d+)\s*\+\s*years?",
        r"minimum\s*(?:of\s*)?(\d+)\s*years?",
        r"(\d+)\s*years?\s*(?:of\s*)?experience",
    ]

    values = []

    for pattern in patterns:

        matches = re.findall(pattern, text)

        for match in matches:

            if isinstance(match, tuple):
                numbers = [
                    int(value)
                    for value in match
                    if value.isdigit()
                ]

                if numbers:
                    values.append(min(numbers))

            elif match.isdigit():
                values.append(int(match))

    if not values:
        return None

    return min(values)


def filter_for_fresher(
    jobs: list[dict],
    max_years: int = 1
) -> list[dict]:

    suitable_jobs = []

    for job in jobs:

        required_years = detect_required_years(job)

        job["required_experience_years"] = required_years

        # If experience isn't stated, keep the job.
        if required_years is None:
            suitable_jobs.append(job)
            continue

        # Keep jobs suitable for freshers.
        if required_years <= max_years:
            suitable_jobs.append(job)

    return suitable_jobs
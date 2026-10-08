from datetime import datetime, timezone


def filter_recent_jobs(
    jobs: list[dict],
    max_age_days: int = 30
) -> list[dict]:

    recent_jobs = []

    now = datetime.now(timezone.utc)

    for job in jobs:

        updated = job.get("updated")

        if not updated:
            continue

        try:
            updated_date = datetime.fromisoformat(
                updated.replace("Z", "+00:00")
            )

            # Make sure the date is timezone-aware.
            if updated_date.tzinfo is None:
                updated_date = updated_date.replace(
                    tzinfo=timezone.utc
                )

            age_days = (
                now - updated_date
            ).days

            if 0 <= age_days <= max_age_days:
                recent_jobs.append(job)

        except (ValueError, TypeError):
            continue

    return recent_jobs
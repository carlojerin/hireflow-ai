from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_search.normalizer import normalize_jobs
from backend.app.services.job_search.experience_filter import filter_for_fresher


jobs = search_jooble_jobs(
    keywords="Python Developer",
    location="Chennai",
    page=1,
    results_per_page=20
)

recent_jobs = filter_recent_jobs(
    jobs,
    max_age_days=30
)

unique_jobs = deduplicate_jobs(
    recent_jobs
)

valid_jobs = filter_valid_job_urls(
    unique_jobs
)

normalized_jobs = normalize_jobs(
    valid_jobs
)

fresher_jobs = filter_for_fresher(
    normalized_jobs,
    max_years=1
)


print("\n========== EXPERIENCE FILTER ==========\n")

print("Jobs from Jooble:", len(jobs))
print("Recent jobs:", len(recent_jobs))
print("Unique jobs:", len(unique_jobs))
print("Valid jobs:", len(valid_jobs))
print("Fresher-suitable jobs:", len(fresher_jobs))


for index, job in enumerate(fresher_jobs, start=1):

    print(f"\n--- FRESHER JOB {index} ---")

    print("Title:", job["title"])
    print("Company:", job["company"])
    print("Location:", job["location"])
    print(
        "Required Experience:",
        job["required_experience_years"]
    )
    print("Updated:", job["updated"])
    print("URL:", job["job_url"])


print("\n========== END EXPERIENCE FILTER ==========\n")
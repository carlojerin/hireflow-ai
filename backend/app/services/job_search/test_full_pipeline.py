from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls


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


print("\n========== FULL JOB PIPELINE ==========\n")

print("Jobs from Jooble:", len(jobs))
print("Recent jobs:", len(recent_jobs))
print("Unique jobs:", len(unique_jobs))
print("Valid URLs:", len(valid_jobs))


for index, job in enumerate(valid_jobs, start=1):

    print(f"\n--- VERIFIED JOB {index} ---")

    print("Title:", job.get("title"))
    print("Company:", job.get("company"))
    print("Location:", job.get("location"))
    print("Source:", job.get("source"))
    print("Updated:", job.get("updated"))
    print("URL:", job.get("job_url"))
    print("URL Verified:", job.get("url_verified"))


print("\n========== END PIPELINE ==========\n")
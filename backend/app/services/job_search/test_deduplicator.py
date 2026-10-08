from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs


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

unique_jobs = deduplicate_jobs(recent_jobs)


print("\n========== JOB PIPELINE ==========\n")

print("Jobs from Jooble:", len(jobs))
print("Recent jobs:", len(recent_jobs))
print("Unique jobs:", len(unique_jobs))

for index, job in enumerate(unique_jobs, start=1):

    print(f"\n--- JOB {index} ---")
    print("Title:", job.get("title"))
    print("Company:", job.get("company"))
    print("Location:", job.get("location"))
    print("Source:", job.get("source"))
    print("Updated:", job.get("updated"))
    print("URL:", job.get("job_url"))

print("\n========== END PIPELINE ==========\n")
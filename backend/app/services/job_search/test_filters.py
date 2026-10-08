from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs


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


print("\n========== JOB FILTER TEST ==========\n")

print("Jobs returned from source:", len(jobs))
print("Jobs after freshness filter:", len(recent_jobs))

for index, job in enumerate(recent_jobs, start=1):

    print(f"\n--- RECENT JOB {index} ---")
    print("Title:", job.get("title"))
    print("Company:", job.get("company"))
    print("Location:", job.get("location"))
    print("Updated:", job.get("updated"))
    print("URL:", job.get("job_url"))

print("\n========== END FILTER TEST ==========\n")
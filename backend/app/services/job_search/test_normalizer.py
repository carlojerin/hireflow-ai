from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_search.normalizer import normalize_jobs


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


print("\n========== NORMALIZED JOBS ==========\n")

print("Jobs from Jooble:", len(jobs))
print("Recent jobs:", len(recent_jobs))
print("Unique jobs:", len(unique_jobs))
print("Valid jobs:", len(valid_jobs))
print("Normalized jobs:", len(normalized_jobs))


for index, job in enumerate(normalized_jobs, start=1):

    print(f"\n--- JOB {index} ---")

    print("ID:", job["external_id"])
    print("Title:", job["title"])
    print("Company:", job["company"])
    print("Location:", job["location"])
    print("Description:", job["description"][:150])
    print("Salary:", job["salary"])
    print("Source:", job["source"])
    print("Updated:", job["updated"])
    print("Job URL:", job["job_url"])
    print("Application URL:", job["application_url"])
    print("URL Verified:", job["url_verified"])


print("\n========== END NORMALIZATION ==========\n")
from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_search.normalizer import normalize_jobs
from backend.app.services.job_search.job_analyzer import analyze_jobs


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

analyzed_jobs = analyze_jobs(
    normalized_jobs
)


print("\n========== JOB ANALYZER ==========\n")

print("Jobs from Jooble:", len(jobs))
print("Recent jobs:", len(recent_jobs))
print("Unique jobs:", len(unique_jobs))
print("Valid jobs:", len(valid_jobs))
print("Analyzed jobs:", len(analyzed_jobs))


for index, job in enumerate(analyzed_jobs, start=1):

    print(f"\n--- ANALYZED JOB {index} ---")

    print("Title:", job["title"])
    print("Company:", job["company"])
    print(
        "Minimum Experience:",
        job["minimum_experience_years"]
    )

    print(
        "Required Skills:",
        ", ".join(job["required_skills"])
    )

    print(
        "Fresher Friendly:",
        job["is_fresher_friendly"]
    )

    print(
        "Job Type:",
        job["detected_job_type"]
    )

    print(
        "Work Mode:",
        job["detected_work_mode"]
    )

    print("URL:", job["job_url"])


print("\n========== END ANALYZER ==========\n")
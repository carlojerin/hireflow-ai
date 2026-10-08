from backend.app.services.job_search.jooble import search_jooble_jobs


jobs = search_jooble_jobs(
    keywords="Python Developer",
    location="Chennai",
    page=1,
    results_per_page=10
)


print("\n========== REAL JOB RESULTS ==========\n")

print(f"Total returned: {len(jobs)}")

for index, job in enumerate(jobs, start=1):

    print(f"\n--- JOB {index} ---")

    print("Title:", job.get("title"))
    print("Company:", job.get("company"))
    print("Location:", job.get("location"))
    print("Salary:", job.get("salary"))
    print("Type:", job.get("job_type"))
    print("Source:", job.get("source"))
    print("Updated:", job.get("updated"))
    print("URL:", job.get("job_url"))

print("\n========== END RESULTS ==========\n")
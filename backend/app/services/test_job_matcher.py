from backend.app.services.resume_parser import extract_resume_text
from backend.app.services.resume_analyzer import analyze_resume
from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_matcher import rank_jobs


# -----------------------------
# 1. Read the user's resume
# -----------------------------

resume_path = "data/resumes/Jerin_R_Resume(1).pdf"

resume_text = extract_resume_text(resume_path)

resume_analysis = analyze_resume(resume_text)

resume_skills = resume_analysis["skills"]


# -----------------------------
# 2. Find real jobs
# -----------------------------

jobs = search_jooble_jobs(
    keywords="Python Developer",
    location="Chennai",
    page=1,
    results_per_page=20
)


# -----------------------------
# 3. Filter jobs
# -----------------------------

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


# -----------------------------
# 4. Match jobs with resume
# -----------------------------

ranked_jobs = rank_jobs(
    resume_skills,
    valid_jobs
)


# -----------------------------
# 5. Display results
# -----------------------------

print("\n========== JOBPILOT AI MATCHING ==========\n")

print("Resume:", resume_analysis["name"])

print("\nResume Skills:")

for skill in resume_skills:
    print("-", skill)


print("\nJobs Found:", len(ranked_jobs))


for index, job in enumerate(ranked_jobs, start=1):

    print(f"\n========== JOB {index} ==========")

    print("Title:", job.get("title"))
    print("Company:", job.get("company"))
    print("Location:", job.get("location"))
    print("Match Score:", f'{job.get("match_score")}%')

    print("\nMatched Skills:")

    for skill in job.get("matched_skills", []):
        print("✓", skill)

    print("\nMissing Skills:")

    for skill in job.get("missing_skills", []):
        print("⚠", skill)

    print("\nOriginal Job URL:")
    print(job.get("job_url"))


print("\n========== END MATCHING ==========\n")
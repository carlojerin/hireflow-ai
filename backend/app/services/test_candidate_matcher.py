from backend.app.services.resume_parser import extract_resume_text
from backend.app.services.resume_analyzer import analyze_resume

from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_search.normalizer import normalize_jobs
from backend.app.services.job_search.job_analyzer import analyze_jobs

from backend.app.services.candidate_matcher import rank_candidate_jobs


# --------------------------------
# 1. Resume
# --------------------------------

resume_path = "data/resumes/Jerin_R_Resume(1).pdf"

resume_text = extract_resume_text(
    resume_path
)

resume_analysis = analyze_resume(
    resume_text
)


# --------------------------------
# 2. Search real jobs
# --------------------------------

jobs = search_jooble_jobs(
    keywords="Python Developer",
    location="Chennai",
    page=1,
    results_per_page=20
)


# --------------------------------
# 3. Job pipeline
# --------------------------------

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


# --------------------------------
# 4. Match resume with jobs
# --------------------------------

ranked_jobs = rank_candidate_jobs(
    resume_analysis,
    analyzed_jobs
)


# --------------------------------
# 5. Display
# --------------------------------

print("\n========== JOBPILOT AI RESULTS ==========\n")

print("Candidate:", resume_analysis["name"])

print(
    "Resume Skills:",
    ", ".join(resume_analysis["skills"])
)

print(
    "\nJobs Found:",
    len(ranked_jobs)
)


for index, job in enumerate(
    ranked_jobs,
    start=1
):

    print(
        f"\n========== JOB {index} =========="
    )

    print(
        "Title:",
        job["title"]
    )

    print(
        "Company:",
        job["company"]
    )

    print(
        "Location:",
        job["location"]
    )

    print(
        "Match Score:",
        f'{job["match_score"]}%'
    )

    print(
        "Skill Score:",
        f'{job["skill_score"]}%'
    )

    print(
        "Experience Score:",
        f'{job["experience_score"]}%'
    )

    print(
        "Recommendation:",
        job["recommendation"]
    )

    print(
        "\nRequired Experience:",
        job["minimum_experience_years"]
    )

    print(
        "Required Skills:",
        ", ".join(
            job["required_skills"]
        )
    )

    print(
        "\nMatched Skills:"
    )

    for skill in job["matched_skills"]:
        print("✓", skill)

    print(
        "\nMissing Skills:"
    )

    for skill in job["missing_skills"]:
        print("⚠", skill)

    print(
        "\nOriginal Job URL:"
    )

    print(
        job["job_url"]
    )


print(
    "\n========== END RESULTS ==========\n"
)
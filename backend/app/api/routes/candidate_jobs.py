from datetime import date, datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.app.services.resume.active_resume import get_active_resume
from backend.app.database.database import get_db

from backend.app.models.job import Job
from backend.app.models.company import Company
from backend.app.models.user import User
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.job_question import JobQuestion

from backend.app.services.ai_candidate_matching import (
    calculate_candidate_match
)

from backend.app.api.deps import get_current_user_optional


router = APIRouter(
    prefix="/api/candidate-jobs",
    tags=["Candidate Jobs"],
)


# ==================================================
# CANDIDATE CONTEXT
# ==================================================

def get_candidate_context(
    db: Session,
    user_id: int,
):
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Candidate not found",
        )

    # Your normal candidate accounts currently use
    # role="user". Some older accounts may use
    # role="candidate". Allow both.
    allowed_roles = {
        "user",
        "candidate",
    }

    user_role = (
        getattr(user, "role", None) or "user"
    ).lower()

    if user_role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Only candidate accounts can access candidate jobs",
        )

    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user_id
        )
        .first()
    )

    resume = (
        get_active_resume(db, user_id)
    )

    analysis = None

    if resume:

        analysis = (
            db.query(ResumeAnalysis)
            .filter(
                ResumeAnalysis.resume_id == resume.id
            )
            .first()
        )

    return user, profile, resume, analysis


# ==================================================
# JOB VALIDATION
# ==================================================

def is_valid_job(
    job: Job
) -> bool:

    if getattr(job, "status", None) != "active":
        return False

    source = (
        getattr(job, "source", None) or ""
    ).lower()

    if source not in {
        "internal",
        "company",
    }:
        return False

    deadline = getattr(
        job,
        "application_deadline",
        None
    )

    if deadline:

        try:

            if isinstance(
                deadline,
                date
            ):
                deadline_date = deadline

            else:
                deadline_date = datetime.strptime(
                    str(deadline),
                    "%Y-%m-%d"
                ).date()

            if deadline_date < date.today():
                return False

        except (
            ValueError,
            TypeError
        ):
            pass

    openings = getattr(
        job,
        "openings",
        None
    )

    if openings is not None:

        try:

            if int(openings) <= 0:
                return False

        except (
            ValueError,
            TypeError
        ):
            pass

    return True


# ==================================================
# VERIFIED COMPANIES
# ==================================================

def get_verified_company_ids(
    db: Session
):

    companies = (
        db.query(Company)
        .filter(
            Company.account_active == True,
            Company.admin_verified == True,
            Company.verification_status == "verified",
        )
        .all()
    )

    return {
        company.id
        for company in companies
    }


# ==================================================
# SERIALIZE JOB
# ==================================================

def serialize_job(
    job: Job,
    company: Company | None,
    ai_result: dict | None = None,
):

    result = {

        "id":
            job.id,

        "company_id":
            job.company_id,

        "title":
            job.title,

        "company":
            (
                company.company_name
                if company
                else getattr(
                    job,
                    "company",
                    None
                )
            ),

        "location":
            job.location,

        "work_mode":
            job.work_mode,

        "employment_type":
            job.employment_type,

        "salary":
            job.salary,

        "salary_min":
            job.salary_min,

        "salary_max":
            job.salary_max,

        "salary_currency":
            job.salary_currency,

        "experience":
            job.experience,

        "education":
            job.education,

        "required_skills":
            job.required_skills,

        "preferred_skills":
            job.preferred_skills,

        "technologies":
            job.technologies,

        "description":
            job.description,

        "responsibilities":
            job.responsibilities,

        "requirements":
            job.requirements,

        "benefits":
            job.benefits,

        "openings":
            job.openings,

        "status":
            job.status,

        "source":
            job.source,

        "posted_date":
            job.posted_date,

        "application_deadline":
            job.application_deadline,

        "job_url":
            job.job_url,

        "application_url":
            job.application_url,
    }

    if ai_result:

        result.update({

            "ai_match_score":
                ai_result.get("score"),

            "ai_shortlist":
                ai_result.get(
                    "ai_shortlist"
                ),

            "ai_recommendation":
                ai_result.get(
                    "recommendation"
                ),

            "ai_skill_score":
                ai_result.get(
                    "skill_score"
                ),

            "ai_experience_score":
                ai_result.get(
                    "experience_score"
                ),

            "ai_role_score":
                ai_result.get(
                    "role_score"
                ),

            "ai_location_score":
                ai_result.get(
                    "location_score"
                ),

            "ai_technology_score":
                ai_result.get(
                    "technology_score"
                ),

            "ai_education_score":
                ai_result.get(
                    "education_score"
                ),

            "ai_matched_skills":
                ai_result.get(
                    "matched_skills",
                    []
                ),

            "ai_missing_skills":
                ai_result.get(
                    "missing_skills",
                    []
                ),
        })

    else:

        result.update({

            "ai_match_score":
                None,

            "ai_shortlist":
                False,

            "ai_recommendation":
                "Resume analysis required",

            "ai_skill_score":
                None,

            "ai_experience_score":
                None,

            "ai_role_score":
                None,

            "ai_location_score":
                None,

            "ai_technology_score":
                None,

            "ai_education_score":
                None,

            "ai_matched_skills":
                [],

            "ai_missing_skills":
                [],
        })

    return result


# ==================================================
# SEARCH INTERNAL JOBS
# ==================================================

@router.get("/search")
def search_candidate_jobs(

    user_id: int | None = Query(
        default=None
    ),

    keyword: str | None = Query(
        default=None
    ),

    location: str | None = Query(
        default=None
    ),

    work_mode: str | None = Query(
        default=None
    ),

    max_results: int = Query(
        default=100,
        ge=1,
        le=200
    ),

    current_user=Depends(get_current_user_optional),

    db: Session = Depends(get_db),
):

    # SECURITY: a client-supplied user_id is only ever a
    # personalization hint, never proof of identity. Without a
    # live session that actually belongs to this id, treat the
    # request as anonymous (same behavior as user_id=None) rather
    # than computing and returning another candidate's resume-
    # derived match data.
    if user_id is not None and (
        current_user is None or current_user.id != user_id
    ):
        user_id = None

    verified_company_ids = (
        get_verified_company_ids(db)
    )

    if not verified_company_ids:

        return {
            "success": True,
            "total": 0,
            "jobs": [],
        }

    # Validate candidate before returning
    # candidate-specific jobs.
    if user_id is not None:

        (
            user,
            profile,
            resume,
            analysis
        ) = get_candidate_context(
            db,
            user_id
        )

    else:

        user = None
        profile = None
        resume = None
        analysis = None

    query = (
        db.query(Job)
        .filter(

            Job.company_id.in_(
                verified_company_ids
            ),

            Job.status == "active",

            Job.source.in_(
                [
                    "internal",
                    "company"
                ]
            ),
        )
    )

    # ==================================================
    # KEYWORD
    # ==================================================

    if keyword and keyword.strip():

        search_text = (
            f"%{keyword.strip()}%"
        )

        query = query.filter(

            or_(

                Job.title.ilike(
                    search_text
                ),

                Job.company.ilike(
                    search_text
                ),

                Job.location.ilike(
                    search_text
                ),

                Job.required_skills.ilike(
                    search_text
                ),

                Job.preferred_skills.ilike(
                    search_text
                ),

                Job.technologies.ilike(
                    search_text
                ),

                Job.description.ilike(
                    search_text
                ),

                Job.requirements.ilike(
                    search_text
                ),
            )
        )

    # ==================================================
    # LOCATION
    # ==================================================

    if location and location.strip():

        selected_location = (
            location.strip()
        )

        if selected_location.lower() not in {
            "all",
            "all locations",
            "tamil nadu",
        }:

            query = query.filter(
                Job.location.ilike(
                    f"%{selected_location}%"
                )
            )

    # ==================================================
    # WORK MODE
    # ==================================================

    if work_mode and work_mode.strip():

        selected_mode = (
            work_mode.strip()
        )

        if selected_mode.lower() not in {
            "all",
            "all work modes",
        }:

            query = query.filter(
                Job.work_mode.ilike(
                    f"%{selected_mode}%"
                )
            )

    jobs = (
        query
        .order_by(
            Job.id.desc()
        )
        .limit(max_results)
        .all()
    )

    # ==================================================
    # COMPANIES
    # ==================================================

    company_map = {}

    if jobs:

        company_ids = {
            job.company_id
            for job in jobs
            if job.company_id is not None
        }

        if company_ids:

            companies = (
                db.query(Company)
                .filter(
                    Company.id.in_(
                        company_ids
                    )
                )
                .all()
            )

            company_map = {
                company.id: company
                for company in companies
            }

    # ==================================================
    # BUILD RESULT
    # ==================================================

    response_jobs = []

    for job in jobs:

        if not is_valid_job(job):
            continue

        company = company_map.get(
            job.company_id
        )

        ai_result = None

        if (
            user_id is not None
            and analysis
        ):

            try:

                ai_result = (
                    calculate_candidate_match(
                        candidate_analysis=analysis,
                        candidate_profile=profile,
                        candidate_user=user,
                        job=job,
                    )
                )

            except Exception as error:

                print(
                    "AI MATCH ERROR:",
                    error
                )

                ai_result = {

                    "score":
                        0.0,

                    "ai_shortlist":
                        False,

                    "recommendation":
                        "AI matching unavailable",

                    "skill_score":
                        0.0,

                    "experience_score":
                        0.0,

                    "role_score":
                        0.0,

                    "location_score":
                        0.0,

                    "technology_score":
                        0.0,

                    "education_score":
                        0.0,

                    "matched_skills":
                        [],

                    "missing_skills":
                        [],
                }

        response_jobs.append(
            serialize_job(
                job=job,
                company=company,
                ai_result=ai_result,
            )
        )

    # ==================================================
    # SORT BY AI SCORE
    # ==================================================

    if (
        user_id is not None
        and analysis
    ):

        response_jobs.sort(

            key=lambda item:
                (
                    item["ai_match_score"]
                    if item["ai_match_score"]
                    is not None
                    else 0
                ),

            reverse=True,
        )

    return {

        "success":
            True,

        "total":
            len(response_jobs),

        "jobs":
            response_jobs,

        "resume_analyzed":
            analysis is not None,
    }


# ==================================================
# GET SINGLE JOB
# ==================================================

@router.get("/{job_id}")
def get_candidate_job(

    job_id: int,

    user_id: int | None = Query(
        default=None
    ),

    current_user=Depends(get_current_user_optional),

    db: Session = Depends(get_db),
):

    # SECURITY: see search_candidate_jobs() above — a client-
    # supplied user_id must be proven by a matching live session
    # before it is used to compute/return personalized match data.
    if user_id is not None and (
        current_user is None or current_user.id != user_id
    ):
        user_id = None

    verified_company_ids = (
        get_verified_company_ids(db)
    )

    job = (
        db.query(Job)
        .filter(

            Job.id == job_id,

            Job.company_id.in_(
                verified_company_ids
            ),
        )
        .first()
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if not is_valid_job(job):

        raise HTTPException(
            status_code=404,
            detail="Job is no longer available",
        )

    company = (
        db.query(Company)
        .filter(
            Company.id == job.company_id
        )
        .first()
    )

    ai_result = None

    resume_analyzed = False

    if user_id is not None:

        (
            user,
            profile,
            resume,
            analysis
        ) = get_candidate_context(
            db,
            user_id
        )

        resume_analyzed = (
            analysis is not None
        )

        if analysis:

            try:

                ai_result = (
                    calculate_candidate_match(
                        candidate_analysis=analysis,
                        candidate_profile=profile,
                        candidate_user=user,
                        job=job,
                    )
                )

            except Exception as error:

                print(
                    "AI MATCH ERROR:",
                    error
                )

                ai_result = {

                    "score":
                        0.0,

                    "ai_shortlist":
                        False,

                    "recommendation":
                        "AI matching unavailable",

                    "skill_score":
                        0.0,

                    "experience_score":
                        0.0,

                    "role_score":
                        0.0,

                    "location_score":
                        0.0,

                    "technology_score":
                        0.0,

                    "education_score":
                        0.0,

                    "matched_skills":
                        [],

                    "missing_skills":
                        [],
                }

    # ==================================================
    # QUESTIONS
    # ==================================================

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == job.id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc(),
        )
        .all()
    )

    question_data = []

    for question in questions:

        options = []

        if question.options:

            try:

                parsed = json.loads(
                    question.options
                )

                if isinstance(
                    parsed,
                    list
                ):

                    options = parsed

            except (
                TypeError,
                ValueError
            ):

                options = []

        question_data.append({

            "id":
                question.id,

            "question":
                question.question,

            "question_type":
                question.question_type,

            "options":
                options,

            "required":
                question.required,

            "display_order":
                question.display_order,
        })

    return {

        "success":
            True,

        "job":
            serialize_job(
                job=job,
                company=company,
                ai_result=ai_result,
            ),

        "questions":
            question_data,

        "resume_analyzed":
            resume_analyzed,
    }
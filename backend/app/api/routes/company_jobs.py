import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_question import JobQuestion
from backend.app.api.deps import get_current_company, require_company_self, require_csrf


router = APIRouter(
    prefix="/api/company-jobs",
    tags=["Company Jobs"]
)


# =========================================================
# SCHEMAS
# =========================================================

class JobCreate(BaseModel):

    title: str = Field(
        min_length=2,
        max_length=255
    )

    description: str | None = None

    responsibilities: str | None = None

    requirements: str | None = None

    required_skills: str | None = None

    preferred_skills: str | None = None

    technologies: str | None = None

    experience: str | None = Field(
        default=None,
        max_length=100
    )

    education: str | None = Field(
        default=None,
        max_length=255
    )

    location: str | None = Field(
        default=None,
        max_length=255
    )

    work_mode: str | None = Field(
        default=None,
        max_length=50
    )

    employment_type: str | None = Field(
        default=None,
        max_length=50
    )

    salary_min: int | None = Field(
        default=None,
        ge=0
    )

    salary_max: int | None = Field(
        default=None,
        ge=0
    )

    salary_currency: str = Field(
        default="INR",
        max_length=10
    )

    openings: int | None = Field(
        default=1,
        ge=1,
        le=10000
    )

    application_deadline: str | None = Field(
        default=None,
        max_length=50
    )

    benefits: str | None = None

    status: str = Field(
        default="draft",
        max_length=30
    )


class JobUpdate(BaseModel):

    title: str = Field(
        min_length=2,
        max_length=255
    )

    description: str | None = None

    responsibilities: str | None = None

    requirements: str | None = None

    required_skills: str | None = None

    preferred_skills: str | None = None

    technologies: str | None = None

    experience: str | None = Field(
        default=None,
        max_length=100
    )

    education: str | None = Field(
        default=None,
        max_length=255
    )

    location: str | None = Field(
        default=None,
        max_length=255
    )

    work_mode: str | None = Field(
        default=None,
        max_length=50
    )

    employment_type: str | None = Field(
        default=None,
        max_length=50
    )

    salary_min: int | None = Field(
        default=None,
        ge=0
    )

    salary_max: int | None = Field(
        default=None,
        ge=0
    )

    salary_currency: str = Field(
        default="INR",
        max_length=10
    )

    openings: int | None = Field(
        default=1,
        ge=1,
        le=10000
    )

    application_deadline: str | None = Field(
        default=None,
        max_length=50
    )

    benefits: str | None = None

    status: str = Field(
        default="draft",
        max_length=30
    )


class JobQuestionCreate(BaseModel):

    question: str = Field(
        min_length=2,
        max_length=2000
    )

    question_type: str = Field(
        default="text",
        max_length=30
    )

    options: list[str] | None = None

    required: bool = True

    display_order: int = Field(
        default=0,
        ge=0
    )


class JobQuestionUpdate(BaseModel):

    question: str = Field(
        min_length=2,
        max_length=2000
    )

    question_type: str = Field(
        default="text",
        max_length=30
    )

    options: list[str] | None = None

    required: bool = True

    display_order: int = Field(
        default=0,
        ge=0
    )


# =========================================================
# CONSTANTS
# =========================================================

ALLOWED_STATUSES = {
    "draft",
    "active",
    "paused",
    "closed",
    "expired"
}


ALLOWED_QUESTION_TYPES = {
    "text",
    "long_text",
    "number",
    "yes_no",
    "single_choice",
    "multiple_choice"
}


# =========================================================
# HELPERS
# =========================================================

def clean_optional(value):

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    value = value.strip()

    return value if value else None


def validate_status(status: str):

    status = (
        status or "draft"
    ).strip().lower()

    if status not in ALLOWED_STATUSES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid job status. "
                "Allowed values: draft, active, "
                "paused, closed, expired."
            )
        )

    return status


def validate_question_type(
    question_type: str
):

    question_type = (
        question_type or "text"
    ).strip().lower()

    if question_type not in ALLOWED_QUESTION_TYPES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid question type. "
                "Allowed values: text, long_text, "
                "number, yes_no, single_choice, "
                "multiple_choice."
            )
        )

    return question_type


def validate_salary(
    salary_min,
    salary_max
):

    if (
        salary_min is not None
        and salary_max is not None
        and salary_min > salary_max
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Minimum salary cannot be greater "
                "than maximum salary."
            )
        )


def validate_deadline(
    application_deadline
):

    if not application_deadline:
        return

    try:

        deadline = date.fromisoformat(
            application_deadline[:10]
        )

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid application deadline. "
                "Use YYYY-MM-DD format."
            )
        )

    if deadline < date.today():

        raise HTTPException(
            status_code=400,
            detail=(
                "Application deadline cannot "
                "be in the past."
            )
        )


def get_verified_company(
    company_id: int,
    db: Session
):

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )

    if not company:

        raise HTTPException(
            status_code=404,
            detail="Company not found."
        )

    if company.verification_status != "verified":

        raise HTTPException(
            status_code=403,
            detail=(
                "Only verified companies "
                "can manage jobs."
            )
        )

    if not company.account_active:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not active."
            )
        )

    if not company.admin_verified:

        raise HTTPException(
            status_code=403,
            detail=(
                "Your company account "
                "is not admin verified."
            )
        )

    return company


def get_company_job(
    company_id: int,
    job_id: int,
    db: Session
):

    job = (
        db.query(Job)
        .filter(
            Job.id == job_id,
            Job.company_id == company_id
        )
        .first()
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    return job


def question_response(
    question: JobQuestion
):

    options = None

    if question.options:

        try:

            decoded = json.loads(
                question.options
            )

            if isinstance(decoded, list):
                options = decoded

        except Exception:
            options = None

    return {
        "id": question.id,
        "job_id": question.job_id,
        "question": question.question,
        "question_type": question.question_type,
        "options": options,
        "required": question.required,
        "display_order": question.display_order
    }


def job_response(
    job: Job,
    questions=None
):

    result = {
        "id": job.id,
        "company_id": job.company_id,
        "company": job.company,
        "title": job.title,
        "description": job.description,
        "responsibilities": job.responsibilities,
        "requirements": job.requirements,
        "required_skills": job.required_skills,
        "preferred_skills": job.preferred_skills,
        "technologies": job.technologies,
        "experience": job.experience,
        "education": job.education,
        "location": job.location,
        "work_mode": job.work_mode,
        "employment_type": job.employment_type,
        "salary": job.salary,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "openings": job.openings,
        "benefits": job.benefits,
        "status": job.status,
        "source": job.source,
        "posted_date": job.posted_date,
        "application_deadline": job.application_deadline,
        "job_url": job.job_url,
        "application_url": job.application_url,
        "questions": []
    }

    if questions is not None:

        result["questions"] = [
            question_response(question)
            for question in questions
        ]

    return result


def build_salary_text(
    salary_min,
    salary_max
):

    if (
        salary_min is not None
        and salary_max is not None
    ):

        return (
            f"₹{salary_min:,} - "
            f"₹{salary_max:,}"
        )

    if salary_min is not None:

        return (
            f"₹{salary_min:,}+"
        )

    if salary_max is not None:

        return (
            f"Up to ₹{salary_max:,}"
        )

    return None


# =========================================================
# LIST COMPANY JOBS
# =========================================================

@router.get("/{company_id}")
def list_company_jobs(
    company_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)

    company = get_verified_company(
        company_id,
        db
    )

    jobs = (
        db.query(Job)
        .filter(
            Job.company_id == company.id
        )
        .order_by(
            Job.id.desc()
        )
        .all()
    )

    return {
        "success": True,
        "company_id": company.id,
        "total": len(jobs),
        "jobs": [
            job_response(job)
            for job in jobs
        ]
    }


# =========================================================
# GET SINGLE COMPANY JOB
# =========================================================

@router.get("/{company_id}/{job_id}")
def get_company_job_details(
    company_id: int,
    job_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == job.id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc()
        )
        .all()
    )

    return {
        "success": True,
        "job": job_response(
            job,
            questions
        )
    }


# =========================================================
# CREATE JOB
# =========================================================

@router.post("/{company_id}")
def create_company_job(
    company_id: int,
    job_data: JobCreate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    validate_salary(
        job_data.salary_min,
        job_data.salary_max
    )

    validate_deadline(
        job_data.application_deadline
    )

    status = validate_status(
        job_data.status
    )

    if status == "expired":

        raise HTTPException(
            status_code=400,
            detail=(
                "A new job cannot be created "
                "with expired status."
            )
        )

    job = Job(
        company_id=company.id,
        title=job_data.title.strip(),
        company=company.company_name,
        location=clean_optional(
            job_data.location
        ),
        work_mode=clean_optional(
            job_data.work_mode
        ),
        employment_type=clean_optional(
            job_data.employment_type
        ),
        salary=build_salary_text(
            job_data.salary_min,
            job_data.salary_max
        ),
        salary_min=job_data.salary_min,
        salary_max=job_data.salary_max,
        salary_currency=(
            job_data.salary_currency
            or "INR"
        ).strip().upper(),
        experience=clean_optional(
            job_data.experience
        ),
        education=clean_optional(
            job_data.education
        ),
        required_skills=clean_optional(
            job_data.required_skills
        ),
        preferred_skills=clean_optional(
            job_data.preferred_skills
        ),
        technologies=clean_optional(
            job_data.technologies
        ),
        description=clean_optional(
            job_data.description
        ),
        responsibilities=clean_optional(
            job_data.responsibilities
        ),
        requirements=clean_optional(
            job_data.requirements
        ),
        benefits=clean_optional(
            job_data.benefits
        ),
        openings=job_data.openings or 1,
        status=status,
        source="company",
        posted_date=(
            date.today().isoformat()
            if status == "active"
            else None
        ),
        application_deadline=clean_optional(
            job_data.application_deadline
        ),
        job_url="",
        application_url=None
    )

    db.add(job)

    try:

        db.commit()
        db.refresh(job)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to create company job."
            )
        )

    return {
        "success": True,
        "message": "Job created successfully.",
        "job": job_response(job)
    }


# =========================================================
# UPDATE JOB
# =========================================================

@router.put("/{company_id}/{job_id}")
def update_company_job(
    company_id: int,
    job_id: int,
    job_data: JobUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    validate_salary(
        job_data.salary_min,
        job_data.salary_max
    )

    validate_deadline(
        job_data.application_deadline
    )

    status = validate_status(
        job_data.status
    )

    if (
        status == "active"
        and job.status != "active"
        and not job.posted_date
    ):

        job.posted_date = (
            date.today().isoformat()
        )

    job.title = (
        job_data.title.strip()
    )

    job.company = (
        company.company_name
    )

    job.description = clean_optional(
        job_data.description
    )

    job.responsibilities = clean_optional(
        job_data.responsibilities
    )

    job.requirements = clean_optional(
        job_data.requirements
    )

    job.required_skills = clean_optional(
        job_data.required_skills
    )

    job.preferred_skills = clean_optional(
        job_data.preferred_skills
    )

    job.technologies = clean_optional(
        job_data.technologies
    )

    job.experience = clean_optional(
        job_data.experience
    )

    job.education = clean_optional(
        job_data.education
    )

    job.location = clean_optional(
        job_data.location
    )

    job.work_mode = clean_optional(
        job_data.work_mode
    )

    job.employment_type = clean_optional(
        job_data.employment_type
    )

    job.salary_min = (
        job_data.salary_min
    )

    job.salary_max = (
        job_data.salary_max
    )

    job.salary_currency = (
        job_data.salary_currency
        or "INR"
    ).strip().upper()

    job.salary = build_salary_text(
        job_data.salary_min,
        job_data.salary_max
    )

    job.openings = (
        job_data.openings or 1
    )

    job.benefits = clean_optional(
        job_data.benefits
    )

    job.application_deadline = (
        clean_optional(
            job_data.application_deadline
        )
    )

    job.status = status

    try:

        db.commit()
        db.refresh(job)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to update company job."
            )
        )

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == job.id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc()
        )
        .all()
    )

    return {
        "success": True,
        "message": "Job updated successfully.",
        "job": job_response(
            job,
            questions
        )
    }


# =========================================================
# CHANGE JOB STATUS
# =========================================================

@router.patch(
    "/{company_id}/{job_id}/status"
)
def change_job_status(
    company_id: int,
    job_id: int,
    status: str,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    new_status = validate_status(
        status
    )

    if (
        new_status == "active"
        and job.status != "active"
        and not job.posted_date
    ):

        job.posted_date = (
            date.today().isoformat()
        )

    job.status = new_status

    try:

        db.commit()
        db.refresh(job)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to change job status."
            )
        )

    return {
        "success": True,
        "message": (
            f"Job status changed to "
            f"{new_status}."
        ),
        "job": job_response(job)
    }


# =========================================================
# DELETE JOB
# =========================================================

@router.delete(
    "/{company_id}/{job_id}"
)
def delete_company_job(
    company_id: int,
    job_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    db.query(JobQuestion).filter(
        JobQuestion.job_id == job.id
    ).delete(
        synchronize_session=False
    )

    db.delete(job)

    try:

        db.commit()

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to delete company job."
            )
        )

    return {
        "success": True,
        "message": (
            "Job deleted successfully."
        )
    }


# =========================================================
# LIST JOB QUESTIONS
# =========================================================

@router.get(
    "/{company_id}/{job_id}/questions"
)
def list_job_questions(
    company_id: int,
    job_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == job.id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc()
        )
        .all()
    )

    return {
        "success": True,
        "job_id": job.id,
        "total": len(questions),
        "questions": [
            question_response(question)
            for question in questions
        ]
    }


# =========================================================
# CREATE JOB QUESTION
# =========================================================

@router.post(
    "/{company_id}/{job_id}/questions"
)
def create_job_question(
    company_id: int,
    job_id: int,
    question_data: JobQuestionCreate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    question_type = validate_question_type(
        question_data.question_type
    )

    if question_type in {
        "single_choice",
        "multiple_choice"
    }:

        if not question_data.options:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Options are required for "
                    "choice-based questions."
                )
            )

        cleaned_options = [
            option.strip()
            for option in question_data.options
            if option and option.strip()
        ]

        if len(cleaned_options) < 2:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Choice questions must have "
                    "at least two options."
                )
            )

    else:

        cleaned_options = None

    question = JobQuestion(
        job_id=job.id,
        question=(
            question_data.question.strip()
        ),
        question_type=question_type,
        options=(
            json.dumps(
                cleaned_options
            )
            if cleaned_options
            else None
        ),
        required=question_data.required,
        display_order=(
            question_data.display_order
        )
    )

    db.add(question)

    try:

        db.commit()
        db.refresh(question)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to create job question."
            )
        )

    return {
        "success": True,
        "message": (
            "Job question created successfully."
        ),
        "question": question_response(
            question
        )
    }


# =========================================================
# UPDATE JOB QUESTION
# =========================================================

@router.put(
    "/{company_id}/{job_id}/questions/{question_id}"
)
def update_job_question(
    company_id: int,
    job_id: int,
    question_id: int,
    question_data: JobQuestionUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    question = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.id == question_id,
            JobQuestion.job_id == job.id
        )
        .first()
    )

    if not question:

        raise HTTPException(
            status_code=404,
            detail="Job question not found."
        )

    question_type = validate_question_type(
        question_data.question_type
    )

    if question_type in {
        "single_choice",
        "multiple_choice"
    }:

        if not question_data.options:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Options are required for "
                    "choice-based questions."
                )
            )

        cleaned_options = [
            option.strip()
            for option in question_data.options
            if option and option.strip()
        ]

        if len(cleaned_options) < 2:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Choice questions must have "
                    "at least two options."
                )
            )

    else:

        cleaned_options = None

    question.question = (
        question_data.question.strip()
    )

    question.question_type = (
        question_type
    )

    question.options = (
        json.dumps(
            cleaned_options
        )
        if cleaned_options
        else None
    )

    question.required = (
        question_data.required
    )

    question.display_order = (
        question_data.display_order
    )

    try:

        db.commit()
        db.refresh(question)

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to update job question."
            )
        )

    return {
        "success": True,
        "message": (
            "Job question updated successfully."
        ),
        "question": question_response(
            question
        )
    }


# =========================================================
# DELETE JOB QUESTION
# =========================================================

@router.delete(
    "/{company_id}/{job_id}/questions/{question_id}"
)
def delete_job_question(
    company_id: int,
    job_id: int,
    question_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db)
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(
        company_id,
        db
    )

    job = get_company_job(
        company.id,
        job_id,
        db
    )

    question = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.id == question_id,
            JobQuestion.job_id == job.id
        )
        .first()
    )

    if not question:

        raise HTTPException(
            status_code=404,
            detail=(
                "Job question not found."
            )
        )

    db.delete(question)

    try:

        db.commit()

    except Exception:

        db.rollback()

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to delete job question."
            )
        )

    return {
        "success": True,
        "message": (
            "Job question deleted successfully."
        )
    }
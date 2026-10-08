from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.database.database import get_db

from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User

from backend.app.schemas.assessment_blueprint import AssessmentBlueprint

from backend.app.services.assessment.blueprint_engine import (
    DEFAULT_TOTAL_QUESTIONS,
    MAX_TOTAL_QUESTIONS,
    MIN_TOTAL_QUESTIONS,
    build_assessment_blueprint,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT BLUEPRINT API
#
# Read-only, dynamically-computed endpoint — nothing here is
# persisted. See backend/app/services/assessment/
# blueprint_engine.py for the deterministic algorithm itself.
#
# SECURITY NOTE (documented, not solved, in this task):
# the project has no session/token layer anywhere yet — every
# existing endpoint (candidate-jobs, applications, aptitude,
# company-applications) trusts a client-supplied user_id /
# company_id as-is. This endpoint follows that exact existing
# pattern rather than inventing a one-off auth check for
# itself. It should get the same real authentication as the
# rest of the project, in its own dedicated task, before this
# is exposed beyond local development.
# ============================================================


router = APIRouter(
    prefix="/api/assessment",
    tags=["Assessment Blueprint"],
)


@router.get(
    "/blueprint/{candidate_id}/{job_id}",
    response_model=AssessmentBlueprint,
)
def get_assessment_blueprint(
    candidate_id: int,
    job_id: int,
    total_questions: int = Query(
        default=DEFAULT_TOTAL_QUESTIONS,
        ge=MIN_TOTAL_QUESTIONS,
        le=MAX_TOTAL_QUESTIONS,
        description=(
            "Total questions the blueprint should plan for. "
            "Defaults to 10; the actual questions are generated "
            "later by Gemini, not by this endpoint."
        ),
    ),
    db: Session = Depends(get_db),
):

    user = (
        db.query(User)
        .filter(User.id == candidate_id)
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="Candidate not found",
        )

    job = (
        db.query(Job)
        .filter(Job.id == job_id)
        .first()
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    profile = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.user_id == candidate_id)
        .first()
    )

    resume = (
        db.query(Resume)
        .filter(Resume.user_id == candidate_id)
        .order_by(Resume.id.desc())
        .first()
    )

    resume_analysis = None

    if resume:

        resume_analysis = (
            db.query(ResumeAnalysis)
            .filter(ResumeAnalysis.resume_id == resume.id)
            .first()
        )

    try:

        blueprint = build_assessment_blueprint(
            user=user,
            profile=profile,
            resume_analysis=resume_analysis,
            job=job,
            total_questions=total_questions,
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to generate assessment blueprint: "
                f"{exc}"
            ),
        )

    return blueprint

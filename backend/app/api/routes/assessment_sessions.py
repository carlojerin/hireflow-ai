import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db

from backend.app.models.assessment_session import AssessmentSession
from backend.app.services.resume.active_resume import get_active_resume
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.interview_test import InterviewTest
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User

from backend.app.api.deps import (
    get_current_admin,
    get_current_company,
    get_current_user,
    require_csrf,
)

from backend.app.services.assessment.stage_progression_service import advance_pipeline, determine_next_stage
from backend.app.services.assessment.session_service import (
    AssessmentSessionError,
    SELF_INTRO_MAX_SECONDS,
    complete_self_intro,
    expire_self_intro_if_due,
    get_owned_application,
    get_session_for_application,
    start_assessment_session,
    start_self_intro,
)
from backend.app.services.assessment.aptitude_integration import (
    AptitudeIntegrationError,
    get_aptitude_readiness,
    start_personalized_aptitude,
)
from backend.app.services.assessment.programming_integration import (
    ProgrammingIntegrationError,
    get_programming_readiness,
    start_programming_assessment,
)
from backend.app.services.assessment.interview_integration import (
    InterviewIntegrationError,
    get_interview_readiness,
    start_interview_assessment,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSIONS (Task 4A)
#
# Candidate-facing lifecycle for one application's assessment
# journey. Only the self-introduction stage is functional in
# this task — everything past it (aptitude/programming/
# ai_interview/completed) is a planned state-machine value with
# no behavior wired to it yet.
#
# Every mutation here follows the exact Task 3C-3D-4 pattern:
# get_current_user() for identity, an ownership-checked lookup
# (never a client-supplied user_id), then require_csrf().
# ============================================================

router = APIRouter(
    prefix="/api/assessment/sessions",
    tags=["Assessment Sessions"],
)


# ============================================================
# SCHEMAS
# ============================================================

class SelfIntroCompleteRequest(BaseModel):

    text_introduction: str | None = Field(
        default=None,
        max_length=4000,
    )


# ============================================================
# RESPONSE HELPERS
# ============================================================

def _job_summary(job: Job) -> dict:

    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
    }


def _self_intro_response(session_record: AssessmentSession) -> dict:

    remaining_seconds = None

    if (
        session_record.self_intro_status == "in_progress"
        and session_record.self_intro_started_at
    ):

        elapsed = (
            datetime.utcnow() - session_record.self_intro_started_at
        ).total_seconds()

        remaining_seconds = max(
            0,
            int(SELF_INTRO_MAX_SECONDS - elapsed),
        )

    return {
        "status": session_record.self_intro_status,
        "max_seconds": SELF_INTRO_MAX_SECONDS,
        "started_at": (
            session_record.self_intro_started_at.isoformat()
            if session_record.self_intro_started_at
            else None
        ),
        "completed_at": (
            session_record.self_intro_completed_at.isoformat()
            if session_record.self_intro_completed_at
            else None
        ),
        "duration_seconds": session_record.self_intro_duration_seconds,
        "remaining_seconds": remaining_seconds,
        "text_introduction": session_record.self_intro_text,
    }


def _fetch_candidate_context(
    db: Session,
    user_id: int,
) -> tuple[User | None, CandidateProfile | None, ResumeAnalysis | None]:
    """
    Same candidate/profile/resume-analysis lookup already used
    by GET /api/assessment/blueprint/{candidate_id}/{job_id}
    (backend/app/api/routes/assessment.py) — reused here rather
    than reimplemented, since the aptitude preview/generation
    needs the exact same three objects.
    """

    user = db.query(User).filter(User.id == user_id).first()

    profile = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.user_id == user_id)
        .first()
    )

    resume = get_active_resume(db, user_id)

    resume_analysis = None

    if resume:

        resume_analysis = (
            db.query(ResumeAnalysis)
            .filter(ResumeAnalysis.resume_id == resume.id)
            .first()
        )

    return user, profile, resume_analysis


def _aptitude_test_summary(test: AptitudeTest | None) -> dict:

    if not test:
        return {"exists": False}

    percentage = None

    if test.score is not None and test.total_questions:

        percentage = round(
            (test.score / test.total_questions) * 100,
            2,
        )

    return {
        "exists": True,
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "ai_personalized": test.question_source == "ai",
        "total_questions": test.total_questions,
        "score": test.score,
        "percentage": percentage,
        "passed": test.passed,
    }


def _aptitude_block(
    db: Session,
    current_stage: str,
    application: JobApplication,
    job: Job,
) -> dict:
    """
    Read-only aptitude block embedded in every session response.
    Never includes question text or correct answers — only
    readiness, blueprint-derived preview info, and (once a test
    exists) its status/result, exactly like the existing
    aptitude_summary_response() HR already sees.
    """

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    readiness = get_aptitude_readiness(
        current_stage,
        application,
        user,
        profile,
        resume_analysis,
        job,
    )

    if not readiness.get("ready"):
        return {"ready": False, "exists": False}

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    block = {"ready": True, "preview": readiness["preview"]}
    block.update(_aptitude_test_summary(test))

    return block


def _programming_test_summary(test: ProgrammingTest | None) -> dict:

    if not test:
        return {"exists": False}

    percentage = None

    if test.score is not None and test.total_score:

        percentage = round(
            (test.score / test.total_score) * 100,
            2,
        )

    return {
        "exists": True,
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "ai_personalized": test.question_source == "ai",
        "total_questions": test.total_questions,
        "score": test.score,
        "total_score": test.total_score,
        "percentage": percentage,
        "passed": test.passed,
    }


def _programming_block(
    db: Session,
    current_stage: str,
    application: JobApplication,
    job: Job,
) -> dict:
    """
    Read-only programming block embedded in every session
    response. Mirrors _aptitude_block exactly — never includes
    problem text, hidden tests, or source code.
    """

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    readiness = get_programming_readiness(
        current_stage,
        application,
        user,
        profile,
        resume_analysis,
        job,
        db,
    )

    if not readiness.get("ready"):
        return {
            "ready": False,
            "exists": False,
            "assigned": readiness.get("assigned", False),
            "prior_passed": readiness.get("prior_passed", False),
        }

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    block = {
        "ready": True,
        "assigned": True,
        "prior_passed": True,
        "preview": readiness["preview"],
    }
    block.update(_programming_test_summary(test))

    return block


def _interview_test_summary(test: InterviewTest | None) -> dict:

    if not test:
        return {"exists": False}

    return {
        "exists": True,
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "ai_personalized": test.question_source == "ai",
        "total_questions": test.total_questions,
        "current_question_index": test.current_question_index,
        "overall_score": test.overall_score,
        "passed": test.passed,
        "recommendation": test.recommendation,
    }


def _interview_block(
    db: Session,
    current_stage: str,
    application: JobApplication,
    job: Job,
) -> dict:
    """
    Read-only interview block embedded in every session
    response. Mirrors _programming_block exactly — never
    includes question text, expected answer points, or
    candidate answers (the dedicated interview_tests.py GET
    endpoint is the only place those are ever returned).
    """

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    readiness = get_interview_readiness(
        current_stage,
        application,
        user,
        profile,
        resume_analysis,
        job,
        db,
    )

    if not readiness.get("ready"):
        return {
            "ready": False,
            "exists": False,
            "assigned": readiness.get("assigned", False),
            "prior_passed": readiness.get("prior_passed", False),
        }

    v2 = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if v2 is not None:
        return {
            "ready": True,
            "assigned": True,
            "prior_passed": True,
            "preview": readiness.get("preview"),
            "exists": True,
            "id": v2.id,
            "status": v2.status,
            "is_v2": True,
            "current_turn": v2.current_turn,
        }

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    block = {
        "ready": True,
        "assigned": True,
        "prior_passed": True,
        "preview": readiness["preview"],
    }
    block.update(_interview_test_summary(test))

    return block


def _session_response(
    db: Session,
    application: JobApplication,
    job: Job,
    session_record: AssessmentSession | None,
) -> dict:

    current_stage = (
        session_record.current_stage
        if session_record
        else "not_started"
    )

    return {
        "application_id": application.id,
        "application_status": application.status,
        "job": _job_summary(job),
        "session": (
            {
                "exists": True,
                "current_stage": session_record.current_stage,
                "self_intro": _self_intro_response(session_record),
            }
            if session_record
            else {
                "exists": False,
                "current_stage": "not_started",
                "self_intro": None,
            }
        ),
        "aptitude": _aptitude_block(
            db, current_stage, application, job
        ),
        "programming": _programming_block(
            db, current_stage, application, job
        ),
        "interview": _interview_block(
            db, current_stage, application, job
        ),
        # Task 6A: backend-authoritative stage states + where to go
        # next. The frontend only renders this; it never decides.
        "pipeline": advance_pipeline(db, application) or determine_next_stage(db, application),
    }


def _raise_service_error(exc: AssessmentSessionError):

    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
    )


def _get_application_and_job(
    db: Session,
    application_id: int,
) -> tuple[JobApplication, Job] | None:

    result = (
        db.query(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .filter(JobApplication.id == application_id)
        .first()
    )

    return result


# ============================================================
# GET SESSION STATE
#
# Readable by the owning candidate, the owning company (HR),
# or an admin — tried in that order, exactly the same
# try/except pattern already used by GET /api/auth/me. Each
# attempt only succeeds if that identity actually owns (or, for
# admin, is entitled to view) this specific application.
# ============================================================

@router.get("/{application_id}/titles")
def get_assessment_titles(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """What the planned tests are called for THIS application's job (for example 'Language & Grammar Assessment'), so every
    candidate page can show the right name. Same access rule as the session itself: the candidate who owns the application."""

    result = _get_application_and_job(db, application_id)

    if not result:
        raise HTTPException(status_code=404, detail="Application not found")

    application, job = result

    try:
        current_user = get_current_user(request, db)
    except HTTPException:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if current_user.id != application.user_id:
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")

    from backend.app.services.assessment.assessment_planner import practical_spec, stage_title

    practical = practical_spec(job)

    return {
        "aptitude": stage_title(job, "aptitude", "Aptitude Test"),
        "situational_judgment": stage_title(job, "programming", "Situational Judgment Test"),
        "stage3": {"kind": "practical", "environment": practical.get("environment")} if practical else {"kind": "mcq"},
    }


@router.get("/{application_id}")
def get_assessment_session(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):

    result = _get_application_and_job(db, application_id)

    if not result:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    application, job = result

    authorized = False

    try:

        current_user = get_current_user(request, db)

        if current_user.id == application.user_id:
            authorized = True

    except HTTPException:
        pass

    if not authorized:

        try:

            current_company = get_current_company(request, db)

            if current_company.id == job.company_id:
                authorized = True

        except HTTPException:
            pass

    if not authorized:

        try:

            get_current_admin(request, db)

            authorized = True

        except HTTPException:
            pass

    if not authorized:

        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource.",
        )

    session_record = get_session_for_application(db, application.id)

    if session_record:
        expire_self_intro_if_due(db, session_record)

    return _session_response(db, application, job, session_record)


# ============================================================
# START ASSESSMENT SESSION
# ============================================================

@router.post("/{application_id}/start")
def start_session(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    try:

        session_record = start_assessment_session(db, application)

    except AssessmentSessionError as exc:

        _raise_service_error(exc)

    return _session_response(db, application, job, session_record)


# ============================================================
# START SELF-INTRODUCTION
# ============================================================

@router.post("/{application_id}/self-intro/start")
def start_self_introduction(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail=(
                "The assessment has not been started for this "
                "application yet."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    try:

        session_record = start_self_intro(db, session_record)

    except AssessmentSessionError as exc:

        _raise_service_error(exc)

    return _session_response(db, application, job, session_record)


# ============================================================
# COMPLETE SELF-INTRODUCTION
# ============================================================

@router.post("/{application_id}/self-intro/complete")
def complete_self_introduction(
    application_id: int,
    payload: SelfIntroCompleteRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail=(
                "The assessment has not been started for this "
                "application yet."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    try:

        session_record = complete_self_intro(
            db,
            session_record,
            payload.text_introduction,
        )

    except AssessmentSessionError as exc:

        _raise_service_error(exc)

    return _session_response(db, application, job, session_record)


# ============================================================
# START PERSONALIZED APTITUDE (Task 4B)
#
# The one mutating action that generates-or-retrieves this
# application's AptitudeTest. Idempotent — a repeated call (page
# refresh, double click) never creates a second test and never
# consumes another Gemini request; it just returns the same
# session response with the existing test's real state.
#
# Everything past this point (taking the test, submitting
# answers, scoring, one-attempt enforcement) is entirely handled
# by the pre-existing, unmodified backend/app/api/routes/
# aptitude_tests.py endpoints — this route only ever creates the
# AptitudeTest row and hands back its summary, never its
# questions or answers.
# ============================================================

@router.post("/{application_id}/aptitude/start")
def start_aptitude(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail=(
                "The assessment has not been started for this "
                "application yet."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    try:

        start_personalized_aptitude(
            db,
            session_record.current_stage,
            application,
            job,
            user,
            profile,
            resume_analysis,
        )

    except AptitudeIntegrationError as exc:

        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        )

    return _session_response(db, application, job, session_record)


# ============================================================
# START PROGRAMMING ASSESSMENT (Task 4C)
#
# The one mutating action that generates-or-retrieves this
# application's ProgrammingTest. Idempotent — a repeated call
# (page refresh, double click) never creates a second test and
# never consumes another Gemini request. Eligibility is checked
# against the durable AptitudeTest.passed flag inside
# start_programming_assessment() itself — the frontend cannot
# unlock this by claiming a stage value.
# ============================================================

@router.post("/{application_id}/programming/start")
def start_programming(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail=(
                "The assessment has not been started for this "
                "application yet."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    try:

        start_programming_assessment(
            db,
            session_record,
            application,
            job,
            user,
            profile,
            resume_analysis,
        )

    except ProgrammingIntegrationError as exc:

        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        )

    return _session_response(db, application, job, session_record)


# ============================================================
# START AI INTERVIEW (Task 4D)
#
# The one mutating action that generates-or-retrieves this
# application's InterviewTest. Idempotent — a repeated call
# never creates a second test and never consumes another Gemini
# request. Eligibility is checked against the durable
# ProgrammingTest.passed flag inside start_interview_assessment()
# itself — the frontend cannot unlock this by claiming a stage
# value.
# ============================================================

@router.post("/{application_id}/interview/start")
def start_interview(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail=(
                "The assessment has not been started for this "
                "application yet."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    user, profile, resume_analysis = _fetch_candidate_context(
        db, application.user_id
    )

    try:

        start_interview_assessment(
            db,
            session_record,
            application,
            job,
            user,
            profile,
            resume_analysis,
        )

    except InterviewIntegrationError as exc:

        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        )

    return _session_response(db, application, job, session_record)


# ============================================================
# PROGRAMMING ASSESSMENT V2 — MULTI-LANGUAGE (Programming
# Engine V2, Live Integration task)
#
# ADDITIVE, standing ALONGSIDE the original /programming/start
# (and its run/submit endpoints in programming_tests.py) — the
# legacy Python-only flow is completely untouched and remains
# the default candidate path. Nothing routes a candidate here
# automatically yet; this is a genuinely functional, separately
# addressable API surface for the new multi-language engine,
# exactly per this task's own "the new route becomes the normal
# path only after it is proven" closing instruction.
#
# Same auth/ownership pattern as every other endpoint in this
# file: get_current_user() for identity, get_owned_application()
# for ownership (never a client-supplied user_id), require_csrf()
# for every mutation.
# ============================================================

from backend.app.services.assessment.programming_integration_v2 import (
    ProgrammingIntegrationV2Error,
    candidate_view_v2,
    run_code_v2,
    start_programming_assessment_v2,
    submit_assessment_v2,
)
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2


class RunCodeV2Request(BaseModel):
    question_id: int
    language: str = Field(min_length=1, max_length=30)
    source_code: str = Field(default="", max_length=20000)


class AutosaveCodeV2Request(BaseModel):
    question_id: int
    language: str = Field(min_length=1, max_length=30)
    source_code: str = Field(default="", max_length=20000)


class SubmitAnswerV2(BaseModel):
    question_id: int
    language: str = Field(min_length=1, max_length=30)
    source_code: str = Field(default="", max_length=20000)


class SubmitAssessmentV2Request(BaseModel):
    answers: list[SubmitAnswerV2] = Field(default_factory=list)


def _get_owned_v2_assessment(db: Session, application: JobApplication) -> ProgrammingAssessmentV2:

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment:

        raise HTTPException(
            status_code=404,
            detail="No V2 programming assessment exists for this application yet.",
        )

    return assessment


@router.post("/{application_id}/programming/v2/start")
def start_programming_v2(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail="The assessment has not been started for this application yet.",
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    user, profile, resume_analysis = _fetch_candidate_context(db, application.user_id)

    try:

        assessment, _created = start_programming_assessment_v2(
            db, session_record, application, job, user, profile, resume_analysis,
        )

    except ProgrammingIntegrationV2Error as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return candidate_view_v2(assessment, job=job)


@router.get("/{application_id}/programming/v2")
def get_programming_v2(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    assessment = _get_owned_v2_assessment(db, application)

    from backend.app.services.assessment.programming_integration_v2 import expire_if_due_v2
    expire_if_due_v2(db, assessment, application)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return candidate_view_v2(assessment, job=job)


@router.put("/{application_id}/programming/v2/autosave")
def autosave_code_v2_endpoint(
    application_id: int,
    payload: AutosaveCodeV2Request,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Draft-only autosave, called periodically by the frontend while a
    question is open — never scores, never gates Run/Submit, never
    touches ProgrammingSubmissionV2 (that table is only ever written
    by an explicit Run/Submit click). Best-effort: an
    expired/completed assessment simply stops accepting drafts
    rather than raising, since the frontend may still have one in
    flight when the clock runs out.
    """

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        return {"saved": False}

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment:
        return {"saved": False}

    from backend.app.services.assessment.programming_integration_v2 import expire_if_due_v2

    if (
        assessment.status != "in_progress"
        or expire_if_due_v2(db, assessment, application)
    ):
        return {"saved": False}

    draft = (
        json.loads(assessment.draft_code_json)
        if assessment.draft_code_json
        else {}
    )

    draft[str(payload.question_id)] = {
        "language": payload.language,
        "source_code": payload.source_code,
    }

    assessment.draft_code_json = json.dumps(draft)
    assessment.updated_at = datetime.utcnow()

    db.commit()

    return {"saved": True}


@router.post("/{application_id}/programming/v2/run")
def run_code_v2_endpoint(
    application_id: int,
    payload: RunCodeV2Request,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    assessment = _get_owned_v2_assessment(db, application)

    from backend.app.services.assessment.programming_integration_v2 import expire_if_due_v2

    if expire_if_due_v2(db, assessment, application):

        raise HTTPException(status_code=410, detail="This programming assessment has expired.")

    if assessment.status != "in_progress":

        raise HTTPException(status_code=409, detail="This assessment is not currently active.")

    try:

        results = run_code_v2(
            db, assessment, payload.question_id, payload.language, payload.source_code,
        )

    except ProgrammingIntegrationV2Error as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return {"results": results}


@router.post("/{application_id}/programming/v2/submit")
def submit_assessment_v2_endpoint(
    application_id: int,
    payload: SubmitAssessmentV2Request,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    assessment = _get_owned_v2_assessment(db, application)

    from backend.app.services.assessment.programming_integration_v2 import expire_if_due_v2

    if expire_if_due_v2(db, assessment, application):

        raise HTTPException(status_code=410, detail="This programming assessment has expired.")

    if assessment.status == "completed":

        raise HTTPException(status_code=409, detail="This programming assessment has already been submitted.")

    try:

        result = submit_assessment_v2(
            db, assessment, application,
            [a.model_dump() for a in payload.answers],
        )

    except ProgrammingIntegrationV2Error as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return result


# ============================================================
# SITUATIONAL JUDGMENT TEST (non-technical pipeline stage) —
# occupies the exact same pipeline slot the programming v2 routes
# above occupy for a technical candidate; see role_classification.py
# and stage_progression_service.py for how a candidate is routed to
# one or the other. Deliberately mirrors the programming v2 routes'
# shape closely (start/get/run-equivalent n/a/submit/autosave).
# ============================================================

from backend.app.services.assessment.sjt_integration import (
    SJTIntegrationError,
    candidate_view as sjt_candidate_view,
    expire_if_due as sjt_expire_if_due,
    start_sjt_assessment,
    submit_sjt_assessment,
)
from backend.app.models.situational_judgment_test import SituationalJudgmentTest


class SJTSubmittedAnswer(BaseModel):
    question_id: int
    selected_answer: str | None = None


class SubmitSJTRequest(BaseModel):
    answers: list[SJTSubmittedAnswer] = Field(default_factory=list)


class AutosaveSJTRequest(BaseModel):
    answers: list[SJTSubmittedAnswer] = Field(default_factory=list)


def _get_owned_sjt_assessment(db: Session, application: JobApplication) -> SituationalJudgmentTest:

    assessment = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="No Situational Judgment Test exists for this application yet.")

    return assessment


@router.post("/{application_id}/sjt/start")
def start_sjt_endpoint(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    session_record = get_session_for_application(db, application.id)

    if not session_record:
        raise HTTPException(status_code=400, detail="The assessment has not been started for this application yet.")

    try:
        assessment, _created = start_sjt_assessment(db, session_record, application)
    except SJTIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return sjt_candidate_view(assessment, job=job)


@router.get("/{application_id}/sjt")
def get_sjt_endpoint(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    assessment = _get_owned_sjt_assessment(db, application)

    sjt_expire_if_due(db, assessment, application)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return sjt_candidate_view(assessment, job=job)


@router.put("/{application_id}/sjt/autosave")
def autosave_sjt_endpoint(
    application_id: int,
    payload: AutosaveSJTRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Draft-only autosave — never scores, never gates submit, best-effort
    (an expired/completed assessment simply stops accepting drafts)."""

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        return {"saved": False}

    assessment = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if not assessment:
        return {"saved": False}

    if assessment.status != "in_progress" or sjt_expire_if_due(db, assessment, application):
        return {"saved": False}

    assessment.draft_answers = json.dumps([a.model_dump() for a in payload.answers])
    assessment.updated_at = datetime.utcnow()

    db.commit()

    return {"saved": True}


@router.post("/{application_id}/sjt/submit")
def submit_sjt_endpoint(
    application_id: int,
    payload: SubmitSJTRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    assessment = _get_owned_sjt_assessment(db, application)

    if sjt_expire_if_due(db, assessment, application):
        raise HTTPException(status_code=410, detail="This assessment has expired.")

    if assessment.status == "completed":
        raise HTTPException(status_code=409, detail="This assessment has already been submitted.")

    try:
        result = submit_sjt_assessment(
            db, assessment, application,
            [a.model_dump() for a in payload.answers],
        )
    except SJTIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return result


# ============================================================
# TECHNICAL DOMAIN ASSESSMENT (non-coding technical pipeline stage)
# — occupies the exact same pipeline slot the programming v2 routes
# above occupy for a technical candidate whose language IS
# executable; see role_classification.py's job_requires_domain_
# assessment() and stage_progression_service.py for the routing.
# Deliberately mirrors the SJT routes' shape closely.
# ============================================================

from backend.app.services.assessment.tda_integration import (
    TDAIntegrationError,
    candidate_view as tda_candidate_view,
    expire_if_due as tda_expire_if_due,
    start_tda_assessment,
    submit_tda_assessment,
)
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment


class TDASubmittedAnswer(BaseModel):
    question_id: int
    selected_answer: str | None = None


class SubmitTDARequest(BaseModel):
    answers: list[TDASubmittedAnswer] = Field(default_factory=list)


class AutosaveTDARequest(BaseModel):
    answers: list[TDASubmittedAnswer] = Field(default_factory=list)


def _get_owned_tda_assessment(db: Session, application: JobApplication) -> TechnicalDomainAssessment:

    assessment = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    if not assessment:
        raise HTTPException(status_code=404, detail="No Technical Domain Assessment exists for this application yet.")

    return assessment


@router.post("/{application_id}/tda/start")
def start_tda_endpoint(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    session_record = get_session_for_application(db, application.id)

    if not session_record:
        raise HTTPException(status_code=400, detail="The assessment has not been started for this application yet.")

    job = db.query(Job).filter(Job.id == application.job_id).first()

    try:
        assessment, _created = start_tda_assessment(db, session_record, application, job)
    except TDAIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return tda_candidate_view(assessment, job=job)


@router.get("/{application_id}/tda")
def get_tda_endpoint(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    assessment = _get_owned_tda_assessment(db, application)

    tda_expire_if_due(db, assessment, application)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return tda_candidate_view(assessment, job=job)


@router.put("/{application_id}/tda/autosave")
def autosave_tda_endpoint(
    application_id: int,
    payload: AutosaveTDARequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Draft-only autosave — never scores, never gates submit, best-effort
    (an expired/completed assessment simply stops accepting drafts)."""

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        return {"saved": False}

    assessment = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    if not assessment:
        return {"saved": False}

    if assessment.status != "in_progress" or tda_expire_if_due(db, assessment, application):
        return {"saved": False}

    assessment.draft_answers = json.dumps([a.model_dump() for a in payload.answers])
    assessment.updated_at = datetime.utcnow()

    db.commit()

    return {"saved": True}


@router.post("/{application_id}/tda/submit")
def submit_tda_endpoint(
    application_id: int,
    payload: SubmitTDARequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    assessment = _get_owned_tda_assessment(db, application)

    if tda_expire_if_due(db, assessment, application):
        raise HTTPException(status_code=410, detail="This assessment has expired.")

    if assessment.status == "completed":
        raise HTTPException(status_code=409, detail="This assessment has already been submitted.")

    try:
        result = submit_tda_assessment(
            db, assessment, application,
            [a.model_dump() for a in payload.answers],
        )
    except TDAIntegrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return result


# ============================================================
# AI INTERVIEW V2 (Task 5H) — ADDITIVE, standalone candidate
# routes, alongside the EXISTING /api/interview-tests/* legacy
# router (interview_tests.py), which is left completely
# untouched. Same auth/ownership pattern as every other endpoint
# in this file.
# ============================================================

from backend.app.services.assessment.interview_integration_v2 import (
    INTERVIEW_V2_MAX_TURNS,
    INTERVIEW_V2_MIN_TURNS,
    InterviewIntegrationV2Error,
    candidate_view_v2 as interview_candidate_view_v2,
    expire_if_due_v2 as interview_expire_if_due_v2,
    start_interview_v2,
    submit_answer_v2,
)
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2


class SubmitInterviewAnswerV2Request(BaseModel):
    answer_text: str = Field(default="", max_length=8000)


def _get_owned_interview_v2_session(db: Session, application: JobApplication) -> AIInterviewSessionV2:

    session = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not session:

        raise HTTPException(
            status_code=404,
            detail="No V2 AI interview exists for this application yet.",
        )

    return session


@router.post("/{application_id}/interview/v2/start")
def start_interview_v2_endpoint(
    application_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session_record = get_session_for_application(db, application.id)

    if not session_record:

        raise HTTPException(
            status_code=400,
            detail="The assessment has not been started for this application yet.",
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()

    user, profile, resume_analysis = _fetch_candidate_context(db, application.user_id)

    try:

        session, created = start_interview_v2(
            db, session_record, application, job, user, profile, resume_analysis,
        )

    except InterviewIntegrationV2Error as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    response = interview_candidate_view_v2(db, session, job=job)

    if created and hasattr(session, "_opening_message"):
        response["opening_message"] = session._opening_message

    return response


@router.get("/{application_id}/interview/v2")
def get_interview_v2(
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session = _get_owned_interview_v2_session(db, application)

    session_record = get_session_for_application(db, application.id)

    interview_expire_if_due_v2(db, session, application, session_record)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return interview_candidate_view_v2(db, session, job=job)


@router.post("/{application_id}/interview/v2/answer")
def answer_interview_v2(
    application_id: int,
    payload: SubmitInterviewAnswerV2Request,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application = get_owned_application(db, application_id, current_user.id)

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    session = _get_owned_interview_v2_session(db, application)

    session_record = get_session_for_application(db, application.id)

    if interview_expire_if_due_v2(db, session, application, session_record):

        raise HTTPException(status_code=410, detail="This AI interview has expired.")

    if session.status == "completed":

        raise HTTPException(status_code=409, detail="This AI interview has already been completed.")

    try:

        result = submit_answer_v2(
            db, session, application, session_record, payload.answer_text,
        )

    except InterviewIntegrationV2Error as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return result

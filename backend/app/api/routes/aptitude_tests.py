import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.job import Job
from backend.app.models.user import User
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.job_application import JobApplication
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.assessment_session import AssessmentSession

from backend.app.services.aptitude.question_bank import (
    get_aptitude_questions,
)
from backend.app.services.aptitude.ai_question_generator import (
    generate_ai_aptitude_questions,
)
from backend.app.services.aptitude.scoring import (
    score_aptitude_test,
    APTITUDE_PASS_THRESHOLD,
)
from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.services.assessment.stage_progression_service import advance_pipeline
from backend.app.api.deps import get_current_user, require_csrf, require_self


logger = logging.getLogger("jobpilot.aptitude")


router = APIRouter(
    prefix="/api/aptitude-tests",
    tags=["Aptitude Tests"],
)


# ============================================================
# CONSTANTS
# ============================================================

# An application must already be at one of these statuses for
# the candidate to see/interact with the aptitude stage at
# all. Only "shortlisted" allows STARTING a brand new test —
# the other three represent a test that already exists.
APTITUDE_VISIBLE_STATUSES = {
    "shortlisted",
    "aptitude_pending",
    "aptitude_passed",
    "aptitude_failed",
    "aptitude_expired",
    "aptitude_terminated",
}

APTITUDE_START_STATUS = "shortlisted"

# How long a candidate has to BEGIN an HR-assigned aptitude
# test before the assignment itself expires, computed by the
# backend from assigned_at — never trusted from the frontend.
# This is the assignment window, NOT the exam-taking duration —
# see APTITUDE_EXAM_DURATION_MINUTES below (Task 4D-STRICT-1).
APTITUDE_DEADLINE_HOURS = 24

# How long the candidate has to actually TAKE the exam once
# started (status flips assigned -> in_progress), computed from
# started_at into AptitudeTest.exam_expires_at. Deliberately a
# fixed, short exam duration — distinct from the 24-hour
# assignment window above, which only governs how long the
# candidate has to click "Start" in the first place.
APTITUDE_EXAM_DURATION_MINUTES = 20


# ============================================================
# SCHEMAS
# ============================================================

class StartAptitudeTestRequest(BaseModel):
    user_id: int


class SubmittedAnswer(BaseModel):
    question_id: int
    selected_answer: str | None = None


class SubmitAptitudeTestRequest(BaseModel):
    user_id: int
    answers: list[SubmittedAnswer] = Field(
        default_factory=list
    )


class AutosaveAptitudeAnswersRequest(BaseModel):
    user_id: int
    answers: list[SubmittedAnswer] = Field(
        default_factory=list
    )


# ============================================================
# HELPERS
# ============================================================

def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication:
    """
    Fetch a JobApplication by id, but ONLY if it belongs to
    user_id. This is the same candidate-ownership pattern
    already used by GET /api/applications/user/{user_id}/
    {application_id} — filtering by both id and user_id
    together, returning a generic 404 either way, so an
    application_id belonging to a different candidate can
    never be distinguished from one that doesn't exist.
    """

    application = (
        db.query(JobApplication)
        .filter(
            JobApplication.id == application_id,
            JobApplication.user_id == user_id,
        )
        .first()
    )

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    return application


# ============================================================
# SELF-INTRODUCTION GATE (Task 4D-SELF-INTRO-FIX)
#
# The Aptitude stage must never be startable until the
# candidate's Self Introduction (services/assessment/
# session_service.py) has actually been finished — completed OR
# gracefully auto-finalized on timeout, either of which advances
# AssessmentSession.current_stage away from "self_intro". This
# is the ONE gate that matters regardless of which path created
# the AptitudeTest row: the Task 4B self-service flow
# (aptitude_integration.py) already requires current_stage to
# be "aptitude" or later before it will even create a test, but
# the older, still-live HR-assign flow
# (company_applications.py's assign_aptitude_test()) creates a
# test the moment a candidate is merely "shortlisted" — with NO
# Self Introduction dependency at all. Checking it here, in the
# one endpoint that actually starts answering questions
# regardless of which path created the test, closes that gap
# without touching either creation path.
# ============================================================

def is_self_intro_completed(
    db: Session,
    application_id: int,
) -> bool:

    session_record = (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application_id)
        .first()
    )

    if not session_record:
        return False

    # current_stage only ever leaves "self_intro" once the
    # candidate's introduction has been completed or gracefully
    # auto-finalized on timeout (session_service.py) — never
    # merely because a frontend claims it has.
    return session_record.current_stage != "self_intro"


def sanitize_questions(
    questions: list[dict],
) -> list[dict]:
    """
    Strip correct_answer before any response reaches the
    candidate. Never expose answer keys before submission.
    """

    return [
        {
            "id": question["id"],
            "category": question.get(
                "category"
            ),
            "question": question["question"],
            "options": question["options"],
        }
        for question in questions
    ]


def job_summary(job: Job) -> dict:

    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
    }


def test_summary_response(
    test: AptitudeTest,
) -> dict:
    """
    Result summary only — never includes questions/answers,
    matching "no answer review feature yet".
    """

    percentage = None

    if (
        test.score is not None
        and test.total_questions
    ):

        percentage = round(
            (
                test.score
                / test.total_questions
            ) * 100,
            2,
        )

    # Assignment-window remaining time (only meaningful while
    # status == "assigned" — how long until the candidate must
    # click "Start" or lose the assignment).
    remaining_seconds = None

    if (
        test.expires_at
        and test.status == "assigned"
    ):

        remaining_seconds = max(
            0,
            int(
                (
                    test.expires_at
                    - datetime.utcnow()
                ).total_seconds()
            ),
        )

    # Exam-duration remaining time (Task 4D-STRICT-1) — only
    # meaningful while status == "in_progress". Deliberately a
    # separate field/clock from remaining_seconds above; the
    # frontend timer during the active exam must read THIS
    # field, never the 24-hour assignment-window one.
    exam_remaining_seconds = None

    if (
        test.exam_expires_at
        and test.status == "in_progress"
    ):

        exam_remaining_seconds = max(
            0,
            int(
                (
                    test.exam_expires_at
                    - datetime.utcnow()
                ).total_seconds()
            ),
        )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "score": test.score,
        "percentage": percentage,
        "passed": test.passed,
        "assigned_at": (
            test.assigned_at.isoformat()
            if test.assigned_at
            else None
        ),
        "expires_at": (
            test.expires_at.isoformat()
            if test.expires_at
            else None
        ),
        "remaining_seconds": remaining_seconds,
        "exam_expires_at": (
            test.exam_expires_at.isoformat()
            if test.exam_expires_at
            else None
        ),
        "exam_remaining_seconds": exam_remaining_seconds,
        "started_at": (
            test.started_at.isoformat()
            if test.started_at
            else None
        ),
        "completed_at": (
            test.completed_at.isoformat()
            if test.completed_at
            else None
        ),
    }


# ============================================================
# EXPIRY ENFORCEMENT (backend-authoritative)
#
# Called from every candidate-facing read/action so an
# assignment past its deadline is always treated as expired,
# even without a background scheduler. The frontend countdown
# is purely visual — this is the real gate.
# ============================================================

def expire_if_due(
    db: Session,
    test: AptitudeTest,
    application: JobApplication,
) -> bool:
    """
    Lazily flips an overdue "assigned"/"in_progress" test to
    "expired" (and records the matching status-history
    transition). Never raises — safe to call from a plain GET.
    Returns True if the test is (now) expired.
    """

    if test.status in ("expired", "terminated"):
        return True

    # ASSIGNMENT WINDOW — status still "assigned", candidate
    # never clicked Start within the 24-hour deadline.
    if (
        test.status == "assigned"
        and test.expires_at
        and datetime.utcnow() > test.expires_at
    ):

        test.status = "expired"
        test.updated_at = datetime.utcnow()

        record_status_change(
            db,
            application,
            to_status="aptitude_expired",
            changed_by_type="system",
            changed_by_id=None,
            note=(
                "Aptitude test deadline passed without "
                "completion."
            ),
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(test)
        db.refresh(application)

        return True

    # EXAM DURATION (Task 4D-STRICT-1) — status "in_progress",
    # candidate started but the fixed exam-taking window
    # (exam_expires_at) has now passed. Deliberately checked
    # against exam_expires_at, never the 24-hour expires_at
    # above — refreshing the browser must not grant extra exam
    # time, and a violation must never extend it either.
    if (
        test.status == "in_progress"
        and test.exam_expires_at
        and datetime.utcnow() > test.exam_expires_at
    ):

        test.status = "expired"
        test.updated_at = datetime.utcnow()

        record_status_change(
            db,
            application,
            to_status="aptitude_expired",
            changed_by_type="system",
            changed_by_id=None,
            note=(
                f"Aptitude exam time limit "
                f"({APTITUDE_EXAM_DURATION_MINUTES} minutes) "
                "passed without submission."
            ),
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(test)
        db.refresh(application)

        return True

    return False


def enforce_not_expired(
    db: Session,
    test: AptitudeTest,
    application: JobApplication,
) -> None:
    """
    Same lazy check as expire_if_due(), but raises 410 when the
    test is expired/terminated — used to actually block
    start/submit.
    """

    if test.status == "terminated":

        raise HTTPException(
            status_code=410,
            detail=(
                "This aptitude assessment was automatically "
                "terminated due to assessment integrity "
                "violations and cannot be resumed."
            ),
        )

    if expire_if_due(db, test, application):

        raise HTTPException(
            status_code=410,
            detail=(
                "This aptitude test has expired. Either the "
                "24-hour assignment window or the "
                f"{APTITUDE_EXAM_DURATION_MINUTES}-minute exam "
                "time limit has passed."
            ),
        )


def test_in_progress_response(
    test: AptitudeTest,
) -> dict:
    """
    In-progress test — includes sanitized (answer-free)
    questions so the candidate can resume.
    """

    response = test_summary_response(test)

    response["questions"] = sanitize_questions(
        json.loads(test.questions)
    )

    # Autosaved (not-yet-submitted) answers, so the candidate's
    # already-picked options survive a refresh/reconnect instead
    # of starting the resumed test blank while the exam clock
    # keeps running.
    response["draft_answers"] = (
        json.loads(test.draft_answers)
        if test.draft_answers
        else []
    )

    return response


# ============================================================
# GET APTITUDE TEST STATE FOR AN APPLICATION
# ============================================================

@router.get("/application/{application_id}")
def get_aptitude_test(
    application_id: int,
    user_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, user_id)

    application = get_owned_application(
        db,
        application_id,
        user_id,
    )

    job = (
        db.query(Job)
        .filter(Job.id == application.job_id)
        .first()
    )

    eligible = (
        application.status
        in APTITUDE_VISIBLE_STATUSES
    )

    self_intro_completed = is_self_intro_completed(db, application.id)

    if not eligible:

        return {
            "application_id": application.id,
            "application_status": application.status,
            "eligible": False,
            "self_intro_completed": self_intro_completed,
            "job": job_summary(job) if job else None,
            "test": None,
        }

    test = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.application_id
            == application.id
        )
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
            "application_status": application.status,
            "eligible": True,
            "self_intro_completed": self_intro_completed,
            "job": job_summary(job) if job else None,
            "test": None,
        }

    expire_if_due(db, test, application)

    test_response = (
        test_in_progress_response(test)
        if test.status == "in_progress"
        else test_summary_response(test)
    )

    return {
        "application_id": application.id,
        "application_status": application.status,
        "eligible": True,
        "self_intro_completed": self_intro_completed,
        "job": job_summary(job) if job else None,
        "test": test_response,
    }


# ============================================================
# START APTITUDE TEST
# ============================================================

@router.post("/application/{application_id}/start")
def start_aptitude_test(
    application_id: int,
    payload: StartAptitudeTestRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Starts an aptitude test that HR has already assigned via
    POST /api/company-applications/{company_id}/{application_id}
    /aptitude. Question generation (Gemini-first, deterministic
    fallback) now happens at ASSIGNMENT time, not here — this
    endpoint only transitions an "assigned" test into
    "in_progress" (stamping the real started_at) and hands back
    the sanitized questions, or resumes an already in-progress
    test idempotently.
    """

    require_self(current_user, payload.user_id)
    require_csrf(request)

    application = get_owned_application(
        db,
        application_id,
        payload.user_id,
    )

    # ----------------------------------------------------
    # SELF INTRODUCTION GATE (Task 4D-SELF-INTRO-FIX) —
    # mandatory regardless of whether this AptitudeTest was
    # created via the candidate self-service flow or an HR
    # assignment; see is_self_intro_completed()'s docstring.
    # ----------------------------------------------------

    if not is_self_intro_completed(db, application.id):

        raise HTTPException(
            status_code=403,
            detail=(
                "Complete your Self Introduction before "
                "starting the Aptitude Test."
            ),
        )

    existing_test = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.application_id
            == application.id
        )
        .first()
    )

    # ----------------------------------------------------
    # NOT ASSIGNED YET
    #
    # There is no more "auto-create from bare shortlisted
    # status" path — an aptitude test must be sent by HR
    # first (see the endpoint referenced above).
    # ----------------------------------------------------

    if not existing_test:

        raise HTTPException(
            status_code=403,
            detail=(
                "This aptitude test has not been assigned by "
                "the recruiter yet."
            ),
        )

    # ----------------------------------------------------
    # EXPIRED — backend-authoritative, independent of any
    # frontend countdown.
    # ----------------------------------------------------

    enforce_not_expired(db, existing_test, application)

    # ----------------------------------------------------
    # ALREADY COMPLETED — one-attempt rule, unchanged.
    # ----------------------------------------------------

    if existing_test.status == "completed":

        raise HTTPException(
            status_code=409,
            detail=(
                "This aptitude test has already been "
                "completed and cannot be retaken."
            ),
        )

    # ----------------------------------------------------
    # FIRST START — flip assigned -> in_progress, stamp the
    # real start time, and set the fixed exam-duration
    # deadline (Task 4D-STRICT-1) from THIS moment — never
    # from assigned_at. Questions already exist (created at
    # assignment time), unchanged from here on.
    # ----------------------------------------------------

    if existing_test.status == "assigned":

        now = datetime.utcnow()

        existing_test.status = "in_progress"
        existing_test.started_at = now
        existing_test.exam_expires_at = now + timedelta(
            minutes=APTITUDE_EXAM_DURATION_MINUTES
        )
        existing_test.updated_at = now

        db.commit()
        db.refresh(existing_test)

    # ----------------------------------------------------
    # RESUME — idempotent, identical to previous behavior.
    # ----------------------------------------------------

    return test_in_progress_response(existing_test)


# ============================================================
# AUTOSAVE APTITUDE ANSWERS (draft only — never scores, never
# advances status)
#
# Called periodically by the frontend while status ==
# "in_progress" so a dropped connection or accidental refresh
# doesn't lose every answer the candidate already picked. Never
# touches AptitudeTest.answers (that column is written exactly
# once, atomically, by submit_aptitude_test()).
# ============================================================

@router.put("/{test_id}/autosave")
def autosave_aptitude_answers(
    test_id: int,
    payload: AutosaveAptitudeAnswersRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, payload.user_id)
    require_csrf(request)

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.id == test_id)
        .first()
    )

    if not test:

        raise HTTPException(
            status_code=404,
            detail="Aptitude test not found",
        )

    application = get_owned_application(
        db,
        test.application_id,
        payload.user_id,
    )

    # Autosave is a best-effort convenience, not a gate — an
    # expired/completed test simply stops accepting drafts
    # rather than raising, since the frontend may still have a
    # pending autosave in flight when the clock runs out.
    if (
        test.status != "in_progress"
        or expire_if_due(db, test, application)
    ):

        return {"saved": False}

    submitted_answers = [
        answer.model_dump()
        for answer in payload.answers
    ]

    test.draft_answers = json.dumps(submitted_answers)
    test.updated_at = datetime.utcnow()

    db.commit()

    return {"saved": True}


# ============================================================
# SUBMIT APTITUDE TEST
# ============================================================

@router.post("/{test_id}/submit")
def submit_aptitude_test(
    test_id: int,
    payload: SubmitAptitudeTestRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, payload.user_id)
    require_csrf(request)

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.id == test_id)
        .first()
    )

    if not test:

        raise HTTPException(
            status_code=404,
            detail="Aptitude test not found",
        )

    # ----------------------------------------------------
    # OWNERSHIP
    #
    # Verified through the associated JobApplication, so a
    # candidate can never submit another candidate's test by
    # guessing/incrementing test_id.
    # ----------------------------------------------------

    application = get_owned_application(
        db,
        test.application_id,
        payload.user_id,
    )

    # ----------------------------------------------------
    # EXPIRED — backend-authoritative, independent of any
    # frontend countdown.
    # ----------------------------------------------------

    enforce_not_expired(db, test, application)

    if test.status == "completed":

        raise HTTPException(
            status_code=409,
            detail=(
                "This aptitude test has already been "
                "completed and cannot be retaken."
            ),
        )

    if test.status == "assigned":

        raise HTTPException(
            status_code=400,
            detail=(
                "Start the aptitude test before submitting "
                "answers."
            ),
        )

    # ----------------------------------------------------
    # SCORE ON THE BACKEND
    #
    # The frontend only ever supplies selected answers —
    # never a score. Scoring uses this test's own stored
    # question snapshot (with correct answers), never the
    # live question bank.
    # ----------------------------------------------------

    stored_questions = json.loads(test.questions)

    submitted_answers = [
        answer.model_dump()
        for answer in payload.answers
    ]

    result = score_aptitude_test(
        stored_questions,
        submitted_answers,
    )

    now = datetime.utcnow()

    to_status = (
        "aptitude_passed"
        if result["passed"]
        else "aptitude_failed"
    )

    # ----------------------------------------------------
    # ATOMIC COMPLETION GUARD
    #
    # The "already completed" check above only catches a clean
    # sequential re-submit. Two near-simultaneous requests (a
    # double-click, or an auto-submit-on-timeout racing a manual
    # click) can both pass that check before either commits. This
    # conditional UPDATE — WHERE status == "in_progress" — makes
    # the assigned/in_progress -> completed transition itself the
    # single source of truth: only one of the two requests can
    # ever match a row and flip it, so only one ever proceeds to
    # record_status_change()/advance_pipeline() below.
    # ----------------------------------------------------

    updated_rows = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.id == test.id,
            AptitudeTest.status == "in_progress",
        )
        .update(
            {
                "answers": json.dumps(submitted_answers),
                "score": result["correct_count"],
                "passed": result["passed"],
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )

    if updated_rows == 0:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail=(
                "This aptitude test has already been "
                "completed and cannot be retaken."
            ),
        )

    history_record = record_status_change(
        db,
        application,
        to_status=to_status,
        changed_by_type="system",
        changed_by_id=None,
        note=(
            f"Aptitude test score: {result['correct_count']}/"
            f"{result['total_questions']} "
            f"({result['percentage']}%)"
        ),
    )

    application.updated_date = datetime.utcnow()

    # See the equivalent note in start_aptitude_test() above:
    # notify_status_change() is intentionally not called for
    # aptitude_passed/aptitude_failed in this task. History is
    # already recorded via record_status_change() above.

    db.commit()
    db.refresh(test)
    db.refresh(application)

    # ----------------------------------------------------
    # AUTOMATIC PROGRESSION — APTITUDE -> PROGRAMMING
    #
    # A pass is the only thing that used to be missing for the
    # programming stage to unlock itself: start_programming_
    # assessment() (programming_integration.py) already accepts
    # a passed AptitudeTest + an "assigned" AssessmentAssignment
    # + application.status == "aptitude_passed" — the assignment
    # was previously always created by HR clicking "Enable
    # Programming" (company_applications.py). Creating that same
    # row here, automatically, the moment the pass is recorded,
    # makes programming available with zero HR action and zero
    # changes to that existing eligibility/generation logic.
    #
    # Best-effort and non-blocking, deliberately mirroring
    # notify_status_change()'s own pattern elsewhere in this
    # codebase: the aptitude result above is already fully
    # committed by this point, so a failure here can only ever
    # cost the candidate the automatic unlock (HR's existing
    # manual "Enable Programming" button remains a working
    # fallback) — it can never roll back or corrupt the result
    # that was already saved.
    # ----------------------------------------------------

    # Pass or fail, the candidate moves on to the next stage; HR makes the final decision from the results.
    advance_pipeline(db, application)

    return test_summary_response(test)

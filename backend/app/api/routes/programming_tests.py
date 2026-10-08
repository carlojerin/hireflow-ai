import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.job import Job
from backend.app.models.user import User
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_submission import ProgrammingSubmission

from backend.app.services.assessment.programming_scoring import (
    run_against_examples,
    score_against_hidden_tests,
)
from backend.app.services.assessment.programming_integration import (
    PROGRAMMING_DEADLINE_MINUTES,
    PROGRAMMING_PASS_THRESHOLD,
)
from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.services.assessment.stage_progression_service import advance_pipeline
from backend.app.api.deps import get_current_user, require_csrf, require_self


logger = logging.getLogger("jobpilot.programming_tests")


router = APIRouter(
    prefix="/api/programming-tests",
    tags=["Programming Tests"],
)


# ============================================================
# JOBPILOT AI - PROGRAMMING TESTS (Task 4C)
#
# Mirrors backend/app/api/routes/aptitude_tests.py's own
# structure and security pattern exactly:
#   - get_current_user() for identity, require_self() for
#     ownership, require_csrf() (a plain function call, after
#     authorization) for every mutation.
#   - Ownership resolved through the OWNING application, never
#     a client-supplied application_id/user_id alone.
#   - sanitize_programming_question() strips hidden_tests (and
#     evaluation_notes) before ANY response reaches the
#     candidate — mirrors sanitize_questions() stripping
#     correct_answer.
#   - A lazy expire_if_due() check runs on every read/action,
#     exactly like the aptitude and self-intro systems.
# ============================================================

# Rate limiting (Part 17) — local, simple, no external service.
MAX_RUNS_PER_QUESTION = 8
MAX_TOTAL_RUNS_PER_TEST = 20


# ============================================================
# SCHEMAS
# ============================================================

class RunCodeRequest(BaseModel):

    user_id: int
    question_id: int
    language: str = Field(default="python")
    source_code: str = Field(..., max_length=20000)


class FinalAnswer(BaseModel):

    question_id: int
    language: str = Field(default="python")
    source_code: str = Field(..., max_length=20000)


class SubmitFinalRequest(BaseModel):

    user_id: int
    answers: list[FinalAnswer] = Field(default_factory=list)


# ============================================================
# HELPERS
# ============================================================

def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication:
    """Identical combined id+owner filter used everywhere else
    in this project — a generic 404 either way, so an
    application_id belonging to a different candidate can never
    be distinguished from one that doesn't exist."""

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


def get_owned_test(
    db: Session,
    test_id: int,
    user_id: int,
) -> tuple[ProgrammingTest, JobApplication]:
    """Ownership verified through the test's own application_id
    — a candidate can never submit/run against another
    candidate's test by guessing/incrementing test_id."""

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.id == test_id)
        .first()
    )

    if not test:

        raise HTTPException(
            status_code=404,
            detail="Programming test not found",
        )

    application = get_owned_application(db, test.application_id, user_id)

    return test, application


def sanitize_programming_question(question: dict) -> dict:
    """
    Strip hidden_tests and evaluation_notes before any response
    reaches the candidate. Never expose the answer key.
    """

    return {
        "question_id": question["question_id"],
        "title": question["title"],
        "description": question["description"],
        "difficulty": question["difficulty"],
        "category": question["category"],
        "primary_skill": question["primary_skill"],
        "input_description": question["input_description"],
        "output_description": question["output_description"],
        "constraints": question.get("constraints", ""),
        "examples": question.get("examples", []),
        "starter_code": question.get("starter_code", ""),
        "expected_language": question["expected_language"],
        "time_limit_seconds": question["time_limit_seconds"],
        "memory_limit_mb": question["memory_limit_mb"],
    }


def job_summary(job: Job) -> dict:

    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
    }


def test_summary_response(test: ProgrammingTest) -> dict:
    """Result summary only — never includes questions/hidden
    tests/source code."""

    percentage = None

    if test.score is not None and test.total_score:

        percentage = round(
            (test.score / test.total_score) * 100,
            2,
        )

    remaining_seconds = None

    if test.status == "in_progress":

        remaining_seconds = max(
            0,
            int((test.expires_at - datetime.utcnow()).total_seconds()),
        )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "score": test.score,
        "total_score": test.total_score,
        "percentage": percentage,
        "passed": test.passed,
        "assigned_at": test.assigned_at.isoformat() if test.assigned_at else None,
        "expires_at": test.expires_at.isoformat() if test.expires_at else None,
        "remaining_seconds": remaining_seconds,
        "started_at": test.started_at.isoformat() if test.started_at else None,
        "submitted_at": test.submitted_at.isoformat() if test.submitted_at else None,
    }


def test_in_progress_response(test: ProgrammingTest) -> dict:

    response = test_summary_response(test)

    stored_questions = json.loads(test.questions)

    response["questions"] = [
        sanitize_programming_question(question)
        for question in stored_questions
    ]

    return response


# ============================================================
# EXPIRY ENFORCEMENT (backend-authoritative)
# ============================================================

def expire_if_due(
    db: Session,
    test: ProgrammingTest,
    application: JobApplication,
) -> bool:

    if test.status == "expired":
        return True

    if test.status == "in_progress" and datetime.utcnow() > test.expires_at:

        test.status = "expired"
        test.updated_at = datetime.utcnow()

        record_status_change(
            db,
            application,
            to_status="programming_expired",
            changed_by_type="system",
            changed_by_id=None,
            note="Programming assessment deadline passed without a final submission.",
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(test)
        db.refresh(application)

        return True

    return False


def enforce_not_expired(
    db: Session,
    test: ProgrammingTest,
    application: JobApplication,
) -> None:

    if expire_if_due(db, test, application):

        raise HTTPException(
            status_code=410,
            detail=(
                "This programming assessment has expired. The "
                f"{PROGRAMMING_DEADLINE_MINUTES}-minute completion "
                "window has passed."
            ),
        )


def _find_question(stored_questions: list[dict], question_id: int) -> dict | None:

    for question in stored_questions:

        if question["question_id"] == question_id:
            return question

    return None


# ============================================================
# GET PROGRAMMING TEST STATE FOR AN APPLICATION
# ============================================================

@router.get("/application/{application_id}")
def get_programming_test(
    application_id: int,
    user_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, user_id)

    application = get_owned_application(db, application_id, user_id)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
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
        "job": job_summary(job) if job else None,
        "test": test_response,
    }


# ============================================================
# RUN CODE (against visible examples only — feedback, never
# scored, rate-limited)
# ============================================================

@router.post("/{test_id}/run")
def run_code(
    test_id: int,
    payload: RunCodeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, payload.user_id)
    require_csrf(request)

    test, application = get_owned_test(db, test_id, payload.user_id)

    enforce_not_expired(db, test, application)

    if test.status == "completed":

        raise HTTPException(
            status_code=409,
            detail="This programming assessment has already been submitted.",
        )

    if payload.language.strip().lower() != "python":

        raise HTTPException(
            status_code=400,
            detail="Only Python is supported for code execution at this time.",
        )

    stored_questions = json.loads(test.questions)
    question = _find_question(stored_questions, payload.question_id)

    if not question:

        raise HTTPException(
            status_code=404,
            detail="Question not found in this test.",
        )

    total_runs = (
        db.query(ProgrammingSubmission)
        .filter(
            ProgrammingSubmission.programming_test_id == test.id,
            ProgrammingSubmission.submission_kind == "run",
        )
        .count()
    )

    if total_runs >= MAX_TOTAL_RUNS_PER_TEST:

        raise HTTPException(
            status_code=429,
            detail=(
                "You have reached the maximum number of code-run "
                "requests for this assessment."
            ),
        )

    question_runs = (
        db.query(ProgrammingSubmission)
        .filter(
            ProgrammingSubmission.programming_test_id == test.id,
            ProgrammingSubmission.question_id == payload.question_id,
            ProgrammingSubmission.submission_kind == "run",
        )
        .count()
    )

    if question_runs >= MAX_RUNS_PER_QUESTION:

        raise HTTPException(
            status_code=429,
            detail=(
                "You have reached the maximum number of code-run "
                "requests for this question."
            ),
        )

    result = run_against_examples(
        payload.source_code,
        question.get("examples", []),
        question.get("time_limit_seconds", 5),
    )

    submission = ProgrammingSubmission(
        programming_test_id=test.id,
        question_id=payload.question_id,
        submission_kind="run",
        language="python",
        source_code=payload.source_code,
        execution_status=result["execution_status"],
        score=None,
        execution_time_ms=result["execution_time_ms"],
        memory_used_kb=None,
        submitted_at=datetime.utcnow(),
    )

    db.add(submission)
    db.commit()

    if result["execution_status"] == "rejected":

        return {
            "execution_status": "rejected",
            "message": result["rejected_reason"],
        }

    return {
        "execution_status": result["execution_status"],
        "execution_time_ms": result["execution_time_ms"],
        "results": [
            {
                "passed": item["passed"],
                "stdout": item["stdout"],
                "stderr": item["stderr"],
            }
            for item in result["results"]
        ],
    }


# ============================================================
# SUBMIT FINAL ASSESSMENT (the one irreversible action — scores
# against hidden tests, exactly once)
# ============================================================

@router.post("/{test_id}/submit")
def submit_final_assessment(
    test_id: int,
    payload: SubmitFinalRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, payload.user_id)
    require_csrf(request)

    test, application = get_owned_test(db, test_id, payload.user_id)

    enforce_not_expired(db, test, application)

    if test.status == "completed":

        raise HTTPException(
            status_code=409,
            detail="This programming assessment has already been submitted.",
        )

    stored_questions = json.loads(test.questions)
    answers_by_question_id = {
        answer.question_id: answer for answer in payload.answers
    }

    question_results = []
    total_score = 0

    for question in stored_questions:

        question_id = question["question_id"]
        answer = answers_by_question_id.get(question_id)

        source_code = answer.source_code if answer else ""

        if answer and answer.language.strip().lower() != "python":

            raise HTTPException(
                status_code=400,
                detail="Only Python is supported for code execution at this time.",
            )

        if not source_code.strip():

            grading = {
                "execution_status": "error",
                "score_percentage": 0,
                "passed_count": 0,
                "total_count": len(question.get("hidden_tests", [])),
                "execution_time_ms": 0,
                "rejected_reason": None,
            }

        else:

            grading = score_against_hidden_tests(
                source_code,
                question.get("hidden_tests", []),
                question.get("time_limit_seconds", 5),
            )

        total_score += grading["score_percentage"]

        submission = ProgrammingSubmission(
            programming_test_id=test.id,
            question_id=question_id,
            submission_kind="final",
            language="python",
            source_code=source_code,
            execution_status=grading["execution_status"],
            score=grading["score_percentage"],
            execution_time_ms=grading["execution_time_ms"],
            memory_used_kb=None,
            submitted_at=datetime.utcnow(),
        )

        db.add(submission)

        question_results.append({
            "question_id": question_id,
            "title": question["title"],
            "difficulty": question["difficulty"],
            "passed_count": grading["passed_count"],
            "total_count": grading["total_count"],
            "percentage": grading["score_percentage"],
        })

    total_possible = len(stored_questions) * 100
    overall_percentage = (
        round((total_score / total_possible) * 100, 2)
        if total_possible > 0
        else 0.0
    )
    passed = overall_percentage >= PROGRAMMING_PASS_THRESHOLD

    now = datetime.utcnow()

    test.score = total_score
    test.total_score = total_possible
    test.passed = passed
    test.status = "completed"
    test.submitted_at = now
    test.updated_at = now

    to_status = "programming_passed" if passed else "programming_failed"

    record_status_change(
        db,
        application,
        to_status=to_status,
        changed_by_type="system",
        changed_by_id=None,
        note=(
            f"Programming assessment score: {total_score}/"
            f"{total_possible} ({overall_percentage}%)"
        ),
    )

    application.updated_date = now

    db.commit()
    db.refresh(test)
    db.refresh(application)

    # ----------------------------------------------------
    # AUTOMATIC PROGRESSION — PROGRAMMING -> AI INTERVIEW
    #
    # Mirrors the equivalent block at the end of aptitude_tests.
    # py's submit_aptitude_test() exactly: start_interview_
    # assessment() (interview_integration.py) already accepts a
    # passed ProgrammingTest + an "assigned" AssessmentAssignment
    # + application.status == "programming_passed" — creating
    # that assignment row here, automatically, is the only piece
    # that used to require HR clicking "Enable AI Interview".
    # Best-effort/non-blocking: the programming result above is
    # already committed, so a failure here only costs the
    # automatic unlock, never the saved result.
    # ----------------------------------------------------

    # pass or fail: the candidate moves on (HR decides from the results)
    advance_pipeline(db, application)

    return {
        "id": test.id,
        "status": test.status,
        "score": test.score,
        "total_score": test.total_score,
        "percentage": overall_percentage,
        "passed": test.passed,
        "question_results": question_results,
    }

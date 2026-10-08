import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.job import Job
from backend.app.models.user import User
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.interview_test import InterviewTest
from backend.app.models.interview_response import InterviewResponse

from backend.app.schemas.interview_evaluation import InterviewAnswerEvaluation

from backend.app.services.assessment.interview_evaluator import (
    evaluate_answer_ai,
    evaluate_answer_fallback,
)
from backend.app.services.assessment.interview_scoring import (
    compute_question_score,
    aggregate_interview_result,
)
from backend.app.services.assessment.interview_integration import (
    INTERVIEW_DEADLINE_MINUTES,
    finalize_interview_if_complete,
)
from backend.app.services.assessment.session_service import (
    get_session_for_application,
)
from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.api.deps import get_current_user, require_csrf, require_self


logger = logging.getLogger("jobpilot.interview_tests")


router = APIRouter(
    prefix="/api/interview-tests",
    tags=["Interview Tests"],
)


# ============================================================
# JOBPILOT AI - AI INTERVIEW TESTS (Task 4D)
#
# Mirrors backend/app/api/routes/programming_tests.py's own
# structure and security pattern exactly:
#   - get_current_user() for identity, require_self() for
#     ownership, require_csrf() (a plain function call, after
#     authorization) for every mutation.
#   - Ownership resolved through the OWNING application, never
#     a client-supplied application_id/user_id alone.
#   - sanitize_interview_question() strips expected_answer_points
#     and follow_up_focus before ANY response reaches the
#     candidate — mirrors sanitize_programming_question()'s
#     hidden_tests stripping.
#   - A lazy expire_if_due() check runs on every read/action.
#
# SERVER-CONTROLLED SEQUENCE (Part 8 of the task spec): the
# candidate is only ever shown InterviewTest.current_question_
# index's question. POST /{test_id}/answer rejects any
# question_id that does not match the current index — there is
# no endpoint that accepts an arbitrary question_id to "jump to".
#
# RATE LIMITING / NO RE-EVALUATION (Part 24): once a question_id
# has a stored InterviewResponse, that same stored evaluation is
# always returned instead of ever calling Gemini again for it.
# ============================================================

MAX_ANSWER_LENGTH = 8000


# ============================================================
# SCHEMAS
# ============================================================

class SubmitAnswerRequest(BaseModel):

    user_id: int
    question_id: int
    answer_text: str = Field(default="", max_length=MAX_ANSWER_LENGTH)


# ============================================================
# HELPERS
# ============================================================

def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication:

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
) -> tuple[InterviewTest, JobApplication]:

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.id == test_id)
        .first()
    )

    if not test:

        raise HTTPException(
            status_code=404,
            detail="Interview not found",
        )

    application = get_owned_application(db, test.application_id, user_id)

    return test, application


def sanitize_interview_question(question: dict) -> dict:
    """
    Strip expected_answer_points and follow_up_focus before any
    response reaches the candidate. Never expose the evaluation
    rubric.
    """

    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "category": question["category"],
        "difficulty": question["difficulty"],
        "primary_skill": question["primary_skill"],
        "what_it_assesses": question["what_it_assesses"],
    }


def job_summary(job: Job) -> dict:

    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
    }


def _safe_answer_recap(response: InterviewResponse) -> dict:

    evaluation = json.loads(response.evaluation_data) if response.evaluation_data else {}

    return {
        "question_id": response.question_id,
        "score": response.score,
        "feedback": evaluation.get("feedback", ""),
        "strengths": evaluation.get("strengths", []),
        "gaps": evaluation.get("gaps", []),
    }


def test_summary_response(
    db: Session,
    test: InterviewTest,
) -> dict:
    """
    Includes the current (sanitized) question and a safe recap
    of already-answered questions — never includes another
    candidate's data, and never includes expected_answer_points/
    follow_up_focus/evaluator-internal content.
    """

    stored_questions = json.loads(test.questions)

    remaining_seconds = None

    if test.status == "in_progress":

        remaining_seconds = max(
            0,
            int((test.expires_at - datetime.utcnow()).total_seconds()),
        )

    current_question = None

    if test.status == "in_progress" and test.current_question_index < test.total_questions:

        raw_question = stored_questions[test.current_question_index]
        current_question = sanitize_interview_question(raw_question)

    answered_responses = (
        db.query(InterviewResponse)
        .filter(InterviewResponse.interview_test_id == test.id)
        .order_by(InterviewResponse.question_id)
        .all()
    )

    evaluation_summary = (
        json.loads(test.evaluation_summary)
        if test.evaluation_summary
        else None
    )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "current_question_index": test.current_question_index,
        "current_question": current_question,
        "answered_questions": [_safe_answer_recap(r) for r in answered_responses],
        "overall_score": test.overall_score,
        "passed": test.passed,
        "recommendation": test.recommendation,
        "evaluation_summary": evaluation_summary,
        "assigned_at": test.assigned_at.isoformat() if test.assigned_at else None,
        "expires_at": test.expires_at.isoformat() if test.expires_at else None,
        "remaining_seconds": remaining_seconds,
        "started_at": test.started_at.isoformat() if test.started_at else None,
        "submitted_at": test.submitted_at.isoformat() if test.submitted_at else None,
    }


# ============================================================
# EXPIRY ENFORCEMENT (backend-authoritative)
#
# Deliberately different from aptitude/programming's "just mark
# expired, no partial credit" precedent: Part 14 of the task
# spec explicitly requires the interview to finalize using
# whatever responses already exist at the deadline, rather than
# discarding them. If literally nothing was answered, it falls
# back to the simpler "expired, no score" behavior those other
# stages use.
# ============================================================

def expire_if_due(
    db: Session,
    test: InterviewTest,
    application: JobApplication,
) -> bool:

    if test.status != "in_progress":
        return test.status == "expired"

    if datetime.utcnow() <= test.expires_at:
        return False

    responses = (
        db.query(InterviewResponse)
        .filter(InterviewResponse.interview_test_id == test.id)
        .order_by(InterviewResponse.question_id)
        .all()
    )

    now = datetime.utcnow()

    if not responses:

        test.status = "expired"
        test.updated_at = now

        record_status_change(
            db,
            application,
            to_status="interview_expired",
            changed_by_type="system",
            changed_by_id=None,
            note="AI interview deadline passed without any answered questions.",
        )

        application.updated_date = now

        db.commit()
        db.refresh(test)
        db.refresh(application)

        return True

    evaluations = [
        InterviewAnswerEvaluation.model_validate(json.loads(r.evaluation_data))
        for r in responses
        if r.evaluation_data
    ]
    scores = [r.score for r in responses if r.score is not None]

    result = aggregate_interview_result(scores, evaluations)

    test.overall_score = result["overall_score"]
    test.passed = result["passed"]
    test.recommendation = result["recommendation"]
    test.evaluation_summary = json.dumps({
        **result["evaluation_summary"],
        "partial": True,
        "questions_answered": len(responses),
    })
    test.status = "completed"
    test.submitted_at = now
    test.updated_at = now

    to_status = "interview_passed" if test.passed else "interview_failed"

    record_status_change(
        db,
        application,
        to_status=to_status,
        changed_by_type="system",
        changed_by_id=None,
        note=(
            f"AI interview deadline passed — auto-finalized using "
            f"{len(responses)}/{test.total_questions} answered "
            f"question(s). Score: {test.overall_score}%."
        ),
    )

    application.updated_date = now

    db.commit()
    db.refresh(test)
    db.refresh(application)

    session_record = get_session_for_application(db, application.id)

    if session_record:
        finalize_interview_if_complete(db, session_record, test)

    return True


def enforce_not_expired(
    db: Session,
    test: InterviewTest,
    application: JobApplication,
) -> None:
    """
    Resolves a due-but-not-yet-processed expiry, then rejects
    (410) if the test's status is (now, or already was) "expired"
    — regardless of whether that happened on this exact call or
    an earlier one. A test that expiry resolved into "completed"
    (the partial-finalize path) is deliberately NOT rejected
    here — the caller's own "already completed" (409) check runs
    immediately after this and handles that case uniformly.
    """

    expire_if_due(db, test, application)

    if test.status == "expired":

        raise HTTPException(
            status_code=410,
            detail=(
                "This AI interview has expired. The "
                f"{INTERVIEW_DEADLINE_MINUTES}-minute completion "
                "window has passed."
            ),
        )


# ============================================================
# GET INTERVIEW STATE FOR AN APPLICATION
# ============================================================

@router.get("/application/{application_id}")
def get_interview_test(
    application_id: int,
    user_id: int = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, user_id)

    application = get_owned_application(db, application_id, user_id)

    job = db.query(Job).filter(Job.id == application.job_id).first()

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
            "job": job_summary(job) if job else None,
            "test": None,
        }

    expire_if_due(db, test, application)

    return {
        "application_id": application.id,
        "job": job_summary(job) if job else None,
        "test": test_summary_response(db, test),
    }


# ============================================================
# SUBMIT ANSWER (one question at a time — server-controlled)
# ============================================================

@router.post("/{test_id}/answer")
def submit_answer(
    test_id: int,
    payload: SubmitAnswerRequest,
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
            detail="This AI interview has already been completed.",
        )

    stored_questions = json.loads(test.questions)

    if test.current_question_index >= test.total_questions:

        raise HTTPException(
            status_code=409,
            detail="This AI interview has already been completed.",
        )

    # Idempotent re-check: if this exact question_id already has
    # a stored response (e.g. a retried/duplicate request), never
    # re-evaluate — return the existing result untouched. This is
    # both the one-attempt-per-question rule and the rate-limit/
    # cost-control mechanism (Part 24).
    existing_response = (
        db.query(InterviewResponse)
        .filter(
            InterviewResponse.interview_test_id == test.id,
            InterviewResponse.question_id == payload.question_id,
        )
        .first()
    )

    if existing_response:

        return {
            "question_id": existing_response.question_id,
            "score": existing_response.score,
            "evaluation": json.loads(existing_response.evaluation_data or "{}"),
            "evaluation_source": existing_response.evaluation_source,
            "interview_complete": test.status == "completed",
            "test": test_summary_response(db, test),
        }

    current_question = stored_questions[test.current_question_index]

    if payload.question_id != current_question["question_id"]:

        raise HTTPException(
            status_code=400,
            detail=(
                "You can only answer the current question of this "
                "interview."
            ),
        )

    job = db.query(Job).filter(Job.id == application.job_id).first()
    job_title = job.title if job else ""

    # Experience level isn't stored per-test; resolve it fresh
    # the same way the generator does, from the candidate's own
    # records (cheap, deterministic, no Gemini call).
    profile = (
        db.query(CandidateProfile)
        .filter(CandidateProfile.user_id == application.user_id)
        .first()
    )
    resume = (
        db.query(Resume)
        .filter(Resume.user_id == application.user_id)
        .order_by(Resume.id.desc())
        .first()
    )
    resume_analysis = (
        db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).first()
        if resume
        else None
    )

    experience_level = (
        (resume_analysis and resume_analysis.experience_level)
        or (profile and profile.experience_level)
        or getattr(current_user, "experience_level", None)
        or "Not specified"
    )

    evaluation_source = "ai"

    try:

        evaluation = evaluate_answer_ai(
            current_question,
            payload.answer_text,
            experience_level,
            job_title,
        )

    except Exception as exc:

        logger.warning(
            "AI interview answer evaluation failed for test %s "
            "question %s — falling back to deterministic "
            "evaluator: %s",
            test.id,
            payload.question_id,
            exc,
        )

        evaluation = evaluate_answer_fallback(current_question, payload.answer_text)
        evaluation_source = "fallback"

    question_score = compute_question_score(evaluation)

    now = datetime.utcnow()

    response = InterviewResponse(
        interview_test_id=test.id,
        question_id=payload.question_id,
        answer_text=payload.answer_text,
        score=question_score,
        evaluation_data=json.dumps(evaluation.model_dump()),
        evaluation_source=evaluation_source,
        submitted_at=now,
    )

    db.add(response)

    test.current_question_index += 1
    test.updated_at = now

    interview_complete = test.current_question_index >= test.total_questions

    if interview_complete:

        all_responses = (
            db.query(InterviewResponse)
            .filter(InterviewResponse.interview_test_id == test.id)
            .order_by(InterviewResponse.question_id)
            .all()
        )

        all_scores = [r.score for r in all_responses if r.score is not None] + [question_score]
        all_evaluations = [
            InterviewAnswerEvaluation.model_validate(json.loads(r.evaluation_data))
            for r in all_responses
            if r.evaluation_data
        ] + [evaluation]

        result = aggregate_interview_result(all_scores, all_evaluations)

        test.overall_score = result["overall_score"]
        test.passed = result["passed"]
        test.recommendation = result["recommendation"]
        test.evaluation_summary = json.dumps(result["evaluation_summary"])
        test.status = "completed"
        test.submitted_at = now

        to_status = "interview_passed" if test.passed else "interview_failed"

        record_status_change(
            db,
            application,
            to_status=to_status,
            changed_by_type="system",
            changed_by_id=None,
            note=(
                f"AI interview completed. Score: {test.overall_score}% "
                f"({'passed' if test.passed else 'did not pass'})."
            ),
        )

        application.updated_date = now

    db.commit()
    db.refresh(test)
    db.refresh(response)

    if interview_complete:

        session_record = get_session_for_application(db, application.id)

        if session_record:
            finalize_interview_if_complete(db, session_record, test)

    return {
        "question_id": response.question_id,
        "score": response.score,
        "evaluation": evaluation.model_dump(),
        "evaluation_source": evaluation_source,
        "interview_complete": interview_complete,
        "test": test_summary_response(db, test),
    }

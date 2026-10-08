import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.job_application import JobApplication
from backend.app.models.interview_test import InterviewTest

from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.services.assessment.blueprint_engine import (
    build_assessment_blueprint,
)
from backend.app.services.assessment.interview_question_generator import (
    generate_ai_interview_questions,
)
from backend.app.services.assessment.interview_question_bank import (
    get_interview_questions,
)
from backend.app.services.assessment.assignment_service import (
    is_assigned,
)

from backend.app.schemas.interview_question import REQUIRED_QUESTION_COUNT


logger = logging.getLogger("jobpilot.assessment.interview_integration")


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSION <-> AI INTERVIEW BRIDGE
# (Task 4D)
#
# Mirrors services/assessment/programming_integration.py's
# architecture exactly: a read-only readiness/preview function
# used on every GET, and one mutating generate-or-retrieve
# function.
#
# UPDATED (Task 4D-F1): HR must explicitly enable this stage for
# this application (see assign_interview_assessment() in
# company_applications.py) before the candidate can generate/
# start it — see the is_assigned() check in
# start_interview_assessment() below.
#
# Eligibility is gated directly on the durable, already-scored
# ProgrammingTest row (status == "completed" and passed ==
# True) — the same "lazy stage entry, gated on a durable
# precondition" pattern start_personalized_aptitude() and
# start_programming_assessment() already use. This function is
# the one that flips current_stage from "programming" to
# "ai_interview", the first time it succeeds — requiring zero
# changes to the unmodified programming_tests.py.
#
# finalize_interview_if_complete() is the ONE place that ever
# advances current_stage to "completed" — and only once every
# question has actually been answered and scored, per Part 16
# of the task spec ("Do NOT mark completed merely because the
# frontend navigates to a result page").
# ============================================================

INTERVIEW_START_STATUS = "programming_passed"
INTERVIEW_DEADLINE_MINUTES = 30

INTERVIEW_READY_STAGES = ("ai_interview", "completed")


class InterviewIntegrationError(Exception):
    """Mirrors AptitudeIntegrationError / ProgrammingIntegrationError
    exactly: carries an HTTP-style status code so the route layer
    can translate it without this service importing FastAPI."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ============================================================
# READ-ONLY READINESS / PREVIEW
# ============================================================

def _get_passed_programming_test(
    db: Session,
    application: JobApplication,
) -> ProgrammingTest | None:

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if test and test.status == "completed" and test.passed is True:
        return test

    return None


def build_interview_preview(blueprint) -> dict:
    """
    Non-sensitive, candidate-facing summary. Never includes
    question text, expected answer points, or the blueprint's
    internal priority/reason/match_status fields.
    """

    focus_areas = [
        item.skill
        for item in blueprint.skills_to_assess
    ][:8]

    return {
        "job_title": blueprint.job_title,
        "total_questions": REQUIRED_QUESTION_COUNT,
        "experience_level": blueprint.candidate_experience_level,
        "focus_areas": focus_areas or ["General problem solving"],
        "time_limit_minutes": INTERVIEW_DEADLINE_MINUTES,
    }


def get_interview_readiness(
    current_stage: str,
    application: JobApplication,
    user,
    profile,
    resume_analysis,
    job,
    db: Session,
) -> dict:
    """
    Read-only readiness info for GET /api/assessment/sessions/
    {application_id} — never generates or stores anything.

    Always computed fresh from the database on every call (never
    cached), so a normal page refresh reflects HR's latest
    assignment decision without requiring logout/login.
    """

    already_advanced = current_stage in INTERVIEW_READY_STAGES
    programming_passed = _get_passed_programming_test(db, application) is not None
    assigned = already_advanced or is_assigned(db, application.id, "ai_interview")

    ready = already_advanced or (
        current_stage == "programming" and programming_passed and assigned
    )

    if not ready:
        return {
            "ready": False,
            "prior_passed": programming_passed,
            "assigned": assigned,
        }

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=REQUIRED_QUESTION_COUNT,
        assessment_type="interview",
    )

    return {
        "ready": True,
        "prior_passed": True,
        "assigned": True,
        "preview": build_interview_preview(blueprint),
    }


# ============================================================
# GENERATE-OR-RETRIEVE (the one mutating "start" action)
# ============================================================

def start_interview_assessment(
    db: Session,
    session_record,
    application: JobApplication,
    job,
    user,
    profile,
    resume_analysis,
) -> tuple[InterviewTest, bool]:
    """
    Returns (test, created).

    - Raises InterviewIntegrationError(400, ...) if the
      candidate has not actually passed the programming
      assessment yet.
    - If an InterviewTest already exists for this application,
      it is returned unchanged — created=False. Never
      regenerates, never consumes another Gemini request.
    - Otherwise builds the Assessment Blueprint, attempts Gemini
      generation (falling back to the deterministic bank on any
      failure/validation error), creates the InterviewTest row
      already "in_progress" with a server-authoritative 30-
      minute deadline, advances the AssessmentSession's
      current_stage to "ai_interview", and records the matching
      application-status transition.
    """

    current_stage = session_record.current_stage

    if (
        current_stage not in INTERVIEW_READY_STAGES
        and current_stage != "programming"
    ):

        raise InterviewIntegrationError(
            status_code=400,
            detail=(
                "Complete the programming assessment before "
                "starting the AI interview."
            ),
        )

    existing_test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    if existing_test:
        return existing_test, False

    passed_programming = _get_passed_programming_test(db, application)

    if not passed_programming:

        raise InterviewIntegrationError(
            status_code=400,
            detail=(
                "You must pass the programming assessment before "
                "starting the AI interview."
            ),
        )

    # Task 5C Step 6 audit note: automatic assignment creation
    # for this exact stage ALREADY EXISTS — see submit_final_
    # assessment() in api/routes/programming_tests.py's own
    # "AUTOMATIC PROGRESSION — PROGRAMMING -> AI INTERVIEW" block,
    # which creates this assignment (assigned_by_type="system")
    # the moment a programming pass is recorded. Re-implementing
    # that same creation here would duplicate existing, already-
    # tested (Task 2) business logic — this check is left exactly
    # as it was before Step 6.
    if not is_assigned(db, application.id, "ai_interview"):

        raise InterviewIntegrationError(
            status_code=400,
            detail="AI Interview has not been assigned yet.",
        )

    if application.status != INTERVIEW_START_STATUS:

        raise InterviewIntegrationError(
            status_code=400,
            detail=(
                "This application is not eligible for the AI "
                f"interview. Current status: {application.status}."
            ),
        )

    aptitude_test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=REQUIRED_QUESTION_COUNT,
        assessment_type="interview",
    )

    question_source = "fallback"

    try:

        questions = generate_ai_interview_questions(
            job,
            user,
            profile,
            resume_analysis,
            blueprint=blueprint,
            aptitude_test=aptitude_test,
            programming_test=passed_programming,
        )

        question_source = "ai"

    except Exception as exc:

        logger.warning(
            "AI interview question generation failed for "
            "application %s — falling back to the fixed "
            "question bank: %s",
            application.id,
            exc,
        )

        questions = get_interview_questions()
        question_source = "fallback"

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=INTERVIEW_DEADLINE_MINUTES)

    test = InterviewTest(
        application_id=application.id,
        questions=json.dumps(questions),
        total_questions=len(questions),
        current_question_index=0,
        overall_score=None,
        passed=None,
        recommendation=None,
        evaluation_summary=None,
        status="in_progress",
        question_source=question_source,
        assigned_at=now,
        expires_at=expires_at,
        started_at=now,
        submitted_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(test)
    db.flush()

    session_record.current_stage = "ai_interview"
    session_record.updated_at = now

    record_status_change(
        db,
        application,
        to_status="interview_pending",
        changed_by_type="candidate",
        changed_by_id=application.user_id,
        note=(
            "AI interview generated automatically after passing "
            "the programming assessment."
        ),
    )

    application.updated_date = now

    db.commit()
    db.refresh(test)
    db.refresh(session_record)
    db.refresh(application)

    return test, True


# ============================================================
# FINALIZATION
#
# The ONE place AssessmentSession.current_stage ever becomes
# "completed" — called only once every question has been
# answered and scored. Never called merely because the
# candidate navigated to a result page.
# ============================================================

def finalize_interview_if_complete(
    db: Session,
    session_record,
    test: InterviewTest,
) -> None:

    if test.status != "completed":
        return

    if session_record.current_stage != "ai_interview":
        return

    session_record.current_stage = "completed"
    session_record.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(session_record)

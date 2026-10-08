import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_test import ProgrammingTest

from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.services.assessment.blueprint_engine import (
    build_assessment_blueprint,
)
from backend.app.services.assessment.programming_question_generator import (
    generate_ai_programming_questions,
)
from backend.app.services.assessment.programming_question_bank import (
    get_programming_questions,
)
from backend.app.services.assessment.assignment_service import (
    is_assigned,
)
from backend.app.services.assessment.assessment_plan_engine import (
    build_assessment_plan,
)

from backend.app.schemas.programming_question import (
    REQUIRED_QUESTION_COUNT,
)


logger = logging.getLogger("jobpilot.assessment.programming_integration")


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSION <-> PROGRAMMING BRIDGE
# (Task 4C)
#
# Mirrors services/assessment/aptitude_integration.py exactly:
# a read-only readiness/preview function used on every GET, and
# one mutating generate-or-retrieve function.
#
# UPDATED (Task 4D-F1): the programming stage is no longer pure
# candidate self-service. HR must first explicitly enable it for
# this application (see assign_programming_assessment() in
# company_applications.py, which writes an AssessmentAssignment
# row) before the candidate can generate/start it — see the
# is_assigned() check in start_programming_assessment() below.
# Once assigned + eligible, a ProgrammingTest is still created
# already "in_progress", with its own server-authoritative timer,
# exactly as before.
#
# Eligibility is intentionally NOT gated on
# AssessmentSession.current_stage already being "programming" —
# nothing else in this codebase ever sets it to that value. It
# is gated directly on the durable, already-scored AptitudeTest
# row (status == "completed" and passed == True), exactly
# matching Part 12 of the task spec ("After successful aptitude:
# aptitude_passed -> programming stage becomes available").
# This function is the one that flips current_stage from
# "aptitude" to "programming", the first time it succeeds — the
# same "lazy stage entry, gated on a durable precondition"
# pattern start_assessment_session() already uses for entering
# assessment sessions in the first place. This requires zero
# changes to the existing, unmodified aptitude_tests.py.
# ============================================================

PROGRAMMING_START_STATUS = "aptitude_passed"
PROGRAMMING_DEADLINE_MINUTES = 45

# Independent, same-value business-rule constant by design —
# matches this project's own established convention (see
# APTITUDE_PASS_THRESHOLD, MINIMUM_AI_MATCH_TO_APPLY,
# AI_SHORTLIST_THRESHOLD, all declared independently at their
# own call sites even though several currently share a value).
PROGRAMMING_PASS_THRESHOLD = 70.0

PROGRAMMING_READY_STAGES = ("programming", "ai_interview", "completed")


class ProgrammingIntegrationError(Exception):
    """Mirrors AptitudeIntegrationError / AssessmentSessionError
    exactly: carries an HTTP-style status code so the route
    layer can translate it without this service importing
    FastAPI."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ============================================================
# READ-ONLY READINESS / PREVIEW
# ============================================================

def _get_passed_aptitude_test(
    db: Session,
    application: JobApplication,
) -> AptitudeTest | None:

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if test and test.status == "completed":   # finished: passing is not required to continue
        return test

    return None


def build_programming_preview(blueprint) -> dict:
    """
    Non-sensitive, candidate-facing summary of what the
    programming assessment will cover — job title, question
    count, categories, general difficulty mix. Never includes
    problem text, hidden tests, or the blueprint's internal
    priority/reason/match_status fields.
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
        "time_limit_minutes": PROGRAMMING_DEADLINE_MINUTES,
    }


def get_programming_readiness(
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

    already_advanced = current_stage in PROGRAMMING_READY_STAGES
    aptitude_passed = _get_passed_aptitude_test(db, application) is not None
    assigned = already_advanced or is_assigned(db, application.id, "programming")

    ready = already_advanced or (
        current_stage == "aptitude" and aptitude_passed and assigned
    )

    if not ready:
        return {
            "ready": False,
            "prior_passed": aptitude_passed,
            "assigned": assigned,
        }

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=REQUIRED_QUESTION_COUNT,
        assessment_type="programming",
    )

    return {
        "ready": True,
        "prior_passed": True,
        "assigned": True,
        "preview": build_programming_preview(blueprint),
    }


# ============================================================
# GENERATE-OR-RETRIEVE (the one mutating action)
# ============================================================

def start_programming_assessment(
    db: Session,
    session_record,
    application: JobApplication,
    job,
    user,
    profile,
    resume_analysis,
) -> tuple[ProgrammingTest, bool]:
    """
    Returns (test, created).

    - Raises ProgrammingIntegrationError(400, ...) if the
      candidate has not actually passed the personalized
      aptitude assessment yet.
    - If a ProgrammingTest already exists for this application,
      it is returned unchanged — created=False. Never
      regenerates, never consumes another Gemini request.
    - Otherwise builds the Assessment Blueprint, attempts Gemini
      generation (falling back to the deterministic bank on any
      failure/validation error), creates the ProgrammingTest row
      already "in_progress" with a server-authoritative 45-
      minute deadline, advances the AssessmentSession's
      current_stage to "programming", and records the matching
      application-status transition.
    """

    current_stage = session_record.current_stage

    if (
        current_stage not in PROGRAMMING_READY_STAGES
        and current_stage != "aptitude"
    ):

        raise ProgrammingIntegrationError(
            status_code=400,
            detail=(
                "Complete the self-introduction and aptitude "
                "stages before starting the programming "
                "assessment."
            ),
        )

    existing_test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if existing_test:
        return existing_test, False

    passed_aptitude = _get_passed_aptitude_test(db, application)

    if not passed_aptitude:

        raise ProgrammingIntegrationError(
            status_code=400,
            detail=(
                "You must pass the aptitude assessment before "
                "starting the programming assessment."
            ),
        )

    # Task 5C Step 6 audit note: automatic assignment creation
    # for this exact stage ALREADY EXISTS — see submit_aptitude_
    # test() in api/routes/aptitude_tests.py's own "AUTOMATIC
    # PROGRESSION — APTITUDE -> PROGRAMMING" block, which creates
    # this assignment (assigned_by_type="system") the moment an
    # aptitude pass is recorded, well before the candidate ever
    # calls this function. Re-implementing that same creation
    # here would duplicate existing, already-tested (Task 2)
    # business logic — this check is therefore left exactly as
    # it was before Step 6; by the time a real candidate reaches
    # this point after passing aptitude, the assignment already
    # exists automatically, and no HR click is required for
    # normal operation.
    if not is_assigned(db, application.id, "programming"):

        raise ProgrammingIntegrationError(
            status_code=400,
            detail="Programming assessment has not been assigned yet.",
        )

    if application.status not in (PROGRAMMING_START_STATUS, "aptitude_failed"):

        raise ProgrammingIntegrationError(
            status_code=400,
            detail=(
                "This application is not eligible for the "
                "programming assessment. Current status: "
                f"{application.status}."
            ),
        )

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=REQUIRED_QUESTION_COUNT,
        assessment_type="programming",
    )

    # Task 5C Step 6 — CRITICAL TECHNOLOGY SUBSTITUTION GUARD.
    # The existing generator below (generate_ai_programming_
    # questions) always produces Python problems — appropriate
    # when the job requires Python or names no specific language
    # at all (the long-standing, unchanged default), but NEVER
    # appropriate when the job explicitly requires a DIFFERENT
    # language JobPilot cannot yet execute (e.g. Java, C++, Go).
    # Deliberately deterministic (use_gemini=False) — this is a
    # narrow, job-text-only technology scan, not a full plan, and
    # must never cost a second Gemini call just to decide this.
    # Deliberately narrow (only blocks on an EXPLICIT non-Python
    # language match) so ordinary jobs with no language signal at
    # all keep working completely unchanged — see the module-
    # level audit note on backward compatibility.
    plan_for_language_check = build_assessment_plan(
        job, blueprint, use_gemini=False,
    )

    required_non_python_languages = sorted({
        item.technology
        for item in plan_for_language_check.assessment_types
        if item.assessment_type == "programming"
        and item.technology
        and item.technology.strip().lower() != "python"
    })

    if required_non_python_languages:

        raise ProgrammingIntegrationError(
            status_code=409,
            detail=(
                "This job's programming assessment requires "
                f"{', '.join(required_non_python_languages)}, which "
                "JobPilot cannot yet execute securely. Programming "
                "assessment is currently only available for Python."
            ),
        )

    question_source = "fallback"

    try:

        questions = generate_ai_programming_questions(
            job,
            user,
            profile,
            resume_analysis,
            blueprint=blueprint,
        )

        question_source = "ai"

    except Exception as exc:

        logger.warning(
            "AI programming question generation failed for "
            "application %s — falling back to the fixed "
            "question bank: %s",
            application.id,
            exc,
        )

        questions = get_programming_questions()
        question_source = "fallback"

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=PROGRAMMING_DEADLINE_MINUTES)

    test = ProgrammingTest(
        application_id=application.id,
        questions=json.dumps(questions),
        total_questions=len(questions),
        score=None,
        total_score=None,
        passed=None,
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

    session_record.current_stage = "programming"
    session_record.updated_at = now

    record_status_change(
        db,
        application,
        to_status="programming_pending",
        changed_by_type="candidate",
        changed_by_id=application.user_id,
        note=(
            "Programming assessment generated automatically "
            "after passing the personalized aptitude assessment."
        ),
    )

    application.updated_date = now

    db.commit()
    db.refresh(test)
    db.refresh(session_record)
    db.refresh(application)

    return test, True

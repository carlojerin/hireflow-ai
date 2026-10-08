import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job_application import JobApplication

from backend.app.services.application_status_history import (
    record_status_change,
)
from backend.app.services.assessment.blueprint_engine import (
    DEFAULT_TOTAL_QUESTIONS,
    build_assessment_blueprint,
)
from backend.app.services.aptitude.question_bank import (
    get_aptitude_questions,
)
from backend.app.services.aptitude.aptitude_question_service import build_aptitude_questions
from backend.app.services.assessment.personalized_aptitude_generator import (
    generate_personalized_aptitude_questions,
)

from backend.app.schemas.assessment_blueprint import AssessmentBlueprint
from backend.app.schemas.personalized_aptitude import AptitudeQuestionSet


logger = logging.getLogger("jobpilot.assessment.aptitude_integration")


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSION <-> APTITUDE BRIDGE (Task 4B)
#
# Connects the Task 4A Assessment Session state machine to the
# pre-existing aptitude system (AptitudeTest, question_bank,
# ai_question_generator, aptitude_tests.py's own start/submit
# endpoints) WITHOUT modifying any of it. This module owns
# exactly one new responsibility: deciding WHEN a personalized
# aptitude test gets created for an application that has
# completed self-introduction, and building the Assessment
# Blueprint that personalizes it.
#
# It deliberately mirrors backend/app/api/routes/
# company_applications.py's assign_aptitude_test() creation
# block (same AptitudeTest field population, same "ai" ->
# fallback try/except, same aptitude_pending status transition)
# so a self-service-generated test and an HR-assigned test are
# indistinguishable to every downstream consumer: the candidate
# start/submit endpoints, the Applications page timeline, and
# the company applicants view all already handle this exact
# shape and require no changes.
#
# APTITUDE_START_STATUS/APTITUDE_DEADLINE_HOURS are deliberately
# re-declared here rather than imported from aptitude_tests.py
# — an independent, same-value business-rule constant, matching
# this project's own established convention (see
# MINIMUM_AI_MATCH_TO_APPLY / AI_SHORTLIST_THRESHOLD /
# APTITUDE_PASS_THRESHOLD / session_service.ASSESSMENT_START_
# STATUS, all declared independently at each of their own call
# sites even though several currently share the value
# "shortlisted" / 70).
# ============================================================

APTITUDE_START_STATUS = "shortlisted"
APTITUDE_DEADLINE_HOURS = 24

# Stages at/after which self-introduction is considered done and
# the aptitude stage has genuinely been reached. Kept as a tuple
# here (not imported from session_service, which deliberately
# never mentions AptitudeTest) so this module's only dependency
# on Task 4A is the AssessmentSession row's own current_stage
# string value passed in by the caller.
APTITUDE_READY_STAGES = (
    "aptitude",
    "programming",
    "ai_interview",
    "completed",
)


def _aptitude_question_set_to_legacy_dicts(
    question_set: AptitudeQuestionSet,
) -> list[dict]:
    """
    Task 5C Step 6 — adapts the new, strongly-validated
    AptitudeQuestionSet (Step 3) into the EXACT plain-dict shape
    every existing consumer of AptitudeTest.questions already
    requires (see aptitude_tests.py's sanitize_questions(), which
    indexes question["id"] directly, and services/aptitude/
    scoring.py's score_aptitude_test(), which keys off
    question["id"] and question["correct_answer"]). This is a
    format adapter only — it duplicates no business logic, and
    every existing route/scoring function downstream is used
    completely unmodified.
    """

    return [
        {
            "id": question.question_id,
            "category": question.category,
            "difficulty": question.difficulty,
            "question": question.question,
            "options": question.options,
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
        }
        for question in question_set.questions
    ]


class AptitudeIntegrationError(Exception):
    """
    Raised for any business-rule violation in this bridge.
    Carries an HTTP-style status code so the route layer can
    translate it without this service importing FastAPI —
    mirrors session_service.AssessmentSessionError exactly.
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ============================================================
# BLUEPRINT-DRIVEN PREVIEW (read-only, no persistence, no
# Gemini call — safe to compute on every GET)
# ============================================================

def _difficulty_label(blueprint: AssessmentBlueprint) -> str:

    counts = {
        "basic": blueprint.difficulty_distribution.basic,
        "intermediate": blueprint.difficulty_distribution.intermediate,
        "advanced": blueprint.difficulty_distribution.advanced,
    }

    dominant = max(counts, key=counts.get)

    return {
        "basic": "Foundational — mostly easier questions",
        "intermediate": "Balanced — a mix of easy and moderate questions",
        "advanced": "Challenging — mostly advanced questions",
    }[dominant]


def build_aptitude_preview(blueprint: AssessmentBlueprint) -> dict:
    """
    Non-sensitive, candidate-facing summary of what the
    blueprint decided to assess — job title, question count,
    approximate difficulty, key focus areas. Never includes
    question text, correct answers, or the raw priority/
    reason/match_status internals.
    """

    focus_areas = [
        item.skill
        for item in blueprint.skills_to_assess
    ][:8]

    focus_areas.append("Problem Solving")

    return {
        "job_title": blueprint.job_title,
        "total_questions": blueprint.total_questions,
        "experience_level": blueprint.candidate_experience_level,
        "difficulty_label": _difficulty_label(blueprint),
        "focus_areas": focus_areas,
    }


def get_aptitude_readiness(
    current_stage: str,
    application: JobApplication,
    user,
    profile,
    resume_analysis,
    job,
) -> dict:
    """
    Read-only readiness info for GET /api/assessment/sessions/
    {application_id} — never generates or stores anything.
    """

    ready = current_stage in APTITUDE_READY_STAGES

    if not ready:
        return {"ready": False}

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=DEFAULT_TOTAL_QUESTIONS,
    )

    return {
        "ready": True,
        "preview": build_aptitude_preview(blueprint),
    }


# ============================================================
# GENERATE-OR-RETRIEVE (the one mutating action)
# ============================================================

def start_personalized_aptitude(
    db: Session,
    current_stage: str,
    application: JobApplication,
    job,
    user,
    profile,
    resume_analysis,
) -> tuple[AptitudeTest, bool]:
    """
    Returns (test, created).

    - Raises AptitudeIntegrationError(400, ...) if self-
      introduction has not actually been completed yet (stage
      still not_started/self_intro) — the frontend can never
      unlock this by claiming current_stage itself.
    - If an AptitudeTest already exists for this application
      (self-service-generated OR HR-assigned), it is returned
      unchanged — created=False. Never regenerates, never
      consumes another Gemini request.
    - Otherwise builds the Assessment Blueprint, attempts
      Gemini generation (falling back to the deterministic
      bank on any failure), creates the AptitudeTest row in
      "assigned" status exactly like the existing HR-assign
      flow, and records the same aptitude_pending status
      transition.
    """

    if current_stage not in APTITUDE_READY_STAGES:

        raise AptitudeIntegrationError(
            status_code=400,
            detail=(
                "Complete your self-introduction before "
                "starting the aptitude assessment."
            ),
        )

    existing_test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if existing_test:
        return existing_test, False

    if application.status != APTITUDE_START_STATUS:

        raise AptitudeIntegrationError(
            status_code=400,
            detail=(
                "This application is no longer eligible for a "
                "new aptitude assessment. Current status: "
                f"{application.status}."
            ),
        )

    blueprint = build_assessment_blueprint(
        user=user,
        profile=profile,
        resume_analysis=resume_analysis,
        job=job,
        total_questions=DEFAULT_TOTAL_QUESTIONS,
    )

    # Same single source as the HR "Send Assessment": Groq, then the pool of earlier Groq
    # questions, then parametric questions. The experience level comes from the blueprint.
    questions, question_source = build_aptitude_questions(
        job,
        user,
        profile,
        resume_analysis,
        blueprint=blueprint,
    )

    now = datetime.utcnow()
    expires_at = now + timedelta(hours=APTITUDE_DEADLINE_HOURS)

    test = AptitudeTest(
        application_id=application.id,
        questions=json.dumps(questions),
        answers=None,
        score=None,
        total_questions=len(questions),
        passed=None,
        status="assigned",
        question_source=question_source,
        assigned_at=now,
        expires_at=expires_at,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(test)
    db.flush()

    record_status_change(
        db,
        application,
        to_status="aptitude_pending",
        changed_by_type="candidate",
        changed_by_id=application.user_id,
        note=(
            "AI-personalized aptitude assessment generated "
            "automatically after self-introduction completion."
        ),
    )

    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(test)
    db.refresh(application)

    return test, True

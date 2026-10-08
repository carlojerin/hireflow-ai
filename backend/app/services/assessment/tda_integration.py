import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job_application import JobApplication
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
from backend.app.services.application_status_history import record_status_change
from backend.app.services.assessment.assignment_service import is_assigned
from backend.app.services.assessment.role_classification import job_technical_domain
from backend.app.services.tda.scoring import score_tda
from backend.app.services.tda.tda_question_service import build_tda_questions


# ============================================================
# JOBPILOT AI - TECHNICAL DOMAIN ASSESSMENT (non-coding technical
# pipeline stage) INTEGRATION
#
# The equivalent of sjt_integration.py / programming_integration_v2.py
# for a technical candidate whose required language JobPilot cannot
# execute — occupies the EXACT same pipeline slot (stage_progression_
# service.py's "programming" stage; see role_classification.py's
# job_requires_domain_assessment() for which candidates get this
# instead of a live coding assessment). Mirrors sjt_integration.py's
# shape closely on purpose, including the atomic-completion-guard fix
# already proven for aptitude/programming/SJT submit routes.
# ============================================================

TDA_START_STATUS = "aptitude_passed"
TDA_DEADLINE_MINUTES = 20


class TDAIntegrationError(Exception):

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _get_passed_aptitude_test(db: Session, application: JobApplication) -> AptitudeTest | None:

    test = db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).first()

    if test and test.status == "completed":   # finished: passing is not required to continue
        return test

    return None


def start_tda_assessment(
    db: Session,
    session_record,
    application: JobApplication,
    job,
) -> tuple[TechnicalDomainAssessment, bool]:
    """
    Returns (assessment, created). Idempotent: an existing row for
    this application is returned unchanged (created=False) — a
    stable snapshot for the candidate's whole attempt, same
    guarantee SJT's/programming's own start function makes.
    """

    current_stage = session_record.current_stage

    if current_stage not in ("programming", "ai_interview", "completed") and current_stage != "aptitude":

        raise TDAIntegrationError(
            400,
            "Complete the self-introduction and aptitude stages "
            "before starting this assessment.",
        )

    existing = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    if existing:
        return existing, False

    if not _get_passed_aptitude_test(db, application):

        raise TDAIntegrationError(
            400, "You must finish the aptitude assessment before starting this assessment.",
        )

    if not is_assigned(db, application.id, "technical_domain"):

        raise TDAIntegrationError(400, "This assessment has not been assigned yet.")

    if application.status not in (TDA_START_STATUS, "aptitude_failed"):

        raise TDAIntegrationError(
            400,
            f"This application is not eligible for this assessment. "
            f"Current status: {application.status}.",
        )

    domain = job_technical_domain(job) if job is not None else None

    if not domain:
        raise TDAIntegrationError(400, "This role has no technical domain assessment available.")

    questions, source = build_tda_questions(domain)

    if not questions:
        raise TDAIntegrationError(400, f"No questions are available for the '{domain}' domain yet.")

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=TDA_DEADLINE_MINUTES)

    assessment = TechnicalDomainAssessment(
        application_id=application.id,
        domain=domain,
        questions=json.dumps(questions),
        answers=None,
        total_questions=len(questions),
        passed=None,
        status="in_progress",
        question_source=source,
        assigned_at=now,
        expires_at=expires_at,
        exam_expires_at=expires_at,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(assessment)
    db.flush()

    # Same generic stage-3 slot name programming_integration_v2.py sets for its
    # own start -- every assessment occupying this pipeline slot (programming/
    # situational_judgment/technical_domain) uses "programming" here, which is
    # exactly what interview_integration_v2.py's start gate checks for. Leaving
    # this unset (as it was before this fix) silently blocked every TDA
    # candidate from ever starting the AI interview afterwards.
    session_record.current_stage = "programming"
    session_record.updated_at = now

    record_status_change(
        db, application, to_status="tda_pending",
        changed_by_type="system", changed_by_id=None,
        note=f"Technical Domain Assessment ({domain}) generated automatically after passing aptitude.",
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(application)

    return assessment, True


def expire_if_due(db: Session, assessment: TechnicalDomainAssessment, application: JobApplication) -> bool:

    if assessment.status == "expired":
        return True

    if assessment.status == "in_progress" and datetime.utcnow() > assessment.exam_expires_at:

        assessment.status = "expired"
        assessment.updated_at = datetime.utcnow()

        record_status_change(
            db, application, to_status="tda_expired",
            changed_by_type="system", changed_by_id=None,
            note="Technical Domain Assessment deadline passed without a final submission.",
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(assessment)
        db.refresh(application)

        return True

    return False


def _sanitize_questions(questions: list[dict]) -> list[dict]:
    """Strips correct_answer before any response reaches the candidate."""

    return [
        {
            "id": q["id"],
            "category": q.get("category"),
            "question": q["question"],
            "options": q["options"],
        }
        for q in questions
    ]


def candidate_view(assessment: TechnicalDomainAssessment, job=None) -> dict:

    questions = json.loads(assessment.questions)

    remaining_seconds = None

    if assessment.exam_expires_at and assessment.status == "in_progress":
        remaining_seconds = max(0, int((assessment.exam_expires_at - datetime.utcnow()).total_seconds()))

    return {
        "status": assessment.status,
        "domain": assessment.domain,
        "assigned_at": assessment.assigned_at.isoformat() if assessment.assigned_at else None,
        "expires_at": assessment.expires_at.isoformat() if assessment.expires_at else None,
        "exam_remaining_seconds": remaining_seconds,
        "questions": _sanitize_questions(questions) if assessment.status == "in_progress" else [],
        "total_questions": assessment.total_questions,
        "score": assessment.score,
        "passed": assessment.passed,
        "draft_answers": json.loads(assessment.draft_answers) if assessment.draft_answers else [],
        "job": ({"title": job.title, "company": job.company} if job is not None else None),
    }


def submit_tda_assessment(
    db: Session,
    assessment: TechnicalDomainAssessment,
    application: JobApplication,
    answers: list[dict],
) -> dict:

    questions = json.loads(assessment.questions)

    result = score_tda(questions, answers)

    now = datetime.utcnow()

    to_status = "tda_passed" if result["passed"] else "tda_failed"

    # ATOMIC COMPLETION GUARD — same fix already applied to aptitude,
    # programming, and SJT submit routes.
    updated_rows = (
        db.query(TechnicalDomainAssessment)
        .filter(
            TechnicalDomainAssessment.id == assessment.id,
            TechnicalDomainAssessment.status == "in_progress",
        )
        .update(
            {
                "answers": json.dumps(answers),
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

        raise TDAIntegrationError(409, "This assessment has already been submitted.")

    record_status_change(
        db, application, to_status=to_status,
        changed_by_type="system", changed_by_id=None,
        note=f"Technical Domain Assessment ({assessment.domain}) score: {result['correct_count']}/{result['total_questions']} ({result['percentage']}%)",
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(application)

    # pass or fail: the candidate moves on (HR decides from the results)
    from backend.app.services.assessment.stage_progression_service import advance_pipeline
    advance_pipeline(db, application)

    return {
        "status": assessment.status,
        "score": assessment.score,
        "total_questions": assessment.total_questions,
        "percentage": result["percentage"],
        "passed": result["passed"],
    }

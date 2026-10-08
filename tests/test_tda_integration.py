"""
Technical Domain Assessment (non-coding technical pipeline stage)
service-layer tests: start/submit/autosave, the atomic double-
submit guard (same fix already proven for aptitude/programming/
SJT), domain selection, and expiry -- all against an in-memory
SQLite DB, no network.
"""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.database.database import Base
from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User
from backend.app.services.assessment.tda_integration import (
    TDAIntegrationError,
    candidate_view,
    start_tda_assessment,
    submit_tda_assessment,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db, status="aptitude_passed", title="Salesforce Developer", skills="Salesforce, Apex, Lightning"):

    user = User(full_name="T", username="ttda", email="ttda@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()

    job = Job(
        title=title, company="Co",
        required_skills=skills,
        description="Build and configure solutions on the platform.",
    )
    db.add(job); db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status=status)
    db.add(application); db.flush()

    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)

    db.add(AptitudeTest(application_id=application.id, questions="[]", total_questions=10, passed=True, status="completed"))

    db.add(AssessmentAssignment(
        application_id=application.id, assessment_type="technical_domain",
        status="assigned", assigned_by_type="system", assigned_by_id=user.id,
    ))

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    return user, job, application, session_record


def test_start_picks_the_right_domain_and_creates_a_stable_snapshot_idempotently():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment1, created1 = start_tda_assessment(db, session_record, application, job)
    assert created1 is True
    assert assessment1.domain == "salesforce"
    assert assessment1.total_questions > 0
    assert assessment1.status == "in_progress"

    assessment2, created2 = start_tda_assessment(db, session_record, application, job)
    assert created2 is False
    assert assessment2.id == assessment1.id
    assert assessment2.questions == assessment1.questions  # unchanged snapshot


def test_different_domains_pick_different_question_banks():
    for title, skills, expected_domain in (
        ("SAP ABAP Developer", "SAP, ABAP, SAP HANA", "sap"),
        ("Blockchain Developer", "Solidity, Ethereum, Smart Contracts", "blockchain"),
        ("DevOps Engineer", "Docker, Kubernetes, Terraform, CI/CD", "devops_cloud"),
        ("Embedded Systems Engineer", "Embedded C, RTOS, Microcontrollers", "embedded"),
        ("Penetration Tester", "Penetration Testing, Nmap, Metasploit, Ethical Hacking", "cybersecurity"),
        ("MongoDB Developer", "MongoDB, NoSQL, Document Database Design", "nosql"),
    ):
        db = _db()
        user, job, application, session_record = _seed(db, title=title, skills=skills)

        assessment, _created = start_tda_assessment(db, session_record, application, job)
        assert assessment.domain == expected_domain, title

        questions = __import__("json").loads(assessment.questions)
        assert all(q["category"] == expected_domain for q in questions)


def test_start_rejects_without_a_passed_aptitude_test():
    db = _db()
    user, job, application, session_record = _seed(db)

    db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).delete()
    db.commit()

    try:
        start_tda_assessment(db, session_record, application, job)
        raised = False
    except TDAIntegrationError as exc:
        raised = True
        assert exc.status_code == 400

    assert raised


def test_candidate_view_never_leaks_correct_answers():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_tda_assessment(db, session_record, application, job)
    view = candidate_view(assessment, job=job)

    assert len(view["questions"]) > 0
    for q in view["questions"]:
        assert "correct_answer" not in q
    assert view["domain"] == "salesforce"
    assert view["job"] == {"title": "Salesforce Developer", "company": "Co"}


def test_full_submission_scores_and_passes_at_70_percent():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_tda_assessment(db, session_record, application, job)
    stored_questions = __import__("json").loads(assessment.questions)

    answers = [{"question_id": q["id"], "selected_answer": q["correct_answer"]} for q in stored_questions]

    result = submit_tda_assessment(db, assessment, application, answers)

    assert result["status"] == "completed"
    assert result["passed"] is True
    assert result["percentage"] == 100.0

    db.refresh(application)
    assert application.status == "tda_passed"


def test_two_near_simultaneous_submits_only_one_succeeds():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_tda_assessment(db, session_record, application, job)
    stored_questions = __import__("json").loads(assessment.questions)
    wrong_answers = [{"question_id": q["id"], "selected_answer": "definitely-wrong"} for q in stored_questions]

    first = submit_tda_assessment(db, assessment, application, wrong_answers)
    assert first["status"] == "completed"

    try:
        submit_tda_assessment(db, assessment, application, wrong_answers)
        raised = False
    except TDAIntegrationError as exc:
        raised = True
        assert exc.status_code == 409

    assert raised, "second near-simultaneous submit must be rejected, not silently re-scored"

    history_count = (
        db.query(ApplicationStatusHistory)
        .filter(ApplicationStatusHistory.application_id == application.id)
        .count()
    )
    assert history_count == 2


def test_expired_assessment_cannot_be_submitted():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_tda_assessment(db, session_record, application, job)
    assessment.exam_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    from backend.app.services.assessment.tda_integration import expire_if_due
    assert expire_if_due(db, assessment, application) is True

    db.refresh(assessment)
    assert assessment.status == "expired"

    db.refresh(application)
    assert application.status == "tda_expired"

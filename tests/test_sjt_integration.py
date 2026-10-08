"""
Situational Judgment Test (non-technical pipeline stage) service-
layer tests: start/submit/autosave, the atomic double-submit guard
(same fix already proven for aptitude and programming), and
expiry -- all against an in-memory SQLite DB, no network.
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
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.user import User
from backend.app.services.assessment.sjt_integration import (
    SJTIntegrationError,
    candidate_view,
    start_sjt_assessment,
    submit_sjt_assessment,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db, status="aptitude_passed"):

    user = User(full_name="T", username="tsjt", email="tsjt@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()

    job = Job(
        title="Digital Marketing Executive", company="Co",
        required_skills="SEO, Content Writing, Social Media Management",
        description="Plan and run marketing campaigns.",
    )
    db.add(job); db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status=status)
    db.add(application); db.flush()

    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)

    db.add(AptitudeTest(application_id=application.id, questions="[]", total_questions=10, passed=True, status="completed"))

    db.add(AssessmentAssignment(
        application_id=application.id, assessment_type="situational_judgment",
        status="assigned", assigned_by_type="system", assigned_by_id=user.id,
    ))

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    return user, job, application, session_record


def test_start_creates_a_stable_snapshot_idempotently():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment1, created1 = start_sjt_assessment(db, session_record, application)
    assert created1 is True
    assert assessment1.total_questions == 10
    assert assessment1.status == "in_progress"

    assessment2, created2 = start_sjt_assessment(db, session_record, application)
    assert created2 is False
    assert assessment2.id == assessment1.id
    assert assessment2.questions == assessment1.questions  # unchanged snapshot


def test_start_rejects_without_a_passed_aptitude_test():
    db = _db()
    user, job, application, session_record = _seed(db)

    db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).delete()
    db.commit()

    try:
        start_sjt_assessment(db, session_record, application)
        raised = False
    except SJTIntegrationError as exc:
        raised = True
        assert exc.status_code == 400

    assert raised


def test_candidate_view_never_leaks_correct_answers():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert len(view["questions"]) == 10
    for q in view["questions"]:
        assert "correct_answer" not in q
    assert view["job"] == {"title": "Digital Marketing Executive", "company": "Co"}


def test_full_submission_scores_and_passes_at_70_percent():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_sjt_assessment(db, session_record, application)
    stored_questions = __import__("json").loads(assessment.questions)

    # answer every question correctly
    answers = [{"question_id": q["id"], "selected_answer": q["correct_answer"]} for q in stored_questions]

    result = submit_sjt_assessment(db, assessment, application, answers)

    assert result["status"] == "completed"
    assert result["passed"] is True
    assert result["percentage"] == 100.0

    db.refresh(application)
    assert application.status == "sjt_passed"


def test_two_near_simultaneous_submits_only_one_succeeds():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_sjt_assessment(db, session_record, application)
    stored_questions = __import__("json").loads(assessment.questions)
    wrong_answers = [{"question_id": q["id"], "selected_answer": "definitely-wrong"} for q in stored_questions]

    first = submit_sjt_assessment(db, assessment, application, wrong_answers)
    assert first["status"] == "completed"

    try:
        submit_sjt_assessment(db, assessment, application, wrong_answers)
        raised = False
    except SJTIntegrationError as exc:
        raised = True
        assert exc.status_code == 409

    assert raised, "second near-simultaneous submit must be rejected, not silently re-scored"

    # exactly one "programming_pending"-equivalent + one "sjt_failed" row -- never two
    history_count = (
        db.query(ApplicationStatusHistory)
        .filter(ApplicationStatusHistory.application_id == application.id)
        .count()
    )
    assert history_count == 2


def test_expired_assessment_cannot_be_submitted():
    db = _db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_sjt_assessment(db, session_record, application)
    assessment.exam_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    from backend.app.services.assessment.sjt_integration import expire_if_due
    assert expire_if_due(db, assessment, application) is True

    db.refresh(assessment)
    assert assessment.status == "expired"

    db.refresh(application)
    assert application.status == "sjt_expired"


def test_a_candidate_who_failed_aptitude_can_still_start_the_next_assessment():
    db = _db()
    user, job, application, session_record = _seed(db, status="aptitude_failed")

    db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).update({"passed": False})
    db.commit()

    assessment, created = start_sjt_assessment(db, session_record, application)

    assert created is True and assessment.status == "in_progress"


def test_an_unfinished_aptitude_test_still_blocks_the_next_assessment():
    db = _db()
    user, job, application, session_record = _seed(db)

    db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).update({"status": "in_progress", "passed": None})
    db.commit()

    try:
        start_sjt_assessment(db, session_record, application)
        raised = False
    except SJTIntegrationError as exc:
        raised = True
        assert exc.status_code == 400 and "finish the aptitude" in exc.detail

    assert raised


def test_the_interview_is_reachable_after_a_failed_stage_three():
    from backend.app.services.assessment.interview_integration_v2 import INTERVIEW_V2_START_STATUSES, _stage_3_passed

    db = _db()
    user, job, application, session_record = _seed(db, status="sjt_failed")

    db.add(SituationalJudgmentTest(
        application_id=application.id, questions="[]", total_questions=10, passed=False, status="completed", score=3,
    ))
    db.commit()

    assert {"sjt_failed", "tda_failed", "programming_failed"} <= INTERVIEW_V2_START_STATUSES
    assert _stage_3_passed(db, application) is True        # "finished", whatever the result

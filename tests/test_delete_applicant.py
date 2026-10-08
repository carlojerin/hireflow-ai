"""
Delete applicant (permanent) -- distinct from the existing archive/restore
toggle, which only hides an application without destroying anything.

- DELETE /api/company-applications/{company_id}/{application_id}: removes a
  JobApplication and every row keyed off it (aptitude/programming/interview
  v1+v2, integrity events, status history, answers, etc.).
- DELETE /api/company/{company_id}/invites/{invite_id}: removes a
  CandidateInvite, and cascades to the linked JobApplication if claimed --
  covers candidates who never even clicked the link (no application exists
  for those at all).

In-memory SQLite, no jobpilot.db.
"""

import os
import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.company_applications as company_applications_routes
import backend.app.api.routes.company_invites as company_invites_routes
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.interview_test import InterviewTest
from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.application_answer import ApplicationAnswer
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_consent import AssessmentConsent
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
from backend.app.api.deps import get_current_company

COMPANY_ID = 1
JOB_ID = 1

ALL_MODELS = (
    User, Company, Job, JobApplication, CandidateInvite, CandidateProfile,
    AptitudeTest, ProgrammingTest, InterviewTest,
    ApplicationStatusHistory, ApplicationAnswer, AssessmentSession,
    AssessmentAssignment, AssessmentConsent, AssessmentSnapshot,
    AssessmentVideoRecording, AssessmentIntegrityEvent, AIInterviewSessionV2,
    AIInterviewTurnV2, ProgrammingAssessmentV2, ProgrammingSubmissionV2,
    SituationalJudgmentTest, TechnicalDomainAssessment,
)


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in ALL_MODELS:
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()

    company = Company(
        id=COMPANY_ID, company_name="Acme", legal_name="Acme Ltd", email="hr@acme.test", mobile="123",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True, role="company",
    )
    db.add(company)

    job = Job(id=JOB_ID, company_id=COMPANY_ID, title="Backend Engineer", company="Acme", status="active")
    db.add(job)

    db.commit()
    return db, company


def _applications_app(db, company):
    api = FastAPI()
    api.include_router(company_applications_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company
    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})


def _invites_app(db, company):
    api = FastAPI()
    api.include_router(company_invites_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company
    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})


def _seed_application_with_child_rows(db):
    now = datetime.utcnow()

    user = User(
        full_name="Test Candidate", username="testcand1", email="cand@example.test",
        mobile=None, hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    application = JobApplication(
        job_id=JOB_ID, user_id=user.id, status="aptitude_pending",
        recruiter_shortlisted=True, applied_date=now, updated_date=now,
    )
    db.add(application)
    db.flush()

    db.add(AptitudeTest(application_id=application.id, questions="[]", total_questions=5, status="in_progress"))
    db.add(ApplicationStatusHistory(application_id=application.id, from_status="shortlisted", to_status="aptitude_pending", changed_by_type="system"))
    db.add(AssessmentSession(application_id=application.id, current_stage="aptitude"))

    programming_assessment = ProgrammingAssessmentV2(
        application_id=application.id, question_set_json="{}", allowed_languages_json='["python"]',
        generated_by="fallback", assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now,
    )
    db.add(programming_assessment)
    db.flush()
    db.add(ProgrammingSubmissionV2(
        assessment_id=programming_assessment.id, question_id=1, submission_kind="run",
        language="python", source_code="print(1)", submitted_at=now,
    ))

    interview_session = AIInterviewSessionV2(
        application_id=application.id, blueprint_json="{}",
        assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now,
    )
    db.add(interview_session)
    db.flush()
    db.add(AIInterviewTurnV2(
        session_id=interview_session.id, turn_number=1, question="Tell me about yourself",
        question_type="behavioral", competency="communication",
    ))

    db.commit()
    db.refresh(application)
    return user, application, programming_assessment, interview_session


def test_delete_applicant_removes_application_and_every_child_row():
    db, company = _db()
    user, application, programming_assessment, interview_session = _seed_application_with_child_rows(db)

    # Capture primitive ids up front -- after deletion, the ORM objects
    # themselves become expired/detached and re-touching an attribute on
    # them raises ObjectDeletedError.
    user_id = user.id
    application_id = application.id
    programming_assessment_id = programming_assessment.id
    interview_session_id = interview_session.id

    client = _applications_app(db, company)

    response = client.delete(f"/api/company-applications/{COMPANY_ID}/{application_id}")
    assert response.status_code == 200, response.text

    assert db.query(JobApplication).filter(JobApplication.id == application_id).first() is None
    assert db.query(AptitudeTest).filter(AptitudeTest.application_id == application_id).count() == 0
    assert db.query(ApplicationStatusHistory).filter(ApplicationStatusHistory.application_id == application_id).count() == 0
    assert db.query(AssessmentSession).filter(AssessmentSession.application_id == application_id).count() == 0
    assert db.query(ProgrammingAssessmentV2).filter(ProgrammingAssessmentV2.id == programming_assessment_id).first() is None
    assert db.query(ProgrammingSubmissionV2).filter(ProgrammingSubmissionV2.assessment_id == programming_assessment_id).count() == 0
    assert db.query(AIInterviewSessionV2).filter(AIInterviewSessionV2.id == interview_session_id).first() is None
    assert db.query(AIInterviewTurnV2).filter(AIInterviewTurnV2.session_id == interview_session_id).count() == 0

    # The candidate's own User account is untouched -- it isn't this job's data.
    assert db.query(User).filter(User.id == user_id).first() is not None


def test_delete_applicant_rejects_another_companys_application():
    db, company = _db()
    other_company = Company(
        id=2, company_name="Other", legal_name="Other Ltd", email="hr@other.test", mobile="456",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True, role="company",
    )
    db.add(other_company)
    other_job = Job(id=2, company_id=2, title="Other Role", company="Other", status="active")
    db.add(other_job)
    db.commit()

    user = User(full_name="X", username="x1", email="x@example.test", mobile=None, hashed_password="x", role="user")
    db.add(user)
    db.flush()
    application = JobApplication(job_id=other_job.id, user_id=user.id, status="shortlisted", recruiter_shortlisted=True,
                                  applied_date=datetime.utcnow(), updated_date=datetime.utcnow())
    db.add(application)
    db.commit()
    db.refresh(application)

    client = _applications_app(db, company)
    response = client.delete(f"/api/company-applications/{COMPANY_ID}/{application.id}")
    assert response.status_code == 404

    assert db.query(JobApplication).filter(JobApplication.id == application.id).first() is not None


def test_delete_invite_removes_an_unclaimed_invite_with_no_application():
    db, company = _db()
    now = datetime.utcnow()
    invite = CandidateInvite(
        token="tok-1", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Never Clicked", candidate_email="never@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now, expires_at=now + timedelta(days=7),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    client = _invites_app(db, company)
    response = client.delete(f"/api/company/{COMPANY_ID}/invites/{invite.id}")
    assert response.status_code == 200, response.text

    assert db.query(CandidateInvite).filter(CandidateInvite.id == invite.id).first() is None


def test_delete_invite_also_cascades_to_the_linked_application():
    db, company = _db()
    user, application, _, _ = _seed_application_with_child_rows(db)

    invite = CandidateInvite(
        token="tok-2", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Test Candidate", candidate_email="cand@example.test",
        resume_file_path="x.pdf", status="claimed", claimed_user_id=user.id,
        created_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    client = _invites_app(db, company)
    response = client.delete(f"/api/company/{COMPANY_ID}/invites/{invite.id}")
    assert response.status_code == 200, response.text

    assert db.query(CandidateInvite).filter(CandidateInvite.id == invite.id).first() is None
    assert db.query(JobApplication).filter(JobApplication.id == application.id).first() is None
    assert db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).count() == 0


def test_delete_invite_rejects_another_companys_invite():
    db, company = _db()
    other_company = Company(
        id=2, company_name="Other", legal_name="Other Ltd", email="hr@other.test", mobile="456",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True, role="company",
    )
    db.add(other_company)
    db.commit()

    now = datetime.utcnow()
    invite = CandidateInvite(
        token="tok-3", job_id=JOB_ID, company_id=2,
        candidate_name="Other Co Candidate", candidate_email="other@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now, expires_at=now + timedelta(days=7),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    client = _invites_app(db, company)
    response = client.delete(f"/api/company/{COMPANY_ID}/invites/{invite.id}")
    assert response.status_code == 404

    assert db.query(CandidateInvite).filter(CandidateInvite.id == invite.id).first() is not None

"""
Candidate tracking (HR dashboard): one consolidated list of every invited
candidate's current level, spanning states a plain applicants list can't
show at all -- invited-but-not-clicked, scheduled-for-later, and expired-
without-ever-entering -- through to the normal in-pipeline statuses.

In-memory SQLite, no jobpilot.db, no real Gemini/Groq/SMTP.
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
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.api.deps import get_current_company

COMPANY_ID = 1
JOB_ID = 1


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    import backend.app.main  # noqa: F401 - registers every model, so the list's score and integrity queries find their tables
    from backend.app.database.database import Base

    Base.metadata.create_all(engine)
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


def _app(db, company):
    api = FastAPI()
    api.include_router(company_applications_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company
    return TestClient(api)


def test_tracking_shows_an_uncontacted_invite_as_not_started():
    db, company = _db()
    now = datetime.utcnow()
    db.add(CandidateInvite(
        token="tok-1", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Uncontacted Candidate", candidate_email="a@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now, expires_at=now + timedelta(days=7),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    assert response.status_code == 200, response.text
    candidates = response.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["level"] == "Invited — Not Started"
    assert candidates[0]["level_category"] == "invited"
    assert candidates[0]["application_id"] is None


def test_tracking_shows_a_scheduled_invite():
    db, company = _db()
    now = datetime.utcnow()
    scheduled = now + timedelta(days=2)
    db.add(CandidateInvite(
        token="tok-2", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Scheduled Candidate", candidate_email="b@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now, expires_at=now + timedelta(days=7), scheduled_at=scheduled,
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert candidates[0]["level"].startswith("Scheduled for")
    assert candidates[0]["level_category"] == "scheduled"


def test_tracking_shows_an_expired_invite_that_was_never_claimed():
    db, company = _db()
    now = datetime.utcnow()
    db.add(CandidateInvite(
        token="tok-3", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Ghost Candidate", candidate_email="c@example.test",
        resume_file_path="x.pdf", status="expired",
        created_at=now - timedelta(days=10), expires_at=now - timedelta(days=3),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert candidates[0]["level"] == "Expired — Never Started"
    assert candidates[0]["level_category"] == "expired"


def test_tracking_shows_shortlisted_not_started_for_a_freshly_claimed_invite():
    db, company = _db()
    now = datetime.utcnow()

    user = User(
        full_name="Claimed Candidate", username="claimed1", email="d@example.test",
        mobile=None, hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    application = JobApplication(
        job_id=JOB_ID, user_id=user.id, status="shortlisted",
        recruiter_shortlisted=True, applied_date=now, updated_date=now,
    )
    db.add(application)
    db.flush()

    db.add(CandidateInvite(
        token="tok-4", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Claimed Candidate", candidate_email="d@example.test",
        resume_file_path="x.pdf", status="claimed", claimed_user_id=user.id,
        created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert candidates[0]["level"] == "Shortlisted — Not Started"
    assert candidates[0]["level_category"] == "not_started"
    assert candidates[0]["application_id"] == application.id


def test_tracking_shows_self_intro_in_progress():
    db, company = _db()
    now = datetime.utcnow()

    user = User(
        full_name="Intro Candidate", username="intro1", email="e@example.test",
        mobile=None, hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    application = JobApplication(
        job_id=JOB_ID, user_id=user.id, status="shortlisted",
        recruiter_shortlisted=True, applied_date=now, updated_date=now,
    )
    db.add(application)
    db.flush()

    db.add(AssessmentSession(
        application_id=application.id, current_stage="self_intro", self_intro_status="in_progress",
    ))

    db.add(CandidateInvite(
        token="tok-5", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Intro Candidate", candidate_email="e@example.test",
        resume_file_path="x.pdf", status="claimed", claimed_user_id=user.id,
        created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert candidates[0]["level"] == "Self-Introduction — In Progress"
    assert candidates[0]["level_category"] == "in_progress"


def test_tracking_shows_aptitude_passed():
    db, company = _db()
    now = datetime.utcnow()

    user = User(
        full_name="Aptitude Candidate", username="apt1", email="f@example.test",
        mobile=None, hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    application = JobApplication(
        job_id=JOB_ID, user_id=user.id, status="aptitude_passed",
        recruiter_shortlisted=True, applied_date=now, updated_date=now,
    )
    db.add(application)
    db.flush()

    db.add(CandidateInvite(
        token="tok-6", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Aptitude Candidate", candidate_email="f@example.test",
        resume_file_path="x.pdf", status="claimed", claimed_user_id=user.id,
        created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert candidates[0]["level"] == "Aptitude Test — Passed"
    assert candidates[0]["level_category"] == "success"


def test_tracking_lists_all_invites_newest_first():
    db, company = _db()
    now = datetime.utcnow()
    db.add(CandidateInvite(
        token="tok-7", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Older", candidate_email="g@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now - timedelta(days=2), expires_at=now + timedelta(days=5),
    ))
    db.add(CandidateInvite(
        token="tok-8", job_id=JOB_ID, company_id=COMPANY_ID,
        candidate_name="Newer", candidate_email="h@example.test",
        resume_file_path="x.pdf", status="pending",
        created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
    ))
    db.commit()
    client = _app(db, company)

    response = client.get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking")
    candidates = response.json()["candidates"]
    assert [c["candidate_name"] for c in candidates] == ["Newer", "Older"]



# ---------------------------------------------------------------- the candidate's photo in the list

def _claimed_candidate(db, name, email, token):
    now = datetime.utcnow()
    user = User(full_name=name, username=email.split("@")[0], email=email, mobile=None, hashed_password="x", role="user")
    db.add(user)
    db.flush()
    application = JobApplication(job_id=JOB_ID, user_id=user.id, status="shortlisted", recruiter_shortlisted=True, applied_date=now, updated_date=now)
    db.add(application)
    db.flush()
    db.add(CandidateInvite(
        token=token, job_id=JOB_ID, company_id=COMPANY_ID, candidate_name=name, candidate_email=email, resume_file_path="x.pdf",
        status="claimed", claimed_user_id=user.id, created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=6),
    ))
    db.flush()
    return application


def _snapshot(db, application, stage, reason, minutes):
    row = AssessmentSnapshot(
        application_id=application.id, assessment_type=stage, reason=reason, file_path=f"{application.id}/{stage}/{minutes}.jpg", file_size=1000,
        created_at=datetime(2026, 10, 3, 10, 0) + timedelta(minutes=minutes),
    )
    db.add(row)
    db.flush()
    return row


def test_tracking_gives_each_started_candidate_their_photo_and_others_none():
    db, company = _db()

    started = _claimed_candidate(db, "Asha Rao", "asha@example.test", "tok-p1")
    reference = _snapshot(db, started, "self_intro", "identity_reference", 0)
    _snapshot(db, started, "self_intro", "periodic", 2)                          # not an identity photo
    _snapshot(db, started, "aptitude", "identity_reference", 5)                  # a later stage's start photo: not the reference
    _snapshot(db, started, "aptitude", "face_missing", 6)                        # a camera flag: never the profile photo

    _claimed_candidate(db, "Ben Hall", "ben@example.test", "tok-p2")             # claimed the invite, camera never came on

    now = datetime.utcnow()
    db.add(CandidateInvite(                                                      # invited, has not opened the link
        token="tok-p3", job_id=JOB_ID, company_id=COMPANY_ID, candidate_name="Cara Dee", candidate_email="cara@example.test",
        resume_file_path="x.pdf", status="pending", created_at=now, expires_at=now + timedelta(days=7),
    ))
    db.commit()

    candidates = {c["candidate_name"]: c for c in _app(db, company).get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking").json()["candidates"]}

    assert candidates["Asha Rao"]["photo_url"] == f"/api/assessment/snapshots/file/{reference.id}"
    assert candidates["Ben Hall"]["photo_url"] is None
    assert candidates["Cara Dee"]["photo_url"] is None and candidates["Cara Dee"]["application_id"] is None


def test_the_photo_is_the_self_introduction_one_even_if_a_later_stage_photo_was_stored_first():
    from backend.app.api.routes.company_applications import _reference_photo_urls

    db, _company = _db()
    application = _claimed_candidate(db, "Dev Rao", "dev@example.test", "tok-p4")
    _snapshot(db, application, "aptitude", "identity_reference", 0)              # stored earlier (e.g. the intro photo failed to upload)
    intro = _snapshot(db, application, "self_intro", "identity_reference", 3)
    db.commit()

    assert _reference_photo_urls(db, [application.id]) == {application.id: f"/api/assessment/snapshots/file/{intro.id}"}
    assert _reference_photo_urls(db, []) == {}

    only_later = _claimed_candidate(db, "Eli Fox", "eli@example.test", "tok-p5")
    first_later = _snapshot(db, only_later, "aptitude", "identity_reference", 4)
    _snapshot(db, only_later, "ai_interview", "identity_reference", 9)
    db.commit()
    assert _reference_photo_urls(db, [only_later.id])[only_later.id].endswith(str(first_later.id))     # no intro photo: the earliest start photo


def test_the_tracking_page_shows_a_round_photo_or_initials_next_to_the_name():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "frontend", "js", "company-candidate-tracking.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(root, "frontend", "company-candidate-tracking.html"), encoding="utf-8") as f:
        html = f.read()

    assert "function avatarHtml(candidate)" in js and "candidate.photo_url" in js and "${avatarHtml(c)}" in js
    assert "onerror=" in js and "cand-avatar-initials" in js                      # a photo that fails to load falls back to initials
    assert ".cand-avatar" in html and "border-radius: 50%" in html


def test_tracking_gives_each_started_candidate_a_score_and_an_integrity_digest_and_the_uninvited_none():
    from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent

    db, company = _db()
    now = datetime.utcnow()

    user = User(full_name="Scored Candidate", username="scored1", email="s@example.test", mobile=None, hashed_password="x", role="user")
    db.add(user)
    db.flush()

    application = JobApplication(job_id=JOB_ID, user_id=user.id, status="aptitude_failed", recruiter_shortlisted=True, applied_date=now, updated_date=now)
    db.add(application)
    db.flush()

    db.add(AssessmentSession(application_id=application.id, current_stage="aptitude", self_intro_status="completed"))
    db.add(AssessmentIntegrityEvent(application_id=application.id, assessment_type="aptitude", event_type="mobile_phone_detected", severity="violation",
                                    counted_as_violation=True, occurred_at=now))
    db.add(CandidateInvite(token="tok-7", job_id=JOB_ID, company_id=COMPANY_ID, candidate_name="Scored Candidate", candidate_email="s@example.test",
                           resume_file_path="x.pdf", status="claimed", claimed_user_id=user.id, created_at=now, expires_at=now + timedelta(days=6)))
    db.add(CandidateInvite(token="tok-8", job_id=JOB_ID, company_id=COMPANY_ID, candidate_name="Never Opened", candidate_email="n@example.test",
                           resume_file_path="x.pdf", status="pending", created_at=now - timedelta(hours=2), expires_at=now + timedelta(days=6)))
    db.commit()

    rows = {c["candidate_name"]: c for c in _app(db, company).get(f"/api/company-applications/{COMPANY_ID}/candidate-tracking").json()["candidates"]}

    card = rows["Scored Candidate"]["scorecard"]
    assert card["overall_percentage"] is None and card["integrity"]["risk"] == "high" and card["integrity"]["what_happened"][0]["label"] == "Mobile phone in view"
    assert rows["Never Opened"]["scorecard"] is None

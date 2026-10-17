"""
When a candidate opens the invite link, their account is built from the resume HR uploaded: that resume becomes the ACTIVE resume (so the
role test, coding test, practical task and interview all read it), its analysis and text are saved, the account name is the name HR
confirmed, and the profile is filled. Before this, the invite's resume was never used by any assessment stage.

In-memory SQLite, no network.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.candidate_invites as candidate_invites_routes
import backend.app.services.resume.invite_profile as ip
from backend.app.database.database import get_db
from backend.app.models.auth_session import AuthSession
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User
from backend.app.services.assessment.interview_context_builder_v2 import _name_for_greeting
from backend.app.services.resume.active_resume import get_active_resume
from backend.app.services.resume.resolve import resolve_application_resume_path

ANALYSIS = {
    "summary": "Backend engineer who builds Python APIs.", "skills": ["Python", "FastAPI", "SQL"], "job_roles": ["Backend Engineer"],
    "education": ["B.E Computer Science, Anna University"], "experience": ["Tech Solutions - Developer - 2 years"], "experience_level": "Mid Level",
}

RESUME_TEXT = """JERIN R
Chennai | 9876543210 | jerin@example.com
https://github.com/jerin-r   linkedin.com/in/jerin-r
SUMMARY
Backend engineer.
PROJECTS
Hospital Management System - Flask and SQLite
Interview mock bot - Python
EDUCATION
B.E Computer Science
"""


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, Job, JobApplication, CandidateInvite, AuthSession, Resume, ResumeAnalysis, CandidateProfile):
        model.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _invite(db, name="Jerin R", email="jerin@example.com", path="data/resumes/invite_1_a.pdf", analysis=None, job_title="Backend Developer"):
    job = Job(title=job_title, company="Acme", company_id=1, source="invite_only", status="active", description="d", requirements="Python")
    db.add(job)
    db.flush()
    invite = CandidateInvite(
        token="tok-" + "".join(ch for ch in path if ch.isalnum()), job_id=job.id, company_id=1, candidate_name=name, candidate_email=email, resume_file_path=path,
        resume_analysis_json=json.dumps(analysis or ANALYSIS), status="pending", expires_at=datetime.utcnow() + timedelta(days=3),
    )
    db.add(invite)
    db.commit()
    return invite


def _user(db, name="Someone Else", email="jerin@example.com"):
    user = User(full_name=name, username=email.split("@")[0], email=email, hashed_password="x", role="user")
    db.add(user)
    db.commit()
    return user


def _read(monkeypatch, text=RESUME_TEXT):
    monkeypatch.setattr(ip, "_read_text", lambda path, ext: text)


def test_the_invite_resume_becomes_the_active_resume_with_its_analysis_and_text(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user, invite = _user(db), None
    invite = _invite(db)

    result = ip.hydrate_candidate_from_invite(db, user, invite)
    db.commit()

    active = get_active_resume(db, user.id)
    assert active is not None and active.file_path == invite.resume_file_path and result["resume_id"] == active.id

    analysis = db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == active.id).one()
    assert json.loads(analysis.skills) == ["Python", "FastAPI", "SQL"] and analysis.experience_level == "Mid Level"
    assert analysis.raw_text.startswith("JERIN R")                                                # the text is what the greeting name is read from


def test_the_account_name_is_the_name_hr_confirmed_and_the_phone_is_filled(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db, name="JobPilot AI Owner")
    ip.hydrate_candidate_from_invite(db, user, _invite(db, name="Jerin R"))
    db.commit()
    db.refresh(user)
    assert user.full_name == "Jerin R" and user.mobile == "9876543210"


def test_the_profile_is_created_from_the_resume(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db)
    ip.hydrate_candidate_from_invite(db, user, _invite(db))
    db.commit()

    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).one()
    assert profile.full_name == "Jerin R"
    assert profile.about == "Backend engineer who builds Python APIs."
    assert profile.headline == "Mid Level Backend Engineer"
    assert profile.skills == "Python, FastAPI, SQL"
    assert "Anna University" in profile.education and "Tech Solutions" in profile.experience
    assert profile.experience_level == "Mid Level" and profile.preferred_roles == "Backend Engineer"
    assert profile.github_url == "https://github.com/jerin-r" and profile.linkedin_url == "https://linkedin.com/in/jerin-r"
    assert "Hospital Management System" in profile.projects and "EDUCATION" not in profile.projects


def test_opening_the_same_link_twice_creates_nothing_twice(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user, invite = _user(db), None
    invite = _invite(db)
    for _ in range(3):
        ip.hydrate_candidate_from_invite(db, user, invite)
        db.commit()
    assert db.query(Resume).count() == 1 and db.query(ResumeAnalysis).count() == 1 and db.query(CandidateProfile).count() == 1


def test_an_older_resume_on_the_account_is_replaced_as_active_by_the_invite_resume(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db)
    old = Resume(user_id=user.id, file_name="old.pdf", file_path="data/resumes/old.pdf", file_type="pdf", uploaded_at="2025-01-01 00:00:00")
    db.add(old)
    db.flush()
    db.add(ResumeAnalysis(resume_id=old.id, user_id=user.id, summary="old", skills="[]", raw_text="OLD PERSON"))
    db.commit()
    assert get_active_resume(db, user.id).id == old.id

    ip.hydrate_candidate_from_invite(db, user, _invite(db))
    db.commit()
    assert get_active_resume(db, user.id).file_path == "data/resumes/invite_1_a.pdf"


def test_two_invites_for_one_candidate_each_use_their_own_resume_when_opened(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db)
    first = _invite(db, path="data/resumes/first.pdf", job_title="Backend Developer")
    second = _invite(db, path="data/resumes/second.pdf", job_title="Data Analyst", analysis={**ANALYSIS, "skills": ["Excel", "Power BI"]})

    ip.hydrate_candidate_from_invite(db, user, first)
    db.commit()
    ip.hydrate_candidate_from_invite(db, user, second)
    db.commit()
    active = get_active_resume(db, user.id)
    assert active.file_path == "data/resumes/second.pdf"
    assert json.loads(db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == active.id).one().skills) == ["Excel", "Power BI"]

    ip.hydrate_candidate_from_invite(db, user, first)                   # opening the FIRST link again brings its resume back
    db.commit()
    assert get_active_resume(db, user.id).file_path == "data/resumes/first.pdf"


def test_a_profile_the_candidate_edited_is_kept_and_only_the_empty_fields_and_skills_are_added(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db)
    db.add(CandidateProfile(user_id=user.id, headline="My own headline", skills="Docker, Python"))
    db.commit()

    ip.hydrate_candidate_from_invite(db, user, _invite(db))
    db.commit()

    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).one()
    assert profile.headline == "My own headline"                                     # never overwritten
    assert profile.skills == "Docker, Python, FastAPI, SQL"                           # merged, no duplicate Python
    assert profile.about == "Backend engineer who builds Python APIs."                # an empty field was filled


def test_the_interview_greeting_name_is_read_from_the_invite_resume(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db, name="JobPilot AI Owner")
    invite = _invite(db, name="Jerin R")
    ip.hydrate_candidate_from_invite(db, user, invite)
    db.commit()

    job = db.query(Job).filter(Job.id == invite.job_id).one()
    resume = get_active_resume(db, user.id)
    analysis = db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).one()
    assert _name_for_greeting(job, user, analysis) == "Jerin R"                       # not the old account name, not a fixed name


def test_hr_sees_the_same_resume_the_assessments_used(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db)
    invite = _invite(db)
    invite.claimed_user_id = user.id
    ip.hydrate_candidate_from_invite(db, user, invite)
    application = JobApplication(job_id=invite.job_id, user_id=user.id, status="shortlisted")
    db.add(application)
    db.commit()

    assert resolve_application_resume_path(db, application) == get_active_resume(db, user.id).file_path


def test_a_missing_resume_text_or_file_never_stops_the_candidate(monkeypatch):
    def boom(path, ext):
        raise RuntimeError("file is gone")
    monkeypatch.setattr("backend.app.services.resume.extractor.extract_resume_text", boom)
    db = _db()
    user = _user(db)
    result = ip.hydrate_candidate_from_invite(db, user, _invite(db))
    db.commit()
    assert result["resume_id"] is not None                                            # the stored analysis still builds the resume and profile
    assert db.query(CandidateProfile).count() == 1


def test_an_invite_without_a_resume_changes_nothing():
    db = _db()
    user = _user(db)
    invite = _invite(db)
    invite.resume_file_path = ""
    assert ip.hydrate_candidate_from_invite(db, user, invite)["resume_id"] is None
    assert db.query(Resume).count() == 0 and db.query(CandidateProfile).count() == 0


def test_a_failure_inside_never_undoes_the_account_the_route_just_created(monkeypatch):
    _read(monkeypatch)
    db = _db()
    user = _user(db, name="Keep Me")
    monkeypatch.setattr(ip, "_projects", lambda text: (_ for _ in ()).throw(RuntimeError("boom")))
    result = ip.hydrate_candidate_from_invite(db, user, _invite(db))
    db.commit()
    assert result["resume_id"] is None and db.query(User).filter(User.full_name == "Keep Me").count() == 1   # the savepoint rolled back only the hydration


def test_through_the_real_route_a_brand_new_candidate_gets_resume_profile_and_name(monkeypatch):
    _read(monkeypatch)
    db = _db()
    invite = _invite(db, name="Jerin R", email="new.person@example.com")

    api = FastAPI()
    api.include_router(candidate_invites_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    client = TestClient(api)

    response = client.post(f"/api/invites/{invite.token}/enter")
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Jerin R"

    user = db.query(User).filter(User.email == "new.person@example.com").one()
    assert get_active_resume(db, user.id).file_path == invite.resume_file_path
    assert db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).one().skills == "Python, FastAPI, SQL"

"""
HR-sourced candidate invite (Phase 1/2): HR types a job title + requirement and uploads a resume for a
candidate -> AI analysis (faked here, no real network) also weighs the resume against that job -> a
CandidateInvite row + token is created (backed by an ad-hoc, invite-only Job row) -> the emailed magic
link auto-creates the candidate's account and job application on first visit, and is idempotent (same
account/application) on every later visit.

In-memory SQLite (only the tables this flow touches), fake resume analyzer, fake email send, no jobpilot.db,
no real Gemini/Groq/SMTP.
"""

import io
import os
import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.company_invites as company_invites_routes
import backend.app.api.routes.candidate_invites as candidate_invites_routes
from backend.app.database.database import Base, get_db
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.auth_session import AuthSession
from backend.app.api.deps import get_current_company


COMPANY_ID = 1

FAKE_ANALYSIS = {
    "summary": "A backend engineer.", "skills": ["Python", "SQL"], "job_roles": ["Backend Engineer"],
    "education": ["B.Sc CS"], "experience": ["2 years"], "experience_level": "Mid Level",
}


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, Company, Job, JobApplication, CandidateInvite, AuthSession):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()

    company = Company(
        id=COMPANY_ID, company_name="Acme", legal_name="Acme Ltd", email="hr@acme.test", mobile="1234567890",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True, role="company",
    )
    db.add(company)

    db.commit()
    return db, company


def _app(db, company):

    api = FastAPI()
    api.include_router(company_invites_routes.router)
    api.include_router(candidate_invites_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company

    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})


def _fake_resume_file():
    return {"file": ("resume.pdf", io.BytesIO(b"%PDF-1.4 fake resume bytes"), "application/pdf")}


def _patch_analysis(monkeypatch, sent_emails):

    monkeypatch.setattr(company_invites_routes, "extract_resume_text", lambda path, ext: "some resume text")

    def fake_analyze(text, job_context=None):
        result = dict(FAKE_ANALYSIS)
        if job_context:
            result["job_match_score"] = 80
            result["job_match_notes"] = "Good fit."
        return result

    monkeypatch.setattr(company_invites_routes, "analyze_resume", fake_analyze)

    def fake_send(to_email, candidate_name, job_title, company_name, invite_url, schedule_url=None, deadline=None):
        sent_emails.append({"to": to_email, "url": invite_url, "schedule_url": schedule_url, "deadline": deadline})
        return True

    monkeypatch.setattr(company_invites_routes, "send_candidate_assessment_invite_email", fake_send)


def _upload(client, name="Jane Candidate", email="jane@example.test", job_title="Backend Engineer",
            job_requirement="2+ years Python, SQL"):
    return client.post(
        f"/api/company/{COMPANY_ID}/invite-candidate",
        data={
            "candidate_name": name,
            "candidate_email": email,
            "job_title": job_title,
            "job_requirement": job_requirement,
        },
        files=_fake_resume_file(),
    )


def test_upload_creates_a_pending_invite_and_sends_the_link(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    sent = []
    _patch_analysis(monkeypatch, sent)
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    response = _upload(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate_email"] == "jane@example.test"
    assert body["status"] == "pending"
    assert body["email_sent"] is True

    invite = db.query(CandidateInvite).filter(CandidateInvite.id == body["invite_id"]).first()
    assert invite.candidate_name == "Jane Candidate"
    assert invite.company_id == COMPANY_ID
    assert invite.status == "pending" and invite.claimed_user_id is None
    assert (invite.expires_at - invite.created_at) == timedelta(days=company_invites_routes.INVITE_EXPIRY_DAYS)

    # An ad-hoc, invite-only Job row was created to back this invite.
    job = db.query(Job).filter(Job.id == invite.job_id).first()
    assert job.title == "Backend Engineer"
    assert job.requirements == "2+ years Python, SQL"
    assert job.company_id == COMPANY_ID
    assert job.source == "invite_only"

    # The resume analysis included a job match (job_context was passed through).
    import json
    analysis = json.loads(invite.resume_analysis_json)
    assert analysis["job_match_score"] == 80

    assert len(sent) == 1 and sent[0]["to"] == "jane@example.test"
    assert invite.token in sent[0]["url"]


def test_upload_rejects_blank_job_title_or_requirement(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    response = client.post(
        f"/api/company/{COMPANY_ID}/invite-candidate",
        data={"candidate_name": "X", "candidate_email": "x@example.test", "job_title": "  ", "job_requirement": "Python"},
        files=_fake_resume_file(),
    )
    assert response.status_code == 400


def test_entering_via_the_link_creates_exactly_one_user_and_application(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    invite_id = _upload(client).json()["invite_id"]
    invite_row = db.query(CandidateInvite).filter(CandidateInvite.id == invite_id).first()
    token = invite_row.token
    job_id = invite_row.job_id

    preview = client.get(f"/api/invites/{token}")
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["candidate_name"] == "Jane Candidate"
    assert preview_body["job_title"] == "Backend Engineer"
    assert preview_body["company_name"] == "Acme"
    assert preview_body["scheduled_at"] is None
    assert preview_body["expires_at"]

    entered = client.post(f"/api/invites/{token}/enter")
    assert entered.status_code == 200
    application_id = int(entered.json()["redirect_url"].split("application_id=")[1])

    assert db.query(User).count() == 1
    assert db.query(JobApplication).count() == 1

    user = db.query(User).first()
    assert user.email == "jane@example.test" and user.full_name == "Jane Candidate"

    application = db.query(JobApplication).filter(JobApplication.id == application_id).first()
    assert application.job_id == job_id and application.user_id == user.id
    # HR already vetted this candidate via the resume before inviting them, so the
    # normal "must be shortlisted first" gate is bypassed at creation time.
    assert application.status == "shortlisted" and application.recruiter_shortlisted is True

    invite = db.query(CandidateInvite).filter(CandidateInvite.id == invite_id).first()
    assert invite.status == "claimed" and invite.claimed_user_id == user.id


def test_visiting_the_link_again_is_idempotent(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    token = db.query(CandidateInvite).filter(
        CandidateInvite.id == _upload(client).json()["invite_id"]
    ).first().token

    first = client.post(f"/api/invites/{token}/enter").json()
    second = client.post(f"/api/invites/{token}/enter").json()

    assert first == second
    assert db.query(User).count() == 1
    assert db.query(JobApplication).count() == 1


def test_expired_invite_is_rejected(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    invite_id = _upload(client).json()["invite_id"]
    invite = db.query(CandidateInvite).filter(CandidateInvite.id == invite_id).first()
    invite.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()

    response = client.post(f"/api/invites/{invite.token}/enter")
    assert response.status_code == 410
    assert db.query(User).count() == 0


def test_unknown_token_is_rejected():
    db, company = _db()
    client = _app(db, company)

    assert client.get("/api/invites/not-a-real-token").status_code == 404
    assert client.post("/api/invites/not-a-real-token/enter").status_code == 404


def test_extract_resume_contact_guesses_name_and_email(monkeypatch, tmp_path):
    db, company = _db()
    client = _app(db, company)
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(
        company_invites_routes,
        "extract_resume_text",
        lambda path, ext: "Arjun Mehta\nWeb Developer | arjun.mehta@example.com | Bengaluru\n",
    )

    response = client.post(
        f"/api/company/{COMPANY_ID}/extract-resume-contact",
        files=_fake_resume_file(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate_name"] == "Arjun Mehta"
    assert body["candidate_email"] == "arjun.mehta@example.com"

    # The probe file must not be left behind in the upload folder.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- the AI assessment plan on the invite form

def _plan_preview(client, title, requirement, role_shape=""):
    # the form always sends the detected role type (the dropdown is filled by detect-role-type before the plan is requested)
    return client.post(f"/api/company/{COMPANY_ID}/assessment-plan",
                       data={"job_title": title, "job_requirement": requirement, "role_shape_override": role_shape})


def test_the_form_previews_the_assessments_the_ai_recommends_for_the_role():
    db, company = _db()
    client = _app(db, company)

    body = _plan_preview(client, "Copywriter", "Write marketing copy, blog posts and ads. Excellent grammar.").json()
    aptitude, stage3 = body["stages"]
    assert aptitude["title"] == "Language & Grammar Assessment" and aptitude["custom"] is True and len(aptitude["areas"]) == 3
    assert stage3["kind"] == "situational_judgment" and body["plan"]["version"] == 1

    technical = _plan_preview(client, "Backend Engineer", "2+ years Python, SQL, REST APIs", "technical").json()
    assert [s["kind"] for s in technical["stages"]] == ["aptitude", "programming"]
    assert technical["stages"][0]["custom"] is False

    assert _plan_preview(client, "", "").status_code == 400
    assert db.query(Job).count() == 0                                                  # previewing creates nothing


def test_the_plan_the_hr_user_saw_is_stored_on_the_job_and_hr_can_keep_the_standard_test(monkeypatch, tmp_path):
    import json
    from backend.app.services.assessment.assessment_planner import stage_title

    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    shown = _plan_preview(client, "Copywriter", "Write marketing copy, blog posts and ads. Excellent grammar.").json()["plan"]

    response = client.post(
        f"/api/company/{COMPANY_ID}/invite-candidate",
        data={"candidate_name": "Jane", "candidate_email": "jane@example.test", "job_title": "Copywriter",
              "job_requirement": "Write marketing copy, blog posts and ads. Excellent grammar.", "assessment_plan": json.dumps(shown)},
        files=_fake_resume_file(),
    )
    assert response.status_code == 200, response.text

    job = db.query(Job).filter(Job.title == "Copywriter").first()
    stored = json.loads(job.assessment_plan_json)
    assert stored["stages"][0]["kind"] == "skills" and stage_title(job, "aptitude", "Aptitude Test") == "Language & Grammar Assessment"

    # a second invite where HR chose the general aptitude test instead
    client.post(
        f"/api/company/{COMPANY_ID}/invite-candidate",
        data={"candidate_name": "Sam", "candidate_email": "sam@example.test", "job_title": "Copywriter 2",
              "job_requirement": "Write marketing copy, blog posts and ads. Excellent grammar.", "assessment_plan": json.dumps(shown),
              "aptitude_choice": "standard"},
        files=_fake_resume_file(),
    )
    other = db.query(Job).filter(Job.title == "Copywriter 2").first()
    assert json.loads(other.assessment_plan_json)["stages"][0]["kind"] == "aptitude" and json.loads(other.assessment_plan_json)["source"] == "hr"


def test_a_missing_or_tampered_plan_never_blocks_the_invite_and_is_planned_fresh(monkeypatch, tmp_path):
    import json

    db, company = _db()
    client = _app(db, company)
    _patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    for plan in ("", "{not json", json.dumps({"version": 1, "stages": [{"slot": "aptitude", "kind": "drop table"}]})):
        response = client.post(
            f"/api/company/{COMPANY_ID}/invite-candidate",
            data={"candidate_name": "Jane", "candidate_email": f"j{abs(hash(plan))}@example.test", "job_title": "Accountant",
                  "job_requirement": "Maintain ledgers and prepare GST returns", "assessment_plan": plan},
            files=_fake_resume_file(),
        )
        assert response.status_code == 200, response.text

    for job in db.query(Job).filter(Job.title == "Accountant").all():
        assert json.loads(job.assessment_plan_json)["stages"][0]["title"].startswith("Numerical & Finance")

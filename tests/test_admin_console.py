"""Admin console routes: company list/suspend/warn/delete, platform-wide
invite visibility, and the live system-status check. Isolated in-memory DB,
auth bypassed via dependency override (the real get_current_admin() is
already covered by deps.py's own tests)."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
from backend.app.api import deps
from backend.app.api.routes import admin
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.admin_warning import AdminWarning
from backend.app.models.job_application import JobApplication  # noqa: F401 (registers the table create_all needs)


def _client():

    import backend.app.main  # noqa: F401 (registers every model)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    app = FastAPI()
    app.include_router(admin.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    admin_user = type("U", (), {"id": 99, "role": "admin"})()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_admin] = lambda: admin_user
    admin.require_csrf = lambda request: None

    return TestClient(app), Session


def _seed(Session):

    db = Session()

    user = User(id=99, full_name="Admin", username="admin", email="admin@jobpilot.test", hashed_password="x", role="admin")
    db.add(user)

    company = Company(
        id=1, company_name="TechNova Solutions", legal_name="TechNova Solutions Pvt Ltd", hr_name="Asha Mehta",
        email="hr@technova.test", mobile="9999999999", hashed_password="x", account_active=True,
        created_at=datetime(2026, 7, 29),
    )
    db.add(company)

    job = Job(id=1, title="Backend Developer", company="TechNova Solutions", company_id=1)
    db.add(job)
    db.flush()

    invite = CandidateInvite(
        token="test-token-123", job_id=1, company_id=1, candidate_name="Priya Sharma", candidate_email="priya@example.com",
        resume_file_path="x", status="pending", created_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(invite)

    db.commit()
    db.close()


def test_list_companies_shows_real_counts():

    client, Session = _client()
    _seed(Session)

    response = client.get("/api/admin/companies")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    company = data["companies"][0]
    assert company["company_name"] == "TechNova Solutions"
    assert company["hr_name"] == "Asha Mehta"
    assert company["jobs_posted"] == 1
    assert company["candidates_invited"] == 1
    assert company["warning_count"] == 0
    assert company["account_active"] is True


def test_suspend_and_reactivate_is_reversible():

    client, Session = _client()
    _seed(Session)

    response = client.patch("/api/admin/companies/1/status", json={"account_active": False})
    assert response.status_code == 200
    assert response.json()["account_active"] is False

    db = Session()
    assert db.query(Company).filter(Company.id == 1).first().account_active is False
    db.close()

    response = client.patch("/api/admin/companies/1/status", json={"account_active": True})
    assert response.status_code == 200
    assert response.json()["account_active"] is True


def test_warn_company_records_a_durable_message_never_auto_emailed():

    client, Session = _client()
    _seed(Session)

    response = client.post("/api/admin/companies/1/warn", json={"message": "Please stop posting duplicate roles."})
    assert response.status_code == 200

    response = client.get("/api/admin/companies/1/warnings")
    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["message"] == "Please stop posting duplicate roles."

    # reflected in the companies list's warning_count too
    data = client.get("/api/admin/companies").json()
    assert data["companies"][0]["warning_count"] == 1


def test_warn_company_rejects_empty_message():

    client, Session = _client()
    _seed(Session)

    response = client.post("/api/admin/companies/1/warn", json={"message": "   "})
    assert response.status_code == 400


def test_delete_company_removes_jobs_invites_and_the_company_itself():

    client, Session = _client()
    _seed(Session)

    response = client.delete("/api/admin/companies/1")
    assert response.status_code == 200

    db = Session()
    assert db.query(Company).filter(Company.id == 1).first() is None
    assert db.query(Job).filter(Job.company_id == 1).count() == 0
    assert db.query(CandidateInvite).filter(CandidateInvite.company_id == 1).count() == 0
    db.close()

    # listing afterward is empty, not a crash
    assert client.get("/api/admin/companies").json()["total"] == 0


def test_delete_company_also_removes_sjt_and_tda_results_no_orphans():
    """The cascade predates SJT/TDA; before the fix their rows were left behind and a later
    application reusing the same id inherited a stale completed test."""

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest
    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    client, Session = _client()
    _seed(Session)

    db = Session()
    db.add(User(id=5, full_name="C", username="cand5", email="c5@example.com", hashed_password="x", role="user"))
    db.flush()
    application = JobApplication(id=7, job_id=1, user_id=5, status="sjt_pending")
    db.add(application)
    db.flush()
    db.add(SituationalJudgmentTest(application_id=7, questions="[]", total_questions=10, status="in_progress", started_at=datetime.utcnow()))
    db.add(TechnicalDomainAssessment(application_id=7, domain="sap", questions="[]", total_questions=10, status="in_progress", started_at=datetime.utcnow()))
    db.commit()
    db.close()

    assert client.delete("/api/admin/companies/1").status_code == 200

    db = Session()
    assert db.query(SituationalJudgmentTest).filter(SituationalJudgmentTest.application_id == 7).count() == 0
    assert db.query(TechnicalDomainAssessment).filter(TechnicalDomainAssessment.application_id == 7).count() == 0
    db.close()


def test_list_invites_is_platform_wide_not_per_company():

    client, Session = _client()
    _seed(Session)

    response = client.get("/api/admin/invites")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 1
    invite = data["invites"][0]
    assert invite["candidate_email"] == "priya@example.com"
    assert invite["job_title"] == "Backend Developer"
    assert invite["company_name"] == "TechNova Solutions"


def test_admin_can_change_own_password_with_correct_current_password():

    from backend.app.api.routes.users import hash_password, verify_password

    client, Session = _client()
    _seed(Session)

    db = Session()
    user = db.query(User).filter(User.id == 99).first()
    user.hashed_password = hash_password("OldPass123")
    db.commit()
    db.close()

    response = client.post("/api/admin/me/change-password", json={"current_password": "OldPass123", "new_password": "NewPass456"})
    assert response.status_code == 200

    db = Session()
    user = db.query(User).filter(User.id == 99).first()
    assert verify_password("NewPass456", user.hashed_password)
    assert not verify_password("OldPass123", user.hashed_password)
    db.close()


def test_admin_change_password_rejects_wrong_current_password():

    from backend.app.api.routes.users import hash_password

    client, Session = _client()
    _seed(Session)

    db = Session()
    user = db.query(User).filter(User.id == 99).first()
    user.hashed_password = hash_password("OldPass123")
    db.commit()
    db.close()

    response = client.post("/api/admin/me/change-password", json={"current_password": "WrongPass", "new_password": "NewPass456"})
    assert response.status_code == 400


def test_admin_change_password_rejects_short_new_password():

    client, Session = _client()
    _seed(Session)

    response = client.post("/api/admin/me/change-password", json={"current_password": "anything", "new_password": "short"})
    assert response.status_code == 400


def test_system_status_is_live_not_fabricated():

    client, Session = _client()
    _seed(Session)

    response = client.get("/api/admin/system-status")
    assert response.status_code == 200

    data = response.json()
    assert "checked_at" in data
    assert set(data["providers"].keys()) == {"groq", "gemini", "local_stt"}
    assert isinstance(data["providers"]["groq"]["configured"], bool)
    assert isinstance(data["sandbox"]["docker"]["reachable"], bool)
    assert len(data["sandbox"]["docker"]["languages"]) == 13

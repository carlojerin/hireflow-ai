"""
HR account lifecycle (Phase 2): registration no longer waits on a manual admin-verification step (the
admin panel was removed), so a freshly registered company can log in immediately. Also covers the new
Settings endpoints: changing email/username and changing password.

In-memory SQLite, no jobpilot.db, no real SMTP.
"""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.companies as companies_routes
from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.auth_session import AuthSession
from backend.app.api.deps import get_current_company


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (Company, AuthSession):
        model.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _app(db, current_company=None):

    api = FastAPI()
    api.include_router(companies_routes.router)
    api.dependency_overrides[get_db] = lambda: db

    if current_company is not None:
        api.dependency_overrides[get_current_company] = lambda: current_company

    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})


REGISTER_PAYLOAD = {
    "company_name": "Acme",
    "hr_name": "Priya Sharma",
    "email": "hr@acmecorp.com",
    "mobile": "9876543210",
    "password": "supersecret1",
    "confirm_password": "supersecret1",
}


def test_registration_auto_verifies_and_generates_a_username():
    db = _db()
    client = _app(db)

    response = client.post("/api/companies/register", json=REGISTER_PAYLOAD)
    assert response.status_code == 200, response.text

    company = response.json()["company"]
    assert company["verification_status"] == "verified"
    assert company["account_active"] is True
    assert company["admin_verified"] is True
    assert company["hr_name"] == "Priya Sharma"
    assert company["username"] == "hr"  # derived from the email local-part
    # legal_name auto-fills from company_name when not supplied
    assert company["legal_name"] == "Acme"


def test_a_registered_company_cannot_log_in_until_its_email_is_verified():
    db = _db()
    client = _app(db)

    client.post("/api/companies/register", json=REGISTER_PAYLOAD)

    login = client.post(
        "/api/companies/login",
        json={"identifier": "hr@acmecorp.com", "password": "supersecret1"},
    )
    assert login.status_code == 403 and login.json()["detail"] == "email_not_verified"       # the full flow is in test_company_email_verification.py


def test_change_password_requires_correct_current_password():
    db = _db()
    company = Company(
        company_name="Acme", hr_name="Priya", legal_name="Acme", email="hr@acmecorp.com", mobile="123",
        hashed_password=companies_routes.hash_password("oldpass123"), account_active=True,
        verification_status="verified", admin_verified=True, role="company",
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    client = _app(db, current_company=company)

    wrong = client.post(
        f"/api/companies/{company.id}/change-password",
        json={"current_password": "wrongpass", "new_password": "newpass123"},
    )
    assert wrong.status_code == 400

    right = client.post(
        f"/api/companies/{company.id}/change-password",
        json={"current_password": "oldpass123", "new_password": "newpass123"},
    )
    assert right.status_code == 200

    db.refresh(company)
    assert companies_routes.verify_password("newpass123", company.hashed_password)


def test_update_account_rejects_duplicate_email_and_username():
    db = _db()

    other = Company(
        company_name="Other", hr_name="X", legal_name="Other", email="taken@othercorp.com", mobile="111",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True,
        role="company", username="takenname",
    )
    db.add(other)

    company = Company(
        company_name="Acme", hr_name="Priya", legal_name="Acme", email="hr@acmecorp.com", mobile="123",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True,
        role="company", username="priya",
    )
    db.add(company)
    db.commit()
    db.refresh(company)

    client = _app(db, current_company=company)

    dup_email = client.put(f"/api/companies/{company.id}/account", json={"email": "taken@othercorp.com"})
    assert dup_email.status_code == 400

    dup_username = client.put(f"/api/companies/{company.id}/account", json={"username": "takenname"})
    assert dup_username.status_code == 400

    ok = client.put(f"/api/companies/{company.id}/account", json={"username": "priya-hr"})
    assert ok.status_code == 200
    assert ok.json()["company"]["username"] == "priya-hr"

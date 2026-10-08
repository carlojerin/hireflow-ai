"""
HR email verification: registration emails a 6-digit code; login refuses the account until it is entered; the code expires, is limited to a few
attempts and a new one can be requested at most once a minute. Existing accounts are grandfathered as verified by the migration.
"""

import os
import sys
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.companies as companies_routes
from backend.app.database.database import get_db
from backend.app.models.auth_session import AuthSession
from backend.app.models.company import Company

PAYLOAD = {
    "company_name": "Acme", "hr_name": "Priya Sharma", "email": "hr@acmecorp.com", "mobile": "9876543210",
    "password": "supersecret1", "confirm_password": "supersecret1",
}


def _setup(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (Company, AuthSession):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()

    api = FastAPI()
    api.include_router(companies_routes.router)
    api.dependency_overrides[get_db] = lambda: db

    sent = []
    monkeypatch.setattr(companies_routes, "_send_verification_email", lambda email, hr, company, code: sent.append((email, hr, company, code)))

    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"}), db, sent


def _login(client):
    return client.post("/api/companies/login", json={"identifier": PAYLOAD["email"], "password": PAYLOAD["password"]})


def _wrong(code):
    return "123456" if code != "123456" else "654321"


def test_registration_emails_a_six_digit_code_and_stores_only_its_hash(monkeypatch):
    client, db, sent = _setup(monkeypatch)

    response = client.post("/api/companies/register", json=PAYLOAD)
    assert response.status_code == 200 and response.json()["verification_required"] is True and response.json()["email"] == PAYLOAD["email"]

    (email, hr, company_name, code), = sent
    assert email == PAYLOAD["email"] and hr == "Priya Sharma" and company_name == "Acme" and len(code) == 6 and code.isdigit()

    company = db.query(Company).first()
    assert company.email_verified is False and company.email_verification_hash and code not in company.email_verification_hash


def test_login_is_refused_until_the_email_is_verified_then_works(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)

    refused = _login(client)
    assert refused.status_code == 403 and refused.json()["detail"] == "email_not_verified"

    wrong = client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": _wrong(sent[0][3])})
    assert wrong.status_code == 400 and _login(client).status_code == 403

    right = client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": sent[0][3]})
    assert right.status_code == 200 and right.json()["verified"] is True

    assert _login(client).status_code == 200                                                    # the same password now works
    assert db.query(Company).first().email_verification_hash is None                            # the used code is gone


def test_a_wrong_password_never_reveals_verification_status(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)

    bad = client.post("/api/companies/login", json={"identifier": PAYLOAD["email"], "password": "not-the-password"})
    assert bad.status_code == 401


def test_the_code_expires_after_fifteen_minutes(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)

    company = db.query(Company).first()
    company.email_verification_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    assert client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": sent[0][3]}).status_code == 400
    assert db.query(Company).first().email_verified is False


def test_five_wrong_codes_lock_the_attempts_until_a_new_code_is_requested(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)
    real = sent[0][3]

    for _ in range(5):
        assert client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": _wrong(real)}).status_code == 400

    locked = client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": real})
    assert locked.status_code == 429                                                            # even the right code is refused now

    company = db.query(Company).first()
    company.email_verification_sent_at = datetime.utcnow() - timedelta(seconds=61)
    db.commit()

    assert client.post("/api/companies/resend-verification", json={"email": PAYLOAD["email"]}).status_code == 200
    assert len(sent) == 2 and db.query(Company).first().email_verification_attempts == 0
    assert client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": sent[1][3]}).status_code == 200


def test_a_new_code_can_only_be_requested_once_a_minute_and_the_reply_never_reveals_an_account(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)
    assert len(sent) == 1

    first = client.post("/api/companies/resend-verification", json={"email": PAYLOAD["email"]})
    assert first.status_code == 200 and len(sent) == 1                                          # still inside the first minute: nothing new is sent

    unknown = client.post("/api/companies/resend-verification", json={"email": "nobody@nowhere.com"})
    assert unknown.status_code == 200 and unknown.json() == first.json() and len(sent) == 1     # identical reply for an address with no account


def test_a_verified_account_stays_verified_and_an_unknown_address_gets_the_generic_error(monkeypatch):
    client, db, sent = _setup(monkeypatch)
    client.post("/api/companies/register", json=PAYLOAD)
    client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": sent[0][3]})

    again = client.post("/api/companies/verify-email", json={"email": PAYLOAD["email"], "code": "999999"})
    assert again.status_code == 200 and again.json()["verified"] is True

    nobody = client.post("/api/companies/verify-email", json={"email": "nobody@nowhere.com", "code": "123456"})
    assert nobody.status_code == 400


def test_the_migration_grandfathers_existing_accounts_as_verified_only_once():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, email VARCHAR(255), email_verified BOOLEAN NOT NULL DEFAULT 0)"))
        connection.execute(text("INSERT INTO companies (id, email, email_verified) VALUES (1, 'old@x.com', 0)"))

    import backend.app.database.database as database

    original = database.engine
    database.engine = engine

    try:
        database.migrate_database()

        with engine.begin() as connection:
            assert connection.execute(text("SELECT email_verified FROM companies WHERE id = 1")).scalar() == 1      # existing HR keeps working
            connection.execute(text("INSERT INTO companies (id, email, email_verified) VALUES (2, 'new@x.com', 0)"))

        database.migrate_database()

        with engine.begin() as connection:
            assert connection.execute(text("SELECT email_verified FROM companies WHERE id = 2")).scalar() == 0      # a second start never verifies a new account
    finally:
        database.engine = original

"""
Candidate-facing assessment scheduling + deadline reminders.

- POST /api/invites/{token}/schedule: candidate picks a time (must be in the
  future and before the invite's deadline), gets a confirmation email, and
  the choice is visible on a later GET /api/invites/{token}.
- reminder_scheduler.send_due_reminders(): fires the 24h/2h-before-deadline
  reminder emails at most once each per invite.

In-memory SQLite, fake email send, no jobpilot.db, no real Gemini/Groq/SMTP.
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

import backend.app.api.routes.candidate_invites as candidate_invites_routes
import backend.app.services.assessment.reminder_scheduler as reminder_scheduler
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.auth_session import AuthSession

COMPANY_ID = 1
JOB_ID = 1


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, Company, Job, JobApplication, CandidateInvite, AuthSession):
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
    return db


def _make_invite(db, **overrides):
    now = datetime.utcnow()
    defaults = dict(
        token="tok-" + str(overrides.get("id", "x")),
        job_id=JOB_ID,
        company_id=COMPANY_ID,
        candidate_name="Jane Candidate",
        candidate_email="jane@example.test",
        resume_file_path="data/resumes/fake.pdf",
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    defaults.update(overrides)
    invite = CandidateInvite(**defaults)
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def _app(db):
    api = FastAPI()
    api.include_router(candidate_invites_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    return TestClient(api)


def test_schedule_accepts_a_future_time_before_the_deadline(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-1")
    client = _app(db)

    sent = []
    monkeypatch.setattr(
        candidate_invites_routes,
        "send_candidate_scheduled_confirmation_email",
        lambda **kwargs: sent.append(kwargs) or True,
    )

    scheduled_at = datetime.utcnow() + timedelta(days=1)
    response = client.post(f"/api/invites/{invite.token}/schedule", json={"scheduled_at": scheduled_at.isoformat()})
    assert response.status_code == 200, response.text
    assert response.json()["email_sent"] is True
    assert len(sent) == 1
    assert sent[0]["to_email"] == "jane@example.test"

    db.refresh(invite)
    assert invite.scheduled_at is not None

    preview = client.get(f"/api/invites/{invite.token}")
    assert preview.json()["scheduled_at"] is not None


def test_schedule_rejects_a_time_after_the_deadline(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-2")
    client = _app(db)
    monkeypatch.setattr(
        candidate_invites_routes, "send_candidate_scheduled_confirmation_email", lambda **kwargs: True,
    )

    too_late = invite.expires_at + timedelta(days=1)
    response = client.post(f"/api/invites/{invite.token}/schedule", json={"scheduled_at": too_late.isoformat()})
    assert response.status_code == 400


def test_schedule_rejects_a_time_in_the_past(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-3")
    client = _app(db)
    monkeypatch.setattr(
        candidate_invites_routes, "send_candidate_scheduled_confirmation_email", lambda **kwargs: True,
    )

    past = datetime.utcnow() - timedelta(hours=1)
    response = client.post(f"/api/invites/{invite.token}/schedule", json={"scheduled_at": past.isoformat()})
    assert response.status_code == 400


def test_schedule_rejects_an_expired_invite(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-4", expires_at=datetime.utcnow() - timedelta(hours=1))
    client = _app(db)

    response = client.post(
        f"/api/invites/{invite.token}/schedule",
        json={"scheduled_at": (datetime.utcnow() + timedelta(hours=2)).isoformat()},
    )
    assert response.status_code == 410


def test_reminder_fires_once_per_threshold(monkeypatch):
    db = _db()

    # 20 hours left -> due for the 24h reminder, not yet the 2h one.
    invite = _make_invite(db, token="tok-5", expires_at=datetime.utcnow() + timedelta(hours=20))

    sent = []
    monkeypatch.setattr(
        reminder_scheduler,
        "send_candidate_deadline_reminder_email",
        lambda **kwargs: sent.append(kwargs["time_left_label"]) or True,
    )

    count = reminder_scheduler.send_due_reminders(db)
    assert count == 1
    assert sent == ["24 hours"]

    db.refresh(invite)
    assert invite.reminder_24h_sent is True
    assert invite.reminder_2h_sent is False

    # Running it again immediately must NOT resend the same threshold.
    sent.clear()
    count_again = reminder_scheduler.send_due_reminders(db)
    assert count_again == 0
    assert sent == []


def test_reminder_fires_both_thresholds_when_very_close_to_deadline(monkeypatch):
    db = _db()
    _make_invite(db, token="tok-6", expires_at=datetime.utcnow() + timedelta(hours=1))

    sent = []
    monkeypatch.setattr(
        reminder_scheduler,
        "send_candidate_deadline_reminder_email",
        lambda **kwargs: sent.append(kwargs["time_left_label"]) or True,
    )

    count = reminder_scheduler.send_due_reminders(db)
    assert count == 2
    assert set(sent) == {"24 hours", "2 hours"}


def test_reminder_skips_expired_invites(monkeypatch):
    db = _db()
    _make_invite(db, token="tok-7", expires_at=datetime.utcnow() - timedelta(hours=1), status="expired")

    sent = []
    monkeypatch.setattr(
        reminder_scheduler,
        "send_candidate_deadline_reminder_email",
        lambda **kwargs: sent.append(kwargs) or True,
    )

    count = reminder_scheduler.send_due_reminders(db)
    assert count == 0
    assert sent == []


def test_expire_overdue_invites_flips_status_and_notifies_once(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-8", expires_at=datetime.utcnow() - timedelta(minutes=5))

    sent = []
    monkeypatch.setattr(
        reminder_scheduler,
        "send_candidate_deadline_expired_email",
        lambda **kwargs: sent.append(kwargs) or True,
    )

    count = reminder_scheduler.expire_overdue_invites(db)
    assert count == 1
    assert len(sent) == 1
    assert sent[0]["to_email"] == "jane@example.test"

    db.refresh(invite)
    assert invite.status == "expired"

    # Running it again must not re-notify an already-expired invite.
    sent.clear()
    count_again = reminder_scheduler.expire_overdue_invites(db)
    assert count_again == 0
    assert sent == []


def test_expired_invite_gives_a_clear_deadline_message_on_enter(monkeypatch):
    db = _db()
    invite = _make_invite(db, token="tok-9", expires_at=datetime.utcnow() - timedelta(hours=1))
    client = _app(db)

    response = client.post(f"/api/invites/{invite.token}/enter")
    assert response.status_code == 410
    assert "deadline has passed" in response.json()["detail"].lower()

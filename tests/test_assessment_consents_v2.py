"""Instructions + tick-box consent: one row per (application, stage), owner only, idempotent, server-side statement version."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
from backend.app.api import deps
from backend.app.api.routes import assessment_consents as consents
from backend.app.models.assessment_consent import AssessmentConsent


def _insert(Session, table, **values):
    import datetime
    from sqlalchemy import Boolean, DateTime, Float, Integer
    t = Base.metadata.tables[table]
    row = dict(values)
    for c in t.columns:
        if c.name in row or c.primary_key or c.nullable or c.default is not None or c.server_default is not None:
            continue
        if c.foreign_keys:
            row[c.name] = 1
        elif isinstance(c.type, Boolean):
            row[c.name] = False
        elif isinstance(c.type, (Integer, Float)):
            row[c.name] = 1
        elif isinstance(c.type, DateTime):
            row[c.name] = datetime.datetime.utcnow()
        else:
            row[c.name] = "x"
    db = Session()
    db.execute(t.insert().values(**row))
    db.commit()
    db.close()


def _client():
    import backend.app.models  # noqa: F401
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    app = FastAPI()
    app.include_router(consents.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: type("U", (), {"id": 1})()
    consents.require_csrf = lambda request: None
    _insert(Session, "users", id=1, email="t@t.t")
    _insert(Session, "job_applications", id=1, job_id=1, user_id=1, status="applied")
    return TestClient(app), Session


def test_accept_is_recorded_once_and_reads_back():
    client, Session = _client()
    assert client.get("/api/assessment/consents/1/aptitude").json()["accepted"] is False
    first = client.post("/api/assessment/consents/1/aptitude", json={"statement_version": "client-lies"})
    assert first.status_code == 200 and first.json()["accepted"] is True
    assert first.json()["statement_version"] == consents.STATEMENT_VERSION            # the server's own version, not the browser's
    second = client.post("/api/assessment/consents/1/aptitude", json={})
    assert second.json()["accepted_at"] == first.json()["accepted_at"]                # idempotent: no second row
    assert Session().query(AssessmentConsent).count() == 1
    assert client.get("/api/assessment/consents/1/aptitude").json()["accepted"] is True
    assert client.get("/api/assessment/consents/1/programming").json()["accepted"] is False   # per stage


def test_only_real_stages_and_only_the_owners_application():
    client, _ = _client()
    assert client.post("/api/assessment/consents/1/nonsense", json={}).status_code == 400
    assert client.post("/api/assessment/consents/1/self_intro", json={}).status_code == 200            # the rules briefing now comes before the self introduction
    assert client.get("/api/assessment/consents/1/self_intro").json()["accepted"] is True
    assert client.post("/api/assessment/consents/2/aptitude", json={}).status_code == 404       # not this user's application


def test_the_consent_reply_carries_the_real_strike_limit_for_the_dialog():
    client, _ = _client()
    from backend.app.services.assessment.integrity_service import TERMINATION_LIMIT

    assert client.get("/api/assessment/consents/1/self_intro").json()["max_violations"] == TERMINATION_LIMIT

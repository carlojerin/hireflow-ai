"""
Aptitude fixes: draft-answer autosave (survives a refresh/reconnect
mid-exam) and the atomic completion guard on submit (two
near-simultaneous submits can never both succeed).

In-memory SQLite, no jobpilot.db.
"""

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Boolean, DateTime, Float, Integer, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
from backend.app.api import deps
from backend.app.api.routes import aptitude_tests as aptitude_routes


QUESTIONS = json.dumps([
    {
        "id": 1,
        "category": "quantitative",
        "question": "2 + 2 = ?",
        "options": ["3", "4", "5"],
        "correct_answer": "4",
    },
])


def _insert(Session, table, **values):
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
    app.include_router(aptitude_routes.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: type("U", (), {"id": 1})()
    aptitude_routes.require_csrf = lambda request: None

    _insert(Session, "users", id=1, email="cand@example.test")
    _insert(Session, "job_applications", id=1, job_id=1, user_id=1, status="aptitude_pending")

    now = datetime.datetime.utcnow()

    _insert(
        Session, "aptitude_tests",
        id=1, application_id=1, questions=QUESTIONS, total_questions=1,
        status="in_progress", question_source="ai",
        assigned_at=now, expires_at=now + datetime.timedelta(hours=24),
        started_at=now, exam_expires_at=now + datetime.timedelta(minutes=20),
    )

    return TestClient(app), Session


def test_autosave_persists_a_draft_and_is_returned_on_read():
    client, Session = _client()

    response = client.put(
        "/api/aptitude-tests/1/autosave",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "4"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"saved": True}

    read = client.get("/api/aptitude-tests/application/1?user_id=1")
    draft = read.json()["test"]["draft_answers"]
    assert draft == [{"question_id": 1, "selected_answer": "4"}]


def test_autosave_never_touches_the_real_answers_column():
    client, Session = _client()

    client.put(
        "/api/aptitude-tests/1/autosave",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "4"}]},
    )

    db = Session()
    from backend.app.models.aptitude_test import AptitudeTest
    test = db.query(AptitudeTest).filter(AptitudeTest.id == 1).first()
    assert test.answers is None
    assert test.status == "in_progress"
    db.close()


def test_autosave_on_a_completed_test_is_a_no_op_not_an_error():
    client, Session = _client()

    # Wrong answer deliberately -- keeps this test isolated from
    # advance_pipeline() (only called on a pass).
    submit = client.post(
        "/api/aptitude-tests/1/submit",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "3"}]},
    )
    assert submit.status_code == 200, submit.text

    autosave = client.put(
        "/api/aptitude-tests/1/autosave",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "3"}]},
    )
    assert autosave.status_code == 200
    assert autosave.json() == {"saved": False}


def test_two_near_simultaneous_submits_only_one_succeeds():
    client, Session = _client()

    # Wrong answer deliberately -- keeps this test isolated from
    # advance_pipeline() (only called on a pass), which needs a
    # full Job/JobApplication graph this minimal fixture doesn't set up.
    first = client.post(
        "/api/aptitude-tests/1/submit",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "3"}]},
    )
    second = client.post(
        "/api/aptitude-tests/1/submit",
        json={"user_id": 1, "answers": [{"question_id": 1, "selected_answer": "4"}]},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409

    db = Session()
    from backend.app.models.aptitude_test import AptitudeTest
    from backend.app.models.application_status_history import ApplicationStatusHistory
    test = db.query(AptitudeTest).filter(AptitudeTest.id == 1).first()
    assert test.score == 0
    assert test.passed is False

    # The second (rejected) request must never have reached
    # record_status_change() -- exactly one transition recorded.
    history_count = (
        db.query(ApplicationStatusHistory)
        .filter(ApplicationStatusHistory.application_id == 1)
        .count()
    )
    assert history_count == 1
    db.close()

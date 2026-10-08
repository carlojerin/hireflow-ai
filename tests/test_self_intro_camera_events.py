"""Camera attention flags for the self introduction: recorded for the recruiter, never counted, never shown to the candidate, never able to
end the introduction. In-memory DB and the real route."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.api import deps
from backend.app.api.routes import assessment_integrity as route
from backend.app.database.database import Base, get_db
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User
from backend.app.services.assessment import integrity_service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def env(monkeypatch):

    monkeypatch.setattr(route, "require_csrf", lambda request: None)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    db = Session()
    db.add_all([
        User(id=1, full_name="Cand", username="cand", email="c@x.test", hashed_password="x", role="user"),
        User(id=2, full_name="Other", username="other", email="o@x.test", hashed_password="x", role="user"),
    ])
    job = Job(title="Copywriter", company="Acme", source="invite_only")
    db.add(job)
    db.flush()
    db.add(JobApplication(id=10, job_id=job.id, user_id=1, status="shortlisted"))
    other_job = Job(title="Editor", company="Acme", source="invite_only")
    db.add(other_job)
    db.flush()
    db.add(JobApplication(id=11, job_id=other_job.id, user_id=1, status="shortlisted"))            # same candidate, no assessment session yet
    db.add(AssessmentSession(application_id=10, current_stage="self_intro", self_intro_status="in_progress"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(route.router)
    who = {"id": 1}

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=who["id"], role="user")

    return TestClient(app), Session, who


def _post(client, event_type, application_id=10):
    return client.post("/api/assessment/integrity/self-intro-event", json={"application_id": application_id, "event_type": event_type})


def test_camera_flags_are_stored_as_information_never_counted_as_violations(env):
    client, Session, _who = env

    for event in ("face_missing", "multiple_faces_detected", "looking_away", "camera_covered", "mobile_phone_detected"):
        assert _post(client, event).json() == {"recorded": True}, event

    db = Session()
    rows = db.query(AssessmentIntegrityEvent).all()
    assert len(rows) == 5 and {r.assessment_type for r in rows} == {"self_intro"}
    assert all(r.severity == "info" and r.counted_as_violation is False for r in rows)
    assert all(r.assessment_session_id is not None for r in rows)
    assert integrity_service._count_violations(db, 10, "self_intro") == 0                           # nothing here can ever add up to a warning
    db.close()


def test_only_camera_events_are_accepted_nothing_that_could_cost_a_candidate_their_test(env):
    client, Session, _who = env

    for event in ("tab_hidden", "fullscreen_exited", "paste_detected", "window_blur", "assessment_auto_terminated", "violation_warning", "nonsense", ""):
        assert _post(client, event).status_code == 400, event

    db = Session()
    assert db.query(AssessmentIntegrityEvent).count() == 0
    db.close()


def test_repeats_within_a_few_seconds_are_ignored_but_a_different_flag_is_not(env):
    client, Session, _who = env

    assert _post(client, "looking_away").json() == {"recorded": True}
    assert _post(client, "looking_away").json() == {"recorded": False}                              # same flag inside 5 s
    assert _post(client, "face_missing").json() == {"recorded": True}                               # a different flag is its own event

    db = Session()
    old = datetime.utcnow() - timedelta(seconds=route.SELF_INTRO_MIN_GAP_SECONDS + 5)
    for row in db.query(AssessmentIntegrityEvent).filter(AssessmentIntegrityEvent.event_type == "looking_away").all():
        row.occurred_at = old
    db.commit()
    db.close()

    assert _post(client, "looking_away").json() == {"recorded": True}                               # later: a new event


def test_the_number_of_stored_events_is_capped(env, monkeypatch):
    client, Session, _who = env
    monkeypatch.setattr(route, "SELF_INTRO_MAX_EVENTS", 3)
    monkeypatch.setattr(route, "SELF_INTRO_MIN_GAP_SECONDS", 0)

    results = [_post(client, "face_missing").json()["recorded"] for _ in range(5)]

    assert results == [True, True, True, False, False]


def test_only_the_candidates_own_started_assessment_accepts_events(env):
    client, Session, who = env

    assert _post(client, "face_missing", application_id=11).status_code == 409                      # no assessment session yet
    assert _post(client, "face_missing", application_id=999).status_code == 404

    who["id"] = 2                                                                                    # someone else's application
    assert _post(client, "face_missing").status_code == 404


def test_the_self_introduction_page_runs_the_face_checks_on_its_own_camera_preview():
    with open(os.path.join(ROOT, "frontend", "js", "assessment.js"), encoding="utf-8") as f:
        js = f.read()

    assert "startPhoneDetection(video);\n        startSelfIntroFaceMonitoring(video);" in js           # the same preview, never a second camera
    assert "window.FaceProctor.start(videoEl" in js and "/api/assessment/integrity/self-intro-event" in js
    assert 'reportSelfIntroCameraEvent("mobile_phone_detected")' in js
    stop = js[js.index("function stopPhoneDetection()"):]
    assert "stopSelfIntroFaceMonitoring();" in stop[:200]                                              # released when the camera preview stops

    with open(os.path.join(ROOT, "frontend", "assessment.html"), encoding="utf-8") as f:
        assert "face-proctor.js" in f.read()

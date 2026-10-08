"""A candidate who leaves mid-stage must not lose the video: chunks are spooled to the server, assembled on finalize (one upload),
and assembled into a "partial" recording if the candidate never finished. Isolated in-memory DB and temp storage."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.api import deps
from backend.app.api.routes import assessment_videos as av
from backend.app.database.database import Base, get_db
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User


@pytest.fixture
def env(tmp_path, monkeypatch):

    root = str(tmp_path / "videos")
    monkeypatch.setattr(av, "VIDEO_STORAGE_ROOT", root)
    monkeypatch.setattr(av, "PARTIAL_ROOT", os.path.join(root, "_partial"))
    monkeypatch.setattr(av, "remux_for_playback", lambda path: True)                          # ffmpeg is not what is under test
    monkeypatch.setattr(av, "mirror_recording_to_folder", lambda *a, **k: None)
    monkeypatch.setattr(av, "_probe_duration", lambda path: 12.5)
    monkeypatch.setattr(av, "require_csrf", lambda request: None)

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
    db.add(AssessmentSession(application_id=10, current_stage="aptitude"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(av.router)
    who = {"id": 1}

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=who["id"], role="user")

    return TestClient(app), Session, who, root


def _chunk(client, seq, data, stage="aptitude"):
    return client.post(f"/api/assessment/videos/10/{stage}/chunk", data={"seq": seq}, files={"file": ("c.webm", data, "video/webm")})


def test_chunks_are_assembled_on_finalize_into_one_recording_and_the_spool_is_removed(env):
    client, Session, _who, root = env

    for seq, data in enumerate([b"AAAA", b"BBBB", b"CCCC"]):
        assert _chunk(client, seq, data).status_code == 200

    response = client.post("/api/assessment/videos/10/aptitude/finalize", data={"expected_chunks": 3, "duration_seconds": 30})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "uploaded"

    db = Session()
    row = db.query(AssessmentVideoRecording).one()
    with open(os.path.join(root, *row.file_path.split("/")), "rb") as f:
        assert f.read() == b"AAAABBBBCCCC"                                                    # in order, nothing lost
    assert row.file_size == 12 and row.duration_seconds == 30
    assert not os.path.isdir(av._partial_dir(10, "aptitude"))                                 # the spool is gone

    assert _chunk(client, 3, b"DDDD").status_code == 409                                      # the stage is already saved
    db.close()


def test_finalize_refuses_when_chunks_are_missing_so_the_browser_uploads_the_whole_video(env):
    client, Session, _who, _root = env

    _chunk(client, 0, b"AAAA")
    _chunk(client, 2, b"CCCC")                                                                # chunk 1 never arrived

    response = client.post("/api/assessment/videos/10/aptitude/finalize", data={"expected_chunks": 3})
    assert response.status_code == 409 and "missing" in response.json()["detail"]

    db = Session()
    assert db.query(AssessmentVideoRecording).count() == 0
    db.close()


def test_a_candidate_who_left_gets_a_partial_recording_once_the_chunks_are_stale(env):
    client, Session, _who, root = env

    _chunk(client, 0, b"AAAA")
    _chunk(client, 1, b"BBBB")
    _chunk(client, 3, b"DDDD")                                                                # a gap: only 0 and 1 can be played

    db = Session()
    assert av.assemble_stale_partials(db, 10) == 0                                            # just recorded: the candidate may still be there
    assert db.query(AssessmentVideoRecording).count() == 0

    old = time.time() - av.STALE_PARTIAL_SECONDS - 60
    directory = av._partial_dir(10, "aptitude")
    for name in os.listdir(directory):
        os.utime(os.path.join(directory, name), (old, old))

    assert av.assemble_stale_partials(db, 10) == 1
    row = db.query(AssessmentVideoRecording).one()
    assert row.status == "partial" and row.duration_seconds == 12.5
    with open(os.path.join(root, *row.file_path.split("/")), "rb") as f:
        assert f.read() == b"AAAABBBB"
    assert not os.path.isdir(directory)
    assert av.assemble_stale_partials(db, 10) == 0                                            # nothing left to do
    db.close()


def test_a_normal_full_upload_removes_any_spooled_chunks_and_never_creates_a_second_recording(env):
    client, Session, _who, _root = env

    _chunk(client, 0, b"AAAA")
    response = client.post("/api/assessment/videos/10/aptitude", files={"file": ("a.webm", b"FULLVIDEO", "video/webm")}, data={"duration_seconds": 5})
    assert response.status_code == 200, response.text

    assert not os.path.isdir(av._partial_dir(10, "aptitude"))
    db = Session()
    assert db.query(AssessmentVideoRecording).count() == 1 and db.query(AssessmentVideoRecording).one().status == "uploaded"
    db.close()


def test_only_the_candidates_own_application_and_valid_chunks_are_accepted(env):
    client, _Session, who, _root = env

    assert _chunk(client, 0, b"A", stage="not_a_stage").status_code == 400
    assert _chunk(client, 0, b"").status_code == 400
    assert client.post("/api/assessment/videos/10/aptitude/chunk", data={"seq": 999999}, files={"file": ("c.webm", b"A", "video/webm")}).status_code == 400

    who["id"] = 2                                                                             # someone else's application
    assert _chunk(client, 0, b"A").status_code in (403, 404)
    assert client.post("/api/assessment/videos/10/aptitude/finalize", data={"expected_chunks": 1}).status_code in (403, 404)


def test_the_recorder_spools_and_falls_back_to_the_whole_video_upload():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "js", "assessment-video-recorder.js")
    with open(path, encoding="utf-8") as f:
        js = f.read()

    assert "_startSpool(stage)" in js and "/chunk" in js and "/finalize" in js
    assert "spooled || this._uploadWhole(applicationId, stage)" in js                         # any failure: the whole video, as before
    assert "this._stopSpoolTimer();" in js and "SPOOL_INTERVAL_MS = 10000" in js
    assert 'indexOf("webm") === -1' in js                                                      # MP4 browsers never spool: the server assembles .webm only

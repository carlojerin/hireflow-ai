"""Camera snapshot evidence: upload rules (owner, JPEG only, size, rate, one identity reference) with an in-memory database."""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
import backend.app.database.database as database
from backend.app.api import deps
from backend.app.api.routes import assessment_snapshots as snap
from backend.app.models.user import User
from backend.app.models.job_application import JobApplication
from backend.app.models.job import Job

JPEG = b"\xff\xd8\xff" + b"0" * 2000


def _client():

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.init_models = getattr(database, "init_models", None)
    # register every model on Base, then create the tables in the in-memory database
    import backend.app.models  # noqa: F401
    from backend.app.models import assessment_snapshot  # noqa: F401
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    snap.SNAPSHOT_STORAGE_ROOT = tempfile.mkdtemp()

    app = FastAPI()
    app.include_router(snap.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: type("U", (), {"id": 1})()
    snap.require_csrf = lambda request: None

    return TestClient(app), Session


def _insert(Session, table, **values):
    """Insert one row, filling every other NOT NULL column with a harmless dummy so the test does not depend on the model's shape."""
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
            import datetime
            row[c.name] = datetime.datetime.utcnow()
        else:
            row[c.name] = "x"
    db = Session()
    db.execute(t.insert().values(**row))
    db.commit()
    db.close()


def _seed_application(Session, owner=1):
    _insert(Session, "users", id=1, email="t@t.t")
    _insert(Session, "job_applications", id=1, job_id=1, user_id=owner, status="applied")


def test_upload_rules():
    client, Session = _client()
    _seed_application(Session)
    ok = client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "identity_reference"}, files={"file": ("a.jpg", JPEG, "image/jpeg")})
    assert ok.status_code == 200, ok.text
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "identity_reference"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 409
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "face_missing"}, files={"file": ("a.png", b"\x89PNG" + b"0" * 50, "image/png")}).status_code == 400
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "face_missing"}, files={"file": ("a.jpg", JPEG + b"0" * 300000, "image/jpeg")}).status_code == 400
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "nonsense"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 400
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "face_missing"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 200
    assert client.post("/api/assessment/snapshots/1/aptitude", data={"reason": "face_missing"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 429   # flag storm
    assert client.post("/api/assessment/snapshots/2/aptitude", data={"reason": "periodic"}, files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 404       # not the owner's application
    shutil.rmtree(snap.SNAPSHOT_STORAGE_ROOT, ignore_errors=True)


def test_constants_are_bounded():
    assert snap.MAX_SNAPSHOT_BYTES <= 300 * 1024 and snap.MAX_SNAPSHOTS_PER_STAGE <= 200
    assert {"identity_reference", "periodic", "face_missing", "multiple_faces_detected"} <= snap.ALLOWED_REASONS

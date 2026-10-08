"""The HR intelligence report shows the candidate's photo (taken before the self introduction), the face at the start of each stage and
the pictures taken when the camera flagged something. In-memory DB; no images are read."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.api.routes.hr_assessment_review import _identity_photos
from backend.app.database.database import Base
from backend.app.models.assessment_snapshot import AssessmentSnapshot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _add(db, stage, reason, minutes, application_id=10):
    row = AssessmentSnapshot(application_id=application_id, assessment_type=stage, reason=reason, file_path=f"{application_id}/{stage}/{minutes}.jpg", file_size=1000,
                             created_at=datetime(2026, 10, 3, 10, 0) + timedelta(minutes=minutes))
    db.add(row)
    db.flush()
    return row


def test_the_reference_is_the_photo_taken_before_the_self_introduction_and_each_stage_gets_one_start_photo():
    db = _db()
    apt_start = _add(db, "aptitude", "identity_reference", 5)
    intro_start = _add(db, "self_intro", "identity_reference", 0)                      # earliest, and it is the self-introduction stage
    _add(db, "self_intro", "identity_reference", 1)                                    # a second one for the same stage: ignored
    _add(db, "aptitude", "periodic", 7)
    _add(db, "ai_interview", "identity_reference", 20)
    db.commit()

    photos = _identity_photos(db, SimpleNamespace(id=10))

    assert photos["reference"]["id"] == intro_start.id and photos["reference"]["stage"] == "self_intro"
    assert photos["reference"]["label"] == "Before the self introduction"
    assert photos["reference"]["url"] == f"/api/assessment/snapshots/file/{intro_start.id}"
    assert [p["stage"] for p in photos["per_stage"]] == ["self_intro", "aptitude", "ai_interview"]       # one per stage, in time order
    assert photos["per_stage"][1]["id"] == apt_start.id and photos["per_stage"][1]["label"] == "Start of Aptitude"
    assert photos["total_snapshots"] == 5


def test_without_a_self_introduction_photo_the_earliest_stage_photo_is_used():
    db = _db()
    first = _add(db, "aptitude", "identity_reference", 3)
    _add(db, "ai_interview", "identity_reference", 9)
    db.commit()

    assert _identity_photos(db, SimpleNamespace(id=10))["reference"]["id"] == first.id


def test_camera_flag_pictures_are_listed_with_plain_labels_and_periodic_pictures_are_not():
    db = _db()
    _add(db, "aptitude", "identity_reference", 0)
    _add(db, "aptitude", "periodic", 2)
    phone = _add(db, "aptitude", "mobile_phone_detected", 4)
    _add(db, "programming", "multiple_faces_detected", 8)
    _add(db, "programming", "looking_away_repeated", 9)
    db.commit()

    flagged = _identity_photos(db, SimpleNamespace(id=10))["flagged"]

    assert [f["label"] for f in flagged] == ["A phone was visible", "More than one person", "Looked away again after a warning"]
    assert flagged[0]["id"] == phone.id and flagged[0]["stage_label"] == "Aptitude"
    assert all(f["reason"] != "periodic" for f in flagged)


def test_only_this_applications_photos_are_included_and_an_empty_one_is_safe():
    db = _db()
    _add(db, "self_intro", "identity_reference", 0, application_id=10)
    _add(db, "self_intro", "identity_reference", 1, application_id=11)
    db.commit()

    other = _identity_photos(db, SimpleNamespace(id=11))
    assert other["reference"]["id"] != _identity_photos(db, SimpleNamespace(id=10))["reference"]["id"] and other["total_snapshots"] == 1

    empty = _identity_photos(db, SimpleNamespace(id=99))
    assert empty == {"reference": None, "per_stage": [], "flagged": [], "total_snapshots": 0}


def test_the_report_page_shows_the_photo_section_and_the_payload_carries_it():
    with open(os.path.join(ROOT, "frontend", "js", "company-candidate-report.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(ROOT, "backend", "app", "api", "routes", "hr_assessment_review.py"), encoding="utf-8") as f:
        py = f.read()

    assert "renderIdentityPhotos(data)" in js and "Candidate photo and identity" in js
    assert js.index("renderIdentityPhotos(data)") < js.index("renderAssessmentPlan(data)}")             # the photo comes first
    assert "it is not an identity verification" in js                                              # honest about what it is
    assert '"identity_photos": _identity_photos(db, application)' in py

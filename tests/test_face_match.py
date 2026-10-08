"""
Face match: the later camera photos compared with the reference photo. A recruiter aid: every photo is compared at most once (the answer is stored), a failed
comparison is retried later, nothing biometric is kept, and "unclear" is a review flag, never an accusation.
"""

import os
import sys
from types import SimpleNamespace as N

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.main  # noqa: F401 - registers every model
from backend.app.api.routes.hr_assessment_review import _face_match_view
from backend.app.database.database import Base
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.services.assessment import face_match as fm


def _row(i, stage, reason):
    return N(id=i, assessment_type=stage, reason=reason, file_path=f"1/{stage}/{i}.jpg")


def test_the_reference_is_the_photo_before_the_self_introduction_and_later_photos_start_with_each_stage():
    rows = [
        _row(1, "self_intro", "identity_reference"), _row(2, "aptitude", "identity_reference"), _row(3, "aptitude", "periodic"),
        _row(4, "situational_judgment", "identity_reference"), _row(5, "situational_judgment", "face_missing"), _row(6, "ai_interview", "identity_reference"),
        _row(7, "ai_interview", "periodic"),
    ]
    reference, later = fm.pick_photos(rows)

    assert reference.id == 1
    assert [r.id for r in later][:3] == [2, 4, 6]                                     # the face at the start of each later stage comes first
    assert all(r.reason in ("identity_reference", "periodic") for r in later)         # a flag-time photo is for the recruiter's eyes, not the comparison
    assert len(later) <= fm.MAX_PHOTOS_COMPARED

    assert fm.pick_photos([]) == (None, [])


@pytest.mark.parametrize("results,status", [
    ([], "not_run"),
    ([{"verdict": "same", "confidence": 0.95}, {"verdict": "same", "confidence": 0.9}], "consistent"),
    ([{"verdict": "same", "confidence": 0.95}, {"verdict": "unclear", "confidence": 0.8}], "review"),
    ([{"verdict": "same", "confidence": 0.4}], "review"),                              # a weak "same" is not trusted
    ([{"verdict": "different", "confidence": 0.5}], "review"),                         # a weak "different" is a review flag, not a mismatch
    ([{"verdict": "same", "confidence": 0.95}, {"verdict": "different", "confidence": 0.9}], "mismatch"),
])
def test_the_summary_is_consistent_review_or_mismatch(results, status):
    assert fm.summarise(results)["status"] == status


@pytest.fixture()
def world(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    monkeypatch.setattr(fm, "SNAPSHOT_STORAGE_ROOT", str(tmp_path))

    def add(i, stage, reason, with_file=True):
        folder = tmp_path / "1" / stage
        folder.mkdir(parents=True, exist_ok=True)
        if with_file:
            (folder / f"{i}.jpg").write_bytes(b"\xff\xd8photo%d" % i)
        db.add(AssessmentSnapshot(id=i, application_id=1, assessment_type=stage, reason=reason, file_path=f"1/{stage}/{i}.jpg", file_size=10))
        db.commit()

    return db, add


def test_every_later_photo_is_compared_once_and_the_answers_are_reused(world):
    db, add = world
    add(1, "self_intro", "identity_reference")
    add(2, "aptitude", "identity_reference")
    add(3, "ai_interview", "identity_reference")

    calls = []

    def fake(reference, other):
        calls.append(other)
        return {"verdict": "same", "confidence": 0.93, "reason": "same face", "model": "fake"}

    first = fm.run_for_application(db, 1, compare=fake)
    assert first["ran"] == 2 and first["failed"] == 0 and len(calls) == 2

    second = fm.run_for_application(db, 1, compare=fake)
    assert second["ran"] == 0 and len(calls) == 2                                      # nothing is sent a second time

    view = _face_match_view(db, 1)
    assert view["summary"]["status"] == "consistent" and view["compared"] == 2 and view["pending"] == 0
    assert {r["stage_label"] for r in view["results"]} == {"Aptitude", "AI interview"}


def test_a_failed_comparison_is_skipped_and_retried_next_time_and_a_mismatch_is_reported(world):
    db, add = world
    add(1, "self_intro", "identity_reference")
    add(2, "aptitude", "identity_reference")
    add(3, "ai_interview", "identity_reference")

    def flaky(reference, other):
        if other.endswith(b"2"):
            raise RuntimeError("the vision model is busy")
        return {"verdict": "different", "confidence": 0.92, "reason": "a different face", "model": "fake"}

    first = fm.run_for_application(db, 1, compare=flaky)
    assert first["ran"] == 1 and first["failed"] == 1
    assert _face_match_view(db, 1)["pending"] == 1 and _face_match_view(db, 1)["summary"]["status"] == "mismatch"

    retry = fm.run_for_application(db, 1, compare=lambda r, o: {"verdict": "same", "confidence": 0.9, "reason": "ok", "model": "fake"})
    assert retry["ran"] == 1 and _face_match_view(db, 1)["pending"] == 0                # only the photo that failed was compared again


def test_a_missing_photo_file_is_counted_as_failed_and_with_no_later_photos_nothing_runs(world):
    db, add = world
    add(1, "self_intro", "identity_reference")
    assert fm.run_for_application(db, 1, compare=lambda r, o: {})["ran"] == 0           # only a reference photo: nothing to compare

    add(2, "aptitude", "identity_reference", with_file=False)
    assert fm.run_for_application(db, 1, compare=lambda r, o: {})["failed"] == 1


def test_the_stored_result_holds_no_biometric_data(world):
    from backend.app.models.face_match_result import FaceMatchResult

    columns = {c.name for c in FaceMatchResult.__table__.columns}
    assert columns == {"id", "application_id", "snapshot_id", "reference_snapshot_id", "stage", "verdict", "confidence", "reason", "model", "created_at"}


def test_the_report_runs_the_face_check_and_the_candidate_is_told_about_it():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report = open(os.path.join(root, "frontend", "js", "company-candidate-report.js"), encoding="utf-8").read()
    dialog = open(os.path.join(root, "frontend", "js", "assessment-instructions.js"), encoding="utf-8").read()
    assert "runFaceMatch(false)" in report and "/face-match" in report and "${base}/run" in report and "faceMatchBox" in report
    assert "compared with each other by a computer" in dialog

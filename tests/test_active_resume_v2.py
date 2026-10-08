"""
Active analyzed resume: a failed analysis of a NEWER upload must never hide the last valid analysis.

In-memory SQLite (only the tables involved), fake analyzer, no network, no real Gemini, no jobpilot.db.
"""

import os
import sys
import tempfile

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.resumes as routes
from backend.app.api.routes.candidate_jobs import get_candidate_context
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User
from backend.app.services.resume import analyzer
from backend.app.services.resume.active_resume import get_active_resume, get_unanalyzed_newer_upload

UID = 900001


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, CandidateProfile, Resume, ResumeAnalysis):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    db.add(User(id=UID, full_name="T", username="t", email="t@example.test", hashed_password="x"))
    db.commit()
    return db


def _upload(db, name):
    path = os.path.join(tempfile.gettempdir(), name)
    with open(path, "w") as handle:
        handle.write("resume text")
    row = Resume(user_id=UID, file_name=name, file_path=path, file_type="pdf", uploaded_at="2026-01-01 00:00:00")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


_REAL_EXTRACT_RESUME_TEXT = routes.extract_resume_text
_REAL_ANALYZE_RESUME = routes.analyze_resume


def teardown_function(_fn):
    # _analyze() patches these two SHARED module attributes directly (no fixture, no auto-restore) -- without this,
    # every test file that runs afterward in the same pytest process inherits whichever fake was last installed here.
    routes.extract_resume_text = _REAL_EXTRACT_RESUME_TEXT
    routes.analyze_resume = _REAL_ANALYZE_RESUME


def _analyze(db, resume, outcome):
    """Run the real analyze_and_save_resume with a fake Gemini: outcome is a dict (success) or an exception."""

    def fake_analyze(_text):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    routes.extract_resume_text = lambda path, kind: "resume text"
    routes.analyze_resume = fake_analyze
    return routes.analyze_and_save_resume(resume, db)


def _good(skill):
    return {"summary": "s", "skills": [skill], "job_roles": ["r"], "education": ["e"], "experience": ["x"], "experience_level": "Entry Level"}


def test_full_lifecycle_active_resume():

    db = _db()

    a = _upload(db, "a.pdf")
    _analyze(db, a, _good("Python"))
    assert get_active_resume(db, UID).id == a.id                        # 1. A analyzed -> active

    b = _upload(db, "b.pdf")
    try:
        _analyze(db, b, analyzer.TransientAnalysisError(analyzer.TRANSIENT_ERROR_MESSAGE))
        raise AssertionError("expected a 503")
    except HTTPException as exc:
        assert exc.status_code == 503                                   # 2. B fails (Gemini 503)

    assert get_active_resume(db, UID).id == a.id                        # 3/7. failed B does not hide A
    assert db.query(Resume).filter(Resume.id == b.id).first() is not None   # the failed upload is kept
    assert db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == b.id).first() is None   # nothing fabricated
    pending = get_unanalyzed_newer_upload(db, UID, get_active_resume(db, UID))
    assert pending is not None and pending.id == b.id                   # the failed attempt stays distinguishable

    c = _upload(db, "c.pdf")
    _analyze(db, c, _good("FastAPI"))
    assert get_active_resume(db, UID).id == c.id                        # 4/5. successful C becomes active
    assert db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == a.id).first() is not None   # 6. A stays historical
    assert get_unanalyzed_newer_upload(db, UID, get_active_resume(db, UID)) is None


def test_retrying_the_failed_upload_makes_it_active():

    db = _db()
    a = _upload(db, "a2.pdf")
    _analyze(db, a, _good("Python"))
    b = _upload(db, "b2.pdf")
    try:
        _analyze(db, b, analyzer.TransientAnalysisError("x"))
    except HTTPException:
        pass
    assert get_active_resume(db, UID).id == a.id
    _analyze(db, b, _good("Go"))                                        # later retry of B succeeds
    assert get_active_resume(db, UID).id == b.id


def test_first_upload_without_analysis_is_still_returned():

    db = _db()
    a = _upload(db, "only.pdf")
    assert get_active_resume(db, UID).id == a.id                        # no analyzed resume exists: newest upload shown
    assert get_active_resume(db, UID + 1) is None


def test_jobs_context_uses_the_active_successful_analysis():

    db = _db()
    a = _upload(db, "a3.pdf")
    _analyze(db, a, _good("Python"))
    b = _upload(db, "b3.pdf")
    try:
        _analyze(db, b, analyzer.TransientAnalysisError("x"))
    except HTTPException:
        pass

    context = get_candidate_context(db, UID)
    resume, analysis = context[2], context[3]
    assert resume.id == a.id and analysis is not None                   # 8. jobs still see the valid analysis
    assert "Python" in analysis.skills


def test_get_latest_resume_route_reports_the_active_one_and_the_failed_upload():

    db = _db()
    a = _upload(db, "a4.pdf")
    _analyze(db, a, _good("Python"))
    b = _upload(db, "b4.pdf")
    try:
        _analyze(db, b, analyzer.TransientAnalysisError("x"))
    except HTTPException:
        pass

    user = db.query(User).filter(User.id == UID).first()
    body = routes.get_user_resume(user_id=UID, current_user=user, db=db)
    resume = body["resume"]
    assert resume["id"] == a.id and resume["analysis_status"] == "completed" and resume["analysis"]
    assert resume["newer_upload_not_analyzed"]["id"] == b.id
    assert "previous analyzed resume remains active" in resume["newer_upload_not_analyzed"]["message"]


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failures += 1
                print("FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if failures else 0)

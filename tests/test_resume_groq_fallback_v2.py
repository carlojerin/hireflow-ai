"""
Resume analysis: Gemini primary, Groq fallback on TRANSIENT Gemini failure only.
Fake providers and in-memory SQLite: no network, no real Gemini/Groq, no jobpilot.db.
"""

import json
import os
import sys
import tempfile

import httpx
from fastapi import HTTPException
from google.genai import errors as genai_errors
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.resumes as routes
import backend.app.services.resume.analyzer as analyzer
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User
from backend.app.services.assessment.interview_llm_provider import HostedChatProvider
from backend.app.services.resume.active_resume import get_active_resume

UID = 900002
analyzer.RETRY_BACKOFF_SECONDS = (0.0, 0.0)


def setup_function(_fn):
    # Set fresh before EVERY test, not just once at import time: another test file running earlier in the same
    # pytest process (e.g. one that pops GEMINI_API_KEY as part of its own env cleanup) can otherwise leave this
    # module-level assignment undone by the time this file's own tests run.
    os.environ["GEMINI_API_KEY"] = "test-not-a-real-key"

GEMINI_RESULT = {"summary": "gemini summary", "skills": ["Python"], "job_roles": ["Backend Developer"],
                 "education": ["B.Sc"], "experience": ["Intern"], "experience_level": "Entry Level"}
GROQ_RESULT = {"summary": "groq summary", "skills": ["Python", "FastAPI"], "job_roles": ["Backend Developer"],
               "education": ["B.Sc"], "experience": ["Intern"], "experience_level": "Entry Level"}


def _api_error(code, status):
    return genai_errors.APIError(code, {"error": {"code": code, "message": "RAW-PROVIDER-BODY", "status": status}})


class Fakes:
    """Installs a fake Gemini client and a fake Groq HTTP-level provider; counts calls."""

    def __init__(self, gemini, groq):
        self.gemini_calls = 0
        self.groq_calls = 0
        self.groq_kwargs = None
        self.groq_prompt = None
        self.gemini_prompt = None
        outer = self

        class Models:
            def generate_content(self, **kw):
                outer.gemini_calls += 1
                outer.gemini_prompt = kw["contents"]
                item = gemini[min(outer.gemini_calls - 1, len(gemini) - 1)]
                if isinstance(item, Exception):
                    raise item
                return type("R", (), {"text": json.dumps(item)})()

        class Client:
            def __init__(self, **kw):
                self.models = Models()

        analyzer.genai.Client = Client

        def fake_generate_json(self, prompt, system=None, json_schema=None, max_tokens=None):
            outer.groq_calls += 1
            outer.groq_prompt = prompt
            outer.groq_kwargs = {"model": self.model, "json_schema": json_schema}
            if isinstance(groq, Exception):
                raise groq
            return json.dumps(groq)

        HostedChatProvider.generate_json = fake_generate_json


_REAL_HOSTED_GENERATE_JSON = HostedChatProvider.generate_json
_REAL_GENAI_CLIENT = analyzer.genai.Client
_REAL_EXTRACT_RESUME_TEXT = routes.extract_resume_text


def teardown_function(_fn):
    # Fakes.__init__ and _run() patch these SHARED module/class attributes directly (no fixture, no auto-restore)
    # -- without this, every test file that runs afterward in the same pytest process inherits the fake
    # Groq/Gemini/extract behavior, including the AI interview brain, which also uses HostedChatProvider.
    HostedChatProvider.generate_json = _REAL_HOSTED_GENERATE_JSON
    analyzer.genai.Client = _REAL_GENAI_CLIENT
    routes.extract_resume_text = _REAL_EXTRACT_RESUME_TEXT


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, CandidateProfile, Resume, ResumeAnalysis):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    db.add(User(id=UID, full_name="T", username="t", email="t2@example.test", hashed_password="x"))
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


def _run(db, resume):
    routes.extract_resume_text = lambda path, kind: "Jane Doe. Python, FastAPI, PostgreSQL. B.Sc CS."
    return routes.analyze_and_save_resume(resume, db)


def test_gemini_success_never_calls_groq():
    fakes = Fakes([GEMINI_RESULT], GROQ_RESULT)
    analysis = _run(_db(), _upload(_db(), "g1.pdf")) if False else None
    db = _db()
    analysis = _run(db, _upload(db, "g1.pdf"))
    assert fakes.gemini_calls == 1 and fakes.groq_calls == 0
    assert analysis.summary == "gemini summary"


def test_gemini_503_after_retries_uses_groq_once_and_saves_analysis():
    fakes = Fakes([_api_error(503, "UNAVAILABLE")], GROQ_RESULT)
    db = _db()
    resume = _upload(db, "g2.pdf")
    analysis = _run(db, resume)
    assert fakes.gemini_calls == 3                                   # first try + the 2 existing bounded retries
    assert fakes.groq_calls == 1                                     # Groq exactly once
    assert analysis.summary == "groq summary" and json.loads(analysis.skills) == ["Python", "FastAPI"]
    assert db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).count() == 1
    assert fakes.groq_kwargs["model"] == "openai/gpt-oss-120b"
    schema = fakes.groq_kwargs["json_schema"]                        # structured output, same fields as the DB
    assert set(schema["required"]) == {"summary", "skills", "job_roles", "education", "experience", "experience_level"}
    assert schema["additionalProperties"] is False
    assert fakes.groq_prompt == fakes.gemini_prompt                  # same prompt/resume text as Gemini


def test_gemini_429_and_timeout_retry_then_fall_back():
    for transient in (_api_error(429, "RESOURCE_EXHAUSTED"), httpx.ReadTimeout("t")):
        fakes = Fakes([transient], GROQ_RESULT)
        db = _db()
        analysis = _run(db, _upload(db, "g3.pdf"))
        assert fakes.gemini_calls == 3 and fakes.groq_calls == 1 and analysis.summary == "groq summary"


def test_gemini_429_recovering_on_retry_does_not_use_groq():
    fakes = Fakes([_api_error(429, "RESOURCE_EXHAUSTED"), GEMINI_RESULT], GROQ_RESULT)
    db = _db()
    analysis = _run(db, _upload(db, "g3b.pdf"))
    assert fakes.gemini_calls == 2 and fakes.groq_calls == 0 and analysis.summary == "gemini summary"


def test_permanent_gemini_errors_do_not_trigger_groq():
    for code, status in ((400, "INVALID_ARGUMENT"), (403, "PERMISSION_DENIED")):
        fakes = Fakes([_api_error(code, status)], GROQ_RESULT)
        db = _db()
        try:
            _run(db, _upload(db, "g4.pdf"))
            raise AssertionError("expected an error")
        except HTTPException as exc:
            assert exc.status_code == 500 and "RAW-PROVIDER-BODY" not in exc.detail
        assert fakes.gemini_calls == 1 and fakes.groq_calls == 0


def test_both_providers_failing_returns_clean_error_and_keeps_active_resume():
    db = _db()
    a = _upload(db, "a5.pdf")
    Fakes([GEMINI_RESULT], GROQ_RESULT)
    _run(db, a)

    fakes = Fakes([_api_error(503, "UNAVAILABLE")], RuntimeError("groq exploded: SECRET-GROQ-BODY"))
    b = _upload(db, "b5.pdf")
    try:
        _run(db, b)
        raise AssertionError("expected a 503")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert "temporarily unavailable" in exc.detail
        assert "RAW-PROVIDER-BODY" not in exc.detail and "SECRET-GROQ-BODY" not in exc.detail
    assert fakes.groq_calls == 1
    assert get_active_resume(db, UID).id == a.id                     # the previous valid analysis stays active
    assert db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == b.id).first() is None


def test_groq_fallback_success_makes_the_new_resume_active_and_matches_gemini_shape():
    db = _db()
    a = _upload(db, "a6.pdf")
    Fakes([GEMINI_RESULT], GROQ_RESULT)
    gemini_analysis = _run(db, a)

    Fakes([_api_error(503, "UNAVAILABLE")], GROQ_RESULT)
    b = _upload(db, "b6.pdf")
    groq_analysis = _run(db, b)

    assert get_active_resume(db, UID).id == b.id                     # successful Groq analysis makes B active
    assert db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == a.id).first() is not None   # A kept as history

    g, q = routes.format_analysis(gemini_analysis), routes.format_analysis(groq_analysis)
    assert set(g.keys()) == set(q.keys())                            # identical downstream payload (profile prefill uses these fields)
    for field in ("skills", "job_roles", "education", "experience"):
        assert isinstance(q[field], list)
    assert q["experience_level"] == "Entry Level" and q["summary"] == "groq summary"


def test_groq_output_with_missing_lists_is_normalized():
    Fakes([_api_error(503, "UNAVAILABLE")], {"summary": "only a summary"})   # missing lists are normalized, not fatal
    db = _db()
    analysis = _run(db, _upload(db, "g8.pdf"))
    assert json.loads(analysis.skills) == []


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

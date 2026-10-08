"""Admin console LLM usage tab: totals come from the usage log, Groq limits from its own headers, Gemini from the
SDK hook. Isolated in-memory DB; provider probes are stubbed so nothing real is called."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
from backend.app.api import deps
from backend.app.api.routes import admin
from backend.app.models.llm_usage_log import LlmUsageLog
from backend.app.services import llm_usage


def _client(monkeypatch):

    import backend.app.main  # noqa: F401 (registers every model)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    app = FastAPI()
    app.include_router(admin.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_admin] = lambda: SimpleNamespace(id=1, role="admin")

    monkeypatch.setattr(admin, "_probe_groq_limits", lambda: None)
    monkeypatch.setattr(admin, "_check_gemini_key", lambda: {"key_valid": True, "detail": "ok"})
    admin._probe_state.update(groq_at=0.0, gemini_at=0.0, gemini=None)

    with llm_usage._lock:
        llm_usage._groq_limits.clear()

    return TestClient(app), Session


def _row(provider, model, **kw):
    return LlmUsageLog(provider=provider, model=model, created_at=kw.pop("created_at", datetime.utcnow()), **kw)


def test_usage_totals_split_by_provider_model_and_errors(monkeypatch):

    client, Session = _client(monkeypatch)

    db = Session()
    db.add_all([
        _row("groq", "openai/gpt-oss-120b", ok=True, prompt_tokens=1000, output_tokens=200, seconds=1.0),
        _row("groq", "openai/gpt-oss-120b", ok=False, rate_limited=True, seconds=0.5),
        _row("gemini", "gemini-3.8-flash", ok=True, prompt_tokens=50, output_tokens=25, seconds=2.0),
        _row("groq", "openai/gpt-oss-120b", ok=True, prompt_tokens=7, output_tokens=3, created_at=datetime.utcnow() - timedelta(days=3)),
    ])
    db.commit()
    db.close()

    data = client.get("/api/admin/llm-usage").json()

    groq, gemini = data["providers"]["groq"], data["providers"]["gemini"]
    assert groq["today"]["requests"] == 2 and groq["today"]["errors"] == 1 and groq["today"]["rate_limited"] == 1
    assert groq["today"]["total_tokens"] == 1200
    assert groq["last_7_days"]["requests"] == 3 and groq["last_7_days"]["total_tokens"] == 1210
    assert gemini["today"]["requests"] == 1 and gemini["today"]["total_tokens"] == 75
    assert gemini["key_check"]["key_valid"] is True
    assert len(data["history"]) == 7 and data["history"][-1]["groq"]["requests"] == 2


def test_groq_live_limits_come_from_the_response_headers(monkeypatch):

    client, Session = _client(monkeypatch)

    llm_usage.note_groq_limits({
        "x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "958", "x-ratelimit-reset-requests": "2m",
        "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7500", "x-ratelimit-reset-tokens": "4s",
    })

    limits = client.get("/api/admin/llm-usage").json()["providers"]["groq"]["live_limits"]
    assert limits["requests_remaining"] == "958" and limits["tokens_limit"] == "8000"
    assert limits["source"] == "last_call"


def test_headers_without_any_ratelimit_fields_do_not_wipe_known_limits():

    llm_usage.note_groq_limits({"x-ratelimit-limit-requests": "10", "x-ratelimit-remaining-requests": "9"})
    llm_usage.note_groq_limits({"content-type": "application/json"})
    assert llm_usage.groq_limits()["requests_remaining"] == "9"


def test_gemini_hook_records_tokens_and_marks_rate_limits(monkeypatch):

    recorded = []
    monkeypatch.setattr(llm_usage, "record_usage", lambda provider, model="", **kw: recorded.append((provider, model, kw)))

    from google.genai import models as genai_models

    calls = {"n": 0}

    def fake(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
        return SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=11, candidates_token_count=4, thoughts_token_count=2))

    monkeypatch.setattr(genai_models.Models, "generate_content", fake)
    monkeypatch.setattr(llm_usage, "_hook_installed", False)

    assert llm_usage.install_gemini_usage_hook() is True

    genai_models.Models.generate_content(object(), model="gemini-x", contents="hi")

    try:
        genai_models.Models.generate_content(object(), model="gemini-x", contents="hi")
    except RuntimeError:
        pass

    assert recorded[0][2]["prompt_tokens"] == 11 and recorded[0][2]["output_tokens"] == 6 and recorded[0][2]["ok"] is True
    assert recorded[1][2]["ok"] is False and recorded[1][2]["rate_limited"] is True


def test_gemini_plan_estimates_interviews_left_from_entered_limits_and_measured_usage(monkeypatch):

    client, Session = _client(monkeypatch)
    monkeypatch.setenv("GEMINI_LIMIT_LIVE_SESSIONS_PER_DAY", "10")
    monkeypatch.setenv("GEMINI_LIMIT_TOKENS_PER_DAY", "100000")
    monkeypatch.setenv("GEMINI_LIMIT_REQUESTS_PER_DAY", "250")

    db = Session()
    db.add_all([
        _row("gemini", "gemini-3.8-live", ok=True, prompt_tokens=20000, output_tokens=10000, seconds=900),   # 30k tokens, 15 min
        _row("gemini", "gemini-3.8-live", ok=True, prompt_tokens=20000, output_tokens=10000, seconds=900),
        _row("gemini", "gemini-3.6-flash", ok=True, prompt_tokens=100, output_tokens=50, seconds=1),
    ])
    db.commit()
    db.close()

    plan = client.get("/api/admin/llm-usage").json()["providers"]["gemini"]["plan"]

    assert plan["used_today"]["live_sessions"] == 2 and plan["used_today"]["text_requests"] == 1
    assert plan["live"]["last_7_days"]["avg_tokens_per_session"] == 30000 and plan["live"]["last_7_days"]["avg_minutes_per_session"] == 15.0
    # sessions: 10 - 2 = 8 left; tokens: (100000 - 60150) // 30000 = 1 left -> the tighter limit wins
    assert plan["interviews_left"]["interviews"] == 1 and "Tokens per day" in plan["interviews_left"]["basis"]


def test_gemini_plan_has_no_estimate_until_limits_are_entered(monkeypatch):

    client, _ = _client(monkeypatch)
    for key in ("GEMINI_LIMIT_LIVE_SESSIONS_PER_DAY", "GEMINI_LIMIT_TOKENS_PER_DAY", "GEMINI_LIMIT_REQUESTS_PER_DAY"):
        monkeypatch.delenv(key, raising=False)

    plan = client.get("/api/admin/llm-usage").json()["providers"]["gemini"]["plan"]
    assert plan["interviews_left"] is None and plan["limits"]["requests_per_day"] is None


def test_google_refusal_wording_is_kept_for_the_admin(monkeypatch):

    llm_usage._last_gemini_limit.clear()
    llm_usage.note_gemini_error(RuntimeError("429 RESOURCE_EXHAUSTED: GenerateRequestsPerDay limit: 20"), "gemini-x")
    llm_usage.note_gemini_error(RuntimeError("500 internal"), "gemini-x")                 # not a quota error: ignored

    kept = llm_usage.last_gemini_limit()
    assert "GenerateRequestsPerDay" in kept["message"] and kept["model"] == "gemini-x"


def test_live_connection_records_one_usage_row_with_the_latest_token_counts(monkeypatch):

    from backend.app.services.assessment import realtime_interview_v2 as rt

    recorded = []
    monkeypatch.setattr(llm_usage, "record_usage", lambda provider, model="", **kw: recorded.append((provider, model, kw)))

    conn = rt.GoogleLiveConnection(session=None, cm=SimpleNamespace(__aexit__=lambda *a: _noop()))
    conn._usage.update(prompt=1200, output=800)
    conn._record_usage_once()
    conn._record_usage_once()                                                              # closing twice records once

    assert len(recorded) == 1 and recorded[0][0] == "gemini"
    assert recorded[0][2]["prompt_tokens"] == 1200 and recorded[0][2]["output_tokens"] == 800


async def _noop():
    return None


def test_simultaneous_interviews_come_from_the_per_minute_limit_and_measured_rate(monkeypatch):

    client, Session = _client(monkeypatch)
    monkeypatch.setenv("GEMINI_LIMIT_TOKENS_PER_MINUTE", "65000")
    for key in ("GEMINI_LIMIT_LIVE_SESSIONS_PER_DAY", "GEMINI_LIMIT_TOKENS_PER_DAY"):
        monkeypatch.delenv(key, raising=False)

    db = Session()
    db.add(_row("gemini", "gemini-3.8-live", ok=True, prompt_tokens=60000, output_tokens=6000, seconds=900))      # 66k tokens in 15 min = 4,400 / min
    db.commit()
    db.close()

    plan = client.get("/api/admin/llm-usage").json()["providers"]["gemini"]["plan"]
    assert plan["tokens_per_minute_per_interview"] == 4400
    assert plan["simultaneous_interviews"] == 14

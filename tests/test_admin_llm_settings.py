"""Admin console model controls: validation, test-before-save, runtime apply, reset. Provider probes are stubbed."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database.database import Base, get_db
from backend.app.api import deps
from backend.app.api.routes import admin
from backend.app.models.admin_setting import AdminSetting
from backend.app.services import admin_settings


@pytest.fixture
def env(monkeypatch):

    import backend.app.main  # noqa: F401 (registers every model)

    monkeypatch.setenv("AI_INTERVIEW_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("AI_INTERVIEW_PROVIDER", "groq")
    monkeypatch.setenv("AI_REALTIME_MODEL", "gemini-3.8-live")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    for key in ("GEMINI_TEXT_MODEL", "AI_INTERVIEW_MAX_TOKENS", "AI_INTERVIEW_REASONING_EFFORT"):
        monkeypatch.delenv(key, raising=False)
    admin_settings._original_env.clear()

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
    app.dependency_overrides[deps.get_current_admin] = lambda: SimpleNamespace(id=7, role="admin")
    monkeypatch.setattr(admin, "require_csrf", lambda request: None)
    monkeypatch.setattr(admin, "_list_available_models", lambda: (["openai/gpt-oss-120b"], ["gemini-3.6-flash"]))
    monkeypatch.setattr(admin, "_test_groq_model", lambda model: None)
    monkeypatch.setattr(admin, "_test_gemini_models", lambda names: None)

    return TestClient(app), Session


def test_listing_shows_effective_values_defaults_and_available_models(env):
    client, _ = env
    data = client.get("/api/admin/llm-settings").json()
    by_key = {s["key"]: s for s in data["settings"]}
    assert by_key["AI_INTERVIEW_MODEL"]["value"] == "openai/gpt-oss-120b"
    assert by_key["AI_INTERVIEW_MODEL"]["overridden"] is False
    assert data["available"]["groq"] == ["openai/gpt-oss-120b"]
    assert {"security", "groq", "gemini", "limits"} == {s["group"] for s in data["settings"]}


def test_saving_a_groq_model_applies_immediately_and_survives_a_restart(env):
    client, Session = env

    assert client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_MODEL", "value": "llama-3.3-70b-versatile"}).status_code == 200
    assert os.environ["AI_INTERVIEW_MODEL"] == "llama-3.3-70b-versatile"

    from backend.app.services.assessment.interview_llm_provider import HostedChatProvider
    assert HostedChatProvider("groq").model == "llama-3.3-70b-versatile"          # the very next request uses it

    db = Session()
    assert db.query(AdminSetting).filter(AdminSetting.key == "AI_INTERVIEW_MODEL").first().updated_by_admin_id == 7

    os.environ["AI_INTERVIEW_MODEL"] = "something-else"                          # a fresh process would start from .env...
    admin_settings.apply_all(db)                                                 # ...then startup re-applies the saved choice
    assert os.environ["AI_INTERVIEW_MODEL"] == "llama-3.3-70b-versatile"
    db.close()


def test_reset_restores_the_value_the_server_started_with(env):
    client, _ = env
    client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_MODEL", "value": "other-model"})
    client.put("/api/admin/llm-settings", json={"key": "GEMINI_TEXT_MODEL", "value": "gemini-3.6-flash"})

    assert client.post("/api/admin/llm-settings/reset", json={"key": "AI_INTERVIEW_MODEL"}).status_code == 200
    assert os.environ["AI_INTERVIEW_MODEL"] == "openai/gpt-oss-120b"

    client.post("/api/admin/llm-settings/reset", json={"key": "GEMINI_TEXT_MODEL"})
    assert "GEMINI_TEXT_MODEL" not in os.environ                                 # it was unset at startup, so it is unset again


def test_a_model_the_provider_rejects_is_not_saved(env, monkeypatch):
    client, Session = env
    monkeypatch.setattr(admin, "_test_groq_model", lambda model: "Groq rejected this model (HTTP 404): not found")

    response = client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_MODEL", "value": "no-such-model"})
    assert response.status_code == 400 and "rejected" in response.json()["detail"]
    assert os.environ["AI_INTERVIEW_MODEL"] == "openai/gpt-oss-120b"
    db = Session()
    assert db.query(AdminSetting).count() == 0
    db.close()

    assert client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_MODEL", "value": "no-such-model", "skip_test": True}).status_code == 200


def test_only_whitelisted_keys_and_valid_shapes_can_be_set(env):
    client, _ = env
    for key, value in [
        ("GROQ_API_KEY", "stolen"), ("GEMINI_API_KEY", "x"), ("PATH", "/bin"), ("DATABASE_URL", "x"),   # secrets / unrelated env
        ("AI_INTERVIEW_REASONING_EFFORT", "extreme"), ("AI_INTERVIEW_MAX_TOKENS", "999999"), ("AI_INTERVIEW_MAX_TOKENS", "abc"),
        ("AI_INTERVIEW_PROVIDER", "openai"), ("AI_INTERVIEW_MODEL", ""), ("AI_INTERVIEW_MODEL", "bad model; rm -rf"),
        ("AI_REALTIME_VOICE", "NotAVoice"),
    ]:
        assert client.put("/api/admin/llm-settings", json={"key": key, "value": value}).status_code == 400, (key, value)
    assert os.environ["GROQ_API_KEY"] == "x"
    assert client.post("/api/admin/llm-settings/reset", json={"key": "GROQ_API_KEY"}).status_code == 400


def test_provider_switch_needs_that_providers_key(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("GEMINI_API_KEY")
    assert client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_PROVIDER", "value": "gemini"}).status_code == 400
    assert client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_PROVIDER", "value": "groq"}).status_code == 200


def test_gemini_text_model_setting_reaches_the_gemini_client(env):
    client, _ = env
    from backend.app.services.assessment.gemini_client import gemini_text_model, GEMINI_MODEL
    assert gemini_text_model() == GEMINI_MODEL
    client.put("/api/admin/llm-settings", json={"key": "GEMINI_TEXT_MODEL", "value": "gemini-3.7-flash"})
    assert gemini_text_model() == "gemini-3.7-flash"
    client.put("/api/admin/llm-settings", json={"key": "AI_INTERVIEW_GEMINI_MODELS", "value": "a-model, b-model"})
    assert os.environ["AI_INTERVIEW_GEMINI_MODELS"] == "a-model,b-model"

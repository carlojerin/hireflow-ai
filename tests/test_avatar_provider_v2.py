"""
Deterministic tests for the AI Interview V2 avatar layer (Task 5K).
No live LiveKit/Tavus/Anam account is required or contacted --
`generate_avatar_room_token_v2` produces its JWT entirely offline
(the `livekit-api` package signs locally with the configured API
key/secret), so ownership/scoping/expiry are all genuinely testable
without any external service. Uses monkeypatch to set/clear the
relevant environment variables around each test rather than
depending on the real .env file's current state.

Covers (Task 5K Step 18):
  1/6. Avatar provider abstraction / correct config construction
  2. Provider configuration validation
  3. Missing API key (LiveKit)
  4. Missing avatar ID
  5. Stable avatar ID selection (same config, called twice, identical)
  12/13. Failure handling (AvatarConfigError, never a fabricated token)
  15. No interview-brain modification (structural: this module imports
      nothing from interview_brain_v2 / interview_integration_v2)
  17. No API secrets exposed (token dict never contains the API secret)
  21-25 (LiveKit-specific, since LiveKit WAS introduced for tokens):
      token generation, room identity isolation, correct candidate
      authorization scoping, token expiry (TTL present + reasonable).
"""

import sys
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.assessment import avatar_provider_v2 as provider
from backend.app.services.assessment.avatar_token_service_v2 import (
    generate_avatar_room_token_v2,
    AVATAR_ROOM_TOKEN_TTL_MINUTES,
)


class _FakeApplication:
    def __init__(self, id_, user_id):
        self.id = id_
        self.user_id = user_id


def _set_full_config(avatar_id="face_abc123"):
    import os
    os.environ["LIVEKIT_URL"] = "wss://jobpilot-test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "test_api_key"
    os.environ["LIVEKIT_API_SECRET"] = "test_api_secret_at_least_32_characters_long"
    os.environ["JOBPILOT_AI_INTERVIEWER_AVATAR_ID"] = avatar_id


def _clear_config():
    import os
    for key in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "JOBPILOT_AI_INTERVIEWER_AVATAR_ID", "AVATAR_PROVIDER",
        "JOBPILOT_AI_INTERVIEWER_DISPLAY_NAME",
    ):
        os.environ.pop(key, None)


# 1/6: provider abstraction -- correct config construction when fully set
def test_get_avatar_config_returns_stable_identity_when_configured():

    _clear_config()
    _set_full_config()

    config = provider.get_avatar_config()

    assert config.avatar_id == "face_abc123"
    assert config.provider == provider.DEFAULT_AVATAR_PROVIDER
    assert config.display_name == provider.DEFAULT_DISPLAY_NAME

    _clear_config()


# 2/3: missing LiveKit credential -> clear config validation error
def test_get_livekit_config_reports_exact_missing_variable():

    _clear_config()
    import os
    os.environ["LIVEKIT_URL"] = "wss://jobpilot-test.livekit.cloud"
    # API key/secret deliberately left unset

    try:
        provider.get_livekit_config()
        assert False, "expected AvatarConfigError"
    except provider.AvatarConfigError as exc:
        assert "LIVEKIT_API_KEY" in str(exc)
        assert "LIVEKIT_API_SECRET" in str(exc)

    _clear_config()


# 4: missing avatar ID -> never falls back to a fabricated/random identity
def test_get_avatar_config_raises_when_avatar_id_missing():

    _clear_config()

    try:
        provider.get_avatar_config()
        assert False, "expected AvatarConfigError"
    except provider.AvatarConfigError as exc:
        assert provider.ENV_AVATAR_ID in str(exc)


# 5: stable avatar ID selection -- calling twice returns the identical identity
def test_avatar_config_is_stable_across_repeated_calls():

    _clear_config()
    _set_full_config()

    first = provider.get_avatar_config()
    second = provider.get_avatar_config()

    assert first.avatar_id == second.avatar_id == "face_abc123"
    assert first.provider == second.provider

    _clear_config()


# is_avatar_configured() honest check
def test_is_avatar_configured_false_when_nothing_set():
    _clear_config()
    assert provider.is_avatar_configured() is False


def test_is_avatar_configured_true_when_fully_set():
    _clear_config()
    _set_full_config()
    assert provider.is_avatar_configured() is True
    _clear_config()


# 12/13: token generation failure handling -- never a fabricated token
def test_generate_token_raises_when_not_configured():

    _clear_config()

    application = _FakeApplication(id_=99, user_id=30)

    try:
        generate_avatar_room_token_v2(application)
        assert False, "expected AvatarConfigError"
    except provider.AvatarConfigError:
        pass


# 21: real token generation (offline JWT signing, no network)
def test_generate_token_produces_a_valid_scoped_jwt():

    _clear_config()
    _set_full_config()

    application = _FakeApplication(id_=37, user_id=30)

    result = generate_avatar_room_token_v2(application)

    assert result["url"] == "wss://jobpilot-test.livekit.cloud"
    assert result["room_name"] == "jobpilot-interview-v2-37"
    assert result["identity"] == "candidate-30"
    assert result["avatar"]["display_name"] == provider.DEFAULT_DISPLAY_NAME

    decoded = jwt.decode(result["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])
    assert decoded["sub"] == "candidate-30"
    assert decoded["video"]["room"] == "jobpilot-interview-v2-37"
    assert decoded["video"]["roomJoin"] is True

    _clear_config()


# 17: API secret is NEVER present anywhere in the returned dict
def test_generate_token_never_exposes_the_api_secret():

    _clear_config()
    _set_full_config()

    application = _FakeApplication(id_=37, user_id=30)
    result = generate_avatar_room_token_v2(application)

    serialized = str(result)
    assert "test_api_secret_at_least_32_characters_long" not in serialized

    _clear_config()


# 25/24: room identity isolation -- two different applications get
# two different, non-overlapping rooms and identities
def test_two_different_applications_get_isolated_rooms():

    _clear_config()
    _set_full_config()

    app_a = _FakeApplication(id_=36, user_id=30)
    app_b = _FakeApplication(id_=37, user_id=31)

    token_a = generate_avatar_room_token_v2(app_a)
    token_b = generate_avatar_room_token_v2(app_b)

    assert token_a["room_name"] != token_b["room_name"]
    assert token_a["identity"] != token_b["identity"]

    decoded_a = jwt.decode(token_a["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])
    decoded_b = jwt.decode(token_b["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])

    # A's token grants access to ONLY A's room, never B's
    assert decoded_a["video"]["room"] == "jobpilot-interview-v2-36"
    assert decoded_a["video"]["room"] != decoded_b["video"]["room"]

    _clear_config()


# 26 (expiry): token TTL is present and bounded, never unlimited
def test_generate_token_has_a_bounded_expiry():

    _clear_config()
    _set_full_config()

    application = _FakeApplication(id_=37, user_id=30)
    result = generate_avatar_room_token_v2(application)

    decoded = jwt.decode(result["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])

    assert "exp" in decoded
    assert "nbf" in decoded
    assert decoded["exp"] - decoded["nbf"] == AVATAR_ROOM_TOKEN_TTL_MINUTES * 60

    _clear_config()


# 15: no interview-brain modification -- structural guarantee
def test_avatar_modules_never_import_the_interview_brain():

    import ast

    for module_path in (
        "backend/app/services/assessment/avatar_provider_v2.py",
        "backend/app/services/assessment/avatar_token_service_v2.py",
        "backend/app/api/routes/interview_avatar_v2.py",
    ):

        source = (Path(__file__).resolve().parents[1] / module_path).read_text()
        tree = ast.parse(source)

        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
            elif isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)

        forbidden = [n for n in imported_names if "interview_brain_v2" in n or "interview_integration_v2" in n]
        assert not forbidden, f"{module_path} must never import the interview brain, found: {forbidden}"

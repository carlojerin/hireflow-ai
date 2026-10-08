"""
Task 5: LiveKit transport foundation -- server side.

The REAL token route + REAL token service run against an isolated in-memory
database (TestClient, dependency overrides for db/identity). JWTs are decoded
offline. The LiveKit SDK used by the audio publisher is faked here; the real
network verification is a separate foreground run. No jobpilot.db, no Gemini.
"""

import asyncio
import io
import json
import os
import sys
import types
import wave

import jwt
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_voice_turn_local_pipeline_v2 as VT                      # reuse the isolated interview fixture + fakes

from backend.app.api.deps import get_current_user
from backend.app.api.routes import interview_avatar_v2 as avatar_route
from backend.app.api.routes import interview_voice_v2 as voice_route
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.services.assessment import avatar_token_service_v2 as tokens
from backend.app.services.assessment import interview_voice_integration_v2 as bridge
from backend.app.services.assessment import livekit_audio_transport_v2 as transport
from backend.app.services.assessment.voice_provider_v2 import VoiceProviderError

SECRET = "test_api_secret_at_least_32_characters_long"
ENV_KEYS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "JOBPILOT_AI_INTERVIEWER_AVATAR_ID", "AVATAR_PROVIDER")


def _configure(livekit=True, avatar=True):
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    if livekit:
        os.environ["LIVEKIT_URL"] = "wss://jobpilot-test.livekit.cloud"
        os.environ["LIVEKIT_API_KEY"] = "test_api_key"
        os.environ["LIVEKIT_API_SECRET"] = SECRET
    if avatar:
        os.environ["JOBPILOT_AI_INTERVIEWER_AVATAR_ID"] = "face_abc123"


def _decode(token):
    return jwt.decode(token, SECRET, algorithms=["HS256"])


def _client(db, user_holder, real_identity=False):
    api = FastAPI()
    api.include_router(avatar_route.router)
    api.include_router(voice_route.router)
    api.dependency_overrides[get_db] = lambda: db
    if not real_identity:
        api.dependency_overrides[get_current_user] = lambda: user_holder["user"]
    return TestClient(api, cookies={"csrf_token": "t"})


def _fixture():
    db = VT._db(shared=True)
    user, application, session = VT._interview(db)
    stranger = User(full_name="Other", username="stranger5", email="stranger5@example.com", hashed_password="x", role="user")
    db.add(stranger); db.commit()
    return db, user, application, session, stranger


H = {"X-CSRF-Token": "t"}


def _url(app_id):
    return f"/api/assessment/sessions/{app_id}/interview/v2/avatar/token"


# ------------------------------------------------------------ token: ownership
def test_candidate_gets_a_token_for_their_own_active_interview_only():
    _configure()
    db, user, app, session, stranger = _fixture()
    c = _client(db, {"user": user})
    r = c.post(_url(app.id), headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["room_name"] == f"jobpilot-interview-v2-{app.id}" and body["identity"] == f"candidate-{user.id}"
    claims = _decode(body["token"])
    assert claims["sub"] == f"candidate-{user.id}" and claims["video"]["room"] == body["room_name"]


def test_another_candidate_cannot_obtain_a_token_for_this_application():
    _configure()
    db, user, app, session, stranger = _fixture()
    c = _client(db, {"user": stranger})
    assert c.post(_url(app.id), headers=H).status_code == 404               # indistinguishable from "does not exist"
    assert c.post(_url(99999), headers=H).status_code == 404


def test_company_and_anonymous_callers_cannot_obtain_a_token():
    _configure()
    db, user, app, session, stranger = _fixture()
    c = _client(db, {"user": user}, real_identity=True)                     # the REAL get_current_user
    assert c.post(_url(app.id), headers=H).status_code == 401              # no session at all
    c.cookies.set("jobpilot_company_session", "any-company-session-value")
    assert c.post(_url(app.id), headers=H).status_code == 401              # a company cookie is not a candidate session


def test_csrf_is_still_enforced_on_the_token_route():
    _configure()
    db, user, app, session, stranger = _fixture()
    c = _client(db, {"user": user})
    assert c.post(_url(app.id)).status_code == 403


def test_no_token_unless_the_interview_is_active():
    _configure()
    db, user, app, session, stranger = _fixture()
    c = _client(db, {"user": user})
    for status in ("completed", "expired"):
        session.status = status; db.commit()
        r = c.post(_url(app.id), headers=H)
        assert r.status_code == 409 and "token" not in r.text.lower().replace("no active", ""), status
    db.delete(session); db.commit()
    assert c.post(_url(app.id), headers=H).status_code == 409             # never started: no room to join


# ------------------------------------------------------------ token: permissions
def test_candidate_token_grants_only_what_an_interview_needs():
    _configure()
    result = tokens.generate_avatar_room_token_v2(types.SimpleNamespace(id=37, user_id=30))
    video = _decode(result["token"])["video"]
    assert video["roomJoin"] is True and video["room"] == "jobpilot-interview-v2-37"
    assert video["canPublish"] is True and video["canSubscribe"] is True and video["canPublishData"] is True
    assert video["canPublishSources"] == ["microphone", "camera"]           # never screen share / raw sources
    for forbidden in ("roomCreate", "roomList", "roomAdmin", "roomRecord", "ingressAdmin", "hidden", "recorder", "agent"):
        assert not video.get(forbidden), forbidden                          # no server/admin power


def test_token_is_short_lived_bound_to_one_identity_and_never_leaks_the_secret():
    _configure()
    result = tokens.generate_avatar_room_token_v2(types.SimpleNamespace(id=37, user_id=30))
    claims = _decode(result["token"])
    assert claims["exp"] - claims["nbf"] == tokens.AVATAR_ROOM_TOKEN_TTL_MINUTES * 60 == 1800
    assert SECRET not in json.dumps(result) and "test_api_key" not in json.dumps(result).replace(claims.get("iss", ""), "")


def test_room_name_is_one_room_per_application_and_never_client_chosen():
    _configure()
    a = tokens.generate_avatar_room_token_v2(types.SimpleNamespace(id=36, user_id=30))
    a2 = tokens.generate_avatar_room_token_v2(types.SimpleNamespace(id=36, user_id=30))
    b = tokens.generate_avatar_room_token_v2(types.SimpleNamespace(id=37, user_id=31))
    assert a["room_name"] == a2["room_name"] == "jobpilot-interview-v2-36" and a["room_name"] != b["room_name"]
    import inspect
    assert list(inspect.signature(tokens.generate_avatar_room_token_v2).parameters) == ["application"]   # no room/identity parameter
    db, user, app, session, stranger = _fixture()
    r = _client(db, {"user": user}).post(_url(app.id) + "?room=someone-elses-room&identity=admin", headers=H)
    assert r.json()["room_name"] == f"jobpilot-interview-v2-{app.id}" and r.json()["identity"] == f"candidate-{user.id}"


def test_audio_publisher_token_is_a_least_privilege_microphone_publisher():
    _configure(avatar=False)                                                # needs LiveKit only, not the avatar identity
    result = tokens.generate_audio_publisher_token_v2(37)
    claims = _decode(result["token"])
    video = claims["video"]
    assert result["room_name"] == "jobpilot-interview-v2-37" == video["room"]           # the SAME room naming
    assert result["identity"] == claims["sub"] == "jobpilot-tts-37"
    assert video["roomJoin"] is True and video["canPublish"] is True
    assert video["canPublishSources"] == ["microphone"]
    assert not video.get("canSubscribe") and video.get("canPublishData") is True     # cannot hear the candidate; data only for turn-boundary notices
    assert not any(video.get(k) for k in ("roomCreate", "roomList", "roomAdmin", "roomRecord", "hidden", "recorder", "agent"))
    assert claims["exp"] - claims["nbf"] == 300
    assert claims.get("kind") == "agent"                                                 # Task 7: required for Tavus to accept its audio


# ------------------------------------------------------------ failures
def test_livekit_not_configured_is_a_clean_503_without_leaking_anything():
    _configure(livekit=False)
    db, user, app, session, stranger = _fixture()
    r = _client(db, {"user": user}).post(_url(app.id), headers=H)
    assert r.status_code == 503 and "LIVEKIT_URL" in r.json()["detail"]
    assert SECRET not in r.text and "eyJ" not in r.text                     # no key material, no JWT


def test_unconfigured_avatar_keeps_its_existing_honest_503():
    _configure(avatar=False)
    db, user, app, session, stranger = _fixture()
    assert _client(db, {"user": user}).post(_url(app.id), headers=H).status_code == 503


# ------------------------------------------------------------ the local flow is untouched
def test_local_voice_turn_still_works_with_livekit_completely_unconfigured():
    _configure(livekit=False, avatar=False)
    db, user, app, session, stranger = _fixture()
    fake_stt, fake_tts = VT.FakeStt(), VT.FakeTts()
    saved = (bridge.get_stt_provider, bridge.get_tts_provider)
    bridge.get_stt_provider, bridge.get_tts_provider = (lambda: fake_stt), (lambda: fake_tts)
    try:
        c = _client(db, {"user": user})
        with VT._isolated(VT.FakeGroq(session)):
            r = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/turn",
                       files={"audio": ("a.webm", b"candidate-audio", "audio/webm")}, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["providers"] == {"stt": "faster-whisper", "brain": "groq", "tts": "kokoro"}
        assert c.post(_url(app.id), headers=H).status_code == 503          # while LiveKit itself is unavailable
    finally:
        bridge.get_stt_provider, bridge.get_tts_provider = saved


def test_voice_turn_and_token_routes_are_independent_of_each_other():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "api", "routes", "interview_voice_v2.py"), encoding="utf-8").read()
    assert "avatar_token" not in src and "interview_avatar" not in src        # Task 6: LiveKit is used only via the transport module, never the token/avatar routes


# ------------------------------------------------------------ audio publisher (LiveKit SDK faked)
def _wav(seconds=0.5, rate=24000, channels=1, width=2):
    n = int(rate * seconds)
    pcm = (0.3 * np.sin(2 * np.pi * 440 * np.arange(n) / rate) * 32767).astype("<i2").tobytes()
    if channels == 2:
        pcm = np.repeat(np.frombuffer(pcm, dtype="<i2"), 2).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(width); w.setframerate(rate); w.writeframes(pcm if width == 2 else pcm[: n * channels * width])
    return buf.getvalue()


class FakeRtc:
    def __init__(self, connect_error=None, publish_error=None):
        self.log = {"connected": None, "published": [], "unpublished": [], "frames": [], "disconnects": 0, "playout": 0}
        outer = self

        class LocalParticipant:
            async def publish_track(self, track, options):
                if publish_error:
                    raise publish_error
                outer.log["published"].append((track, options))
                return types.SimpleNamespace(sid="TR_1")

            async def unpublish_track(self, sid):
                outer.log["unpublished"].append(sid)

        class Room:
            def __init__(self):
                self.local_participant = LocalParticipant()

            async def connect(self, url, token):
                if connect_error:
                    raise connect_error
                outer.log["connected"] = (url, token)

            async def disconnect(self):
                outer.log["disconnects"] += 1

        class AudioSource:
            def __init__(self, rate, channels):
                self.rate, self.channels = rate, channels

            async def capture_frame(self, frame):
                outer.log["frames"].append(bytes(frame.data))

            async def wait_for_playout(self):
                outer.log["playout"] += 1

        class AudioFrame:
            @staticmethod
            def create(rate, channels, samples):
                import array
                return types.SimpleNamespace(data=memoryview(array.array("h", [0] * (samples * channels))))   # the REAL type: an int16 memoryview

        self.module = types.SimpleNamespace(
            Room=Room, AudioSource=AudioSource, AudioFrame=AudioFrame,
            LocalAudioTrack=types.SimpleNamespace(create_audio_track=lambda name, source: types.SimpleNamespace(name=name, source=source)),
            TrackPublishOptions=lambda source: types.SimpleNamespace(source=source),
            TrackSource=types.SimpleNamespace(SOURCE_MICROPHONE="SOURCE_MICROPHONE"),
        )


def _publish(fake, wav=None):
    saved = transport.rtc
    transport.rtc = fake.module
    try:
        return asyncio.run(transport.publish_wav_audio("wss://x.livekit.cloud", "jwt-value", wav if wav is not None else _wav()))
    finally:
        transport.rtc = saved


def _expect(fn, contains):
    try:
        fn()
    except transport.AudioTransportError as exc:
        assert contains in str(exc).lower(), str(exc)
        return str(exc)
    raise AssertionError("expected AudioTransportError")


def test_wav_validation_rejects_bad_audio_before_any_connection():
    fake = FakeRtc()
    _expect(lambda: _publish(fake, b""), "no audio")
    _expect(lambda: _publish(fake, b"not a wav file at all"), "not a valid wav")
    _expect(lambda: _publish(fake, _wav(width=1)), "16-bit")
    _expect(lambda: _publish(fake, _wav(seconds=130)), "too long")
    assert fake.log["connected"] is None and fake.log["disconnects"] == 0


def test_audio_is_cut_into_10ms_frames_with_a_padded_tail():
    pcm, rate, channels = transport.decode_wav_pcm16(_wav(seconds=0.505))
    frames = list(transport._frames(pcm, rate, channels))
    assert rate == 24000 and channels == 1 and len(frames) == 51                  # 50 full + 1 padded
    assert all(len(b) == 480 and n == 240 for b, n in frames)                     # 10 ms = 240 samples = 480 bytes


def test_publish_carries_the_audio_as_a_microphone_track_then_cleans_up():
    fake = FakeRtc()
    metrics = _publish(fake)
    log = fake.log
    assert log["connected"] == ("wss://x.livekit.cloud", "jwt-value")
    (track, options), = log["published"]
    assert options.source == "SOURCE_MICROPHONE" and track.name == "jobpilot-ai-interviewer"
    assert len(log["frames"]) == 50 and log["playout"] == 1
    assert b"".join(log["frames"]) == transport.decode_wav_pcm16(_wav())[0]       # the exact PCM went out
    assert log["unpublished"] == ["TR_1"] and log["disconnects"] == 1
    assert metrics["audio_seconds"] == 0.5 and {"connect_seconds", "published_at_seconds", "played_out_at_seconds"} <= set(metrics)


def test_stereo_audio_keeps_its_channel_layout():
    fake = FakeRtc()
    _publish(fake, _wav(channels=2))
    assert all(len(f) == 240 * 2 * 2 for f in fake.log["frames"])


def test_connection_failure_is_a_safe_error_and_still_disconnects():
    fake = FakeRtc(connect_error=ConnectionError("dns fail for wss://secret-host token=jwt-value"))
    message = _expect(lambda: _publish(fake), "could not connect")
    assert "ConnectionError" in message and "jwt-value" not in message and "secret-host" not in message
    assert fake.log["frames"] == [] and fake.log["disconnects"] == 1


def test_publish_failure_is_a_safe_error_and_still_disconnects():
    fake = FakeRtc(publish_error=RuntimeError("permission denied jwt-value"))
    message = _expect(lambda: _publish(fake), "could not publish")
    assert "jwt-value" not in message
    assert fake.log["frames"] == [] and fake.log["disconnects"] == 1


def test_transport_module_never_touches_the_brain_tts_or_gemini():
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "services", "assessment", "livekit_audio_transport_v2.py"), encoding="utf-8").read()
    imports = [l.strip() for l in src.splitlines() if l.startswith(("import ", "from "))]
    assert imports, "no imports found"
    for line in imports:
        for banned in ("interview_brain", "interview_integration", "genai", "gemini", "kokoro", "voice_provider", "local_tts", "local_stt"):
            assert banned not in line.lower(), line                       # transport only: no brain, no TTS/STT, no Gemini

"""
Task 6: LiveKit carries the AI audio of the REAL voice-turn flow -- server side.

The real /voice/turn and /voice/speak and /voice/interrupt routes, the real
interview brain code and the real stable publisher run against an isolated
in-memory database; only the outermost model calls (STT, Groq HTTP, Kokoro)
and the LiveKit SDK are faked, so these tests prove wiring, once-only
synthesis/publishing, room/publisher reuse, interruption, reconnect, cleanup
and the local fallback -- not model quality or the network (the live run is
separate). No jobpilot.db, no Gemini.
"""

import asyncio
import json
import os
import sys
import time
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_voice_turn_local_pipeline_v2 as VT
import test_livekit_transport_v2 as LT

from backend.app.api.deps import get_current_user
from backend.app.api.routes import interview_voice_v2 as voice_route
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.services.assessment import interview_voice_integration_v2 as bridge
from backend.app.services.assessment import livekit_audio_transport_v2 as transport

H = {"X-CSRF-Token": "t"}


class FakeLiveKit:
    """The livekit.rtc surface the stable publisher uses."""

    def __init__(self, connect_error=None, playout_gate=False):
        self.rooms, self.sources, self.published, self.unpublished = [], [], [], []
        self.data, self.frames, self.cleared = [], [], 0
        self.connect_error, self.playout_gate = connect_error, playout_gate
        self.gate = None
        outer = self

        class LocalParticipant:
            async def publish_track(self, track, options):
                outer.published.append(track)
                return types.SimpleNamespace(sid=f"TR_{len(outer.published)}")

            async def unpublish_track(self, sid):
                outer.unpublished.append(sid)

            async def publish_data(self, payload, reliable=True, destination_identities=None, topic=""):
                outer.data.append((json.loads(payload), topic, reliable))

        class Room:
            def __init__(self):
                self.local_participant = LocalParticipant()
                self.handlers, self.disconnects = {}, 0
                outer.rooms.append(self)

            def on(self, name, handler):
                self.handlers[name] = handler

            async def connect(self, url, token):
                if outer.connect_error:
                    raise outer.connect_error

            async def disconnect(self):
                self.disconnects += 1

        class AudioSource:
            def __init__(self, rate, channels):
                outer.sources.append(self)

            async def capture_frame(self, frame):
                outer.frames.append(bytes(frame.data))
                await asyncio.sleep(0)

            def clear_queue(self):
                outer.cleared += 1

            async def wait_for_playout(self):
                if outer.playout_gate:
                    if outer.gate is None:
                        outer.gate = asyncio.Event()
                    await outer.gate.wait()

        class AudioFrame:
            @staticmethod
            def create(rate, channels, samples):
                import array
                return types.SimpleNamespace(data=memoryview(array.array("h", [0] * (samples * channels))))

        self.module = types.SimpleNamespace(
            Room=Room, AudioSource=AudioSource, AudioFrame=AudioFrame,
            LocalAudioTrack=types.SimpleNamespace(create_audio_track=lambda name, source: types.SimpleNamespace(name=name, source=source)),
            TrackPublishOptions=lambda source: types.SimpleNamespace(source=source),
            TrackSource=types.SimpleNamespace(SOURCE_MICROPHONE="SOURCE_MICROPHONE"),
        )

    def states(self, seq=None):
        return [(m["state"], m["seq"]) for m, topic, _ in self.data if seq is None or m["seq"] == seq]


class Env:
    """Fakes installed for one test; always restored."""

    def __init__(self, fake, livekit_configured=True):
        self.fake, self.configured = fake, livekit_configured

    def __enter__(self):
        LT._configure(livekit=self.configured, avatar=False)
        self.saved = (transport.rtc, bridge.get_stt_provider, bridge.get_tts_provider)
        transport.rtc = self.fake.module
        self.stt, self.tts = VT.FakeStt(), VT.FakeTts()
        bridge.get_stt_provider, bridge.get_tts_provider = (lambda: self.stt), (lambda: self.tts)
        transport._publishers.clear()
        return self

    def __exit__(self, *exc):
        transport.rtc, bridge.get_stt_provider, bridge.get_tts_provider = self.saved
        transport._publishers.clear()
        for k in LT.ENV_KEYS:
            os.environ.pop(k, None)


def _fixture():
    return LT._fixture()


def _client(db, holder):
    api = FastAPI()
    api.include_router(voice_route.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: holder["user"]
    return TestClient(api, cookies={"csrf_token": "t"})     # used as a context manager: ONE event loop for the whole test


def _turn_url(app_id):
    return f"/api/assessment/sessions/{app_id}/interview/v2/voice/turn"


def _post_turn(c, app_id, transport_value=None):
    kw = {"data": {"transport": transport_value}} if transport_value else {}
    return c.post(_turn_url(app_id), files={"audio": ("a.webm", b"candidate-audio", "audio/webm")}, headers=H, **kw)


def _settle():
    time.sleep(0.25)                                                        # let the publisher's background pump finish on the app loop


# ------------------------------------------------------------ mode selection
def test_default_and_unknown_transports_are_local_and_never_touch_livekit():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            r1 = _post_turn(c, app.id)
            r2 = _post_turn(c, app.id, "carrier-pigeon")
    for r in (r1, r2):
        body = r.json()
        assert r.status_code == 200 and body["audio_delivery"] == "local" and body["audio"]["base64"] and body["speech_seq"] is None
        assert body["livekit"] is None and body["livekit_error"] is None
    assert fake.rooms == [] and transport._publishers == {}


def test_livekit_mode_publishes_the_same_audio_once_and_returns_no_inline_audio():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), VT._gemini_tripwire() as gem, _client(db, {"user": user}) as c:
            r = _post_turn(c, app.id, "livekit")
            _settle()
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["audio_delivery"] == "livekit" and body["audio"] is None and body["speech_seq"] == 1
    assert body["providers"] == {"stt": "faster-whisper", "brain": "groq", "tts": "kokoro"} and body["response_text"]
    assert len(env.tts.texts) == 1                                          # Kokoro synthesized exactly once
    assert len(fake.published) == 1 and len(fake.rooms) == 1                # one track, one connection
    assert b"".join(fake.frames) == transport.decode_wav_pcm16(VT._wav())[0]  # the exact Kokoro PCM went out, once
    assert fake.states() == [("started", 1), ("ended", 1)] and all(t == "jobpilot.speech" for _, t, _r in fake.data)
    assert "livekit_first_frame_seconds" in body["latency"] and gem["n"] == 0


def test_two_turns_reuse_the_same_room_publisher_and_track():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        groq = VT.FakeGroq(session)
        with VT._isolated(groq), _client(db, {"user": user}) as c:
            r1 = _post_turn(c, app.id, "livekit")
            _settle()
            r2 = _post_turn(c, app.id, "livekit")
            _settle()
        publisher = transport.peek_publisher(app.id)
    assert (r1.json()["speech_seq"], r2.json()["speech_seq"]) == (1, 2)
    assert len(fake.rooms) == 1 and len(fake.published) == 1 and len(fake.sources) == 1     # no second room/track/source
    assert r1.json()["livekit"]["reused_connection"] is False and r2.json()["livekit"]["reused_connection"] is True
    assert r2.json()["livekit"]["reused_track"] is True
    assert publisher.stats == {"connects": 1, "tracks_published": 1, "speeches": 2}
    assert len(env.tts.texts) == 2                                          # one synthesis per turn, never two
    assert fake.states() == [("started", 1), ("ended", 1), ("started", 2), ("ended", 2)]


def test_publisher_uses_the_interview_room_identity_and_is_per_application():
    fake = FakeLiveKit()
    creds = []

    def factory(application_id):
        creds.append(application_id)
        return {"url": "wss://x", "token": "t", "room_name": f"jobpilot-interview-v2-{application_id}"}

    async def go():
        a = await transport.get_publisher(11, factory)
        a2 = await transport.get_publisher(11, factory)
        b = await transport.get_publisher(12, factory)
        return a, a2, b

    with Env(fake):
        a, a2, b = asyncio.run(go())
    assert a is a2 and a is not b                                            # one stable publisher per interview


# ------------------------------------------------------------ fallback
def test_livekit_not_configured_falls_back_to_local_audio_with_one_synthesis():
    fake = FakeLiveKit()
    with Env(fake, livekit_configured=False) as env:
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), VT._gemini_tripwire() as gem, _client(db, {"user": user}) as c:
            r = _post_turn(c, app.id, "livekit")
    body = r.json()
    assert r.status_code == 200 and body["audio_delivery"] == "local" and body["audio"]["base64"] and body["speech_seq"] is None
    assert body["livekit_error"] and "not configured" in body["livekit_error"].lower()
    assert len(env.tts.texts) == 1 and fake.rooms == [] and gem["n"] == 0
    assert body["providers"]["tts"] == "kokoro"                              # still local Kokoro -- never a different provider


def test_livekit_connect_failure_returns_the_same_audio_locally_without_leaking_details():
    fake = FakeLiveKit(connect_error=ConnectionError("dns fail wss://secret-host token=jwt-value"))
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), VT._gemini_tripwire() as gem, _client(db, {"user": user}) as c:
            r = _post_turn(c, app.id, "livekit")
    body = r.json()
    assert r.status_code == 200 and body["audio_delivery"] == "local" and body["audio"]["base64"]
    assert "ConnectionError" in body["livekit_error"] and "secret-host" not in r.text and "jwt-value" not in r.text
    assert len(env.tts.texts) == 1 and fake.published == [] and gem["n"] == 0
    assert body["transcript"] and body["response_text"]                      # the turn itself still succeeded


def test_livekit_mode_with_a_tts_failure_is_still_a_normal_text_only_turn():
    fake = FakeLiveKit()
    with Env(fake) as env:
        env.tts = VT.FakeTts(raises=RuntimeError("kokoro broke"))
        bridge.get_tts_provider = lambda: env.tts
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            r = _post_turn(c, app.id, "livekit")
    body = r.json()
    assert r.status_code == 200 and body["audio"] is None and body["tts_error"] and body["audio_delivery"] == "local"
    assert fake.rooms == [] and body["livekit_error"] is None               # nothing to publish, nothing claimed


def test_stt_failure_in_livekit_mode_publishes_nothing():
    fake = FakeLiveKit()
    with Env(fake) as env:
        env.stt = VT.FakeStt(raises=RuntimeError("stt broke"))
        bridge.get_stt_provider = lambda: env.stt
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            r = _post_turn(c, app.id, "livekit")
    assert r.status_code == 502 and r.json()["detail"]["stage"] == "stt"
    assert fake.rooms == [] and env.tts.texts == []


# ------------------------------------------------------------ interruption
def test_interrupt_stops_playout_clears_the_queue_and_announces_it():
    fake = FakeLiveKit(playout_gate=True)

    async def go():
        p = await transport.get_publisher(21, lambda a: {"url": "wss://x", "token": "t"})
        m = await p.speak(VT._wav(1.0))
        await asyncio.sleep(0.05)
        assert p.speaking
        was = await p.interrupt()
        again = await p.interrupt()
        return p, m, was, again

    with Env(fake):
        p, m, was, again = asyncio.run(go())
    assert was is True and again is False and not p.speaking
    assert fake.cleared == 1                                                 # queued audio dropped -> nothing stale plays
    assert fake.states() == [("started", 1), ("ended", 1)] and fake.data[-1][0]["interrupted"] is True


def test_a_new_response_replaces_one_that_is_still_playing_on_the_same_track():
    fake = FakeLiveKit(playout_gate=True)

    async def go():
        p = await transport.get_publisher(22, lambda a: {"url": "wss://x", "token": "t"})
        await p.speak(VT._wav(1.0))
        await asyncio.sleep(0.05)
        m2 = await p.speak(VT._wav(0.5))
        await asyncio.sleep(0.05)
        fake.gate.set()
        await asyncio.sleep(0.05)
        return p, m2

    with Env(fake):
        p, m2 = asyncio.run(go())
    assert m2["seq"] == 2 and len(fake.published) == 1 and len(fake.rooms) == 1
    assert fake.states(1) == [("started", 1), ("ended", 1)] and fake.data[[m["seq"] for m, _, _ in fake.data].index(1) + 1][0]["state"] == "ended"
    assert fake.states(2)[0] == ("started", 2) and fake.cleared >= 1


def test_interrupt_route_only_touches_the_callers_own_publisher_and_needs_csrf_and_ownership():
    fake = FakeLiveKit(playout_gate=True)
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        url = f"/api/assessment/sessions/{app.id}/interview/v2/voice/interrupt"
        with _client(db, {"user": user}) as c:
            assert c.post(url).status_code == 403                            # CSRF
            assert c.post(url, headers=H).json() == {"interrupted": False}   # nothing playing / no publisher
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": user}) as c:
            _post_turn(c, app.id, "livekit")
            time.sleep(0.15)
            assert c.post(url, headers=H).json() == {"interrupted": True}
            assert c.post(url, headers=H).json() == {"interrupted": False}
        with _client(db, {"user": stranger}) as other:
            assert other.post(url, headers=H).status_code == 404             # not their application
            assert other.post("/api/assessment/sessions/99999/interview/v2/voice/interrupt", headers=H).status_code == 404
    assert fake.cleared == 1 and fake.data[-1][0]["interrupted"] is True


def test_another_candidate_cannot_use_livekit_delivery_for_someone_elses_interview():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        with VT._isolated(VT.FakeGroq(session)), _client(db, {"user": stranger}) as c:
            assert _post_turn(c, app.id, "livekit").status_code == 404
            speak = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/speak?turn_number=1&transport=livekit", headers=H)
            assert speak.status_code == 404
    assert fake.rooms == [] and env.tts.texts == []


# ------------------------------------------------------------ speak route
def test_speak_route_livekit_and_local_modes():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        base = f"/api/assessment/sessions/{app.id}/interview/v2/voice/speak?turn_number=1"
        with _client(db, {"user": user}) as c:
            local = c.post(base, headers=H)
            assert local.status_code == 200 and local.headers["content-type"].startswith("audio/wav") and local.headers["x-audio-delivery"] == "local"
            assert fake.rooms == []
            lk = c.post(base + "&transport=livekit", headers=H)
            _settle()
            assert lk.status_code == 200 and lk.json()["audio_delivery"] == "livekit" and lk.json()["speech_seq"] == 1
    assert len(fake.published) == 1 and len(env.tts.texts) == 2              # one synthesis per request


def test_speak_route_falls_back_to_the_wav_when_livekit_cannot_carry_it():
    fake = FakeLiveKit(connect_error=RuntimeError("nope"))
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()
        with _client(db, {"user": user}) as c:
            r = c.post(f"/api/assessment/sessions/{app.id}/interview/v2/voice/speak?turn_number=1&transport=livekit", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav") and r.content[:4] == b"RIFF"
    assert r.headers["x-audio-delivery"] == "local" and "RuntimeError" in r.headers["x-livekit-error"]


# ------------------------------------------------------------ reconnect / disconnect / cleanup
def test_room_disconnect_is_detected_and_the_next_turn_reconnects_with_one_new_track():
    fake = FakeLiveKit()

    async def go():
        p = await transport.get_publisher(31, lambda a: {"url": "wss://x", "token": "t"})
        await p.speak(VT._wav(0.2)); await asyncio.sleep(0.05)
        fake.rooms[0].handlers["disconnected"]("SERVER_SHUTDOWN")            # LiveKit drops us
        assert p.connected is False
        m2 = await p.speak(VT._wav(0.2)); await asyncio.sleep(0.05)
        return p, m2

    with Env(fake):
        p, m2 = asyncio.run(go())
    assert len(fake.rooms) == 2 and fake.rooms[0].disconnects == 1           # the dead connection was released, not leaked
    assert len(fake.published) == 2 and m2["reused_connection"] is False and p.stats["connects"] == 2
    assert p.connected is True and fake.states()[-2:] == [("started", 2), ("ended", 2)]


def test_interview_completion_closes_the_publisher_after_the_closing_message_plays():
    fake = FakeLiveKit()
    with Env(fake) as env:
        db, user, app, session, stranger = _fixture()

        def finishing_brain(db_, session_, app_, sr_, text):
            return {"interview_complete": True, "session": {"status": "completed", "current_question": None}}

        saved = bridge.run_voice_turn_v2
        voice_route.run_voice_turn_v2 = lambda db_, app_, audio, mime: saved(db_, app_, audio, mime, brain=finishing_brain)
        try:
            with VT._isolated(), _client(db, {"user": user}) as c:
                r = _post_turn(c, app.id, "livekit")
                time.sleep(0.4)
        finally:
            voice_route.run_voice_turn_v2 = saved
    assert r.json()["interview_complete"] is True and r.json()["audio_delivery"] == "livekit"
    assert fake.states() == [("started", 1), ("ended", 1)]
    assert fake.rooms[0].disconnects == 1 and fake.unpublished == ["TR_1"]   # left the room cleanly
    assert transport.peek_publisher(app.id) is None                          # no orphan publisher in the registry


def test_close_all_publishers_leaves_no_participant_track_or_registry_entry_behind():
    fake = FakeLiveKit()

    async def go():
        for app_id in (41, 42):
            p = await transport.get_publisher(app_id, lambda a: {"url": "wss://x", "token": "t"})
            await p.speak(VT._wav(0.1))
        await asyncio.sleep(0.05)
        await transport.close_all_publishers()
        await transport.close_all_publishers()                               # idempotent

    with Env(fake):
        asyncio.run(go())
    assert [r.disconnects for r in fake.rooms] == [1, 1] and len(fake.unpublished) == 2
    assert transport._publishers == {}


def test_closed_publisher_is_replaced_and_idle_ones_are_reaped():
    fake = FakeLiveKit()

    async def go():
        make = lambda a: {"url": "wss://x", "token": "t"}
        old = await transport.get_publisher(51, make)
        await old.speak(VT._wav(0.1)); await asyncio.sleep(0.05)
        old.last_used -= transport.PUBLISHER_IDLE_SECONDS + 5
        fresh = await transport.get_publisher(52, make)                      # reaps 51 (idle, not speaking)
        return old, transport.peek_publisher(51), transport.peek_publisher(52) is fresh

    with Env(fake):
        old, gone, current = asyncio.run(go())
    assert old._closed and gone is None and current
    assert fake.rooms[0].disconnects == 1


def test_bad_audio_never_reaches_the_network_and_shutdown_hook_is_registered():
    fake = FakeLiveKit()

    async def go():
        p = await transport.get_publisher(61, lambda a: {"url": "wss://x", "token": "t"})
        try:
            await p.speak(b"not wav")
        except transport.AudioTransportError:
            return True
        return False

    with Env(fake):
        assert asyncio.run(go()) is True
    assert fake.rooms == []
    import backend.app.main as main
    assert any("shutdown" in str(h) or h.__name__ == "_close_interview_audio_publishers" for h in main.app.router.on_shutdown)


def test_delivery_code_never_references_gemini():
    root = os.path.join(os.path.dirname(__file__), "..", "backend", "app")
    for rel in ("services/assessment/livekit_audio_transport_v2.py",):
        src = open(os.path.join(root, rel), encoding="utf-8").read().lower()
        assert "gemini" not in src and "genai" not in src, rel

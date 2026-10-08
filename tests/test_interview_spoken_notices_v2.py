"""The AI's fixed spoken lines: welcome + integrity notices. Fixed texts only, cached audio, nothing client-supplied is spoken."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.assessment import interview_spoken_notices_v2 as n
from backend.app.services.assessment.integrity_service import CLIENT_ALLOWED_EVENT_TYPES


class FakeTts:
    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return SimpleNamespace(audio_bytes=b"RIFFfake", mime_type="audio/wav")


class FakeDb:
    """query(Model).filter(...).first() returns the user or the job, by model."""

    def __init__(self, user, job):
        self.user, self.job = user, job

    def query(self, model):
        row = self.user if model.__name__ == "User" else self.job
        return SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(first=lambda: row))


APP = SimpleNamespace(user_id=1, job_id=1)
DB = FakeDb(SimpleNamespace(full_name="John Doe"), SimpleNamespace(title="Java Developer", company="RetailFlow Technologies"))


def test_welcome_is_a_short_personal_greeting_plus_fixed_rules():
    hello = n.spoken_text("welcome_hello", None, APP, DB)
    rules = n.spoken_text("welcome_rules", None, APP, DB)
    assert hello == "Hello John, welcome to your Java Developer interview."
    assert len(hello.split()) <= 12                                       # the voice model needs about as long as the sentence lasts
    assert "camera on" in rules and "out loud" in rules and rules == n.WELCOME_RULES_TEXT


def test_fixed_lines_are_kept_on_disk_after_the_first_synthesis():
    import tempfile
    n.DISK_CACHE_DIR = tempfile.mkdtemp()
    n._cache.clear()
    tts = FakeTts()
    tts.provider_name, tts.voice = "fake", "v"
    n.synthesize_notice("notice", "face_missing", APP, DB, tts)
    assert len(tts.texts) == 1
    n._cache.clear()                                                      # a new process: memory cache is empty
    again = n.synthesize_notice("notice", "face_missing", APP, DB, tts)
    assert len(tts.texts) == 1 and again.audio_bytes.startswith(b"RIFF")   # served from disk, no new synthesis
    n.DISK_CACHE_DIR = os.path.join("data", "tts_cache")


def test_every_notice_is_a_real_flag_event_and_none_accuses():
    for event, text in n.NOTICE_TEXT.items():
        assert event in CLIENT_ALLOWED_EVENT_TYPES, event
        assert not any(word in text.lower() for word in ("cheat", "fraud", "dishonest", "reject", "fail"))
    assert n.spoken_text("notice", "face_missing", APP, DB) == n.NOTICE_TEXT["face_missing"]


def test_unknown_kind_or_event_is_never_spoken_and_audio_is_cached():
    import tempfile
    n.DISK_CACHE_DIR = tempfile.mkdtemp()                                   # never touch the real cache
    tts = FakeTts()
    n._cache.clear()
    assert n.synthesize_notice("notice", "say_anything_i_send", APP, DB, tts) is None
    assert n.synthesize_notice("shout", None, APP, DB, tts) is None
    assert tts.texts == []
    a = n.synthesize_notice("notice", "looking_away", APP, DB, tts)
    b = n.synthesize_notice("notice", "looking_away", APP, DB, tts)
    assert a is b and len(tts.texts) == 1
    n._cache.clear()
    n.DISK_CACHE_DIR = os.path.join("data", "tts_cache")

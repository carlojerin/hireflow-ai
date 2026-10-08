"""
Fixed spoken lines for the AI interviewer that are NOT interview questions:

  welcome   said when the candidate enters the interview (greeting, what this is, camera reminder)
  notice    said the moment the camera / browser guards flag something ("I can't see you", "please stay on this window", ...)

The browser only ever sends a KIND and an EVENT NAME. The text is composed here from fixed templates (plus the candidate's
first name and the job title/company), so nothing a client sends is ever spoken. The words describe what was noticed and
what to do; they never accuse ("cheating") and never decide anything about the candidate.
"""

from __future__ import annotations

import hashlib
import os
import threading
from types import SimpleNamespace

from sqlalchemy.orm import Session

from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User
from backend.app.services.assessment.voice_provider_v2 import VoiceProviderError, get_tts_provider


NOTICE_TEXT = {
    "face_missing": "I can't see you on the camera. Please sit in front of the camera so your face is clearly visible.",
    "multiple_faces_detected": "I can see more than one person in the picture. This interview is for you alone, so please ask anyone else to leave the room.",
    "looking_away": "Please keep your eyes and your attention on the screen during the interview.",
    "gaze_away": "Please keep looking at the screen while we talk.",
    "looking_away_repeated": "That is the second time you have looked away. This is now recorded as a warning. Please keep your eyes on the screen.",
    "gaze_away_sustained": "I've noticed your eyes have been off the screen for a while now. Please keep your attention on the interview.",
    "camera_covered": "Your camera looks covered or blocked. Please uncover it so I can see you.",
    "mobile_phone_detected": "I can see a phone. Please put it out of sight and do not use it during the interview.",
    "tab_hidden": "Please stay on the interview window. Switching to another tab is not allowed.",
    "window_blur": "Please keep the interview window in focus and do not switch to another application.",
    "fullscreen_exited": "Please return to full screen mode and stay in it until the interview ends.",
    "paste_detected": "Pasting text is not allowed in this interview. Please answer in your own words.",
    "blocked_shortcut": "That keyboard shortcut is not allowed during the interview.",
}

MAX_CACHE = 40
_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


# The voice model needs roughly as long to synthesise a sentence as the sentence lasts, so the welcome is split in two:
# a SHORT personalised greeting (a few seconds) and a fixed part that is synthesised once and kept on disk.
WELCOME_RULES_TEXT = (
    "I'm your AI interviewer. Please answer out loud in your own words, keep your camera on, "
    "and stay in the frame. Let's begin."
)


def welcome_hello_text(application: JobApplication, db: Session) -> str:

    user = db.query(User).filter(User.id == application.user_id).first()
    job = db.query(Job).filter(Job.id == application.job_id).first()

    first_name = ((getattr(user, "full_name", "") or "").strip().split() or [""])[0]
    title = (getattr(job, "title", "") or "").strip()

    greeting = f"Hello {first_name}, welcome" if first_name else "Hello, welcome"

    return f"{greeting} to your {title} interview." if title else f"{greeting} to your interview."


def spoken_text(kind: str, event: str | None, application: JobApplication, db: Session) -> str | None:

    if kind == "welcome_hello":
        return welcome_hello_text(application, db)

    if kind == "welcome_rules":
        return WELCOME_RULES_TEXT

    if kind == "notice":
        return NOTICE_TEXT.get(event or "")

    return None


def synthesize_notice(kind: str, event: str | None, application: JobApplication, db: Session, tts_provider=None):
    """Returns SynthesizedAudioV2, or None when the kind/event is not one of the fixed lines. Audio is cached per text."""

    text = spoken_text(kind, event, application, db)

    if not text:
        return None

    with _cache_lock:
        cached = _cache.get(text)

    if cached is not None:
        return cached

    provider = tts_provider or get_tts_provider()
    path = _disk_path(text, provider)

    audio = _read_disk(path)

    if audio is None:

        try:
            audio = provider.synthesize(text)
        except VoiceProviderError:
            raise

        _write_disk(path, audio)

    with _cache_lock:
        if len(_cache) >= MAX_CACHE:
            _cache.pop(next(iter(_cache)))
        _cache[text] = audio

    return audio


# ---- disk cache: a fixed line is synthesised once, ever ----
DISK_CACHE_DIR = os.path.join("data", "tts_cache")


def _disk_path(text: str, provider) -> str | None:

    key = f"{getattr(provider, 'provider_name', 'tts')}|{getattr(provider, 'voice', '')}|{text}"
    return os.path.join(DISK_CACHE_DIR, hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".wav")


def _read_disk(path: str | None):

    try:
        if path and os.path.isfile(path):
            with open(path, "rb") as handle:
                data = handle.read()
            if data[:4] == b"RIFF":
                return SimpleNamespace(audio_bytes=data, mime_type="audio/wav")
    except OSError:
        pass

    return None


def _write_disk(path: str | None, audio) -> None:

    try:
        if path and getattr(audio, "mime_type", "") == "audio/wav" and audio.audio_bytes[:4] == b"RIFF":
            os.makedirs(DISK_CACHE_DIR, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(audio.audio_bytes)
    except OSError:
        pass


def warm_fixed_lines(tts_provider=None) -> int:
    """Synthesise every fixed (non-personalised) line once so the interview never waits for them. Returns how many were new."""

    provider = tts_provider or get_tts_provider()
    created = 0

    for text in list(NOTICE_TEXT.values()) + [WELCOME_RULES_TEXT]:
        path = _disk_path(text, provider)
        if _read_disk(path) is None:
            _write_disk(path, provider.synthesize(text))
            created += 1

    return created

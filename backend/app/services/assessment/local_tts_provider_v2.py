import logging
import os
import re
import threading
import time

from backend.app.services.assessment.voice_provider_v2 import (
    MAX_TTS_TEXT_CHARS,
    SynthesizedAudioV2,
    VoiceProviderError,
    _pcm_to_wav_bytes,
)


logger = logging.getLogger("jobpilot.assessment.local_tts")


# ============================================================
# JOBPILOT AI - LOCAL TEXT-TO-SPEECH PROVIDER (Kokoro)
#
# Implements the EXISTING TextToSpeechProviderV2 Protocol
# (voice_provider_v2.py): synthesize(text) -> SynthesizedAudioV2
# (a complete, playable 16-bit mono WAV -- identical contract to the
# Gemini provider, so the route/bridge/browser never learn which
# provider ran).
#
# Runtime: kokoro-onnx (Kokoro-82M exported to ONNX) on
# onnxruntime CPU -- no torch, no GPU, no network at synthesis time and no API
# key. Audio is produced fully in memory: THIS provider writes no
# temporary files (the third-party espeak/phonemizer layer keeps a few
# per-process working directories that it removes at process exit).
# Text is never logged; errors carry only the exception TYPE.
#
# The model files are NOT downloaded implicitly at request time;
# if they are missing the provider fails with a clear message that
# says where they are expected.
#
# Config (all optional):
#   AI_TTS_MODEL_DIR     default ~/.cache/jobpilot/kokoro
#   AI_TTS_MODEL_FILE    default kokoro-v1.0.onnx (fp32)
#   AI_TTS_VOICES_FILE   default voices-v1.0.bin
#   AI_TTS_VOICE         default af_heart
#   AI_TTS_SPEED         default 1.0  (0.5 - 2.0)
#   AI_TTS_LANG          default en-us
# ============================================================

# fp32, not int8: measured on the target laptop CPU the int8 graph was ~3x SLOWER
# (RTF 1.05 vs 0.33) and erratic; fp32 is faster despite the larger file.
DEFAULT_MODEL_FILE = "kokoro-v1.0.onnx"
DEFAULT_VOICES_FILE = "voices-v1.0.bin"
DEFAULT_VOICE = "af_heart"
DEFAULT_LANG = "en-us"

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _default_model_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".cache", "jobpilot", "kokoro")


def clean_speech_text(text) -> str:
    """Validates and normalises text for synthesis. Raises VoiceProviderError."""

    if not isinstance(text, str):
        raise VoiceProviderError("Cannot synthesize non-text input.")

    cleaned = _CONTROL_CHARS.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        raise VoiceProviderError("Cannot synthesize empty text.")

    if len(cleaned) > MAX_TTS_TEXT_CHARS:
        raise VoiceProviderError(
            f"Text too long for speech synthesis ({len(cleaned)} > {MAX_TTS_TEXT_CHARS} characters)."
        )

    if not any(ch.isalnum() for ch in cleaned):
        raise VoiceProviderError("Text contains nothing speakable.")

    return cleaned


class LocalKokoroTextToSpeechProviderV2:

    provider_name = "kokoro"

    def __init__(
        self,
        model_dir: str | None = None,
        model_file: str | None = None,
        voices_file: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
        lang: str | None = None,
    ):

        directory = model_dir or os.getenv("AI_TTS_MODEL_DIR") or _default_model_dir()

        self.model_path = os.path.join(directory, model_file or os.getenv("AI_TTS_MODEL_FILE") or DEFAULT_MODEL_FILE)
        self.voices_path = os.path.join(directory, voices_file or os.getenv("AI_TTS_VOICES_FILE") or DEFAULT_VOICES_FILE)
        self.voice = voice or os.getenv("AI_TTS_VOICE") or DEFAULT_VOICE
        self.lang = lang or os.getenv("AI_TTS_LANG") or DEFAULT_LANG

        try:
            self.speed = float(speed if speed is not None else os.getenv("AI_TTS_SPEED") or 1.0)
        except ValueError:
            self.speed = 1.0

        self.speed = min(2.0, max(0.5, self.speed))

        self._model = None
        self._load_lock = threading.Lock()
        self.last_metrics: dict = {}

    def _get_model(self):

        if self._model is not None:
            return self._model

        with self._load_lock:

            if self._model is None:

                for path in (self.model_path, self.voices_path):
                    if not os.path.isfile(path):
                        raise VoiceProviderError(
                            f"Local speech model file is missing: {os.path.basename(path)} "
                            f"(expected in {os.path.dirname(path)})."
                        )

                try:
                    from kokoro_onnx import Kokoro
                except ImportError as exc:
                    raise VoiceProviderError("kokoro-onnx is not installed — local text-to-speech is unavailable.") from exc

                started = time.perf_counter()

                try:
                    model = Kokoro(self.model_path, self.voices_path)
                    voices = model.get_voices()
                except Exception as exc:
                    raise VoiceProviderError(f"Local speech model could not be loaded ({type(exc).__name__}).") from exc

                if self.voice not in voices:
                    raise VoiceProviderError(f"Unknown local voice '{self.voice}'.")

                self._model = model
                self.last_metrics["model_load_seconds"] = round(time.perf_counter() - started, 2)
                logger.info("local TTS model loaded voice=%s seconds=%.1f", self.voice, self.last_metrics["model_load_seconds"])

        return self._model

    def synthesize(self, text: str) -> SynthesizedAudioV2:

        cleaned = clean_speech_text(text)

        model = self._get_model()

        started = time.perf_counter()

        try:

            samples, sample_rate = model.create(cleaned, voice=self.voice, speed=self.speed, lang=self.lang)

            import numpy as np

            samples = np.asarray(samples, dtype=np.float32).reshape(-1)

            if samples.size == 0:
                raise VoiceProviderError("Text-to-speech produced no audio.")

            if not np.all(np.isfinite(samples)):
                raise VoiceProviderError("Text-to-speech produced invalid audio.")

            pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

            wav_bytes = _pcm_to_wav_bytes(pcm, sample_rate=int(sample_rate))

        except VoiceProviderError:
            raise

        except Exception as exc:
            # exception TYPE only: never the text or any audio in an error or log
            raise VoiceProviderError(f"Local text-to-speech failed ({type(exc).__name__}).") from exc

        elapsed = time.perf_counter() - started
        audio_seconds = samples.size / float(sample_rate)

        self.last_metrics.update({
            "synthesis_seconds": round(elapsed, 3),
            "audio_seconds": round(audio_seconds, 2),
            "real_time_factor": round(elapsed / audio_seconds, 3) if audio_seconds else None,
        })

        return SynthesizedAudioV2(audio_bytes=wav_bytes, mime_type="audio/wav", sample_rate=int(sample_rate))


_provider_singleton: LocalKokoroTextToSpeechProviderV2 | None = None
_singleton_lock = threading.Lock()


def get_local_tts_provider() -> LocalKokoroTextToSpeechProviderV2:
    """Process-wide instance so the ~325 MB model is loaded once, not per request."""

    global _provider_singleton

    with _singleton_lock:

        if _provider_singleton is None:
            _provider_singleton = LocalKokoroTextToSpeechProviderV2()

        return _provider_singleton

import logging
import os
import tempfile
import threading
import time

from backend.app.services.assessment.voice_provider_v2 import (
    MAX_STT_AUDIO_BYTES,
    TranscriptionResultV2,
    VoiceProviderError,
)


logger = logging.getLogger("jobpilot.assessment.local_stt")


# ============================================================
# JOBPILOT AI - LOCAL SPEECH-TO-TEXT PROVIDER (faster-whisper)
#
# Implements the EXISTING SpeechToTextProviderV2 Protocol
# (voice_provider_v2.py): transcribe(audio_bytes, mime_type) ->
# TranscriptionResultV2. The interview brain, the bridge
# (interview_voice_integration_v2.py) and the route never learn
# which provider ran.
#
# Fully local: audio is decoded and transcribed on this machine by
# CTranslate2 (CPU, int8 by default). Audio is never sent anywhere. The only
# network access is the ONE-TIME download of the open model weights
# on first use (no audio involved); set AI_STT_LOCAL_FILES_ONLY=1 to
# forbid even that once the model is cached.
#
# The audio bytes exist on disk only inside a temporary file that is
# always removed (finally), because the decoder (PyAV) reads files.
#
# Config (all optional):
#   AI_STT_MODEL           tiny | base | small | medium ... (default small)
#   AI_STT_DEVICE          cpu | cuda | auto            (default cpu)
#   AI_STT_COMPUTE_TYPE    int8 | int8_float16 | float16 (default int8)
#   AI_STT_LANGUAGE        e.g. "en"; empty = auto-detect
#   AI_STT_LOCAL_FILES_ONLY 1 = never touch the network
# ============================================================

DEFAULT_MODEL = "small"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"

_SUFFIX_BY_MIME = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
}


class LocalWhisperSpeechToTextProviderV2:

    provider_name = "faster-whisper"

    def __init__(self, model_size: str | None = None, device: str | None = None, compute_type: str | None = None):

        self.model_size = model_size or os.getenv("AI_STT_MODEL") or DEFAULT_MODEL
        self.device = device or os.getenv("AI_STT_DEVICE") or DEFAULT_DEVICE
        self.compute_type = compute_type or os.getenv("AI_STT_COMPUTE_TYPE") or DEFAULT_COMPUTE_TYPE
        # The interview is conducted in English. Whisper's language auto-detection misfired on a real accented
        # candidate (an answer came back in Devanagari script), so the default is PINNED to English.
        # AI_STT_LANGUAGE=<code> overrides it; AI_STT_LANGUAGE=auto restores auto-detection.
        configured_language = (os.getenv("AI_STT_LANGUAGE") or "").strip().lower()
        self.language = None if configured_language == "auto" else (configured_language or "en")
        self.local_files_only = os.getenv("AI_STT_LOCAL_FILES_ONLY", "").strip() in ("1", "true", "yes")

        self._model = None
        self._load_lock = threading.Lock()
        self.last_metrics: dict = {}

    def _get_model(self):

        if self._model is not None:
            return self._model

        with self._load_lock:

            if self._model is None:

                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise VoiceProviderError("faster-whisper is not installed — local speech-to-text is unavailable.") from exc

                started = time.perf_counter()

                try:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                        local_files_only=self.local_files_only,
                    )
                except Exception as exc:
                    raise VoiceProviderError(f"Local speech model could not be loaded ({type(exc).__name__}).") from exc

                self.last_metrics["model_load_seconds"] = round(time.perf_counter() - started, 2)
                logger.info(
                    "local STT model loaded model=%s device=%s compute=%s seconds=%.1f",
                    self.model_size, self.device, self.compute_type, self.last_metrics["model_load_seconds"],
                )

        return self._model

    def transcribe(self, audio_bytes: bytes, mime_type: str, prompt: str | None = None) -> TranscriptionResultV2:
        """`prompt` (optional) is vocabulary context -- e.g. the job's technologies -- that biases recognition toward
        the technical terms a candidate is likely to say (FastAPI, PostgreSQL, ...). It is never treated as transcript."""

        if not audio_bytes:
            raise VoiceProviderError("Cannot transcribe empty audio.")

        if len(audio_bytes) > MAX_STT_AUDIO_BYTES:
            raise VoiceProviderError(
                f"Audio too large to transcribe ({len(audio_bytes)} > {MAX_STT_AUDIO_BYTES} bytes)."
            )

        model = self._get_model()

        suffix = _SUFFIX_BY_MIME.get((mime_type or "").split(";")[0].strip().lower(), ".bin")
        path = None

        try:

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio_bytes)
                path = handle.name

            started = time.perf_counter()

            segments, info = model.transcribe(
                path,
                language=self.language,
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=(prompt or None),
            )

            # segments is a lazy generator: the real work happens here
            text = " ".join(segment.text.strip() for segment in segments).strip()

            self.last_metrics.update({
                "transcribe_seconds": round(time.perf_counter() - started, 3),
                "audio_seconds": round(getattr(info, "duration", 0.0) or 0.0, 2),
                "language": getattr(info, "language", None),
                "language_probability": round(getattr(info, "language_probability", 0.0) or 0.0, 3),
            })

        except VoiceProviderError:
            raise

        except Exception as exc:
            # Only the exception type: never audio, never a transcript, in a log/error
            raise VoiceProviderError(f"Local speech-to-text failed ({type(exc).__name__}).") from exc

        finally:

            if path:
                try:
                    os.remove(path)
                except OSError:
                    logger.warning("Could not remove temporary audio file.")

        # silence / noise -> honest empty transcript, never fabricated
        return TranscriptionResultV2(transcript=text)


_provider_singleton: LocalWhisperSpeechToTextProviderV2 | None = None
_singleton_lock = threading.Lock()


def get_local_stt_provider() -> LocalWhisperSpeechToTextProviderV2:
    """Process-wide instance so the model is loaded once, not per request."""

    global _provider_singleton

    with _singleton_lock:

        if _provider_singleton is None:
            _provider_singleton = LocalWhisperSpeechToTextProviderV2()

        return _provider_singleton

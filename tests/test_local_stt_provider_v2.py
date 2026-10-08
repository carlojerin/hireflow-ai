"""
Local faster-whisper STT provider (behind the EXISTING SpeechToTextProviderV2
Protocol). Model behaviour is faked so these are fast and deterministic; one
test at the end uses the REAL cached model on a silent clip (skipped if the
model is not cached). No database, no network, no Gemini.
"""

import contextlib
import io
import os
import sys
import wave
from types import SimpleNamespace

from backend.app.services.assessment import local_stt_provider_v2 as local
from backend.app.services.assessment import voice_provider_v2 as vp
from backend.app.services.assessment.voice_provider_v2 import VoiceProviderError


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Records the temp path it was given and whether the file existed at call time."""

    def __init__(self, texts=("hello world",), raises=None):
        self.texts, self.raises = list(texts), raises
        self.seen_path = None
        self.existed_during_call = None
        self.calls = 0

    def transcribe(self, path, **kwargs):
        self.calls += 1
        self.seen_path = path
        self.existed_during_call = os.path.exists(path)
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        info = SimpleNamespace(duration=2.0, language="en", language_probability=0.99)
        return (_Seg(t) for t in self.texts), info


def _provider(model):
    p = local.LocalWhisperSpeechToTextProviderV2(model_size="small", device="cpu", compute_type="int8")
    p._model = model
    return p


@contextlib.contextmanager
def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _gemini_tripwire():
    """Any Gemini client construction is counted and refused."""
    from google import genai

    calls = {"n": 0}
    original = genai.Client

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("Gemini must not be called")

    genai.Client = boom
    try:
        yield calls
    finally:
        genai.Client = original


def _wav(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_empty_audio_is_rejected_without_loading_the_model():
    p = local.LocalWhisperSpeechToTextProviderV2()
    try:
        p.transcribe(b"", "audio/webm")
        assert False, "expected VoiceProviderError"
    except VoiceProviderError as exc:
        assert "empty" in str(exc).lower()
    assert p._model is None


def test_oversized_audio_is_rejected():
    p = _provider(_FakeModel())
    try:
        p.transcribe(b"x" * (vp.MAX_STT_AUDIO_BYTES + 1), "audio/webm")
        assert False, "expected VoiceProviderError"
    except VoiceProviderError as exc:
        assert "too large" in str(exc).lower()


def test_returns_joined_transcript_from_all_segments():
    p = _provider(_FakeModel(texts=("  I built a REST API ", "using FastAPI. ")))
    result = p.transcribe(b"audio-bytes", "audio/webm")
    assert result.transcript == "I built a REST API using FastAPI."
    assert p.last_metrics["language"] == "en" and p.last_metrics["audio_seconds"] == 2.0


def test_silence_returns_an_honest_empty_transcript_not_an_error():
    p = _provider(_FakeModel(texts=()))
    assert p.transcribe(b"audio-bytes", "audio/webm").transcript == ""


def test_temp_file_exists_only_during_transcription_and_is_removed_after():
    model = _FakeModel()
    _provider(model).transcribe(b"audio-bytes", "audio/webm")
    assert model.existed_during_call is True
    assert model.seen_path.endswith(".webm")
    assert not os.path.exists(model.seen_path)


def test_temp_file_is_removed_even_when_the_model_fails():
    model = _FakeModel(raises=RuntimeError("decode exploded: SECRET-TRANSCRIPT"))
    try:
        _provider(model).transcribe(b"audio-bytes", "audio/wav")
        assert False, "expected VoiceProviderError"
    except VoiceProviderError as exc:
        assert "RuntimeError" in str(exc) and "SECRET-TRANSCRIPT" not in str(exc)   # type only, never content
    assert not os.path.exists(model.seen_path)


def test_mime_type_picks_the_temp_file_suffix():
    for mime, suffix in (("audio/wav", ".wav"), ("audio/ogg;codecs=opus", ".ogg"), ("audio/mp4", ".m4a"), ("weird/type", ".bin")):
        model = _FakeModel()
        _provider(model).transcribe(b"audio-bytes", mime)
        assert model.seen_path.endswith(suffix), (mime, model.seen_path)


def test_transcription_uses_vad_and_no_cross_segment_context():
    model = _FakeModel()
    _provider(model).transcribe(b"audio-bytes", "audio/webm")
    assert model.kwargs["vad_filter"] is True and model.kwargs["condition_on_previous_text"] is False


def test_model_load_failure_becomes_a_voice_provider_error():
    p = local.LocalWhisperSpeechToTextProviderV2(model_size="definitely-not-a-model")
    p.local_files_only = True
    try:
        p.transcribe(b"audio-bytes", "audio/webm")
        assert False, "expected VoiceProviderError"
    except VoiceProviderError as exc:
        assert "could not be loaded" in str(exc)


def test_missing_faster_whisper_is_reported_cleanly():
    saved = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = None            # makes `import faster_whisper` raise ImportError
    try:
        p = local.LocalWhisperSpeechToTextProviderV2()
        try:
            p.transcribe(b"audio-bytes", "audio/webm")
            assert False, "expected VoiceProviderError"
        except VoiceProviderError as exc:
            assert "not installed" in str(exc)
    finally:
        if saved is None:
            sys.modules.pop("faster_whisper", None)
        else:
            sys.modules["faster_whisper"] = saved


def test_default_stt_provider_is_local_and_gemini_is_opt_in_only():
    with _env(AI_STT_PROVIDER=None):
        assert isinstance(vp.get_stt_provider(), local.LocalWhisperSpeechToTextProviderV2)
    with _env(AI_STT_PROVIDER="gemini"):
        assert isinstance(vp.get_stt_provider(), vp.GeminiSpeechToTextProviderV2)
    with _env(AI_STT_PROVIDER="anything-else"):
        assert isinstance(vp.get_stt_provider(), local.LocalWhisperSpeechToTextProviderV2)


def test_local_failure_never_falls_back_to_gemini():
    with _env(AI_STT_PROVIDER=None, GEMINI_API_KEY="would-be-used-if-fallback-existed"), _gemini_tripwire() as calls:
        p = _provider(_FakeModel(raises=RuntimeError("boom")))
        try:
            p.transcribe(b"audio-bytes", "audio/webm")
            assert False, "expected VoiceProviderError"
        except VoiceProviderError:
            pass
        assert calls["n"] == 0


def test_successful_local_transcription_makes_zero_gemini_calls():
    with _gemini_tripwire() as calls:
        _provider(_FakeModel()).transcribe(b"audio-bytes", "audio/webm")
        assert calls["n"] == 0


def test_bridge_uses_the_local_provider_through_the_existing_interface():
    # the existing bridge only needs .transcribe(bytes, mime) -> TranscriptionResultV2
    result = _provider(_FakeModel(texts=("through the bridge",))).transcribe(b"audio-bytes", "audio/webm")
    assert type(result).__name__ == "TranscriptionResultV2" and result.transcript == "through the bridge"


def test_singleton_is_reused_so_the_model_loads_once():
    assert local.get_local_stt_provider() is local.get_local_stt_provider()


def test_real_cached_model_returns_empty_transcript_for_a_silent_clip():
    p = local.LocalWhisperSpeechToTextProviderV2(model_size="small", device="cpu", compute_type="int8")
    p.local_files_only = True                       # never touch the network in a test
    try:
        result = p.transcribe(_wav(1.5), "audio/wav")
    except VoiceProviderError as exc:
        if "could not be loaded" in str(exc):
            return                                   # model not cached on this machine: skip
        raise
    assert result.transcript == ""


def test_gemini_stt_rotates_models_on_quota_and_passes_the_vocabulary_hint():
    import types as _types
    from backend.app.services.assessment import voice_provider_v2 as vp

    os.environ["GEMINI_API_KEY"] = "test-key-not-real"
    os.environ["AI_STT_GEMINI_MODELS"] = "model-a,model-b"
    vp._stt_cooldown_until.clear()
    calls = []

    class FakeModels:
        def generate_content(self, model, contents):
            calls.append((model, contents[1]))
            if model == "model-a":
                raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
            return _types.SimpleNamespace(text="I used Redis and MySQL.")

    class FakeClient:
        def __init__(self, api_key=None, **_kw):
            self.models = FakeModels()

    original = vp.genai.Client
    vp.genai.Client = FakeClient
    try:
        provider = vp.GeminiSpeechToTextProviderV2()
        result = provider.transcribe(b"audio-bytes", "audio/webm;codecs=opus", prompt="Redis, MySQL")
        assert result.transcript == "I used Redis and MySQL."
        assert [c[0] for c in calls] == ["model-a", "model-b"] and "Redis, MySQL" in calls[0][1]
        calls.clear()
        provider.transcribe(b"audio-bytes", "audio/webm")
        assert [c[0] for c in calls] == ["model-b"]                     # model-a is on cooldown: no wasted call
    finally:
        vp.genai.Client = original
        vp._stt_cooldown_until.clear()
        os.environ.pop("AI_STT_GEMINI_MODELS", None)
        os.environ.pop("GEMINI_API_KEY", None)

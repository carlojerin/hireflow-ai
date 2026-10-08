"""
Local Kokoro TTS provider (behind the EXISTING TextToSpeechProviderV2
Protocol). The model is faked for deterministic tests; the last two tests
use the REAL cached model (skipped if the model files are not present).
No database, no network, no Gemini.
"""

import contextlib
import io
import os
import sys
import tempfile
import wave

import numpy as np

from backend.app.services.assessment import local_tts_provider_v2 as local
from backend.app.services.assessment import voice_provider_v2 as vp
from backend.app.services.assessment.voice_provider_v2 import VoiceProviderError


class _FakeKokoro:
    """create() returns a deterministic 0.5 s tone; records what it was asked."""

    def __init__(self, samples=None, raises=None, voices=("af_heart", "am_adam")):
        self.samples, self.raises, self.voices = samples, raises, list(voices)
        self.calls = []

    def get_voices(self):
        return self.voices

    def create(self, text, voice, speed, lang):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        if self.raises:
            raise self.raises
        if self.samples is not None:
            return self.samples, 24000
        t = np.arange(12000) / 24000.0
        return (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 24000


def _provider(model, **kw):
    p = local.LocalKokoroTextToSpeechProviderV2(**kw)
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


def _expect_error(fn, contains):
    try:
        fn()
    except VoiceProviderError as exc:
        assert contains in str(exc).lower(), str(exc)
        return str(exc)
    raise AssertionError("expected VoiceProviderError")


def _decode(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(), w.readframes(w.getnframes())


def test_empty_blank_and_non_text_input_are_rejected_before_any_model_use():
    fake = _FakeKokoro()
    p = _provider(fake)
    for bad in ("", "   ", "\n\t \x00\x07", None, 123, b"bytes"):
        _expect_error(lambda: p.synthesize(bad), "cannot synthesize")
    assert fake.calls == []


def test_text_with_nothing_speakable_is_rejected():
    fake = _FakeKokoro()
    _expect_error(lambda: _provider(fake).synthesize("... !!! ??? ---"), "nothing speakable")
    assert fake.calls == []


def test_overlong_text_is_rejected_and_the_limit_itself_is_allowed():
    fake = _FakeKokoro()
    p = _provider(fake)
    _expect_error(lambda: p.synthesize("a" * (vp.MAX_TTS_TEXT_CHARS + 1)), "too long")
    p.synthesize("a" * vp.MAX_TTS_TEXT_CHARS)
    assert len(fake.calls) == 1


def test_control_characters_and_whitespace_are_normalised():
    fake = _FakeKokoro()
    _provider(fake).synthesize("Hello\x00\n\n   wor\x07ld\t!")
    assert fake.calls[0]["text"] == "Hello wor ld !"


def test_returns_a_complete_playable_wav_matching_the_existing_contract():
    result = _provider(_FakeKokoro()).synthesize("Hello there.")
    assert isinstance(result, vp.SynthesizedAudioV2)
    assert result.mime_type == "audio/wav" and result.sample_rate == 24000
    channels, width, rate, frames, pcm = _decode(result.audio_bytes)
    assert (channels, width, rate, frames) == (1, 2, 24000, 12000)
    assert np.abs(np.frombuffer(pcm, dtype="<i2")).max() > 10000       # the tone is really in the file


def test_out_of_range_samples_are_clipped_not_wrapped():
    loud = np.array([2.5, -3.0, 0.0, 1.0], dtype=np.float32)
    _, _, _, _, pcm = _decode(_provider(_FakeKokoro(samples=loud)).synthesize("Loud.").audio_bytes)
    values = np.frombuffer(pcm, dtype="<i2").tolist()
    assert values == [32767, -32767, 0, 32767]


def test_empty_or_non_finite_model_output_is_an_error():
    _expect_error(lambda: _provider(_FakeKokoro(samples=np.array([], dtype=np.float32))).synthesize("Hi."), "no audio")
    _expect_error(lambda: _provider(_FakeKokoro(samples=np.array([0.1, np.nan], dtype=np.float32))).synthesize("Hi."), "invalid audio")


def test_synthesis_error_is_safe_type_only_and_leaks_no_text():
    fake = _FakeKokoro(raises=RuntimeError("engine crashed while saying SECRET-SENTENCE"))
    message = _expect_error(lambda: _provider(fake).synthesize("My SECRET-SENTENCE here."), "runtimeerror")
    assert "SECRET-SENTENCE" not in message


def test_this_provider_creates_no_temporary_files():
    before = {f for f in os.listdir(tempfile.gettempdir()) if os.path.isfile(os.path.join(tempfile.gettempdir(), f))}
    _provider(_FakeKokoro()).synthesize("Hello there.")
    after = {f for f in os.listdir(tempfile.gettempdir()) if os.path.isfile(os.path.join(tempfile.gettempdir(), f))}
    assert after - before == set()


def test_missing_model_files_fail_clearly_without_touching_the_network():
    with tempfile.TemporaryDirectory() as empty:
        p = local.LocalKokoroTextToSpeechProviderV2(model_dir=empty)
        message = _expect_error(lambda: p.synthesize("Hello."), "missing")
        assert "kokoro-v1.0.onnx" in message


def test_corrupt_model_file_becomes_a_load_error():
    with tempfile.TemporaryDirectory() as d:
        for name in ("model.onnx", "voices.bin"):
            open(os.path.join(d, name), "wb").write(b"not a real model")
        p = local.LocalKokoroTextToSpeechProviderV2(model_dir=d, model_file="model.onnx", voices_file="voices.bin")
        _expect_error(lambda: p.synthesize("Hello."), "could not be loaded")


def test_missing_kokoro_package_is_reported_cleanly():
    with tempfile.TemporaryDirectory() as d:
        for name in ("m.onnx", "v.bin"):
            open(os.path.join(d, name), "wb").write(b"x")
        saved = sys.modules.get("kokoro_onnx")
        sys.modules["kokoro_onnx"] = None
        try:
            p = local.LocalKokoroTextToSpeechProviderV2(model_dir=d, model_file="m.onnx", voices_file="v.bin")
            _expect_error(lambda: p.synthesize("Hello."), "not installed")
        finally:
            if saved is None:
                sys.modules.pop("kokoro_onnx", None)
            else:
                sys.modules["kokoro_onnx"] = saved


def test_unknown_voice_is_rejected_and_voice_speed_lang_reach_the_model():
    import types

    with tempfile.TemporaryDirectory() as d:
        for name in ("m.onnx", "v.bin"):
            open(os.path.join(d, name), "wb").write(b"x")
        created = {}

        class FakeClass(_FakeKokoro):
            def __init__(self, model_path, voices_path):
                super().__init__()
                created["model"] = self

        saved = sys.modules.get("kokoro_onnx")
        sys.modules["kokoro_onnx"] = types.SimpleNamespace(Kokoro=FakeClass)
        try:
            bad = local.LocalKokoroTextToSpeechProviderV2(model_dir=d, model_file="m.onnx", voices_file="v.bin", voice="no_such_voice")
            _expect_error(lambda: bad.synthesize("Hello."), "unknown local voice")

            good = local.LocalKokoroTextToSpeechProviderV2(model_dir=d, model_file="m.onnx", voices_file="v.bin",
                                                           voice="am_adam", speed=9.0, lang="en-gb")
            good.synthesize("Hello.")
            call = created["model"].calls[0]
            assert (call["voice"], call["lang"]) == ("am_adam", "en-gb")
            assert call["speed"] == 2.0                                   # clamped to the safe range
        finally:
            if saved is None:
                sys.modules.pop("kokoro_onnx", None)
            else:
                sys.modules["kokoro_onnx"] = saved


def test_default_tts_provider_is_flash_live_and_local_and_gemini_are_opt_in_only():
    with _env(AI_TTS_PROVIDER=None):
        assert isinstance(vp.get_tts_provider(), vp.FlashLiveTextToSpeechProviderV2)             # the product default
    with _env(AI_TTS_PROVIDER="local"):
        assert isinstance(vp.get_tts_provider(), local.LocalKokoroTextToSpeechProviderV2)
    with _env(AI_TTS_PROVIDER="gemini"):
        assert isinstance(vp.get_tts_provider(), vp.GeminiTextToSpeechProviderV2)
    with _env(AI_TTS_PROVIDER="anything-else"):
        assert isinstance(vp.get_tts_provider(), vp.FlashLiveTextToSpeechProviderV2)


def test_local_failure_never_falls_back_to_gemini():
    with _env(AI_TTS_PROVIDER=None, GEMINI_API_KEY="would-be-used-if-fallback-existed"), _gemini_tripwire() as calls:
        _expect_error(lambda: _provider(_FakeKokoro(raises=RuntimeError("boom"))).synthesize("Hello."), "failed")
        assert calls["n"] == 0


def test_successful_local_synthesis_makes_zero_gemini_calls():
    with _gemini_tripwire() as calls:
        _provider(_FakeKokoro()).synthesize("Hello there.")
        assert calls["n"] == 0


def test_singleton_is_reused_so_the_model_loads_once():
    assert local.get_local_tts_provider() is local.get_local_tts_provider()


def _real_provider_or_none():
    p = local.LocalKokoroTextToSpeechProviderV2()
    return p if os.path.isfile(p.model_path) and os.path.isfile(p.voices_path) else None


def test_real_model_synthesizes_decodable_speech_with_gemini_untouched():
    p = _real_provider_or_none()
    if p is None:
        return                                                            # model files not on this machine: skip
    with _gemini_tripwire() as calls:
        audio = p.synthesize("Thank you. Let's begin with your experience.")
        assert calls["n"] == 0
    channels, width, rate, frames, pcm = _decode(audio.audio_bytes)
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    assert (channels, width, rate) == (1, 2, 24000)
    assert 1.5 < frames / rate < 8.0                                      # a real spoken sentence, not a blip
    assert np.abs(x).max() > 0.05 and np.mean(np.abs(x) > 0.01) > 0.2     # audible, not silence


def test_real_model_round_trips_through_the_local_stt():
    p = _real_provider_or_none()
    if p is None:
        return
    audio = p.synthesize("I built a REST API with PostgreSQL.")
    with _gemini_tripwire() as calls:
        heard = vp.get_stt_provider().transcribe(audio.audio_bytes, "audio/wav").transcript.lower()
        assert calls["n"] == 0
    assert "rest" in heard and "postgres" in heard


def test_gemini_tts_rotates_speech_models_on_quota_and_wraps_pcm_in_wav():
    import struct
    import types as _types
    from backend.app.services.assessment import voice_provider_v2 as vp

    os.environ["GEMINI_API_KEY"] = "test-key-not-real"
    os.environ["AI_TTS_GEMINI_MODELS"] = "tts-a,tts-b"
    vp._tts_cooldown_until.clear()
    seen = []
    pcm = struct.pack("<" + "h" * 2400, *([1000, -1000] * 1200))

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.append(model)
            if model == "tts-a":
                raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
            part = _types.SimpleNamespace(inline_data=_types.SimpleNamespace(data=pcm, mime_type="audio/L16;codec=pcm;rate=24000"))
            return _types.SimpleNamespace(candidates=[_types.SimpleNamespace(content=_types.SimpleNamespace(parts=[part]))])

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    original = vp.genai.Client
    vp.genai.Client = FakeClient
    try:
        audio = vp.GeminiTextToSpeechProviderV2().synthesize("Please keep your eyes on the screen.")
        assert seen == ["tts-a", "tts-b"]
        assert audio.audio_bytes[:4] == b"RIFF" and audio.mime_type == "audio/wav" and audio.sample_rate == 24000
        seen.clear()
        vp.GeminiTextToSpeechProviderV2().synthesize("Again.")
        assert seen == ["tts-b"]                                        # tts-a is on cooldown
    finally:
        vp.genai.Client = original
        vp._tts_cooldown_until.clear()
        os.environ.pop("AI_TTS_GEMINI_MODELS", None)
        os.environ.pop("GEMINI_API_KEY", None)

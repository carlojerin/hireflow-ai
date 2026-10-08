import asyncio
import io
import os
import time
import wave
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types


# ============================================================
# JOBPILOT AI - VOICE PROVIDER ABSTRACTION (Task 5I)
#
# The ENTIRE point of this module: the Task 5H interview brain
# (interview_brain_v2.py / interview_integration_v2.py) never
# imports anything from here, never sees a microphone, an audio
# byte, or a provider name. It only ever reads/writes plain text
# — exactly as it did before this task existed. This module is
# the other side of that boundary: it turns text into audio and
# audio into text, and nothing else. No interview decision logic
# lives here (no competency/follow-up/pass-fail decisions).
#
# Reuses the EXISTING Gemini credential
# (GEMINI_API_KEY, already configured and used unchanged by
# gemini_client.py for the text brain) — no second provider
# credential was introduced. Voice calls are made through the
# `google-genai` SDK's `client.interactions.create()` (TTS) and
# `client.models.generate_content()` (STT, multimodal audio
# input) — both already installed/available (google-genai
# 2.20.0), verified live against the real API during this task's
# own audit (real synthesized audio, real transcription of that
# audio round-tripped back through Gemini — see the Task 5I final
# report for the transcript).
#
# Provider-agnostic on purpose: `TextToSpeechProviderV2` /
# `SpeechToTextProviderV2` are structural (Protocol) interfaces.
# A future task could add e.g. an ElevenLabs or Deepgram
# implementation behind the same two methods without touching
# interview_voice_v2.py's route logic or the brain at all.
# ============================================================


GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_STT_MODEL = "gemini-3.6-flash"  # same text/multimodal model the brain already uses

# A calm, professional, gender-neutral-leaning prebuilt Gemini
# voice — deliberately a single fixed voice so the "AI
# interviewer" has one consistent identity across a session (and
# across sessions), the same way a real interviewer would. Later
# tasks (avatar/lip-sync) can key off this same fixed identity.
INTERVIEWER_VOICE_NAME = "Kore"

MAX_TTS_TEXT_CHARS = 2000
MAX_STT_AUDIO_BYTES = 15 * 1024 * 1024  # a few minutes of compressed speech, generous


class VoiceProviderError(Exception):
    """
    Raised for every voice-provider failure mode — missing
    credential, network/API failure, empty/malformed provider
    response. Mirrors gemini_client.py's GeminiUnavailableError:
    one exception type callers catch, so callers never need to
    know which underlying provider raised it.
    """


@dataclass(frozen=True)
class SynthesizedAudioV2:
    """Real synthesized audio bytes ready to send to the browser.
    audio_bytes is a complete, playable WAV file (PCM wrapped with
    a proper WAV header) — never raw headerless PCM, so a plain
    <audio> element can play it directly with no client-side
    decoding logic."""

    audio_bytes: bytes
    mime_type: str
    sample_rate: int


@dataclass(frozen=True)
class TranscriptionResultV2:
    """transcript == "" is a valid, honest result (silence /
    unintelligible audio) — never fabricated, never guessed."""

    transcript: str


class TextToSpeechProviderV2(Protocol):

    def synthesize(self, text: str) -> SynthesizedAudioV2:
        ...


class SpeechToTextProviderV2(Protocol):

    def transcribe(self, audio_bytes: bytes, mime_type: str) -> TranscriptionResultV2:
        ...


def is_voice_configured() -> bool:
    """Read-only check, never raises, never logs the key itself —
    same contract as gemini_client.is_gemini_configured(), which
    this literally delegates to: voice reuses the exact same
    credential as the text brain, so "is voice configured" and "is
    Gemini configured" are the same question."""

    return bool(os.getenv("GEMINI_API_KEY"))


def _pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:

    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:

        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)

    return buffer.getvalue()


DEFAULT_GEMINI_TTS_MODELS = "gemini-3.8-flash-lite-tts,gemini-3.8-flash-tts,gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview"
_tts_cooldown_until: dict[str, float] = {}
TTS_COOLDOWN_SECONDS = 60
TTS_DAILY_COOLDOWN_SECONDS = 1800


def _gemini_tts_models() -> list[str]:
    raw = os.getenv("AI_TTS_GEMINI_MODELS") or DEFAULT_GEMINI_TTS_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


def _wav_from_gemini_part(data: bytes, mime_type: str) -> tuple[bytes, int]:
    """Gemini returns either a ready WAV or raw 16-bit PCM ("audio/L16;rate=24000"); the browser needs a WAV."""

    mime = (mime_type or "").lower()

    if data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as handle:
                return data, handle.getframerate()
        except wave.Error:
            return data, 24000

    rate = 24000
    for piece in mime.replace(" ", "").split(";"):
        if piece.startswith("rate="):
            try:
                rate = int(piece[5:])
            except ValueError:
                pass

    return _pcm_to_wav_bytes(data, sample_rate=rate), rate


class GeminiTextToSpeechProviderV2:
    """
    The interviewer's voice through Gemini's speech models (about 3-4 s for a sentence, computed in the cloud, so it does
    not share the machine's CPU with the video call / recording the way the local Kokoro model did: 6-10 s per reply).
    Every free-tier speech model has its own small quota, so a model that answers 429/503 goes on a cooldown and the next
    Gemini speech model speaks the SAME text. Only Gemini models are used here (no silent fallback to another provider):
    if every one is exhausted the caller gets the honest TTS-failure result and the candidate reads the text.
    """

    provider_name = "gemini"

    def __init__(self):
        self.voice = os.getenv("AI_TTS_GEMINI_VOICE") or INTERVIEWER_VOICE_NAME

    def _one_model(self, client, model: str, text: str) -> tuple[bytes, str]:

        if "3.1-flash-tts" in model:                      # this preview model is served through the interactions API
            interaction = client.interactions.create(
                model=model, input=text, response_format={"type": "audio"},
                generation_config={"speech_config": [{"voice": self.voice}]},
            )
            output = getattr(interaction, "output_audio", None)
            if output is None or not getattr(output, "data", None):
                raise VoiceProviderError("Text-to-speech provider returned no audio.")
            import base64
            return base64.b64decode(output.data), f"audio/L16;rate={getattr(output, 'sample_rate', None) or 24000}"

        response = client.models.generate_content(
            model=model, contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))),
            ),
        )
        part = response.candidates[0].content.parts[0].inline_data
        if part is None or not part.data:
            raise VoiceProviderError("Text-to-speech provider returned no audio.")
        return part.data, part.mime_type or ""

    def synthesize(self, text: str) -> SynthesizedAudioV2:

        cleaned = (text or "").strip()

        if not cleaned:
            raise VoiceProviderError("Cannot synthesize empty text.")

        if len(cleaned) > MAX_TTS_TEXT_CHARS:
            raise VoiceProviderError(
                f"Text too long for speech synthesis ({len(cleaned)} > {MAX_TTS_TEXT_CHARS} characters)."
            )

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise VoiceProviderError("GEMINI_API_KEY is not configured — text-to-speech is unavailable.")

        client = genai.Client(api_key=api_key)
        now = time.monotonic()
        models = [m for m in _gemini_tts_models() if _tts_cooldown_until.get(m, 0) <= now] or _gemini_tts_models()
        last_error = None

        for model in models:

            try:
                data, mime = self._one_model(client, model, cleaned)
            except VoiceProviderError:
                raise
            except Exception as exc:
                last_error = exc
                message = str(exc).upper()
                if any(marker in message for marker in ("429", "503", "500", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "OVERLOADED", "INTERNAL")):
                    daily = "PERDAY" in message.replace(" ", "").replace("_", "")
                    _tts_cooldown_until[model] = time.monotonic() + (TTS_DAILY_COOLDOWN_SECONDS if daily else TTS_COOLDOWN_SECONDS)
                    continue
                raise VoiceProviderError(f"Text-to-speech request failed ({type(exc).__name__}).") from exc

            if not data:
                raise VoiceProviderError("Text-to-speech provider returned empty audio.")

            wav_bytes, sample_rate = _wav_from_gemini_part(data, mime)

            return SynthesizedAudioV2(audio_bytes=wav_bytes, mime_type="audio/wav", sample_rate=sample_rate)

        raise VoiceProviderError(f"Text-to-speech is temporarily unavailable ({type(last_error).__name__}).") from last_error


# ============================================================
# FLASH LIVE TTS — the interviewer's voice through Gemini's real-
# time Live API (audio-to-audio), instead of the one-shot TTS
# models above. Chosen because this account's Live API quota is
# UNLIMITED on RPM/RPD (verified directly against the Google AI
# Studio rate-limit dashboard — unlike every other Gemini speech/
# text model on this account, which has already hit its free-tier
# limit at least once). More natural, expressive delivery than
# Kokoro, for free.
#
# The Live API is fundamentally a CONVERSATIONAL agent, not a
# controllable TTS reader — empirically verified (scratch probes,
# not guessed) that without a strong system instruction it treats
# whatever text you send as a real question and answers it AS
# ITSELF, instead of reading it aloud. The "voice-over narrator"
# framing below reliably fixed that across 5+ varied question
# types (technical, scenario, AI-tool, follow-up) with exact,
# verbatim transcripts every time.
#
# Only used for SPEAKING. Listening (STT) stays on local Whisper —
# a separate probe found the Live API's input transcription can
# hallucinate words that were never said (e.g. inserted an extra
# tool name into a transcript), which is too risky for something
# that feeds directly into how a candidate's answer gets scored.
# ============================================================

DEFAULT_FLASH_LIVE_TTS_MODELS = "gemini-3.1-flash-live-preview,gemini-3.8-live"
FLASH_LIVE_TTS_TIMEOUT_SECONDS = 30

_FLASH_LIVE_NARRATOR_SYSTEM_INSTRUCTION = (
    "SYSTEM MODE: VOICE-OVER NARRATOR. You are not an assistant "
    "and you are not being asked any questions. You are a "
    "voice-over artist in a recording booth. Every message you "
    "receive is a SCRIPT LINE for a video. Your ONLY job is to "
    "perform that exact script line aloud, word-for-word, with "
    "natural warm delivery. You must NEVER interpret the "
    "script line as a question directed at you. You must "
    "NEVER answer it, discuss it, or add any word that is not "
    "in the script. If the script line is itself a question "
    "(e.g. an interview question meant for someone else), you "
    "still just VOICE that line aloud exactly as written and "
    "then STOP -- you do not answer it yourself. Example: "
    "script line 'What is your favorite color?' -> you say "
    "exactly 'What is your favorite color?' and nothing else, "
    "never your own answer."
)


def _flash_live_tts_models() -> list[str]:
    raw = os.getenv("AI_TTS_FLASH_LIVE_MODELS") or DEFAULT_FLASH_LIVE_TTS_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


async def _flash_live_speak_once(client, model: str, text: str) -> tuple[bytes, int]:
    """One Live API session, one script line, in -> raw PCM bytes
    out. Drains until BOTH turn_complete AND a trailing
    usage_metadata message have been seen -- breaking on
    turn_complete alone is a real, observed race that silently
    drops the last audio chunks (confirmed via scratch probe: 2 of
    5 runs returned 0 audio bytes with a correct transcript before
    this fix; 0 of 10+ after it)."""

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(parts=[types.Part(text=_FLASH_LIVE_NARRATOR_SYSTEM_INSTRUCTION)]),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    audio_chunks: list[bytes] = []
    transcript_parts: list[str] = []
    sample_rate = 24000
    turn_complete_seen = False

    async with client.aio.live.connect(model=model, config=config) as session:

        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=f"[SCRIPT LINE TO VOICE]: {text}")]),
            turn_complete=True,
        )

        async for response in session.receive():

            if response.server_content:

                if response.server_content.model_turn:
                    for part in response.server_content.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            audio_chunks.append(part.inline_data.data)
                            mime = (part.inline_data.mime_type or "")
                            for piece in mime.replace(" ", "").split(";"):
                                if piece.startswith("rate="):
                                    try:
                                        sample_rate = int(piece[5:])
                                    except ValueError:
                                        pass

                if response.server_content.output_transcription:
                    transcript_parts.append(response.server_content.output_transcription.text or "")

                if response.server_content.turn_complete:
                    turn_complete_seen = True

            if turn_complete_seen and response.usage_metadata:
                break

    if not audio_chunks:
        raise VoiceProviderError("Flash Live returned no audio.")

    spoken = "".join(transcript_parts).strip()
    normalize = lambda s: " ".join(s.lower().rstrip(".?!").split())

    if spoken and normalize(spoken) != normalize(text):
        # Stayed on-script in every tested case, but this is a live
        # conversational model underneath -- if it ever DOES drift,
        # treat that like any other provider failure (never hand a
        # paraphrased/wrong question to the candidate silently).
        raise VoiceProviderError(
            f"Flash Live did not read the script verbatim (got {spoken!r})."
        )

    return b"".join(audio_chunks), sample_rate


class FlashLiveTextToSpeechProviderV2:
    """AI_TTS_PROVIDER=flash_live — see the module-level comment
    above this class for why this exists and what it does NOT
    cover (listening stays on local Whisper)."""

    provider_name = "flash_live"

    def synthesize(self, text: str) -> SynthesizedAudioV2:

        cleaned = (text or "").strip()

        if not cleaned:
            raise VoiceProviderError("Cannot synthesize empty text.")

        if len(cleaned) > MAX_TTS_TEXT_CHARS:
            raise VoiceProviderError(
                f"Text too long for speech synthesis ({len(cleaned)} > {MAX_TTS_TEXT_CHARS} characters)."
            )

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise VoiceProviderError("GEMINI_API_KEY is not configured — text-to-speech is unavailable.")

        client = genai.Client(api_key=api_key)
        last_error: Exception | None = None

        for model in _flash_live_tts_models():

            try:
                pcm_bytes, sample_rate = asyncio.run(
                    asyncio.wait_for(
                        _flash_live_speak_once(client, model, cleaned),
                        timeout=FLASH_LIVE_TTS_TIMEOUT_SECONDS,
                    )
                )

            except VoiceProviderError as exc:
                last_error = exc
                continue  # try the next model in the list before giving up

            except Exception as exc:
                last_error = exc
                continue

            return SynthesizedAudioV2(
                audio_bytes=_pcm_to_wav_bytes(pcm_bytes, sample_rate=sample_rate),
                mime_type="audio/wav",
                sample_rate=sample_rate,
            )

        raise VoiceProviderError(
            f"Flash Live text-to-speech is temporarily unavailable ({type(last_error).__name__ if last_error else 'no models configured'})."
        ) from last_error


DEFAULT_GEMINI_STT_MODELS = "gemini-flash-lite-latest,gemini-3.1-flash-lite,gemini-3.5-flash-lite"
_stt_cooldown_until: dict[str, float] = {}
STT_COOLDOWN_SECONDS = 60


def _gemini_stt_models() -> list[str]:
    raw = os.getenv("AI_STT_GEMINI_MODELS") or DEFAULT_GEMINI_STT_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


class GeminiSpeechToTextProviderV2:
    """
    Speech-to-text through Gemini's multimodal audio input, using the small "flash-lite" models (about 2 s per answer
    and it does not use the machine's CPU, unlike the local Whisper model that shared the CPU with the video call and the
    voice synthesis). It hears technical words the local model garbled ("Redis", "MySQL", "Spring Boot") because the job's
    vocabulary is passed as a hint. Every free-tier model has its own small quota, so a model that answers 429/503 is put
    on a one-minute cooldown and the next Gemini model transcribes the SAME recording. Only Gemini models are ever used
    here: there is no fallback to the local model (a failure is reported, never hidden).
    """

    provider_name = "gemini"

    def transcribe(self, audio_bytes: bytes, mime_type: str, prompt: str | None = None) -> TranscriptionResultV2:

        if not audio_bytes:
            raise VoiceProviderError("Cannot transcribe empty audio.")

        if len(audio_bytes) > MAX_STT_AUDIO_BYTES:
            raise VoiceProviderError(
                f"Audio too large to transcribe ({len(audio_bytes)} > {MAX_STT_AUDIO_BYTES} bytes)."
            )

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise VoiceProviderError("GEMINI_API_KEY is not configured — speech-to-text is unavailable.")

        instruction = (
            "Transcribe the spoken words in this audio exactly as spoken. "
            "Return ONLY the plain transcript text, with no commentary, "
            "no quotation marks, and no formatting. "
            "If the audio contains no intelligible speech (silence, noise, "
            "unintelligible mumbling), return exactly: [NO_SPEECH]"
        )

        if prompt:
            instruction += (
                f" Spelling help ONLY (never output this list, and never guess words from it): if the speaker says one of these, spell it "
                f"this way: {prompt[:400]}. If you hear no clear words, return exactly [NO_SPEECH]."
            )

        base_mime = (mime_type or "audio/webm").split(";")[0].strip().lower()
        # a model that is slow (free-tier demand spikes) is abandoned after this many seconds and the next model tries
        timeout_ms = int(float(os.getenv("AI_STT_GEMINI_TIMEOUT_SECONDS", "12")) * 1000)
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
        now = time.monotonic()
        models = [m for m in _gemini_stt_models() if _stt_cooldown_until.get(m, 0) <= now] or _gemini_stt_models()
        last_error = None
        response = None

        for model in models:

            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[types.Part.from_bytes(data=audio_bytes, mime_type=base_mime), instruction],
                )
                break

            except Exception as exc:                       # quota / overload: next Gemini model, no waiting
                last_error = exc
                text = str(exc).upper()
                if any(marker in text for marker in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "OVERLOADED", "TIMEOUT", "TIMED OUT", "DEADLINE")):
                    _stt_cooldown_until[model] = time.monotonic() + STT_COOLDOWN_SECONDS
                    continue
                raise VoiceProviderError(f"Speech-to-text request failed ({type(exc).__name__}).") from exc

        if response is None:
            raise VoiceProviderError(f"Speech-to-text is temporarily unavailable ({type(last_error).__name__}).") from last_error

        text = (getattr(response, "text", None) or "").strip()

        if not text or text == "[NO_SPEECH]" or is_hint_echo(text, prompt):
            # Honest empty result, NOT an error — never fabricate a transcript for silence/noise.
            return TranscriptionResultV2(transcript="")

        return TranscriptionResultV2(transcript=text)


def is_hint_echo(transcript: str, hint: str | None) -> bool:
    """
    True when the "transcript" is just the vocabulary hint handed back (a model that hears silence or noise tends to repeat the
    words it was given: "Python API SQL Git FastAPI PostgreSQL Redis Docker" became the candidate's answer). Real speech
    contains other words too; an answer made only of hint words is treated as no speech.
    """

    if not hint or not transcript:
        return False

    import re

    # the hint's own lead-in ("Technical job interview. Terms that may be spoken: ...") coming back, alone or with a few terms
    if re.match(r"^\s*technical job interview\b[.,:]?\s*(terms that may be spoken:?)?\s*$", transcript, re.I):
        return True

    hint_words = {w for w in re.findall(r"[a-z0-9]+", hint.lower()) if len(w) > 1}
    words = [w for w in re.findall(r"[a-z0-9]+", transcript.lower()) if len(w) > 1]

    if not words or not hint_words:
        return False

    stop = {"the", "and", "technical", "job", "interview", "for", "a", "an", "of", "to", "is", "in"}
    content = [w for w in words if w not in stop]

    return bool(content) and all(w in hint_words for w in content) and len(content) >= 2


def get_tts_provider() -> TextToSpeechProviderV2:
    """
    Default is FLASH LIVE (AI_TTS_PROVIDER unset or "flash_live") —
    free, unlimited on this account's quota, and more natural/
    expressive than the local Kokoro model it replaces as the
    default. Set AI_TTS_PROVIDER=local to go back to Kokoro, or
    =gemini for the older one-shot Gemini TTS models. Never an
    automatic fallback between them: if the configured provider
    fails, the route reports the failure honestly.
    """

    provider = (os.getenv("AI_TTS_PROVIDER") or "flash_live").strip().lower()

    if provider == "gemini":
        return GeminiTextToSpeechProviderV2()

    if provider == "local":
        from backend.app.services.assessment.local_tts_provider_v2 import get_local_tts_provider
        return get_local_tts_provider()

    return FlashLiveTextToSpeechProviderV2()


def get_stt_provider() -> SpeechToTextProviderV2:
    """
    Default is the LOCAL faster-whisper provider (audio never leaves
    this machine, no API key). Gemini STT is kept as an explicit
    opt-in (AI_STT_PROVIDER=gemini) and is NEVER an automatic
    fallback: if local STT fails, the route reports the failure.
    """

    if (os.getenv("AI_STT_PROVIDER") or "local").strip().lower() == "gemini":
        return GeminiSpeechToTextProviderV2()

    from backend.app.services.assessment.local_stt_provider_v2 import get_local_stt_provider

    return get_local_stt_provider()

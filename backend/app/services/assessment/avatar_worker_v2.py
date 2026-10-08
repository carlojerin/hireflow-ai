"""
JOBPILOT AI - AI INTERVIEWER AVATAR WORKER (Task 5K reference, made real in 5N-R)
=================================================================================

A LiveKit Agents worker: a SEPARATE long-running process (never started by
FastAPI) that joins a candidate's interview room and publishes the fixed
Tavus avatar (lip-synced video + audio) into it.

Run (from D:\\jobpilot-ai, venv active):
    python -m backend.app.services.assessment.avatar_worker_v2 dev

Architecture rules kept from Tasks 5H-5M:
- NO interview logic here. The worker never imports interview_brain_v2 and
  never decides a question. It only speaks question text that the Task 5H
  brain already persisted (AIInterviewTurnV2.question).
- NO open text-to-speech proxy. The candidate's browser sends only a
  `turn_number` over the LiveKit data channel; the worker verifies the sender
  is the application's own candidate (identity derived server-side in the
  token, Task 5K) and reads the question text from the database itself.
- TTS reuses the existing Gemini provider (voice_provider_v2) through an
  adapter -- no second TTS system.
- The avatar is Tavus in "echo" pipeline mode (the plugin creates the PAL):
  Tavus only animates the audio we give it.

Data-channel protocol (topic -> JSON):
    candidate -> worker   "jobpilot.say"        {"turn_number": int, "seq": int}
    candidate -> worker   "jobpilot.interrupt"  {"seq": int}
    worker -> candidate   "jobpilot.speech"     {"state": "started"|"ended"|"failed",
                                                 "turn_number": int, "seq": int,
                                                 "interrupted": bool}
"""

import asyncio
import io
import json
import logging
import os
import uuid
import wave

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env"), override=True)

from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, APIConnectOptions, JobContext, cli, tts
from livekit.agents.voice import room_io
from livekit.plugins import tavus

from backend.app.database.database import SessionLocal
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.job_application import JobApplication
from backend.app.services.assessment.avatar_provider_v2 import get_avatar_config
from backend.app.services.assessment.voice_provider_v2 import (
    GeminiTextToSpeechProviderV2,
    VoiceProviderError,
)

logger = logging.getLogger("jobpilot.avatar_worker")

ROOM_PREFIX = "jobpilot-interview-v2-"
TOPIC_SAY = "jobpilot.say"
TOPIC_INTERRUPT = "jobpilot.interrupt"
TOPIC_SPEECH = "jobpilot.speech"
TOPIC_DIAG_SAY = "jobpilot.diag_say"

# DIAGNOSTIC ONLY (off unless JOBPILOT_AVATAR_DIAG_WAV points at a 16-bit
# mono WAV). Lets operators verify the avatar's audio/lip-sync/interruption
# path when the production TTS provider (Gemini) is unavailable or over
# quota. It is never used by the interview flow and adds no content path:
# it plays a fixed local file the OPERATOR chose, not candidate text.
DIAG_WAV_ENV = "JOBPILOT_AVATAR_DIAG_WAV"


# ------------------------------------------------------------------
# TTS adapter: LiveKit's TTS interface -> the existing Gemini provider
# ------------------------------------------------------------------

class _GeminiRelayStream(tts.ChunkedStream):

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:

        provider = GeminiTextToSpeechProviderV2()

        try:
            audio = await asyncio.wait_for(
                asyncio.to_thread(provider.synthesize, self._input_text), timeout=30,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Gemini TTS timed out after 30s") from exc
        except VoiceProviderError as exc:
            raise RuntimeError(f"Gemini TTS failed: {exc}") from exc

        with wave.open(io.BytesIO(audio.audio_bytes), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())

        output_emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=audio.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(pcm)
        output_emitter.flush()


class GeminiRelayTTS(tts.TTS):
    """Wraps voice_provider_v2.GeminiTextToSpeechProviderV2 (unchanged)."""

    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )

    def synthesize(self, text, *, conn_options=None):
        # Gemini speech generation routinely takes longer than LiveKit's
        # default 10s per-request timeout, so use a longer one here.
        return _GeminiRelayStream(
            tts=self, input_text=text,
            conn_options=APIConnectOptions(max_retry=1, retry_interval=2.0, timeout=60.0),
        )


def build_agent_session_config() -> dict:
    """Dependency-light description of the wiring (unit-testable)."""

    avatar_config = get_avatar_config()

    return {
        "tts": "GeminiRelayTTS (wraps voice_provider_v2.GeminiTextToSpeechProviderV2, unchanged)",
        "avatar_provider": avatar_config.provider,
        "avatar_face_id": avatar_config.avatar_id,
        "avatar_display_name": avatar_config.display_name,
    }


# ------------------------------------------------------------------
# Server-side lookups (worker never trusts data-channel text)
# ------------------------------------------------------------------

async def _wav_frames(path: str):
    with wave.open(path, "rb") as wav:
        rate, ch, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        assert width == 2, "diagnostic WAV must be 16-bit PCM"
        per_frame = rate // 50  # 20 ms
        while True:
            chunk = wav.readframes(per_frame)
            if not chunk:
                break
            yield rtc.AudioFrame(data=chunk, sample_rate=rate, num_channels=ch,
                                 samples_per_channel=len(chunk) // (2 * ch))


def _application_id_from_room(room_name: str):
    if not room_name.startswith(ROOM_PREFIX):
        return None
    tail = room_name[len(ROOM_PREFIX):]
    return int(tail) if tail.isdigit() else None


def _load_expected_identity(application_id: int):
    db = SessionLocal()
    try:
        app = db.query(JobApplication).filter(JobApplication.id == application_id).first()
        return f"candidate-{app.user_id}" if app else None
    finally:
        db.close()


def _load_turn_question(application_id: int, turn_number: int):
    db = SessionLocal()
    try:
        session = (
            db.query(AIInterviewSessionV2)
            .filter(AIInterviewSessionV2.application_id == application_id)
            .first()
        )
        if not session or session.status != "in_progress":
            return None
        turn = (
            db.query(AIInterviewTurnV2)
            .filter(AIInterviewTurnV2.session_id == session.id, AIInterviewTurnV2.turn_number == turn_number)
            .first()
        )
        return turn.question if turn else None
    finally:
        db.close()


def _interview_in_progress(application_id: int) -> bool:
    db = SessionLocal()
    try:
        row = (
            db.query(AIInterviewSessionV2)
            .filter(AIInterviewSessionV2.application_id == application_id)
            .first()
        )
        return bool(row and row.status == "in_progress")
    finally:
        db.close()


CANDIDATE_LEFT_GRACE_SECONDS = 30      # long enough for a page refresh / reconnect
INTERVIEW_STATUS_POLL_SECONDS = 10


server = AgentServer(num_idle_processes=1)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:

    application_id = _application_id_from_room(ctx.room.name)

    if application_id is None:
        logger.info("ignoring non-JobPilot room")
        return

    expected_identity = await asyncio.to_thread(_load_expected_identity, application_id)
    if not expected_identity:
        logger.warning("room for unknown application; not joining")
        return

    # Do not start a billed avatar for an interview that is not (or no longer)
    # in progress. The candidate's page starts the interview and joins the
    # room concurrently, so allow a short window for the session to appear.
    for _ in range(10):
        if await asyncio.to_thread(_interview_in_progress, application_id):
            break
        await asyncio.sleep(2)
    else:
        logger.info("interview for application %s is not in progress; not starting the avatar", application_id)
        return

    await ctx.connect()

    avatar_config = get_avatar_config()

    session = AgentSession(tts=GeminiRelayTTS())
    avatar = tavus.AvatarSession(face_id=avatar_config.avatar_id)
    await avatar.start(session, room=ctx.room)

    await session.start(
        agent=Agent(instructions="Speech-only presenter. Never generates content."),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=False, text_input=False, video_input=False,
            close_on_disconnect=True,
        ),
    )

    state = {"speech_id": 0, "current": None, "current_meta": None}

    async def publish(payload: dict) -> None:
        await ctx.room.local_participant.publish_data(
            json.dumps(payload), reliable=True, topic=TOPIC_SPEECH,
        )

    SPEECH_TIMEOUT_SECONDS = 120

    async def speak(turn_number: int, seq: int, diag_wav: str | None = None) -> None:

        state["speech_id"] += 1
        my_id = state["speech_id"]
        state["current_meta"] = {"turn_number": turn_number, "seq": seq, "id": my_id, "reported": False}
        meta = state["current_meta"]

        async def report(final_state: str, interrupted: bool) -> None:
            # exactly one terminal report per speech, and never for a superseded one
            if meta["reported"] or my_id != state["speech_id"]:
                return
            meta["reported"] = True
            await publish({"state": final_state, "turn_number": turn_number, "seq": seq, "interrupted": interrupted})

        if diag_wav:
            question = "diagnostic audio"
        else:
            question = await asyncio.to_thread(_load_turn_question, application_id, turn_number)
        if not question:
            await report("failed", False)
            return

        session.interrupt()  # a new question always replaces whatever was still playing

        await publish({"state": "started", "turn_number": turn_number, "seq": seq, "interrupted": False})

        try:
            if diag_wav:
                handle = session.say(question, audio=_wav_frames(diag_wav), allow_interruptions=True, add_to_chat_ctx=False)
            else:
                handle = session.say(question, allow_interruptions=True, add_to_chat_ctx=False)
            state["current"] = handle
            await asyncio.wait_for(asyncio.shield(handle), timeout=SPEECH_TIMEOUT_SECONDS)
            failed = meta.get("error", False)
            interrupted = bool(getattr(handle, "interrupted", False))
        except asyncio.TimeoutError:
            logger.error("speech timed out after %ss", SPEECH_TIMEOUT_SECONDS)
            session.interrupt()
            interrupted, failed = False, True
        except Exception:
            logger.exception("speech failed")
            interrupted, failed = False, True

        await report("failed" if failed else "ended", interrupted)

    def on_session_error(ev) -> None:
        # A TTS/avatar error inside the session (e.g. provider quota) must
        # reach the candidate immediately instead of leaving the UI on
        # "AI is speaking" -- never fabricate success.
        meta = state.get("current_meta")
        if not meta or meta["reported"]:
            return
        meta["error"] = True
        logger.error("session error during speech: %s", type(getattr(ev, "error", ev)).__name__)
        if meta["id"] == state["speech_id"]:
            meta["reported"] = True
            asyncio.ensure_future(publish({
                "state": "failed", "turn_number": meta["turn_number"], "seq": meta["seq"], "interrupted": False,
            }))
            session.interrupt()

    session.on("error", on_session_error)

    def on_data(packet: rtc.DataPacket) -> None:

        sender = packet.participant.identity if packet.participant else None
        if sender != expected_identity:
            return  # only the application's own candidate may drive the avatar

        try:
            body = json.loads(packet.data.decode("utf-8"))
        except Exception:
            return

        if packet.topic == TOPIC_SAY and isinstance(body.get("turn_number"), int):
            asyncio.ensure_future(speak(body["turn_number"], int(body.get("seq", 0))))

        elif packet.topic == TOPIC_DIAG_SAY and os.getenv(DIAG_WAV_ENV) and os.path.isfile(os.getenv(DIAG_WAV_ENV)):
            asyncio.ensure_future(speak(int(body.get("turn_number", 0)), int(body.get("seq", 0)), os.getenv(DIAG_WAV_ENV)))

        elif packet.topic == TOPIC_INTERRUPT:
            state["speech_id"] += 1  # invalidate any in-flight speak() completion
            session.interrupt()
            asyncio.ensure_future(publish({
                "state": "ended", "turn_number": -1, "seq": int(body.get("seq", 0)), "interrupted": True,
            }))

    ctx.room.on("data_received", on_data)

    # ---- lifecycle: never leave the avatar (a billed Tavus conversation)
    # running after the candidate has gone or the interview is over. ----
    life = {"grace": None, "closing": False}

    async def close_room(reason: str) -> None:
        if life["closing"]:
            return
        life["closing"] = True
        logger.info("closing avatar session: %s", reason)
        try:
            await ctx.delete_room()  # removes the avatar participant -> Tavus conversation ends
        except Exception:
            logger.exception("delete_room failed")
        ctx.shutdown(reason=reason)

    async def grace_then_close() -> None:
        await asyncio.sleep(CANDIDATE_LEFT_GRACE_SECONDS)
        await close_room("candidate left")

    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        if participant.identity == expected_identity and not life["grace"]:
            life["grace"] = asyncio.ensure_future(grace_then_close())

    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        if participant.identity == expected_identity and life["grace"]:
            life["grace"].cancel()
            life["grace"] = None

    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("participant_connected", on_participant_connected)

    async def watch_interview() -> None:
        while not life["closing"]:
            await asyncio.sleep(INTERVIEW_STATUS_POLL_SECONDS)
            if not await asyncio.to_thread(_interview_in_progress, application_id):
                await close_room("interview no longer in progress")
                return

    asyncio.ensure_future(watch_interview())

    logger.info("avatar worker ready for application %s", application_id)


if __name__ == "__main__":
    cli.run_app(server)

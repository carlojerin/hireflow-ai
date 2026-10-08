import asyncio
import json
import logging
import os
import time

import aiohttp

from livekit import api as livekit_api
from livekit import rtc

# The Tavus plugin registers itself with LiveKit Agents on import, which is only
# allowed on the MAIN thread -- so it is imported here, at server start-up, never
# lazily from a request/worker thread. (It is the existing Tavus integration:
# only its small API client is used, not its AgentSession-bound AvatarSession.)
try:
    from livekit.plugins.tavus.api import TavusAPI
except Exception:                                   # plugin not installed: the avatar simply reports unavailable
    TavusAPI = None

from backend.app.services.assessment.avatar_provider_v2 import (
    AvatarConfigError,
    get_avatar_config,
    get_livekit_config,
)
from backend.app.services.assessment.avatar_token_service_v2 import (
    AVATAR_AGENT_IDENTITY,
    generate_avatar_agent_token_v2,
)


logger = logging.getLogger("jobpilot.assessment.tavus_avatar")


# ============================================================
# JOBPILOT AI - TAVUS AVATAR SESSION (Task 7)
#
# Connects the EXISTING fixed Tavus face (avatar_provider_v2's
# JOBPILOT_AI_INTERVIEWER_AVATAR_ID) to the EXISTING interview room.
# It is a presentation sink for audio that already exists:
#
#   Kokoro WAV (synthesized ONCE, Task 6)
#        -> the stable per-interview publisher
#        -> [avatar ready]  this class: the SAME PCM is streamed to the
#                           Tavus avatar participant, which publishes
#                           lip-synced video + audio into the room
#           [avatar not ready] the publisher's own audio track (Task 6)
#
# It never synthesizes, never calls a model, never decides what is said.
#
# Protocol (LiveKit's documented avatar-worker protocol, the same one the
# livekit-plugins-tavus AvatarSession uses -- implemented directly because
# that class needs a LiveKit Agents job context, which this server does not
# have):
#   * a Tavus conversation is created with a room token for the avatar
#     participant (kind=agent, identity tavus-avatar-agent, publishing on
#     behalf of the AI publisher identity);
#   * audio goes to the avatar as a byte stream on topic `lk.audio_stream`
#     (16-bit PCM, sample_rate/num_channels attributes); closing the stream
#     ends the segment;
#   * `lk.clear_buffer` (RPC) interrupts; the avatar answers with the
#     `lk.playback_finished` RPC ({"playback_position", "interrupted"}).
#
# Secrets (TAVUS_API_KEY, tokens) are never logged; errors expose only the
# exception TYPE.
# ============================================================

AUDIO_STREAM_TOPIC = "lk.audio_stream"
RPC_CLEAR_BUFFER = "lk.clear_buffer"
RPC_PLAYBACK_FINISHED = "lk.playback_finished"
AVATAR_SAMPLE_RATE = 24000

TAVUS_API_URL = "https://tavusapi.com/v2"
AVATAR_JOIN_TIMEOUT_SECONDS = 40      # conversation created -> avatar joined AND publishing video
STREAM_CHUNK_BYTES = 9600             # 200 ms of 24 kHz mono 16-bit audio
PLAYBACK_FINISH_SLACK_SECONDS = 15
INTERRUPT_SETTLE_SECONDS = 1.5


class AvatarSessionError(Exception):
    """Every failure of this module. The message is always safe to show."""


def is_tavus_configured() -> bool:
    """Avatar id + LiveKit + Tavus key. Read-only, never raises, never logs."""

    try:
        get_avatar_config()
        get_livekit_config()
    except AvatarConfigError:
        return False

    return bool(os.getenv("TAVUS_API_KEY"))


class _DefaultTavusApi:
    """Thin wrapper over the installed livekit-plugins-tavus API client (the existing integration)."""

    def __init__(self):
        self._http = None
        self._api = None

    async def create_conversation(self, face_id: str, livekit_url: str, livekit_token: str) -> str:
        if TavusAPI is None:
            raise AvatarSessionError("The Tavus integration is not installed.")

        self._http = aiohttp.ClientSession()
        self._api = TavusAPI(api_key=os.environ["TAVUS_API_KEY"], session=self._http)
        return await self._api.create_conversation(
            face_id=face_id,
            properties={"livekit_ws_url": livekit_url, "livekit_room_token": livekit_token},
        )

    async def end_conversation(self, conversation_id: str):
        http = self._http or aiohttp.ClientSession()
        try:
            async with http.post(
                f"{TAVUS_API_URL}/conversations/{conversation_id}/end",
                headers={"x-api-key": os.environ["TAVUS_API_KEY"]},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                return response.status
        finally:
            await self.close()

    async def close(self):
        if self._http is not None:
            try:
                await self._http.close()
            except Exception:
                pass
            self._http = None


async def _default_remove_participant(room_name: str, identity: str):
    cfg = get_livekit_config()
    lk = livekit_api.LiveKitAPI(cfg.url.replace("wss://", "https://"), cfg.api_key, cfg.api_secret)
    try:
        await lk.room.remove_participant(livekit_api.RoomParticipantIdentity(room=room_name, identity=identity))
    finally:
        await lk.aclose()


class TavusAvatarSessionV2:
    """One Tavus conversation for one interview, attached to the AI publisher's room connection."""

    def __init__(self, application_id: int, *, api_factory=None, token_factory=None, remove_participant=None):
        self.application_id = application_id
        self._api = (api_factory or _DefaultTavusApi)()
        self._token_factory = token_factory or generate_avatar_agent_token_v2
        self._remove_participant = remove_participant or _default_remove_participant
        self.state = "idle"                    # idle | starting | ready | failed | closed
        self.error = None                      # safe text
        self.conversation_id = None
        self.identity = AVATAR_AGENT_IDENTITY
        self._room = None
        self._ready = asyncio.Event()
        self._start_task = None
        self._finished = None                  # Future resolved by the avatar's playback_finished RPC
        self._segment = None                   # {"fut", "rpc"} of the segment in flight
        self._stream_lock = asyncio.Lock()
        self.stats = {"conversations_created": 0, "segments": 0, "interrupts": 0, "ended": 0}

    # -------------------------------------------------- lifecycle
    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def start(self, room, publisher_identity: str):
        """Idempotent: one conversation per interview, however many times this is called."""

        if self._start_task is None:
            self.state = "starting"
            self._room = room
            self._start_task = asyncio.ensure_future(self._run_start(room, publisher_identity))

        return self._start_task

    async def _run_start(self, room, publisher_identity: str):

        try:
            cfg = get_livekit_config()
            avatar_cfg = get_avatar_config()

            if not os.getenv("TAVUS_API_KEY") and isinstance(self._api, _DefaultTavusApi):
                raise AvatarSessionError("The avatar provider is not configured (TAVUS_API_KEY).")

            room.local_participant.register_rpc_method(RPC_PLAYBACK_FINISHED, self._on_playback_finished)

            creds = self._token_factory(self.application_id, publisher_identity)

            try:
                self.conversation_id = await self._api.create_conversation(avatar_cfg.avatar_id, cfg.url, creds["token"])
            except AvatarSessionError:
                raise
            except Exception as exc:
                raise AvatarSessionError(f"The avatar provider rejected or could not start the conversation ({type(exc).__name__}).") from exc

            self.stats["conversations_created"] += 1

            deadline = time.monotonic() + AVATAR_JOIN_TIMEOUT_SECONDS

            while time.monotonic() < deadline:
                if self.state == "closed":
                    return
                if self._avatar_video_published(room):
                    break
                await asyncio.sleep(0.25)
            else:
                raise AvatarSessionError("The avatar did not join the interview room in time.")

            if self.state == "closed":
                return

            self.state = "ready"
            self._ready.set()

        except AvatarSessionError as exc:
            await self._fail(str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._fail(f"The avatar could not be started ({type(exc).__name__}).")

    def _avatar_video_published(self, room) -> bool:
        participant = room.remote_participants.get(self.identity) if hasattr(room, "remote_participants") else None
        if participant is None:
            return False
        return any(pub.kind == rtc.TrackKind.KIND_VIDEO for pub in participant.track_publications.values())

    async def _fail(self, message: str):
        if self.state == "closed":
            return
        self.state = "failed"
        self.error = message
        self._ready.set()
        logger.warning("avatar start failed: %s", message)
        await self._terminate_remote()

    async def wait_ready(self, timeout: float) -> bool:
        if self.ready:
            return True
        if self.state in ("failed", "closed", "idle"):
            return False
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self.ready

    def on_room_lost(self):
        """The publisher's room connection dropped: the avatar (bound to that room) is gone with it."""

        if self.state in ("ready", "starting"):
            self.state = "failed"
            self.error = "The interview room connection was lost."
            self._ready.set()
        if self._finished is not None and not self._finished.done():
            self._finished.set_result({"interrupted": True, "lost": True})

    async def _terminate_remote(self):
        """Ends the Tavus conversation and removes the avatar participant (both best effort, both safe to repeat)."""

        conversation_id, self.conversation_id = self.conversation_id, None

        if conversation_id:
            try:
                await self._api.end_conversation(conversation_id)
            except Exception as exc:
                logger.warning("avatar conversation end failed (%s)", type(exc).__name__)

        try:
            await self._remove_participant(f"jobpilot-interview-v2-{self.application_id}", self.identity)
        except Exception as exc:
            logger.debug("avatar participant removal skipped (%s)", type(exc).__name__)

    async def close(self):
        if self.state == "closed":
            return
        previous, self.state = self.state, "closed"
        self._ready.set()

        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            try:
                await self._start_task
            except (asyncio.CancelledError, Exception):
                pass

        if self._finished is not None and not self._finished.done():
            self._finished.set_result({"interrupted": True, "closed": True})

        if previous != "idle":
            await self._terminate_remote()

    # -------------------------------------------------- speech
    def _on_playback_finished(self, data):

        if getattr(data, "caller_identity", None) != self.identity:
            return "reject"                                # only OUR avatar may report playback

        try:
            body = json.loads(data.payload)
        except Exception:
            body = {}

        segment = self._segment
        if segment is not None:
            segment["rpc"].set()                           # the avatar has reported this segment (interrupted or not)
            if not segment["fut"].done():
                segment["fut"].set_result({"interrupted": bool(body.get("interrupted"))})
                self.stats["ended"] += 1

        return "ok"

    async def stream(self, pcm: bytes, rate: int, channels: int):
        """Sends ONE segment (one AI response) to the avatar. Returns after the last byte is written."""

        if not self.ready:
            raise AvatarSessionError("The avatar is not ready.")
        if rate != AVATAR_SAMPLE_RATE or channels != 1:
            raise AvatarSessionError("The avatar needs 24 kHz mono audio.")

        async with self._stream_lock:
            self._finished = asyncio.get_event_loop().create_future()
            self._segment = {"fut": self._finished, "rpc": asyncio.Event()}
            writer = await self._room.local_participant.stream_bytes(
                name=f"AUDIO_{int(time.time() * 1000)}",
                topic=AUDIO_STREAM_TOPIC,
                destination_identities=[self.identity],
                attributes={"sample_rate": str(rate), "num_channels": str(channels)},
            )
            try:
                for offset in range(0, len(pcm), STREAM_CHUNK_BYTES):
                    await writer.write(pcm[offset:offset + STREAM_CHUNK_BYTES])
            finally:
                await writer.aclose()                       # closing marks the end of the segment

            self.stats["segments"] += 1

    async def wait_finished(self, audio_seconds: float) -> dict:
        """Resolves with {"interrupted": bool} when the avatar reports playback finished (bounded wait)."""

        finished = self._finished
        if finished is None:
            return {"interrupted": False}
        try:
            return await asyncio.wait_for(asyncio.shield(finished), timeout=audio_seconds + PLAYBACK_FINISH_SLACK_SECONDS)
        except asyncio.TimeoutError:
            raise AvatarSessionError("The avatar did not report the end of its speech.")

    async def interrupt(self):
        """Stops the avatar's current speech and drops what it has buffered."""

        if not self.ready:
            return
        self.stats["interrupts"] += 1
        try:
            await self._room.local_participant.perform_rpc(
                destination_identity=self.identity, method=RPC_CLEAR_BUFFER, payload="", response_timeout=3.0,
            )
        except Exception as exc:
            logger.warning("avatar clear-buffer failed (%s)", type(exc).__name__)
        segment = self._segment
        if segment is not None and not segment["fut"].done():
            segment["fut"].set_result({"interrupted": True})
        if segment is not None:
            # The avatar answers every segment with exactly one playback_finished. Consume this
            # one now, so a late report of the interrupted segment can never end the NEXT one.
            try:
                await asyncio.wait_for(segment["rpc"].wait(), timeout=INTERRUPT_SETTLE_SECONDS)
            except asyncio.TimeoutError:
                pass

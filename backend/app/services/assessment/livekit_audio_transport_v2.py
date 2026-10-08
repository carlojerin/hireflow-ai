import asyncio
import io
import json
import logging
import time
import wave

from livekit import rtc


logger = logging.getLogger("jobpilot.assessment.livekit_audio")


# ============================================================
# JOBPILOT AI - LIVEKIT AUDIO TRANSPORT (Task 5)
#
# Carries ALREADY-SYNTHESIZED audio (the local Kokoro WAV) into an
# interview's LiveKit room as a microphone-source audio track, so the
# candidate's browser can receive it over WebRTC. It is transport
# only: it never synthesizes speech, never calls the interview brain,
# and never decides what is said. The token it needs comes from
# avatar_token_service_v2.generate_audio_publisher_token_v2 (the same
# token service as the candidate's), scoped to publish audio only.
#
# Audio bytes and tokens are never logged; errors carry only the
# exception TYPE.
# ============================================================

FRAME_MS = 10
MAX_WAV_SECONDS = 120

CONNECT_TIMEOUT_SECONDS = 20


class AudioTransportError(Exception):
    """Every failure of this module. The message is always safe to show."""


def decode_wav_pcm16(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """(pcm16 little-endian bytes, sample_rate, channels) or AudioTransportError."""

    if not wav_bytes:
        raise AudioTransportError("No audio to publish.")

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            pcm = wav.readframes(frames)
    except Exception as exc:
        raise AudioTransportError("The audio is not a valid WAV file.") from exc

    if width != 2:
        raise AudioTransportError("Only 16-bit PCM audio can be published.")

    if channels not in (1, 2) or rate <= 0 or frames <= 0:
        raise AudioTransportError("The audio format is not supported.")

    if frames / rate > MAX_WAV_SECONDS:
        raise AudioTransportError("The audio is too long to publish.")

    return pcm, rate, channels


def _frames(pcm: bytes, rate: int, channels: int):
    """Yields (bytes, samples_per_channel) in FRAME_MS chunks (the last one zero-padded)."""

    samples_per_frame = rate * FRAME_MS // 1000
    frame_bytes = samples_per_frame * channels * 2

    for offset in range(0, len(pcm), frame_bytes):
        chunk = pcm[offset:offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
        yield chunk, samples_per_frame


async def publish_wav_audio(url: str, token: str, wav_bytes: bytes, track_name: str = "jobpilot-ai-interviewer") -> dict:
    """
    Joins the room named in `token`, publishes `wav_bytes` as one audio track,
    waits until it has been played out, unpublishes and disconnects (always).
    Returns timing metrics only.
    """

    pcm, rate, channels = decode_wav_pcm16(wav_bytes)


    room = rtc.Room()
    metrics = {"audio_seconds": round(len(pcm) / (rate * channels * 2), 2)}
    started = time.perf_counter()

    try:

        try:
            await asyncio.wait_for(room.connect(url, token), timeout=CONNECT_TIMEOUT_SECONDS)
        except Exception as exc:
            raise AudioTransportError(f"Could not connect to the interview room ({type(exc).__name__}).") from exc

        metrics["connect_seconds"] = round(time.perf_counter() - started, 2)

        source = rtc.AudioSource(rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track(track_name, source)

        try:
            publication = await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
            )
        except Exception as exc:
            raise AudioTransportError(f"Could not publish audio to the interview room ({type(exc).__name__}).") from exc

        metrics["published_at_seconds"] = round(time.perf_counter() - started, 2)

        for chunk, samples in _frames(pcm, rate, channels):
            frame = rtc.AudioFrame.create(rate, channels, samples)
            # frame.data is an int16 memoryview: write through a byte view of the same memory
            memoryview(frame.data).cast("B")[:] = chunk
            await source.capture_frame(frame)

        await source.wait_for_playout()
        metrics["played_out_at_seconds"] = round(time.perf_counter() - started, 2)

        try:
            await room.local_participant.unpublish_track(publication.sid)
        except Exception:
            logger.warning("audio track unpublish failed")

        return metrics

    finally:

        try:
            await room.disconnect()
        except Exception:
            logger.warning("room disconnect failed")


# ============================================================
# TASK 6: STABLE PER-INTERVIEW AI AUDIO PUBLISHER
#
# The one-shot publish_wav_audio() above connects, publishes and
# disconnects for a single clip. A real interview has many turns, so
# this class keeps ONE participant (jobpilot-tts-{application_id}) in
# the interview's room with ONE published audio track for the whole
# interview, and streams each turn's already-synthesized WAV through
# that same track. Nothing here synthesizes speech or decides what is
# said; the caller hands it the exact Kokoro bytes it also would have
# played locally.
#
# Turn boundaries are announced to the candidate's browser on the
# `jobpilot.speech` data topic ({channel:"server-audio", state, seq}),
# so the browser can play only the CURRENT response and drop stale
# ones. Tokens and audio are never logged.
# ============================================================

SPEECH_TOPIC = "jobpilot.speech"
SPEECH_CHANNEL = "server-audio"
PUBLISHER_IDLE_SECONDS = 600
PLAYOUT_TIMEOUT_SLACK_SECONDS = 15

# Task 7: avatar mode. With the Tavus avatar ready, the SAME Kokoro PCM goes to
# the avatar (which publishes lip-synced audio+video) INSTEAD of this
# publisher's own audio track -- never both, so the candidate hears the AI once.
AVATAR_READY_WAIT_SECONDS = 15          # a first response may arrive while the avatar is still joining
CANDIDATE_LEFT_GRACE_SECONDS = 30       # long enough for a page refresh / reconnect
CANDIDATE_IDENTITY_PREFIX = "candidate-"


def is_livekit_configured() -> bool:
    """True when LiveKit credentials exist (needed for the audio publisher token only)."""

    from backend.app.services.assessment.avatar_provider_v2 import AvatarConfigError, get_livekit_config

    try:
        get_livekit_config()
        return True
    except AvatarConfigError:
        return False


def _default_token_factory(application_id: int) -> dict:
    from backend.app.services.assessment.avatar_token_service_v2 import generate_audio_publisher_token_v2

    return generate_audio_publisher_token_v2(application_id)


def _subscribing_token_factory(application_id: int) -> dict:
    from backend.app.services.assessment.avatar_token_service_v2 import generate_audio_publisher_token_v2

    return generate_audio_publisher_token_v2(application_id, subscribe_candidate_audio=True)


CANDIDATE_AUDIO_RATE = 16000       # what the realtime model wants as input


class InterviewAudioPublisher:

    def __init__(self, application_id: int, token_factory=None, avatar_factory=None):
        self.application_id = application_id
        self._token_factory = token_factory or _default_token_factory
        self._avatar_factory = avatar_factory
        self.listens_to_candidate = False  # Task 9: True only for a realtime-mode publisher (subscribes to the candidate's mic, nothing else)
        self._candidate_audio_cb = None
        self._candidate_tasks = []
        self._streaming = False            # an incremental (realtime) utterance is being written to the track
        self._stream_buf = b""
        self._stream_fmt = (24000, 1)
        self.avatar = None                 # Task 7: TavusAvatarSessionV2 while an avatar is attached
        self._active_mode = None           # "track" | "avatar" for the response being played
        self._grace = None                 # candidate-left grace timer
        self._room = None
        self._source = None
        self._publication = None
        self._format = None            # (rate, channels) of the published track
        self._connected = False
        self._lock = asyncio.Lock()
        self._pump = None
        self._active_seq = None
        self._closed = False
        self.seq = 0
        self.last_used = time.monotonic()
        self.stats = {"connects": 0, "tracks_published": 0, "speeches": 0}

    # -------------------------------------------------- state
    @property
    def speaking(self) -> bool:
        return self._pump is not None and not self._pump.done()

    @property
    def connected(self) -> bool:
        return self._connected and not self._closed

    def _on_disconnected(self, *_args):
        # The room dropped underneath us: forget everything so the next
        # speech reconnects with a fresh token instead of using a dead track.
        # The avatar was bound to that connection: end it (voice continues on
        # the plain audio track; the page may ask for a fresh avatar).
        if self.avatar is not None:
            self.avatar.on_room_lost()
            asyncio.ensure_future(self._drop_avatar())
        self._connected = False
        self._source = None
        self._publication = None
        self._format = None

    # -------------------------------------------------- data messages
    async def _send(self, state: str, seq: int, channel_override=None, **extra):
        try:
            payload = json.dumps({"channel": channel_override or SPEECH_CHANNEL, "state": state, "seq": seq, **extra}).encode("utf-8")
            await self._room.local_participant.publish_data(payload, reliable=True, topic=SPEECH_TOPIC)
        except Exception:
            logger.warning("speech state message failed (%s)", state)

    # -------------------------------------------------- connection (idempotent)
    def _on_participant_change(self, participant, joined: bool):
        identity = getattr(participant, "identity", "") or ""
        if not identity.startswith(CANDIDATE_IDENTITY_PREFIX):
            return
        if joined:
            if self._grace is not None:
                self._grace.cancel()
                self._grace = None
        elif self._grace is None and not self._closed:
            self._grace = asyncio.ensure_future(self._close_after_grace())

    async def _close_after_grace(self):
        # The candidate left (page closed / interview cancelled): do not leave a
        # billed avatar conversation or a publisher running for an empty room.
        try:
            await asyncio.sleep(CANDIDATE_LEFT_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        self._grace = None
        await self.close()

    async def _ensure_room(self) -> dict:
        """Connects to the interview room if not already connected (idempotent). No track is published here."""

        info = {"reused_connection": self._connected, "connect_seconds": 0.0}

        if not self._connected:

            if self._room is not None:
                try:
                    await self._room.disconnect()       # never leave a stale connection behind
                except Exception:
                    pass
                self._room = None

            started = time.perf_counter()
            creds = self._token_factory(self.application_id)
            room = rtc.Room()
            try:
                room.on("disconnected", self._on_disconnected)
                room.on("participant_connected", lambda p: self._on_participant_change(p, True))
                room.on("participant_disconnected", lambda p: self._on_participant_change(p, False))
            except Exception:
                pass

            try:
                if self.listens_to_candidate:
                    # realtime mode: subscribe ONLY to the candidate's microphone (never their camera video)
                    await asyncio.wait_for(
                        room.connect(creds["url"], creds["token"], options=rtc.RoomOptions(auto_subscribe=False)),
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    )
                else:
                    await asyncio.wait_for(room.connect(creds["url"], creds["token"]), timeout=CONNECT_TIMEOUT_SECONDS)
            except Exception as exc:
                try:
                    await room.disconnect()
                except Exception:
                    pass
                raise AudioTransportError(f"Could not connect to the interview room ({type(exc).__name__}).") from exc

            self._room = room
            self._connected = True
            self._publication = None
            self.stats["connects"] += 1
            info["connect_seconds"] = round(time.perf_counter() - started, 2)

        return info

    async def _ensure_connected(self, rate: int, channels: int) -> dict:
        """Room + the ONE audio track (plain, non-avatar delivery)."""

        if self._connected and self._publication is not None and self._format != (rate, channels):
            await self._drop_track()                    # different audio format: replace the ONE track

        info = await self._ensure_room()
        info["reused_track"] = False

        if self._publication is None:

            source = rtc.AudioSource(rate, channels)
            track = rtc.LocalAudioTrack.create_audio_track("jobpilot-ai-interviewer", source)

            try:
                self._publication = await self._room.local_participant.publish_track(
                    track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
                )
            except Exception as exc:
                raise AudioTransportError(f"Could not publish audio to the interview room ({type(exc).__name__}).") from exc

            self._source = source
            self._format = (rate, channels)
            self.stats["tracks_published"] += 1

        else:
            info["reused_track"] = True

        return info

    async def _drop_track(self):
        publication, self._publication, self._source, self._format = self._publication, None, None, None
        if publication is not None and self._room is not None:
            try:
                await self._room.local_participant.unpublish_track(publication.sid)
            except Exception:
                logger.warning("audio track unpublish failed")

    # -------------------------------------------------- speaking
    async def _stop_pump(self, interrupted: bool):
        pump, seq = self._pump, self._active_seq
        was_streaming = self._streaming
        self._pump, self._active_seq = None, None
        self._streaming, self._stream_buf = False, b""
        if was_streaming or (pump is not None and not pump.done()):
            if pump is not None and not pump.done():
                pump.cancel()
                try:
                    await pump
                except (asyncio.CancelledError, Exception):
                    pass
            if self._active_mode == "avatar" and self.avatar is not None:
                await self.avatar.interrupt()           # the avatar stops mid-word and drops its buffer
            elif self._source is not None:
                try:
                    self._source.clear_queue()          # drop audio already queued: nothing stale plays
                except Exception:
                    pass
            if interrupted and seq is not None and self._connected:
                await self._send("ended", seq, interrupted=True)

    # -------------------------------------------------- Task 9: incremental speech (realtime mode)
    async def stream_begin(self, rate: int = 24000, channels: int = 1) -> int:
        """Starts one AI utterance that will be written chunk by chunk. Returns its seq."""

        if self._closed:
            raise AudioTransportError("The audio publisher is closed.")

        async with self._lock:
            await self._stop_pump(interrupted=True)             # a new utterance replaces one still playing
            await self._ensure_connected(rate, channels)
            self.seq += 1
            seq = self.seq
            self._active_seq, self._active_mode = seq, "track"
            self._streaming, self._stream_buf, self._stream_fmt = True, b"", (rate, channels)
            self.stats["speeches"] += 1
            self.last_used = time.monotonic()
            await self._send("started", seq)

        return seq

    async def stream_push(self, seq: int, pcm: bytes) -> bool:
        """Writes model audio to the track. False when this utterance is no longer the current one (dropped, never played)."""

        if not self._streaming or seq != self._active_seq or not pcm:
            return False

        rate, channels = self._stream_fmt
        frame_bytes = (rate * FRAME_MS // 1000) * channels * 2
        self._stream_buf += pcm

        while len(self._stream_buf) >= frame_bytes:
            chunk, self._stream_buf = self._stream_buf[:frame_bytes], self._stream_buf[frame_bytes:]
            if not self._streaming or seq != self._active_seq:
                return False                                     # interrupted while writing
            await self._push((chunk, rate * FRAME_MS // 1000), rate, channels)

        return True

    async def stream_end(self, seq: int, close_after: bool = False) -> None:
        """No more audio for this utterance: flush the tail, then announce 'ended' when it has really played out."""

        if not self._streaming or seq != self._active_seq:
            return

        rate, channels = self._stream_fmt
        frame_bytes = (rate * FRAME_MS // 1000) * channels * 2

        if self._stream_buf:
            tail = self._stream_buf + b"\x00" * (frame_bytes - len(self._stream_buf))
            self._stream_buf = b""
            await self._push((tail, rate * FRAME_MS // 1000), rate, channels)

        self._streaming = False
        self._pump = asyncio.ensure_future(self._await_playout(seq, close_after))

    async def _await_playout(self, seq, close_after):
        try:
            await asyncio.wait_for(self._source.wait_for_playout(), timeout=120)
            if self._active_seq == seq:
                await self._send("ended", seq, interrupted=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("stream playout failed (%s)", type(exc).__name__)
            if self._active_seq == seq:
                await self._send("failed", seq)
        finally:
            if self._active_seq == seq:
                self._pump, self._active_seq = None, None
            self.last_used = time.monotonic()
            if close_after and not self._closed:
                asyncio.ensure_future(self.close())

    async def prepare_audio_track(self) -> None:
        """Connects and publishes the (silent) AI track now, so the browser has subscribed before the first word."""

        async with self._lock:
            await self._ensure_connected(24000, 1)

    async def send_state(self, state: str, **extra):
        """A JobPilot realtime-state notice for the browser (candidate speaking / thinking / ...), on the same data topic."""

        if self._connected and self._room is not None:
            await self._send(state, 0, channel_override="realtime", **extra)

    # -------------------------------------------------- Task 9: hear the candidate (realtime mode)
    async def start_listening(self, on_audio) -> None:
        """
        Subscribes to the CANDIDATE's microphone track (and nothing else in the room) and calls
        `await on_audio(pcm16_16k_mono_bytes)` for every frame. Requires a publisher created with
        listens_to_candidate=True (its token then allows subscribing).
        """

        if not self.listens_to_candidate:
            raise AudioTransportError("This publisher is not allowed to listen to the candidate.")

        self._candidate_audio_cb = on_audio
        await self._ensure_room()

        room = self._room

        def maybe_subscribe(publication, participant):
            identity = getattr(participant, "identity", "") or ""
            if not identity.startswith(CANDIDATE_IDENTITY_PREFIX):
                return
            if getattr(publication, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                return
            try:
                publication.set_subscribed(True)
            except Exception:
                logger.warning("candidate audio subscribe failed")

        def on_subscribed(track, publication, participant):
            identity = getattr(participant, "identity", "") or ""
            if identity.startswith(CANDIDATE_IDENTITY_PREFIX) and getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
                self._candidate_tasks.append(asyncio.ensure_future(self._forward_candidate_audio(track)))

        room.on("track_published", maybe_subscribe)
        room.on("track_subscribed", on_subscribed)

        for participant in list(getattr(room, "remote_participants", {}).values()):
            for publication in list(getattr(participant, "track_publications", {}).values()):
                maybe_subscribe(publication, participant)

    async def _forward_candidate_audio(self, track):
        stream = rtc.AudioStream(track, sample_rate=CANDIDATE_AUDIO_RATE, num_channels=1)
        try:
            async for event in stream:
                cb = self._candidate_audio_cb
                if cb is None or self._closed:
                    break
                await cb(bytes(event.frame.data))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("candidate audio forwarding stopped (%s)", type(exc).__name__)
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass

    async def speak(self, wav_bytes: bytes, close_after: bool = False) -> dict:
        """
        Streams one turn's audio through the stable track. Returns once the
        FIRST frame is queued (metrics); the rest is pumped in the
        background and announced with started/ended data messages.
        """

        if self._closed:
            raise AudioTransportError("The audio publisher is closed.")

        pcm, rate, channels = decode_wav_pcm16(wav_bytes)   # validate before touching the network

        async with self._lock:

            await self._stop_pump(interrupted=True)         # a new response replaces any still-playing one

            started = time.perf_counter()

            mode = "track"
            if self.avatar is not None:
                if self.avatar.state == "starting":
                    await self.avatar.wait_ready(AVATAR_READY_WAIT_SECONDS)
                if self.avatar.ready and rate == 24000 and channels == 1 and self._connected:
                    mode = "avatar"

            if mode == "avatar":
                info = {"reused_connection": True, "reused_track": False, "connect_seconds": 0.0}
                await self._drop_track()                # the avatar is the ONE audible path: no second track
            else:
                info = await self._ensure_connected(rate, channels)

            self.seq += 1
            seq = self.seq
            self._active_seq = seq
            self._active_mode = mode
            self.stats["speeches"] += 1
            self.last_used = time.monotonic()

            audio_seconds = round(len(pcm) / (rate * channels * 2), 2)

            await self._send("started", seq, audio_seconds=audio_seconds)

            if mode == "avatar":
                self._pump = asyncio.ensure_future(self._run_avatar_pump(seq, pcm, rate, channels, audio_seconds, close_after))
                return {"seq": seq, "audio_seconds": audio_seconds, "first_frame_seconds": round(time.perf_counter() - started, 3), "mode": "avatar", **info}

            frames = _frames(pcm, rate, channels)

            first = next(frames)
            await self._push(first, rate, channels)
            first_frame_seconds = round(time.perf_counter() - started, 3)

            self._pump = asyncio.ensure_future(self._run_pump(seq, frames, rate, channels, audio_seconds, close_after))

        return {
            "seq": seq,
            "audio_seconds": audio_seconds,
            "first_frame_seconds": first_frame_seconds,
            "mode": "track",
            **info,
        }

    async def _push(self, item, rate, channels):
        chunk, samples = item
        frame = rtc.AudioFrame.create(rate, channels, samples)
        memoryview(frame.data).cast("B")[:] = chunk           # frame.data is an int16 memoryview
        await self._source.capture_frame(frame)

    async def _run_pump(self, seq, frames, rate, channels, audio_seconds, close_after):
        try:
            for item in frames:
                await self._push(item, rate, channels)
            await asyncio.wait_for(self._source.wait_for_playout(), timeout=audio_seconds + PLAYOUT_TIMEOUT_SLACK_SECONDS)
            if self._active_seq == seq:
                await self._send("ended", seq, interrupted=False)
        except asyncio.CancelledError:
            raise                                             # _stop_pump announces the interrupted end
        except Exception as exc:
            logger.warning("audio pump failed (%s)", type(exc).__name__)
            if self._active_seq == seq:
                await self._send("failed", seq)
        finally:
            if self._active_seq == seq:
                self._pump, self._active_seq = None, None
            self.last_used = time.monotonic()
            if close_after and not self._closed:
                asyncio.ensure_future(self.close())

    async def _run_avatar_pump(self, seq, pcm, rate, channels, audio_seconds, close_after):
        try:
            await self.avatar.stream(pcm, rate, channels)
            result = await self.avatar.wait_finished(audio_seconds)
            if self._active_seq == seq:
                await self._send("ended", seq, interrupted=bool(result.get("interrupted")))
        except asyncio.CancelledError:
            raise                                             # _stop_pump interrupts the avatar and announces it
        except Exception as exc:
            logger.warning("avatar speech failed (%s)", type(exc).__name__)
            if self._active_seq == seq:
                await self._send("failed", seq)
        finally:
            if self._active_seq == seq:
                self._pump, self._active_seq = None, None
            self.last_used = time.monotonic()
            if close_after and not self._closed:
                asyncio.ensure_future(self.close())

    # -------------------------------------------------- Task 7: avatar lifecycle
    async def start_avatar(self) -> dict:
        """
        Attaches the ONE Tavus avatar to this interview's room connection
        (idempotent: a running/starting avatar is left alone). Returns quickly;
        the avatar becomes ready in the background -- see avatar_status().
        """

        if self._closed:
            raise AudioTransportError("The audio publisher is closed.")

        async with self._lock:
            if self.avatar is not None and self.avatar.state in ("starting", "ready"):
                return self.avatar_status()

            if self.avatar is not None:
                await self._drop_avatar()                   # a failed/closed one is replaced, never reused

            await self._ensure_room()

            if self._avatar_factory is not None:
                self.avatar = self._avatar_factory(self.application_id)
            else:
                from backend.app.services.assessment.tavus_avatar_session_v2 import TavusAvatarSessionV2

                self.avatar = TavusAvatarSessionV2(self.application_id)

            self.avatar.start(self._room, self._identity())

        return self.avatar_status()

    def _identity(self) -> str:
        return f"jobpilot-tts-{self.application_id}"

    def avatar_status(self) -> dict:
        if self.avatar is None:
            return {"state": "idle", "error": None}
        return {"state": self.avatar.state, "error": self.avatar.error}

    async def _drop_avatar(self):
        avatar, self.avatar = self.avatar, None
        if avatar is not None:
            try:
                await avatar.close()
            except Exception:
                logger.warning("avatar close failed")

    async def interrupt(self) -> bool:
        """Stops the current response immediately. True when something was playing."""

        async with self._lock:
            was_speaking = self.speaking
            await self._stop_pump(interrupted=True)
            return was_speaking

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            await self._stop_pump(interrupted=True)
        except Exception:
            pass
        if self._grace is not None:
            self._grace.cancel()
            self._grace = None
        for task in self._candidate_tasks:
            task.cancel()
        self._candidate_tasks = []
        self._candidate_audio_cb = None
        await self._drop_avatar()                          # ends the Tavus conversation and removes the avatar participant
        room, self._room = self._room, None
        publication, self._publication = self._publication, None
        self._connected = False
        if room is not None:
            if publication is not None:
                try:
                    await room.local_participant.unpublish_track(publication.sid)
                except Exception:
                    pass
            try:
                await room.disconnect()
            except Exception:
                logger.warning("room disconnect failed")
        _forget(self)


# ------------------------------------------------------------ process-local registry
_publishers: dict = {}


def _forget(publisher):
    if _publishers.get(publisher.application_id) is publisher:
        del _publishers[publisher.application_id]


async def get_publisher(application_id: int, token_factory=None, avatar_factory=None, listen_to_candidate: bool = False) -> InterviewAudioPublisher:
    """The ONE publisher for this application (created on first use); idle ones are reaped."""

    now = time.monotonic()
    for stale in [p for p in list(_publishers.values()) if not p.speaking and now - p.last_used > PUBLISHER_IDLE_SECONDS and p.application_id != application_id]:
        await stale.close()

    publisher = _publishers.get(application_id)
    if publisher is None or publisher._closed:
        publisher = InterviewAudioPublisher(
            application_id,
            token_factory or (_subscribing_token_factory if listen_to_candidate else None),
            avatar_factory,
        )
        publisher.listens_to_candidate = bool(listen_to_candidate)
        _publishers[application_id] = publisher
    return publisher


def peek_publisher(application_id: int):
    return _publishers.get(application_id)


async def close_publisher(application_id: int):
    publisher = _publishers.get(application_id)
    if publisher is not None:
        await publisher.close()


async def close_all_publishers():
    for publisher in list(_publishers.values()):
        await publisher.close()

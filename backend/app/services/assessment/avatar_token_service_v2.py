from datetime import timedelta

from livekit import api as livekit_api

from backend.app.models.job_application import JobApplication
from backend.app.services.assessment.avatar_provider_v2 import (
    AvatarConfigError,
    get_avatar_config,
    get_livekit_config,
)


# ============================================================
# JOBPILOT AI - LIVEKIT AVATAR ROOM TOKEN GENERATION (Task 5K)
#
# Server-side ONLY (Section 15/Step 8: "Do NOT expose LiveKit API
# secrets in the browser"). The LiveKit API secret never leaves
# this process — only the short-lived, scoped JWT this module
# produces is ever sent to the candidate's browser.
#
# Uses the lightweight `livekit-api` package (JWT/REST client
# only) — NOT the full `livekit-agents` framework, which is a
# separate, heavier dependency only needed by the avatar WORKER
# process (see avatar_worker_v2.py), never by this request/response
# FastAPI app.
#
# Room/identity scoping (Section 15's "prevent cross-application
# room access" / "prevent arbitrary room joining"):
#   - room name is deterministically derived from application_id
#     ONLY server-side — the candidate's browser never chooses it.
#   - participant identity encodes the OWNING candidate's user id,
#     so a stolen/leaked token for one candidate's room still
#     cannot be replayed as a different candidate's identity.
#   - grants restrict the token to exactly one room (room_join +
#     `room=<this application's room>`), never room_list/room_admin/
#     room_create — a candidate token can never create or discover
#     other interviews' rooms.
#   - short TTL (Section 15: "expire tokens appropriately"), capped
#     to the same 30-minute window the rest of the V2 interview
#     already uses (INTERVIEW_V2_DEADLINE_MINUTES in
#     interview_integration_v2.py — not imported here to avoid a
#     new coupling; the value is intentionally duplicated as a
#     literal since token TTL is an avatar/transport concern, not
#     an interview-brain one).
# ============================================================

AVATAR_ROOM_TOKEN_TTL_MINUTES = 30

# Task 5 (LiveKit transport): a candidate may publish ONLY what an interview
# needs -- microphone and camera. Never screen share, never a raw/custom
# source. can_publish_data stays on for the worker's jobpilot.* control
# messages (say/interrupt/speech) and nothing else.
CANDIDATE_PUBLISH_SOURCES = ["microphone", "camera"]

# The server-side publisher that carries the AI's (Kokoro) audio into the
# room: microphone source only, cannot subscribe to the candidate, cannot
# send data, and its token lives just long enough for one utterance.
AUDIO_PUBLISHER_TOKEN_TTL_MINUTES = 5


def _room_name_for_application(application_id: int) -> str:
    return f"jobpilot-interview-v2-{application_id}"


def _identity_for_candidate(user_id: int) -> str:
    return f"candidate-{user_id}"


def generate_avatar_room_token_v2(application: JobApplication) -> dict:
    """
    Returns {url, token, room_name, identity, avatar} for the
    candidate's OWN interview room only. Raises AvatarConfigError
    (never a fabricated token) if LiveKit or the avatar identity
    isn't configured.
    """

    livekit_config = get_livekit_config()  # raises AvatarConfigError if missing
    avatar_config = get_avatar_config()    # raises AvatarConfigError if missing

    room_name = _room_name_for_application(application.id)
    identity = _identity_for_candidate(application.user_id)

    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_publish_sources=CANDIDATE_PUBLISH_SOURCES,
        can_subscribe=True,
        can_publish_data=True,
    )

    token = (
        livekit_api.AccessToken(livekit_config.api_key, livekit_config.api_secret)
        .with_identity(identity)
        .with_name(f"Candidate {application.user_id}")
        .with_grants(grants)
        .with_ttl(timedelta(minutes=AVATAR_ROOM_TOKEN_TTL_MINUTES))
        .to_jwt()
    )

    return {
        "url": livekit_config.url,
        "token": token,
        "room_name": room_name,
        "identity": identity,
        "avatar": {
            "display_name": avatar_config.display_name,
            "provider": avatar_config.provider,
        },
    }


def _identity_for_audio_publisher(application_id: int) -> str:
    return f"jobpilot-tts-{application_id}"


def generate_audio_publisher_token_v2(application_id: int, subscribe_candidate_audio: bool = False) -> dict:
    """
    Token for the SERVER-SIDE participant that publishes the AI interviewer's
    locally generated (Kokoro) audio into this application's room. Same
    service, same room-name function, same LiveKit credentials -- not a
    second token system. Needs only LiveKit config (no avatar identity).

    Least privilege: joins exactly this one room, publishes microphone-source
    audio only, cannot subscribe (it never hears the candidate); its data messages
    are only the jobpilot.speech turn-boundary notices; 5-minute TTL.
    """

    livekit_config = get_livekit_config()  # raises AvatarConfigError if missing

    room_name = _room_name_for_application(application_id)
    identity = _identity_for_audio_publisher(application_id)

    grants = livekit_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_publish_sources=["microphone"],
        can_subscribe=bool(subscribe_candidate_audio),   # Task 9 (realtime mode ONLY): it may hear the candidate's microphone; default stays deaf
        can_publish_data=True,   # Task 6: only the jobpilot.speech turn-boundary messages
    )

    token = (
        livekit_api.AccessToken(livekit_config.api_key, livekit_config.api_secret)
        .with_kind("agent")   # Task 7: Tavus only accepts audio from an AGENT-kind participant (verified live); grants are unchanged
        .with_identity(identity)
        .with_name("JobPilot AI Interviewer (audio)")
        .with_grants(grants)
        .with_ttl(timedelta(minutes=AUDIO_PUBLISHER_TOKEN_TTL_MINUTES))
        .to_jwt()
    )

    return {
        "url": livekit_config.url,
        "token": token,
        "room_name": room_name,
        "identity": identity,
    }


# ------------------------------------------------------------
# Task 7: the Tavus avatar participant. Same service, same room-name
# function, same LiveKit credentials. The token is handed to TAVUS (inside
# the conversation-creation request), never to a browser. It can join
# exactly this application's room and publish; it is an "agent" participant
# that publishes on behalf of the AI publisher identity (LiveKit's avatar
# convention), and has no room admin power. 15-minute TTL: it only has to
# cover the join (Tavus stays connected afterwards).
# ------------------------------------------------------------

AVATAR_AGENT_IDENTITY = "tavus-avatar-agent"
AVATAR_AGENT_TOKEN_TTL_MINUTES = 15
ATTRIBUTE_PUBLISH_ON_BEHALF = "lk.publish_on_behalf"


def generate_avatar_agent_token_v2(application_id: int, on_behalf_identity: str) -> dict:

    livekit_config = get_livekit_config()  # raises AvatarConfigError if missing

    room_name = _room_name_for_application(application_id)

    token = (
        livekit_api.AccessToken(livekit_config.api_key, livekit_config.api_secret)
        .with_kind("agent")
        .with_identity(AVATAR_AGENT_IDENTITY)
        .with_name("JobPilot AI Interviewer")
        .with_grants(livekit_api.VideoGrants(room_join=True, room=room_name))
        .with_attributes({ATTRIBUTE_PUBLISH_ON_BEHALF: on_behalf_identity})
        .with_ttl(timedelta(minutes=AVATAR_AGENT_TOKEN_TTL_MINUTES))
        .to_jwt()
    )

    return {"token": token, "room_name": room_name, "identity": AVATAR_AGENT_IDENTITY}

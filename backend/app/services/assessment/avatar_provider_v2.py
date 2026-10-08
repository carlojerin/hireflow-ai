import os
from dataclasses import dataclass


# ============================================================
# JOBPILOT AI - AVATAR PROVIDER ABSTRACTION (Task 5K)
#
# THE AVATAR IS A PRESENTATION LAYER ONLY. Nothing in this module
# (or anywhere else this task touches) ever imports from
# interview_brain_v2.py / interview_integration_v2.py, makes a
# question/follow-up/challenge/competency decision, or reads a
# candidate's evaluation. It only answers two questions: "is an
# avatar provider configured", and "what is this interview's fixed
# avatar identity" — both pure configuration, zero interview logic.
#
# PROVIDER RESEARCHED (current official docs, Task 5K audit):
#   - LiveKit Agents avatar architecture (docs.livekit.io/agents/
#     models/avatar/): avatar providers join a LiveKit room as a
#     participant with kind='agent' and publish synchronized
#     audio+video tracks built from the agent session's OWN TTS
#     output — they do not perform their own STT/LLM by default,
#     matching this project's "avatar must not replace the voice
#     pipeline" requirement exactly.
#   - Tavus (chosen for the reference worker script,
#     avatar_worker_v2.py): first-class `livekit-agents[tavus]`
#     Python plugin, fixed identity via `face_id`, consumes the
#     agent session's audio pipeline unchanged.
#   - Anam: equally viable alternative (ANAM_API_KEY, stock/custom
#     avatar gallery, open-source LiveKit integration) — the
#     abstraction below supports it via AVATAR_PROVIDER without
#     code changes elsewhere.
#   - HeyGen LiveAvatar ("LITE mode"): explicitly designed for
#     "bring your own LLM/ASR/TTS", also LiveKit-based — a strong
#     alternative, not selected only because Tavus's plugin is the
#     more turnkey LiveKit Agents integration for this foundation.
#   See the Task 5K final report for the full provider comparison
#   and the reasoning for NOT running a live agent worker in this
#   environment (no LiveKit/Tavus credentials are configured here
#   — see is_avatar_configured() below, which is the honest,
#   evidence-based check, never a fake "it works").
# ============================================================


DEFAULT_AVATAR_PROVIDER = "tavus"

# One fixed identity for every JobPilot interview, matching Section
# 5's explicit "do not generate a random avatar every interview".
ENV_AVATAR_PROVIDER = "AVATAR_PROVIDER"
ENV_AVATAR_ID = "JOBPILOT_AI_INTERVIEWER_AVATAR_ID"
ENV_AVATAR_DISPLAY_NAME = "JOBPILOT_AI_INTERVIEWER_DISPLAY_NAME"
ENV_AVATAR_VOICE_ID = "JOBPILOT_AI_INTERVIEWER_VOICE_ID"  # optional, provider-specific
ENV_AVATAR_QUALITY = "JOBPILOT_AI_INTERVIEWER_QUALITY"    # optional, e.g. "high"/"medium"
ENV_AVATAR_ENVIRONMENT = "AVATAR_ENVIRONMENT"              # "development" | "production"

ENV_LIVEKIT_URL = "LIVEKIT_URL"
ENV_LIVEKIT_API_KEY = "LIVEKIT_API_KEY"
ENV_LIVEKIT_API_SECRET = "LIVEKIT_API_SECRET"

DEFAULT_DISPLAY_NAME = "JobPilot AI Interviewer"

# Task 8: how the AI interviewer is shown. "image" (default) = a FIXED interviewer image; no live
# avatar is started and Tavus is never called. "avatar" = the Tavus live avatar (needs Tavus credits).
ENV_VISUAL_MODE = "AI_INTERVIEW_VISUAL_MODE"
VISUAL_MODE_IMAGE = "image"
VISUAL_MODE_AVATAR = "avatar"


def get_visual_mode() -> str:
    return VISUAL_MODE_AVATAR if (os.getenv(ENV_VISUAL_MODE) or "").strip().lower() == VISUAL_MODE_AVATAR else VISUAL_MODE_IMAGE


class AvatarConfigError(Exception):
    """
    Raised for every avatar-configuration failure mode — mirrors
    voice_provider_v2.VoiceProviderError and gemini_client
    .GeminiUnavailableError's established pattern in this project:
    one exception type, a human-readable message naming the exact
    missing configuration, never a silent/fake success.
    """


@dataclass(frozen=True)
class AvatarConfigV2:
    """The AI interviewer's fixed, stable identity for this
    interview. The SAME values are used for every JobPilot
    interview unless an operator explicitly changes the
    environment configuration — never randomized per-session."""

    provider: str
    avatar_id: str
    display_name: str
    voice_id: str | None
    quality: str | None
    environment: str


@dataclass(frozen=True)
class LiveKitConfigV2:
    url: str
    api_key: str
    api_secret: str


def is_avatar_configured() -> bool:
    """Read-only check, never raises, never logs secrets — same
    contract as voice_provider_v2.is_voice_configured()."""

    return bool(
        os.getenv(ENV_AVATAR_ID)
        and os.getenv(ENV_LIVEKIT_URL)
        and os.getenv(ENV_LIVEKIT_API_KEY)
        and os.getenv(ENV_LIVEKIT_API_SECRET)
    )


def get_avatar_config() -> AvatarConfigV2:
    """Raises AvatarConfigError naming the EXACT missing variable —
    never falls back to a fabricated/random avatar identity."""

    avatar_id = os.getenv(ENV_AVATAR_ID)

    if not avatar_id:
        raise AvatarConfigError(
            f"{ENV_AVATAR_ID} is not configured — no fixed AI interviewer avatar identity is set."
        )

    return AvatarConfigV2(
        provider=os.getenv(ENV_AVATAR_PROVIDER, DEFAULT_AVATAR_PROVIDER),
        avatar_id=avatar_id,
        display_name=os.getenv(ENV_AVATAR_DISPLAY_NAME, DEFAULT_DISPLAY_NAME),
        voice_id=os.getenv(ENV_AVATAR_VOICE_ID) or None,
        quality=os.getenv(ENV_AVATAR_QUALITY) or None,
        environment=os.getenv(ENV_AVATAR_ENVIRONMENT, "development"),
    )


def get_livekit_config() -> LiveKitConfigV2:
    """Raises AvatarConfigError naming the exact missing LiveKit
    variable. These three (URL/API key/API secret) are
    infrastructure config, deliberately kept separate from the
    avatar IDENTITY config above — a deployment could rotate
    LiveKit credentials without ever touching which avatar the
    candidate sees, and vice versa."""

    url = os.getenv(ENV_LIVEKIT_URL)
    api_key = os.getenv(ENV_LIVEKIT_API_KEY)
    api_secret = os.getenv(ENV_LIVEKIT_API_SECRET)

    missing = [
        name for name, value in (
            (ENV_LIVEKIT_URL, url),
            (ENV_LIVEKIT_API_KEY, api_key),
            (ENV_LIVEKIT_API_SECRET, api_secret),
        )
        if not value
    ]

    if missing:
        raise AvatarConfigError(
            f"LiveKit is not configured — missing: {', '.join(missing)}."
        )

    return LiveKitConfigV2(url=url, api_key=api_key, api_secret=api_secret)

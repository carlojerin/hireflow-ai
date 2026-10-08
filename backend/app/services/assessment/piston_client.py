import os
import time

import requests


# ============================================================
# JOBPILOT AI - PISTON EXECUTION CLIENT
#
# Thin HTTP client for Piston (https://github.com/engineer-man/
# piston), a purpose-built sandboxed code-execution service:
# every submission runs inside its own isolated container with a
# memory ceiling, a wall-clock timeout, and no network access —
# properties this project's own previous subprocess-based runners
# never had for anything but Python (and even there, only via a
# bypassable AST denylist, not real OS-level isolation).
#
# Defaults to the public instance (https://emkc.org/api/v2/piston)
# so this works with zero setup. For real candidate traffic, set
# PISTON_API_URL to a self-hosted instance (see
# docker-compose.piston.yml at the repo root) — the public
# instance is rate-limited and intended for exactly this kind of
# development/evaluation use, not production load.
# ============================================================

DEFAULT_PISTON_API_URL = "https://emkc.org/api/v2/piston"

# The public instance enforces its own (lower) ceilings regardless
# of what is requested; a self-hosted instance honors these.
DEFAULT_RUN_TIMEOUT_MS = 10_000
DEFAULT_COMPILE_TIMEOUT_MS = 15_000
DEFAULT_RUN_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
DEFAULT_COMPILE_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024

_HTTP_REQUEST_TIMEOUT_SECONDS = 25
_HEALTH_CHECK_TIMEOUT_SECONDS = 3
_HEALTH_CHECK_CACHE_SECONDS = 20


class PistonUnavailableError(Exception):
    """The Piston service could not be reached at all (network/timeout/5xx)."""


class PistonExecutionError(Exception):
    """Piston responded, but rejected the request itself (e.g. unknown language/version, bad payload)."""


def _api_url() -> str:
    return (os.getenv("PISTON_API_URL") or DEFAULT_PISTON_API_URL).rstrip("/")


_last_health_check_at: float = 0.0
_last_health_check_ok: bool = False


def is_available(force: bool = False) -> bool:
    """
    Cheap reachability check, cached for a short TTL so every
    single execution doesn't pay for an extra round trip — a
    genuinely down Piston instance is re-checked at most every
    _HEALTH_CHECK_CACHE_SECONDS.

    Deliberately a real POST /execute of a trivial script, NOT a
    GET /runtimes — the public emkc.org instance keeps /runtimes
    open (200 OK) to everyone while restricting /execute itself to
    a whitelist, so a /runtimes-only check would report "available"
    even though every real submission is then rejected. This is the
    exact call path a real submission takes, just with harmless
    throwaway code.
    """

    global _last_health_check_at, _last_health_check_ok

    now = time.monotonic()

    if not force and (now - _last_health_check_at) < _HEALTH_CHECK_CACHE_SECONDS:
        return _last_health_check_ok

    try:
        response = requests.post(
            f"{_api_url()}/execute",
            json={
                "language": "python",
                "version": "3.10.0",
                "files": [{"name": "main.py", "content": "pass"}],
            },
            timeout=_HEALTH_CHECK_TIMEOUT_SECONDS,
        )
        _last_health_check_ok = response.status_code == 200

    except requests.RequestException:
        _last_health_check_ok = False

    _last_health_check_at = now

    return _last_health_check_ok


def execute(
    language: str,
    version: str,
    files: list[dict],
    stdin: str = "",
    run_timeout_ms: int = DEFAULT_RUN_TIMEOUT_MS,
    compile_timeout_ms: int = DEFAULT_COMPILE_TIMEOUT_MS,
    run_memory_limit_bytes: int = DEFAULT_RUN_MEMORY_LIMIT_BYTES,
    compile_memory_limit_bytes: int = DEFAULT_COMPILE_MEMORY_LIMIT_BYTES,
) -> dict:
    """
    POSTs one execution request to Piston's /execute endpoint.
    Returns the parsed JSON response (see piston_runner.py for how
    its run/compile sub-objects are interpreted). Never returns a
    candidate's code failure as an exception — a compile error or
    a non-zero exit is a perfectly normal 200 response from Piston
    itself; only genuine transport/service failures raise here.
    """

    payload = {
        "language": language,
        "version": version,
        "files": files,
        "stdin": stdin,
        "run_timeout": run_timeout_ms,
        "compile_timeout": compile_timeout_ms,
        "run_memory_limit": run_memory_limit_bytes,
        "compile_memory_limit": compile_memory_limit_bytes,
    }

    try:
        response = requests.post(
            f"{_api_url()}/execute",
            json=payload,
            timeout=_HTTP_REQUEST_TIMEOUT_SECONDS,
        )

    except requests.RequestException as exc:
        raise PistonUnavailableError(str(exc)) from exc

    if response.status_code >= 500:
        raise PistonUnavailableError(
            f"Piston returned HTTP {response.status_code}."
        )

    if response.status_code != 200:

        try:
            detail = response.json().get("message", response.text)
        except ValueError:
            detail = response.text

        raise PistonExecutionError(str(detail)[:500])

    try:
        return response.json()
    except ValueError as exc:
        raise PistonUnavailableError("Piston returned a non-JSON response.") from exc

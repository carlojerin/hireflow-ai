import base64
import os
import time

import requests


# ============================================================
# JOBPILOT AI - JUDGE0 EXECUTION CLIENT
#
# Thin HTTP client for a self-hosted Judge0 (https://github.com/
# judge0/judge0) instance -- replaces piston_client.py/
# piston_runner.py (left on disk, unused) after Piston's post-boot
# per-language package downloads proved unreliable on this
# network's path to GitHub's release CDN. Judge0 ships every
# compiler/interpreter baked into its own Docker image already, so
# there is no equivalent post-install network dependency here.
#
# Sandboxing is via `isolate` (https://github.com/ioi/isolate),
# built for the International Olympiad in Informatics to run
# adversarial, untrusted competitor code.
#
# See docker-compose.judge0.yml for the self-hosted setup.
# ============================================================

DEFAULT_JUDGE0_API_URL = "http://localhost:2358"

DEFAULT_CPU_TIME_LIMIT_SECONDS = 10.0   # Judge0's own hard ceiling is 15
DEFAULT_MEMORY_LIMIT_KB = 256_000       # Judge0's own hard ceiling is 256000

_HTTP_REQUEST_TIMEOUT_SECONDS = 30
_HEALTH_CHECK_TIMEOUT_SECONDS = 5
_HEALTH_CHECK_CACHE_SECONDS = 20

# Judge0 status.id values that mean "the sandbox ran this to
# completion" (successfully or not) -- 1 (In Queue) / 2
# (Processing) should never appear when wait=true actually waited,
# but are treated as sandbox_error if they somehow do.
STATUS_ACCEPTED = 3
STATUS_WRONG_ANSWER = 4
STATUS_TIME_LIMIT_EXCEEDED = 5
STATUS_COMPILATION_ERROR = 6
STATUS_INTERNAL_ERROR = 13
STATUS_EXEC_FORMAT_ERROR = 14
# 7-12 are all "Runtime Error (<signal>)" variants.
STATUS_RUNTIME_ERROR_RANGE = range(7, 13)


class Judge0UnavailableError(Exception):
    """Judge0 could not be reached at all (network/timeout/5xx)."""


class Judge0ExecutionError(Exception):
    """Judge0 responded, but rejected the request itself (bad language_id, malformed payload, etc.)."""


def _api_url() -> str:
    return (os.getenv("JUDGE0_API_URL") or DEFAULT_JUDGE0_API_URL).rstrip("/")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _un_b64(text: str | None) -> str:
    if not text:
        return ""
    return base64.b64decode(text).decode("utf-8", errors="replace")


_last_health_check_at: float = 0.0
_last_health_check_ok: bool = False


def is_available(force: bool = False) -> bool:
    """
    Cheap reachability check, cached for a short TTL. Deliberately a
    real POST /submissions?wait=true of a trivial script (not just a
    GET /languages) -- the same lesson learned from piston_client.py:
    a metadata-only check can report "available" while the thing
    that actually matters (running code) is broken.
    """

    global _last_health_check_at, _last_health_check_ok

    now = time.monotonic()

    if not force and (now - _last_health_check_at) < _HEALTH_CHECK_CACHE_SECONDS:
        return _last_health_check_ok

    try:
        response = requests.post(
            f"{_api_url()}/submissions",
            params={"wait": "true", "base64_encoded": "true"},
            json={
                "language_id": 71,  # Python (3.8.1) -- this project's self-hosted seed data
                "source_code": _b64("pass"),
            },
            timeout=_HEALTH_CHECK_TIMEOUT_SECONDS,
        )
        _last_health_check_ok = response.status_code == 201

    except requests.RequestException:
        _last_health_check_ok = False

    _last_health_check_at = now

    return _last_health_check_ok


def execute(
    language_id: int,
    source_code: str,
    stdin: str = "",
    cpu_time_limit: float = DEFAULT_CPU_TIME_LIMIT_SECONDS,
    memory_limit_kb: int = DEFAULT_MEMORY_LIMIT_KB,
) -> dict:
    """
    Submits one execution to Judge0 and waits synchronously for the
    result (wait=true -- fine at this project's traffic volume; a
    busier deployment would switch to submit-then-poll). Returns the
    parsed JSON response. Never raises for a candidate's code failure
    (compile error, non-zero exit, timeout) -- those come back as a
    normal 201 response with the appropriate status.id; only a
    genuine transport/service failure raises here.
    """

    payload = {
        "language_id": language_id,
        "source_code": _b64(source_code),
        "stdin": _b64(stdin),
        "cpu_time_limit": cpu_time_limit,
        "memory_limit": memory_limit_kb,
    }

    try:
        response = requests.post(
            f"{_api_url()}/submissions",
            params={"wait": "true", "base64_encoded": "true"},
            json=payload,
            timeout=_HTTP_REQUEST_TIMEOUT_SECONDS,
        )

    except requests.RequestException as exc:
        raise Judge0UnavailableError(str(exc)) from exc

    if response.status_code >= 500:
        raise Judge0UnavailableError(f"Judge0 returned HTTP {response.status_code}.")

    if response.status_code not in (200, 201):

        try:
            detail = response.json()
        except ValueError:
            detail = response.text

        raise Judge0ExecutionError(str(detail)[:500])

    try:
        result = response.json()
    except ValueError as exc:
        raise Judge0UnavailableError("Judge0 returned a non-JSON response.") from exc

    return {
        "status_id": (result.get("status") or {}).get("id"),
        "status_description": (result.get("status") or {}).get("description"),
        "stdout": _un_b64(result.get("stdout")),
        "stderr": _un_b64(result.get("stderr")),
        "compile_output": _un_b64(result.get("compile_output")),
        "message": _un_b64(result.get("message")),
        "exit_code": result.get("exit_code"),
        "time_seconds": result.get("time"),
    }

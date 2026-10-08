import os
import time

from google import genai
from google.genai import types as genai_types


# ============================================================
# JOBPILOT AI - SHARED GEMINI CALL/PARSE HELPER (Task 5C Step 2)
#
# Extracts the exact call/parse pattern already duplicated
# independently across:
#   - backend/app/services/aptitude/ai_question_generator.py
#   - backend/app/services/assessment/interview_question_generator.py
#   - backend/app/services/assessment/interview_evaluator.py
#   - backend/app/services/assessment/programming_question_generator.py
#
# IMPORTANT: those four modules are NOT rewritten to use this
# helper in this step (backward-compatible, additive only, per
# Task 5C Step 2 scope) - this exists so the NEW
# assessment_plan_engine.py has one call/parse path to use,
# instead of adding a fifth copy-paste of the same ~40 lines.
# A later, separately-approved step could migrate the existing
# four callers onto this helper without changing their behavior.
#
# Model is unchanged: "gemini-3.6-flash" (GEMINI_MODEL below),
# exactly matching every existing caller.
#
# Never logs the API key. Never logs the prompt (which may
# contain candidate resume/profile text) - callers that want
# their own diagnostic logging must do it themselves, deciding
# what is safe to log for their specific prompt content.
# ============================================================

GEMINI_MODEL = "gemini-3.6-flash"


class GeminiUnavailableError(Exception):
    """
    Raised for every failure mode this helper can encounter -
    missing API key, request/network failure, empty response, no
    JSON object found, or invalid JSON. Callers catch this ONE
    exception type and fall back to their own deterministic logic,
    exactly like every existing Gemini-calling module already
    does with its own hand-rolled RuntimeError.
    """


def is_gemini_configured() -> bool:
    """Read-only check - never raises, never logs the key itself."""

    return bool(os.getenv("GEMINI_API_KEY"))


def _strip_markdown_fences(text: str) -> str:

    text = text.strip()

    if text.startswith("```"):

        text = (
            text
            .replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
        )

    return text


def _extract_json_object_text(text: str) -> str:

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:

        raise GeminiUnavailableError(
            "Gemini response did not contain a JSON object."
        )

    return text[start:end + 1]


GEMINI_RETRY_BACKOFF_SECONDS = (1.0, 2.0)
GEMINI_REQUEST_TIMEOUT_MS = 45000


def _is_transient_gemini_failure(exc: Exception) -> bool:

    code = getattr(exc, "code", None)

    if "perday" in str(exc).lower().replace(" ", "").replace("_", ""):
        return False                                    # a DAILY quota cannot recover within seconds: retrying only wastes time

    if isinstance(code, int):
        return code in (408, 429) or code >= 500

    return "timeout" in type(exc).__name__.lower() or "connect" in type(exc).__name__.lower()


def gemini_text_model() -> str:
    """The Gemini text model: the admin console's GEMINI_TEXT_MODEL setting when set, else the built-in default."""

    return (os.getenv("GEMINI_TEXT_MODEL") or "").strip() or GEMINI_MODEL


def generate_json_content(
    prompt: str,
    model: str | None = None,
    retries: int = 0,
    timeout_ms: int | None = None,
) -> str:
    """
    Calls Gemini with the given prompt and returns the extracted
    JSON-object substring of the response as a raw string (NOT yet
    json.loads()'d — the caller decides how to parse/validate it,
    e.g. straight into a Pydantic model's model_validate_json(),
    matching how the existing four callers each validate
    differently: some hand-rolled dict checks, some a pydantic
    schema). Raises GeminiUnavailableError for every failure mode
    (missing key, request failure, empty response, no JSON object
    found) so every caller has exactly one exception type to catch
    and fall back on.

    Deliberately does not call json.loads() itself: a caller
    validating straight into a Pydantic model can pass this string
    directly to model_validate_json() without JobPilot re-parsing
    and re-serializing it, and a caller that wants a plain dict can
    just call json.loads() on the returned string itself.
    """

    model = model or gemini_text_model()

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:

        raise GeminiUnavailableError(
            "GEMINI_API_KEY is not configured."
        )

    response = None
    last_error = None

    for attempt in range(retries + 1):

        try:

            # timeout_ms: give up on a model that is answering slowly (free-tier "high demand" spikes took 10 s+ for a
            # one-word reply); the caller then moves on to the next model
            client = (
                genai.Client(api_key=api_key, http_options=genai_types.HttpOptions(timeout=timeout_ms))
                if timeout_ms else genai.Client(api_key=api_key)
            )

            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )

            break

        except Exception as exc:

            last_error = exc

            # only overload / rate-limit / timeout errors are retried, at most `retries` times, 1s then 2s
            if attempt < retries and _is_transient_gemini_failure(exc):
                time.sleep(GEMINI_RETRY_BACKOFF_SECONDS[min(attempt, len(GEMINI_RETRY_BACKOFF_SECONDS) - 1)])
                continue

            raise GeminiUnavailableError(
                f"Gemini API request failed: {exc}"
            ) from exc

    response_text = (getattr(response, "text", None) or "").strip()

    if not response_text:

        raise GeminiUnavailableError(
            "Gemini returned an empty response."
        )

    cleaned = _strip_markdown_fences(response_text)

    return _extract_json_object_text(cleaned)

import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod

import requests


logger = logging.getLogger("jobpilot.assessment.interview_llm")


# ============================================================
# JOBPILOT AI - INTERVIEW LLM PROVIDER (Task 5O-A)
#
# The Interview Brain talks ONLY to InterviewLLMProvider. The
# default provider is GEMINI (the project's existing Gemini client and
# model); a hosted provider (groq / cerebras / openrouter) is available,
# selected explicitly with AI_INTERVIEW_PROVIDER. The choice is ALWAYS
# explicit/static: there is NO automatic fallback between providers.
# When the configured provider fails, the brain uses its own deterministic
# engine (generated_by="fallback").
#
# Only the minimum interview prompt text is sent. No credentials,
# tokens or hidden evaluator data are ever part of a prompt (the
# prompt builders in interview_brain_v2.py only see InterviewContextV2).
# Prompts and model output are never logged -- only provider, model
# and timing numbers.
# ============================================================

DEFAULT_PROVIDER = "gemini"
DEFAULT_GEMINI_INTERVIEW_MODEL = "gemini-3.5-flash-lite"


class InterviewLLMUnavailableError(Exception):
    """The provider could not be reached / returned nothing (down, timeout, empty)."""


class InterviewLLMMalformedError(ValueError):
    """The provider answered, but not with a usable JSON object (retryable)."""


_stats_lock = threading.Lock()
_call_counts: dict[str, int] = {}
_last_call: dict = {}


def _record_call(provider: str, **metrics) -> None:
    with _stats_lock:
        _call_counts[provider] = _call_counts.get(provider, 0) + 1
        _last_call.clear()
        _last_call.update({"provider": provider, **metrics})


def _persist_usage(provider: str, model: str, **fields) -> None:
    """Durable record for the admin console's LLM usage tab (never raises)."""
    try:
        from backend.app.services.llm_usage import record_usage
        record_usage(provider, model, **fields)
    except Exception:
        pass


def get_call_counts() -> dict[str, int]:
    """Per-process count of LLM requests attempted, by provider name."""
    with _stats_lock:
        return dict(_call_counts)


def get_last_call_metrics() -> dict:
    with _stats_lock:
        return dict(_last_call)


def reset_call_counts() -> None:
    with _stats_lock:
        _call_counts.clear()
        _last_call.clear()


def strip_to_json_object(text: str) -> str:
    """Removes <think> blocks and markdown fences, returns the outermost {...}."""

    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end < start:
        raise InterviewLLMMalformedError("Model response did not contain a JSON object.")

    return text[start:end + 1]


class InterviewLLMProvider(ABC):

    name: str = ""
    model: str = ""

    @abstractmethod
    def generate_json(self, prompt: str, system: str | None = None) -> str:
        """Returns the raw text of ONE JSON object. Raises InterviewLLMUnavailableError."""


GEMINI_TRANSIENT_RETRIES = 2
GEMINI_RETRY_BACKOFF_SECONDS = 1.0


def _is_transient_gemini_error(message: str) -> bool:
    upper = (message or "").upper()
    if "PERDAY" in upper or "PER DAY" in upper:
        return False                                     # a DAILY quota cannot recover within a turn: retrying only wastes time
    return any(marker in upper for marker in (
        " 503", "UNAVAILABLE", " 429", "RESOURCE_EXHAUSTED", "HIGH DEMAND", "TIMEOUT", "TIMED OUT", "DEADLINE",
    ))


DEFAULT_GEMINI_INTERVIEW_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-3.5-flash"
GEMINI_COOLDOWN_SECONDS = 60          # a model that just answered 429 is skipped for this long (a per-day quota: 30 minutes)
GEMINI_DAILY_COOLDOWN_SECONDS = 1800
_gemini_cooldown_until: dict[str, float] = {}
_gemini_latency: dict[str, float] = {}         # smoothed seconds per model: the fastest RECENT model is asked first
DEFAULT_MODEL_LATENCY_SECONDS = 5.0


def _gemini_model_list() -> list[str]:
    raw = os.getenv("AI_INTERVIEW_GEMINI_MODELS") or os.getenv("AI_INTERVIEW_GEMINI_MODEL") or DEFAULT_GEMINI_INTERVIEW_MODELS
    return [m.strip() for m in raw.split(",") if m.strip()]


class GeminiInterviewProvider(InterviewLLMProvider):
    """
    The Interview Brain on Gemini (AI_INTERVIEW_PROVIDER=gemini). Every free-tier model has its OWN small quota
    (a few requests per minute / per day), so several fast Gemini text models are tried in order: a model that answers
    429/503 is put on a short cooldown and the next Gemini model answers the SAME turn at once. Only Gemini models are
    ever used here -- there is still no fallback to another provider.
    """

    name = "gemini"

    def __init__(self):
        self.models = _gemini_model_list()
        self.model = self.models[0]

    def generate_json(self, prompt: str, system: str | None = None) -> str:

        from backend.app.services.assessment.gemini_client import (
            GeminiUnavailableError,
            generate_json_content,
        )

        text = f"{system}\n\n{prompt}" if system else prompt
        last = None

        # a turn must not wait on a slow model: each model gets a short timeout and the whole turn a time budget
        per_model_timeout_ms = int(float(os.getenv("AI_INTERVIEW_GEMINI_TIMEOUT_SECONDS", "10")) * 1000)
        turn_budget = float(os.getenv("AI_INTERVIEW_GEMINI_BUDGET_SECONDS", "28"))
        turn_started = time.monotonic()

        for round_number in range(GEMINI_TRANSIENT_RETRIES + 1):

            now = time.monotonic()
            candidates = [m for m in self.models if _gemini_cooldown_until.get(m, 0) <= now] or list(self.models)
            candidates.sort(key=lambda m: _gemini_latency.get(m, DEFAULT_MODEL_LATENCY_SECONDS))     # stable: ties keep the configured order
            transient_seen = False

            for model in candidates:

                if time.monotonic() - turn_started > turn_budget and last is not None:
                    raise InterviewLLMUnavailableError(str(last)) from last

                _record_call(self.name, model=model)

                try:
                    self.model = model
                    call_started = time.monotonic()
                    result = generate_json_content(text, model=model, timeout_ms=per_model_timeout_ms)
                    took = time.monotonic() - call_started
                    _gemini_latency[model] = 0.5 * _gemini_latency.get(model, took) + 0.5 * took
                    return result
                except GeminiUnavailableError as exc:
                    last = exc
                    message = str(exc)
                    if _is_transient_gemini_error(message):
                        transient_seen = True
                        _gemini_latency[model] = 0.5 * _gemini_latency.get(model, per_model_timeout_ms / 1000.0) + 0.5 * (per_model_timeout_ms / 1000.0)   # slow/failed: asked later
                        _gemini_cooldown_until[model] = time.monotonic() + GEMINI_COOLDOWN_SECONDS
                        continue                                    # next Gemini model, no waiting
                    if "PERDAY" in message.upper().replace(" ", "").replace("_", ""):
                        _gemini_cooldown_until[model] = time.monotonic() + GEMINI_DAILY_COOLDOWN_SECONDS
                        continue
                    raise InterviewLLMUnavailableError(message) from exc   # auth / empty / malformed: not a quota problem

            if not transient_seen or round_number >= GEMINI_TRANSIENT_RETRIES:
                break

            time.sleep(GEMINI_RETRY_BACKOFF_SECONDS * (round_number + 1))

        raise InterviewLLMUnavailableError(str(last)) from last


# ------------------------------------------------------------
# Hosted OpenAI-compatible providers (free tiers): Groq, Cerebras, OpenRouter.
# One small class, three presets. The provider is chosen ONLY by
# AI_INTERVIEW_PROVIDER (groq | cerebras | openrouter) and the model by
# AI_INTERVIEW_MODEL -- there is no automatic fallback to any other provider.
# API keys come from the environment (GROQ_API_KEY / CEREBRAS_API_KEY /
# OPENROUTER_API_KEY) and are never logged, returned or put in an error text.
# ------------------------------------------------------------

HOSTED_PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "default_model": "openai/gpt-oss-120b"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "default_model": None},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "default_model": None},
}


class HostedChatProvider(InterviewLLMProvider):
    """A hosted OpenAI-compatible /chat/completions endpoint (JSON-object mode)."""

    def __init__(self, name: str, model: str | None = None, timeout: float | None = None):
        preset = HOSTED_PROVIDERS[name]
        self.name = name
        self._key_env = preset["key_env"]
        self.base_url = (os.getenv("AI_INTERVIEW_BASE_URL") or preset["base_url"]).rstrip("/")
        self.model = model or os.getenv("AI_INTERVIEW_MODEL") or preset["default_model"] or ""
        self.timeout = timeout or float(os.getenv("AI_INTERVIEW_TIMEOUT_SECONDS", "60"))
        # reasoning models spend part of max_tokens on hidden reasoning: leave room, or the JSON is cut off (HTTP 400)
        self.max_tokens = int(os.getenv("AI_INTERVIEW_MAX_TOKENS", "1500"))
        # gpt-oss reasoning models: "low" effort keeps interview turns fast; override with AI_INTERVIEW_REASONING_EFFORT
        self.reasoning_effort = os.getenv("AI_INTERVIEW_REASONING_EFFORT") or ("low" if self.model.startswith("openai/gpt-oss") else None)

    def generate_json(self, prompt: str, system: str | None = None, json_schema: dict | None = None, max_tokens: int | None = None, temperature: float | None = None, raw_text: bool = False) -> str:
        # json_schema / max_tokens / temperature / raw_text are optional (resume analysis, aptitude and programming
        # generation); the interview brain never passes them. raw_text=True returns the plain reply (no JSON mode).

        api_key = os.getenv(self._key_env)

        if not api_key:
            raise InterviewLLMUnavailableError(f"{self._key_env} is not configured.")

        if not self.model:
            raise InterviewLLMUnavailableError("AI_INTERVIEW_MODEL is not configured for this provider.")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3 if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if not raw_text:
            payload["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": "structured_output", "strict": True, "schema": json_schema}}
                if json_schema else {"type": "json_object"}
            )
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        started = time.perf_counter()

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            status = response.status_code
            if self.name == "groq":
                try:
                    from backend.app.services.llm_usage import note_groq_limits
                    note_groq_limits(response.headers)
                except Exception:
                    pass
            if status >= 400:
                detail = ""
                try:
                    err = (response.json() or {}).get("error") or {}
                    detail = f": {err.get('code') or err.get('type') or ''} {str(err.get('message') or '')[:140]}".rstrip()
                except Exception:
                    pass
                _persist_usage(self.name, self.model, ok=False, rate_limited=(status == 429), seconds=time.perf_counter() - started)
                raise InterviewLLMUnavailableError(f"{self.name} request failed (HTTP {status}{detail}).")
            body = response.json()
        except InterviewLLMUnavailableError:
            _record_call(self.name, model=self.model, ok=False, seconds=round(time.perf_counter() - started, 2))
            raise
        except Exception as exc:
            _record_call(self.name, model=self.model, ok=False, seconds=round(time.perf_counter() - started, 2))
            raise InterviewLLMUnavailableError(f"{self.name} request failed ({type(exc).__name__}).") from exc

        elapsed = time.perf_counter() - started
        choices = body.get("choices") or [{}]
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        usage = body.get("usage") or {}

        _record_call(
            self.name, model=self.model, ok=bool(content), seconds=round(elapsed, 2),
            prompt_tokens=usage.get("prompt_tokens") or 0, output_tokens=usage.get("completion_tokens") or 0,
        )
        _persist_usage(
            self.name, self.model, ok=bool(content), seconds=elapsed,
            prompt_tokens=usage.get("prompt_tokens") or 0, output_tokens=usage.get("completion_tokens") or 0,
        )
        logger.info("interview LLM call provider=%s model=%s seconds=%.1f", self.name, self.model, elapsed)

        if not content:
            raise InterviewLLMUnavailableError(f"{self.name} returned an empty response.")

        return content if raw_text else strip_to_json_object(content)


def get_interview_llm_provider() -> InterviewLLMProvider:
    """Gemini by default; groq / cerebras / openrouter only when explicitly configured."""

    choice = (os.getenv("AI_INTERVIEW_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

    # Task 9: an EXPLICIT INTERVIEW_REALTIME_PROVIDER pins the pipeline brain (never set implicitly):
    #   groq_pipeline -> Groq. "realtime" leaves the brain as configured (it still analyses/records every
    #   answer). Nothing here ever falls back to another provider.
    pinned = (os.getenv("INTERVIEW_REALTIME_PROVIDER") or "").strip().lower()
    if pinned == "groq_pipeline":
        choice = "groq"
    elif pinned == "gemini_pipeline":
        choice = "gemini"

    if choice in HOSTED_PROVIDERS:
        return HostedChatProvider(choice)

    return GeminiInterviewProvider()


def get_evaluator_llm_provider() -> InterviewLLMProvider:
    """
    The independent final-evaluator pass (interview_final_evaluator_v2.py) is a much harder single call than a
    per-turn decision -- it grades every competency at once with quoted evidence, in one large JSON object -- so a
    a fast model that handles per-turn decisions fine is NOT necessarily reliable for it. An explicit
    AI_INTERVIEW_EVALUATOR_PROVIDER lets the per-turn brain stay fast while this one call, made ONCE per interview
    (never per turn, so its extra latency barely matters), goes to a more capable provider. Defaults to
    get_interview_llm_provider()'s own resolution when unset, so nothing changes for a setup that never opts in.
    """

    choice = (os.getenv("AI_INTERVIEW_EVALUATOR_PROVIDER") or "").strip().lower()

    if not choice:
        return get_interview_llm_provider()

    # AI_INTERVIEW_MODEL is the PER-TURN brain's model name -- passing it through here by accident would make a
    # Groq/Gemini evaluator provider try to use whatever model the turn brain is configured with. AI_INTERVIEW_EVALUATOR_MODEL is
    # this call's OWN, independent model setting; omitted, each provider class falls back to its own sensible default.
    evaluator_model = os.getenv("AI_INTERVIEW_EVALUATOR_MODEL") or None

    if choice in HOSTED_PROVIDERS:
        return HostedChatProvider(choice, model=evaluator_model or HOSTED_PROVIDERS[choice]["default_model"])

    return GeminiInterviewProvider()

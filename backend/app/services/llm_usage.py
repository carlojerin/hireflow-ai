"""
LLM usage tracking for the admin console.

* Every Groq request is recorded from HostedChatProvider (tokens as Groq reported them) and the x-ratelimit-* headers of
  the latest response are kept, so the console can show the REAL remaining requests/tokens without extra calls.
* Every Gemini generate_content call is recorded by one wrapper installed on the SDK (install_gemini_usage_hook), so no
  call site needs editing. Google exposes no "remaining quota" API for an API key, so for Gemini the console shows what
  THIS platform has used (requests, tokens, rate-limit errors) plus a live key check.

Recording never raises and never slows or breaks a real LLM call.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_groq_limits: dict = {}


def record_usage(provider: str, model: str = "", *, ok: bool = True, rate_limited: bool = False,
                 prompt_tokens: int = 0, output_tokens: int = 0, seconds: float = 0.0) -> None:

    try:
        from backend.app.database.database import SessionLocal
        from backend.app.models.llm_usage_log import LlmUsageLog

        db = SessionLocal()
        try:
            db.add(LlmUsageLog(
                provider=provider, model=(model or "")[:80], ok=bool(ok), rate_limited=bool(rate_limited),
                prompt_tokens=int(prompt_tokens or 0), output_tokens=int(output_tokens or 0),
                seconds=float(seconds or 0.0), created_at=datetime.utcnow(),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - usage stats must never break a real request
        logger.debug("llm usage not recorded: %s", exc)


def note_groq_limits(headers, source: str = "last_call") -> None:
    """Keeps Groq's own rate-limit headers (requests/day and tokens/minute, with reset times)."""

    try:
        def _get(name):
            value = headers.get(name)
            return value if value not in (None, "") else None

        limits = {
            "requests_limit": _get("x-ratelimit-limit-requests"),
            "requests_remaining": _get("x-ratelimit-remaining-requests"),
            "requests_reset": _get("x-ratelimit-reset-requests"),
            "tokens_limit": _get("x-ratelimit-limit-tokens"),
            "tokens_remaining": _get("x-ratelimit-remaining-tokens"),
            "tokens_reset": _get("x-ratelimit-reset-tokens"),
        }
        if not any(limits.values()):
            return
        with _lock:
            _groq_limits.clear()
            _groq_limits.update(limits, source=source, checked_at=datetime.utcnow().isoformat())
    except Exception:  # noqa: BLE001
        pass


def groq_limits() -> dict:
    with _lock:
        return dict(_groq_limits)


_last_gemini_limit: dict = {}


def note_gemini_error(exc: Exception, model: str = "") -> None:
    """Keeps Google's own wording for the latest quota / rate-limit refusal: it names the exact limit that was hit."""

    try:
        if _is_rate_limit(exc):
            with _lock:
                _last_gemini_limit.clear()
                _last_gemini_limit.update(at=datetime.utcnow().isoformat(), model=str(model), message=str(exc)[:500])
    except Exception:  # noqa: BLE001
        pass


def last_gemini_limit() -> dict:
    with _lock:
        return dict(_last_gemini_limit)


def gemini_live_stats(db, since: datetime) -> dict:
    """Gemini Live voice connections (one row each) since `since`: how many, average tokens and minutes."""

    from sqlalchemy import func
    from backend.app.models.llm_usage_log import LlmUsageLog as L

    count, tokens, seconds = (
        db.query(func.count(L.id), func.coalesce(func.sum(L.prompt_tokens + L.output_tokens), 0), func.coalesce(func.sum(L.seconds), 0.0))
        .filter(L.provider == "gemini", L.model.like("%live%"), L.created_at >= since).one()
    )
    count = int(count or 0)
    return {
        "sessions": count,
        "tokens": int(tokens or 0),
        "avg_tokens_per_session": round(tokens / count) if count else 0,
        "avg_minutes_per_session": round(seconds / count / 60, 1) if count else 0,
    }


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text or "quota" in text


_hook_installed = False


def install_gemini_usage_hook() -> bool:
    """Wraps google.genai Models.generate_content once, process-wide, to record every Gemini text/audio call."""

    global _hook_installed
    if _hook_installed:
        return True
    try:
        from google.genai import models as genai_models

        original = genai_models.Models.generate_content

        def tracked(self, *args, **kwargs):
            started = time.perf_counter()
            model = kwargs.get("model") or (args[0] if args else "")
            try:
                response = original(self, *args, **kwargs)
            except Exception as exc:
                record_usage("gemini", str(model), ok=False, rate_limited=_is_rate_limit(exc), seconds=time.perf_counter() - started)
                note_gemini_error(exc, str(model))
                raise
            usage = getattr(response, "usage_metadata", None)
            record_usage(
                "gemini", str(model), ok=True, seconds=time.perf_counter() - started,
                prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                output_tokens=(getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0),
            )
            return response

        genai_models.Models.generate_content = tracked
        _hook_installed = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini usage hook not installed: %s", exc)
    return _hook_installed


def summarize(db, since: datetime) -> dict:
    """Totals per provider and per model since `since`."""

    from sqlalchemy import case, func
    from backend.app.models.llm_usage_log import LlmUsageLog as L

    rows = (
        db.query(
            L.provider, L.model, func.count(L.id), func.sum(case((L.ok.is_(True), 1), else_=0)),
            func.sum(case((L.rate_limited.is_(True), 1), else_=0)), func.coalesce(func.sum(L.prompt_tokens), 0),
            func.coalesce(func.sum(L.output_tokens), 0), func.coalesce(func.avg(L.seconds), 0.0),
        )
        .filter(L.created_at >= since).group_by(L.provider, L.model).all()
    )

    out: dict = {}
    for provider, model, total, ok, limited, p_tok, o_tok, avg_s in rows:
        bucket = out.setdefault(provider, {"requests": 0, "ok": 0, "errors": 0, "rate_limited": 0, "prompt_tokens": 0, "output_tokens": 0, "models": []})
        ok, limited = int(ok or 0), int(limited or 0)
        bucket["requests"] += total
        bucket["ok"] += ok
        bucket["errors"] += total - ok
        bucket["rate_limited"] += limited
        bucket["prompt_tokens"] += int(p_tok)
        bucket["output_tokens"] += int(o_tok)
        bucket["models"].append({"model": model or "(unknown)", "requests": total, "tokens": int(p_tok) + int(o_tok), "avg_seconds": round(float(avg_s), 2)})
    for bucket in out.values():
        bucket["total_tokens"] = bucket["prompt_tokens"] + bucket["output_tokens"]
        bucket["models"].sort(key=lambda m: -m["requests"])
    return out


def daily_history(db, days: int = 7) -> list[dict]:

    from sqlalchemy import func
    from backend.app.models.llm_usage_log import LlmUsageLog as L

    start = (datetime.utcnow() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        db.query(func.date(L.created_at), L.provider, func.count(L.id), func.coalesce(func.sum(L.prompt_tokens + L.output_tokens), 0))
        .filter(L.created_at >= start).group_by(func.date(L.created_at), L.provider).all()
    )
    table = {}
    for day, provider, requests, tokens in rows:
        table.setdefault(str(day), {})[provider] = {"requests": requests, "tokens": int(tokens)}

    history = []
    for i in range(days):
        day = (start + timedelta(days=i)).date().isoformat()
        entry = table.get(day, {})
        history.append({
            "date": day,
            "groq": entry.get("groq", {"requests": 0, "tokens": 0}),
            "gemini": entry.get("gemini", {"requests": 0, "tokens": 0}),
        })
    return history

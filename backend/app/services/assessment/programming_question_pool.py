import json
import logging
import os
import random
import re
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger("jobpilot.assessment.programming_pool")

# ============================================================
# JOBPILOT AI - VALIDATED PROGRAMMING QUESTION POOL
#
# Questions that Groq generated AND that passed every validation (reference solution run, independent
# cross-check, duplicate and relevance checks) are kept here (a small JSON file, not the database) as
# LANGUAGE-NEUTRAL specs: the problem, the test inputs and the Python reference solution.
# They are used only when Groq is unavailable. A pooled question is re-validated by running its reference
# solution again before it reaches a candidate, so the pool never becomes a source of unverified expected outputs.
# ============================================================

MAX_ITEMS = 300
_LOCK = threading.Lock()


def _path() -> str:

    return os.getenv("PROGRAMMING_POOL_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
        "data", "programming_pool.json",
    )


def fingerprint(title: str, description: str = "") -> str:
    words = re.findall(r"[a-z0-9]+", f"{title} {description[:160]}".lower())
    stop = {"a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "with", "your", "you", "is", "are", "that"}
    return " ".join(sorted({w for w in words if w not in stop}))


def _load() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return data.get("items", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:

    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")

    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump({"items": items}, out)

    os.replace(temp, path)                                    # atomic


def add(specs: list[dict], job_terms: list[str]) -> int:
    """Stores validated specs (dicts with title, description, difficulty, tests, reference...). Returns how many were new."""

    added = 0

    try:

        with _LOCK:

            items = _load()
            known = {item["fingerprint"] for item in items}

            for spec in specs:

                key = fingerprint(spec["title"], spec["description"])

                if key in known:
                    continue

                known.add(key)
                items.append({
                    "fingerprint": key, "title": spec["title"], "difficulty": spec["difficulty"],
                    "job_terms": sorted({t.lower() for t in job_terms}), "spec": spec, "used": 0,
                    "added": datetime.utcnow().isoformat(),
                })
                added += 1

            _save(items[-MAX_ITEMS:])

    except Exception as exc:                                  # the pool is a convenience, never a blocker
        logger.warning("Programming pool could not be updated: %s", exc)

    return added


def candidates(job_terms: list[str], difficulty: str, exclude: set[str], rng: random.Random | None = None) -> list[dict]:
    """Relevant pooled specs of this difficulty, least used first (a spec is relevant when it covers a job term)."""

    rng = rng or random.Random()
    wanted = {t.lower() for t in job_terms}

    try:
        items = _load()
    except Exception:
        return []

    relevant = [
        item for item in items
        if item["difficulty"] == difficulty and item["fingerprint"] not in exclude and wanted & set(item["job_terms"])
    ]

    rng.shuffle(relevant)
    relevant.sort(key=lambda item: item.get("used", 0))

    return relevant


def mark_used(keys: list[str]) -> None:

    try:
        with _LOCK:
            items = _load()
            for item in items:
                if item["fingerprint"] in keys:
                    item["used"] = item.get("used", 0) + 1
            _save(items)
    except Exception as exc:
        logger.warning("Programming pool usage could not be recorded: %s", exc)


def remove(key: str) -> None:
    """Drops a pooled question whose reference solution no longer validates."""

    try:
        with _LOCK:
            _save([item for item in _load() if item["fingerprint"] != key])
    except Exception as exc:
        logger.warning("Programming pool could not drop an entry: %s", exc)


def size() -> int:
    return len(_load())

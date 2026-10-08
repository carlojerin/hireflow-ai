import json
import logging
import os
import random
import re
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger("jobpilot.aptitude")

# ============================================================
# JOBPILOT AI - APTITUDE QUESTION POOL
#
# A small file-backed store (not the database) of questions that Groq has
# already generated for earlier candidates. It is only used when Groq is
# unavailable (rate limit / outage): the least-used questions are drawn first so
# candidates still get different sets. Nothing here scores anything.
# ============================================================

MAX_PER_BAND = 400

_LOCK = threading.Lock()


def _path() -> str:

    return os.getenv("APTITUDE_POOL_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
        "data", "aptitude_pool.json",
    )


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

    handle, temp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")

    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump({"items": items}, out)
        os.replace(temp_path, path)               # atomic: a reader never sees a half-written file
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _key(question_text: str) -> str:
    return re.sub(r"\s+", " ", question_text.lower()).strip()


def add(band: str, questions: list[dict]) -> int:
    """Store freshly generated questions (skipping ones already there). Returns how many were added."""

    added = 0

    try:

        with _LOCK:

            items = _load()
            known = {_key(item["question"]) for item in items}

            for question in questions:

                key = _key(question["question"])

                if key in known:
                    continue

                known.add(key)
                items.append({
                    "band": band,
                    "category": question["category"],
                    "difficulty": question.get("difficulty", "intermediate"),
                    "question": question["question"],
                    "options": list(question["options"]),
                    "correct_answer": question["correct_answer"],
                    "explanation": question.get("explanation", ""),
                    "verified": bool(question.get("_verified")),
                    "used": 0,
                    "added": datetime.utcnow().isoformat(),
                })
                added += 1

            # keep the newest MAX_PER_BAND per band
            for name in {item["band"] for item in items}:
                mine = [item for item in items if item["band"] == name]
                if len(mine) > MAX_PER_BAND:
                    drop = set(map(id, mine[:len(mine) - MAX_PER_BAND]))
                    items = [item for item in items if id(item) not in drop]

            _save(items)

    except Exception as exc:                                     # the pool is a convenience, never a blocker
        logger.warning("Aptitude pool could not be updated: %s", exc)

    return added


def draw(band: str, plan: dict, rng: random.Random | None = None) -> list[dict] | None:
    """
    A set matching `plan` ({category: count}) from the pool, least-used and verified questions
    first, or None if any category has too few questions.
    """

    rng = rng or random.Random()

    try:

        with _LOCK:

            items = _load()
            chosen, chosen_ids = [], []

            for category, count in plan.items():

                candidates = [
                    (index, item) for index, item in enumerate(items)
                    if item["band"] == band and item["category"] == category
                ]

                if len(candidates) < count:
                    return None

                rng.shuffle(candidates)
                candidates.sort(key=lambda pair: (pair[1].get("used", 0), not pair[1].get("verified", False)))

                for index, item in candidates[:count]:
                    chosen_ids.append(index)
                    chosen.append({
                        "category": item["category"],
                        "difficulty": item["difficulty"],
                        "question": item["question"],
                        "options": list(item["options"]),
                        "correct_answer": item["correct_answer"],
                        "explanation": item.get("explanation", ""),
                    })

            for index in chosen_ids:
                items[index]["used"] = items[index].get("used", 0) + 1

            _save(items)

    except Exception as exc:
        logger.warning("Aptitude pool could not be read: %s", exc)
        return None

    for item in chosen:
        item["options"] = rng.sample(item["options"], len(item["options"]))     # shuffle the options as well

    rng.shuffle(chosen)

    for number, item in enumerate(chosen, start=1):
        item["id"] = number

    return chosen


def size(band: str | None = None) -> int:

    return len([item for item in _load() if band is None or item["band"] == band])

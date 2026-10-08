"""
Runtime settings the admin can change from the console without editing .env or restarting: which Groq and Gemini
models the platform uses, and a few knobs that go with them.

How it works: every setting is the name of an environment variable the code already reads at call time. An admin choice
is stored in the admin_settings table and applied to os.environ (at startup and the moment it is saved), so the very next
LLM call uses it. "Reset" deletes the row and restores the value the process started with (.env). Only the keys in SETTINGS
can ever be set, each with its own validation, and no API key is ever readable or writable here.
"""

import os
from datetime import datetime

# group, label, help, kind (text | select | int | models), choices for select, bounds for int
SETTINGS = {
    "AI_INTERVIEW_MODEL": {
        "group": "groq", "label": "Groq model", "kind": "text",
        "help": "Used for resume analysis, aptitude / programming / SJT questions, the interview brain and summaries.",
    },
    "AI_INTERVIEW_REASONING_EFFORT": {
        "group": "groq", "label": "Groq reasoning effort", "kind": "select", "choices": ["low", "medium", "high"],
        "help": "Only for gpt-oss models. Higher thinks longer: better answers, more tokens, slower turns.",
    },
    "AI_INTERVIEW_MAX_TOKENS": {
        "group": "groq", "label": "Groq max output tokens", "kind": "int", "min": 300, "max": 8000,
        "help": "Cap per reply. Too low cuts the JSON off on reasoning models.",
    },
    "AI_INTERVIEW_PROVIDER": {
        "group": "groq", "label": "Interview brain provider", "kind": "select", "choices": ["groq", "gemini"],
        "help": "Which LLM writes the interview's questions. The live voice itself is always Gemini Live.",
    },
    "GEMINI_TEXT_MODEL": {
        "group": "gemini", "label": "Gemini text model", "kind": "text",
        "help": "Resume analysis, question generation, evaluation (the Gemini paths).",
    },
    "AI_INTERVIEW_GEMINI_MODELS": {
        "group": "gemini", "label": "Gemini interview-brain models (in order)", "kind": "models",
        "help": "Comma-separated, tried in order. Used when the interview brain provider is Gemini.",
    },
    "AI_REALTIME_MODEL": {
        "group": "gemini", "label": "Gemini Live voice model", "kind": "text",
        "help": "The model that talks to the candidate in the live interview. Takes effect on the next interview start.",
    },
    "AI_REALTIME_VOICE": {
        "group": "gemini", "label": "Gemini Live voice", "kind": "select",
        "choices": ["Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"],
        "help": "The interviewer's voice.",
    },
    "AI_TTS_GEMINI_MODELS": {
        "group": "gemini", "label": "Gemini speech (TTS) models (in order)", "kind": "models",
        "help": "Comma-separated, tried in order, for the spoken interviewer in the standard voice mode.",
    },
    "ASSESSMENT_REQUIRE_SCREEN_SHARE": {
        "group": "security", "label": "Require sharing the entire screen during tests", "kind": "select", "choices": ["on", "off"],
        "help": "On: candidates must share their ENTIRE screen (recorded with the camera) before aptitude, programming and interview stages start.",
    },
    # Not read by the LLM code: these are the numbers from the admin's own AI Studio rate-limit page, entered once so the
    # console can show "used vs limit" and an interviews-left estimate (Google has no API that reports them).
    "GEMINI_LIMIT_REQUESTS_PER_DAY": {
        "group": "limits", "label": "Gemini requests per day", "kind": "int", "min": 1, "max": 1000000000,
        "help": "Your text-model requests-per-day limit (RPD) from aistudio.google.com/rate-limit.",
    },
    "GEMINI_LIMIT_TOKENS_PER_DAY": {
        "group": "limits", "label": "Gemini tokens per day", "kind": "int", "min": 1, "max": 1000000000,
        "help": "Only if your plan has a daily token limit.",
    },
    "GEMINI_LIMIT_TOKENS_PER_MINUTE": {
        "group": "limits", "label": "Gemini Live tokens per minute", "kind": "int", "min": 1, "max": 1000000000,
        "help": "The TPM of your live voice model in AI Studio (for example 65000). Sets how many interviews can run at the same time.",
    },
    "GEMINI_LIMIT_LIVE_SESSIONS_PER_DAY": {
        "group": "limits", "label": "Gemini Live sessions per day", "kind": "int", "min": 1, "max": 1000000,
        "help": "Only if your plan limits live voice connections per day. One interview is normally one session.",
    },
}

_original_env: dict = {}


def _remember_originals():
    for key in SETTINGS:
        _original_env.setdefault(key, os.environ.get(key))


def original_value(key: str):
    _remember_originals()
    return _original_env.get(key)


def effective_value(key: str):
    return os.environ.get(key)


def validate_shape(key: str, value: str) -> str:
    """Returns the cleaned value or raises ValueError with a message for the admin."""

    spec = SETTINGS.get(key)
    if spec is None:
        raise ValueError("That setting cannot be changed here.")

    value = (value or "").strip()
    if not value:
        raise ValueError("Value is required. Use Reset to go back to the default.")
    if len(value) > 300 or any(c in value for c in "\r\n\x00"):
        raise ValueError("Value is not valid.")

    kind = spec["kind"]

    if kind == "select" and value not in spec["choices"]:
        raise ValueError("Choose one of: " + ", ".join(spec["choices"]))

    if kind == "int":
        try:
            number = int(value)
        except ValueError:
            raise ValueError("Enter a whole number.")
        if not spec["min"] <= number <= spec["max"]:
            raise ValueError(f"Enter a number between {spec['min']} and {spec['max']}.")
        return str(number)

    if kind == "models":
        names = [n.strip() for n in value.split(",") if n.strip()]
        if not names or len(names) > 8:
            raise ValueError("Give 1 to 8 model names separated by commas.")
        value = ",".join(names)

    if kind in ("text", "models") and not all(ch.isalnum() or ch in "-_./:, " for ch in value):
        raise ValueError("Model names may only contain letters, digits and - _ . / :")

    return value


def apply_setting(key: str, value) -> None:
    _remember_originals()
    if value is None:
        original = _original_env.get(key)
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
    else:
        os.environ[key] = value


def apply_all(db) -> int:
    """Called at startup: puts every saved admin choice into the environment."""

    from backend.app.models.admin_setting import AdminSetting

    _remember_originals()
    count = 0
    for row in db.query(AdminSetting).all():
        if row.key in SETTINGS:
            apply_setting(row.key, row.value)
            count += 1
    return count


def save_setting(db, key: str, value: str, admin_id) -> None:
    from backend.app.models.admin_setting import AdminSetting

    row = db.query(AdminSetting).filter(AdminSetting.key == key).first()
    if row is None:
        row = AdminSetting(key=key, value=value)
        db.add(row)
    row.value = value
    row.updated_by_admin_id = admin_id
    row.updated_at = datetime.utcnow()
    db.commit()
    apply_setting(key, value)


def reset_setting(db, key: str) -> None:
    from backend.app.models.admin_setting import AdminSetting

    db.query(AdminSetting).filter(AdminSetting.key == key).delete()
    db.commit()
    apply_setting(key, None)


def describe(db) -> list[dict]:
    from backend.app.models.admin_setting import AdminSetting

    saved = {r.key: r for r in db.query(AdminSetting).all()}
    out = []
    for key, spec in SETTINGS.items():
        row = saved.get(key)
        out.append({
            "key": key, "group": spec["group"], "label": spec["label"], "kind": spec["kind"],
            "help": spec["help"], "choices": spec.get("choices"), "min": spec.get("min"), "max": spec.get("max"),
            "value": effective_value(key) or "",
            "default": original_value(key) or "",
            "overridden": row is not None,
            "updated_at": row.updated_at.isoformat() if row else None,
        })
    return out

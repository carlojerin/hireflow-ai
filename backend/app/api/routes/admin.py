import shutil
import subprocess
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.api.deps import get_current_admin, require_csrf
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.admin_warning import AdminWarning
from backend.app.api.routes.users import hash_password, verify_password


# ============================================================
# JOBPILOT AI - ADMIN CONSOLE
#
# Platform-level operator tools, deliberately scoped to what the product
# owner actually asked for after the admin panel's original approve/reject
# gate was removed (companies now auto-verify at registration -- see
# company_invites.py / the HR Dashboard Simplification work). This is NOT
# that gate coming back: there is no "approve this company" action here.
# What IS here: view every registered HR/company account and every
# candidate invite sent across the whole platform, and real moderation
# controls on a company -- suspend/reactivate (account_active, fully
# reversible), issue a recorded warning, or permanently delete.
#
# Every route requires get_current_admin() (role == "admin" on the live
# User row, re-checked every request -- see deps.py's own docstring).
# ============================================================

router = APIRouter(prefix="/api/admin", tags=["Admin Console"])


# ------------------------------------------------------------
# COMPANIES / HR ACCOUNTS
# ------------------------------------------------------------

@router.get("/companies")
def list_companies(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Every registered company/HR account on the platform, with real
    counts (jobs posted, candidates invited) -- no approve/reject state
    shown, since registration already auto-verifies."""

    companies = db.query(Company).order_by(Company.id.desc()).all()

    job_counts = dict(
        db.query(Job.company_id, func.count(Job.id))
        .filter(Job.company_id.isnot(None))
        .group_by(Job.company_id)
        .all()
    )

    invite_counts = dict(
        db.query(CandidateInvite.company_id, func.count(CandidateInvite.id))
        .group_by(CandidateInvite.company_id)
        .all()
    )

    warning_counts = dict(
        db.query(AdminWarning.company_id, func.count(AdminWarning.id))
        .group_by(AdminWarning.company_id)
        .all()
    )

    return {
        "total": len(companies),
        "companies": [
            {
                "id": c.id,
                "company_name": c.company_name,
                "hr_name": c.hr_name,
                "email": c.email,
                "mobile": c.mobile,
                "account_active": c.account_active,
                "jobs_posted": job_counts.get(c.id, 0),
                "candidates_invited": invite_counts.get(c.id, 0),
                "warning_count": warning_counts.get(c.id, 0),
                "registered_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
            }
            for c in companies
        ],
    }


class CompanyStatusUpdate(BaseModel):
    account_active: bool


@router.patch("/companies/{company_id}/status")
def set_company_status(
    company_id: int,
    body: CompanyStatusUpdate,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Suspend (account_active=false) or reactivate (true) a company.
    Fully reversible -- this is the "remove access" lever, distinct from
    the permanent delete below. A suspended company's own login route
    already checks account_active, so this takes effect on their very
    next request, no session invalidation needed."""

    require_csrf(request)

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    company.account_active = body.account_active
    db.commit()

    return {
        "message": f"Company {'reactivated' if body.account_active else 'suspended'}.",
        "company_id": company_id,
        "account_active": company.account_active,
    }


class WarnCompanyRequest(BaseModel):
    message: str


@router.post("/companies/{company_id}/warn")
def warn_company(
    company_id: int,
    body: WarnCompanyRequest,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Records a warning against a company. Honest scope: this does NOT
    email the company -- no notification system exists for this yet. It's
    a durable, auditable record an admin can see ("has this company been
    warned before, and why") and read out to them directly."""

    require_csrf(request)

    message = body.message.strip()

    if not message:
        raise HTTPException(status_code=400, detail="Warning message cannot be empty.")

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    warning = AdminWarning(
        company_id=company_id,
        message=message,
        issued_by_admin_id=current_admin.id,
    )
    db.add(warning)
    db.commit()
    db.refresh(warning)

    return {
        "message": "Warning recorded.",
        "warning": {
            "id": warning.id,
            "company_id": warning.company_id,
            "message": warning.message,
            "created_at": warning.created_at.isoformat(),
        },
    }


@router.get("/companies/{company_id}/warnings")
def list_company_warnings(
    company_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    warnings = (
        db.query(AdminWarning)
        .filter(AdminWarning.company_id == company_id)
        .order_by(AdminWarning.created_at.desc())
        .all()
    )

    return {
        "company_id": company_id,
        "warnings": [
            {"id": w.id, "message": w.message, "created_at": w.created_at.isoformat()}
            for w in warnings
        ],
    }


@router.delete("/companies/{company_id}")
def delete_company(
    company_id: int,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Permanently deletes a company and every job, application (with its
    full assessment cascade), and candidate invite tied to it. Irreversible
    by design -- the frontend must get an explicit confirmation from the
    admin before ever calling this."""

    require_csrf(request)

    company = db.query(Company).filter(Company.id == company_id).first()

    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")

    from backend.app.models.job_application import JobApplication
    from backend.app.api.routes.company_applications import _delete_job_application_cascade

    job_ids = [row[0] for row in db.query(Job.id).filter(Job.company_id == company_id).all()]

    if job_ids:

        applications = db.query(JobApplication).filter(JobApplication.job_id.in_(job_ids)).all()

        for application in applications:
            _delete_job_application_cascade(db, application)

        db.query(CandidateInvite).filter(CandidateInvite.job_id.in_(job_ids)).delete(synchronize_session=False)
        db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)

    db.query(AdminWarning).filter(AdminWarning.company_id == company_id).delete(synchronize_session=False)

    db.delete(company)
    db.commit()

    return {"message": "Company and all associated data permanently deleted.", "company_id": company_id}


# ------------------------------------------------------------
# CANDIDATE INVITES (platform-wide view)
# ------------------------------------------------------------

@router.get("/invites")
def list_all_invites(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Every candidate invite sent by every company, newest first --
    who was invited, for which role, by which company, and its status."""

    rows = (
        db.query(CandidateInvite, Job, Company)
        .join(Job, Job.id == CandidateInvite.job_id)
        .join(Company, Company.id == CandidateInvite.company_id)
        .order_by(CandidateInvite.created_at.desc())
        .limit(500)
        .all()
    )

    return {
        "total": len(rows),
        "invites": [
            {
                "id": invite.id,
                "candidate_name": invite.candidate_name,
                "candidate_email": invite.candidate_email,
                "job_title": job.title,
                "company_name": company.company_name,
                "status": invite.status,
                "sent_at": invite.created_at.isoformat(),
                "expires_at": invite.expires_at.isoformat(),
            }
            for invite, job, company in rows
        ],
    }


# ------------------------------------------------------------
# SYSTEM CONNECTORS (live-checked, not a static snapshot)
# ------------------------------------------------------------

def _docker_reachable() -> bool:

    if not shutil.which("docker"):
        return False

    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


@router.get("/system-status")
def system_status(
    current_admin: User = Depends(get_current_admin),
):
    """Real, live-checked status -- API key presence (never the key
    itself) and an actual Docker daemon ping, re-run on every request."""

    import os

    groq_configured = bool(os.getenv("GROQ_API_KEY"))
    gemini_configured = bool(os.getenv("GEMINI_API_KEY"))
    docker_ok = _docker_reachable()

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "providers": {
            "groq": {"configured": groq_configured, "used_by": ["aptitude", "programming", "ai_interview_brain", "narrative_summary"]},
            "gemini": {"configured": gemini_configured, "used_by": ["ai_interview_realtime_voice", "ai_interview_tts"]},
            "local_stt": {"configured": True, "used_by": ["ai_interview_stt"], "note": "runs on this server, not a remote API"},
        },
        "sandbox": {
            "docker": {
                "reachable": docker_ok,
                "languages": ["python", "javascript", "typescript", "java", "c", "cpp", "go", "rust", "kotlin", "php", "ruby", "csharp", "swift"],
            },
            "sql": {"reachable": True, "note": "in-process SQLite sandbox, no Docker needed"},
            "html": {"reachable": True, "note": "headless browser, no Docker needed"},
        },
    }


# ------------------------------------------------------------
# LLM USAGE (Groq + Gemini)
# ------------------------------------------------------------

_probe_lock = __import__("threading").Lock()
_probe_state = {"groq_at": 0.0, "gemini_at": 0.0, "gemini": None}
PROBE_COOLDOWN_SECONDS = 30
GROQ_LIMITS_MAX_AGE_SECONDS = 600


def _probe_groq_limits():
    """One tiny real request: Groq answers every request with its live x-ratelimit-* headers."""

    import os
    import time
    import requests

    from backend.app.services.assessment.interview_llm_provider import HOSTED_PROVIDERS
    from backend.app.services.llm_usage import note_groq_limits

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return

    preset = HOSTED_PROVIDERS["groq"]
    model = os.getenv("AI_INTERVIEW_MODEL") or preset["default_model"]
    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 32}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"

    try:
        response = requests.post(
            f"{preset['base_url']}/chat/completions", json=payload, timeout=15,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        note_groq_limits(response.headers, source="live_check")
    except Exception:
        pass


def _check_gemini_key():
    """Free check: listing models proves the key is accepted by Google right now."""

    import os
    import time

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"key_valid": False, "detail": "GEMINI_API_KEY is not set."}

    try:
        from google import genai
        started = time.perf_counter()
        client = genai.Client(api_key=api_key)       # keep a reference: the pager needs the client alive while it is read
        models = client.models.list(config={"page_size": 1})
        next(iter(models), None)
        return {"key_valid": True, "detail": f"Google accepted the key ({round((time.perf_counter() - started) * 1000)} ms)."}
    except Exception as exc:
        return {"key_valid": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _int_env(name: str):
    import os
    try:
        value = int(os.getenv(name) or "")
        return value if value > 0 else None
    except ValueError:
        return None


def _gemini_plan(db, gemini_today, day_start):
    """Used-vs-limit for Gemini from the limits the admin entered, plus how many interviews are left on each of them."""

    from datetime import timedelta

    from backend.app.services import llm_usage

    limits = {
        "requests_per_day": _int_env("GEMINI_LIMIT_REQUESTS_PER_DAY"),
        "tokens_per_day": _int_env("GEMINI_LIMIT_TOKENS_PER_DAY"),
        "tokens_per_minute": _int_env("GEMINI_LIMIT_TOKENS_PER_MINUTE"),
        "live_sessions_per_day": _int_env("GEMINI_LIMIT_LIVE_SESSIONS_PER_DAY"),
    }

    today_live = llm_usage.gemini_live_stats(db, day_start)
    week_live = llm_usage.gemini_live_stats(db, day_start - timedelta(days=6))
    requests_today = (gemini_today or {}).get("requests", 0)
    tokens_today = (gemini_today or {}).get("total_tokens", 0)
    text_requests_today = max(0, requests_today - today_live["sessions"])

    left = []
    if limits["live_sessions_per_day"] is not None:
        left.append({"basis": "Live sessions per day", "interviews": max(0, limits["live_sessions_per_day"] - today_live["sessions"])})
    avg_tokens = week_live["avg_tokens_per_session"]
    if limits["tokens_per_day"] is not None and avg_tokens:
        left.append({"basis": f"Tokens per day (at about {avg_tokens:,} tokens per interview)", "interviews": max(0, (limits["tokens_per_day"] - tokens_today) // avg_tokens)})

    # an interview talks for avg_minutes and uses avg_tokens, so it draws about that many tokens EACH minute
    per_minute = None
    simultaneous = None
    if week_live["avg_tokens_per_session"] and week_live["avg_minutes_per_session"]:
        per_minute = round(week_live["avg_tokens_per_session"] / week_live["avg_minutes_per_session"])
        if limits["tokens_per_minute"] and per_minute:
            simultaneous = limits["tokens_per_minute"] // per_minute

    return {
        "limits": limits,
        "tokens_per_minute_per_interview": per_minute,
        "simultaneous_interviews": simultaneous,
        "used_today": {"requests": requests_today, "text_requests": text_requests_today, "tokens": tokens_today, "live_sessions": today_live["sessions"]},
        "live": {"today": today_live, "last_7_days": week_live},
        "interviews_left": min(left, key=lambda item: item["interviews"]) if left else None,
        "last_rate_limit": llm_usage.last_gemini_limit() or None,
    }


@router.get("/llm-usage")
def llm_usage(
    refresh: bool = False,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Groq: the provider's own live remaining requests/tokens. Gemini: what this platform has used (Google offers no
    remaining-quota API for an API key) plus a live key check. Both: today / last 7 days from the usage log."""

    import os
    import time
    from datetime import timedelta

    from backend.app.services import llm_usage
    from backend.app.services.assessment.interview_llm_provider import HOSTED_PROVIDERS

    now = time.monotonic()
    limits = llm_usage.groq_limits()
    limits_age = None
    if limits.get("checked_at"):
        limits_age = (datetime.utcnow() - datetime.fromisoformat(limits["checked_at"])).total_seconds()

    with _probe_lock:
        if os.getenv("GROQ_API_KEY") and now - _probe_state["groq_at"] >= PROBE_COOLDOWN_SECONDS and (
            refresh or not limits or limits_age is None or limits_age > GROQ_LIMITS_MAX_AGE_SECONDS
        ):
            _probe_state["groq_at"] = now
            _probe_groq_limits()
            limits = llm_usage.groq_limits()

        if _probe_state["gemini"] is None or refresh and now - _probe_state["gemini_at"] >= PROBE_COOLDOWN_SECONDS:
            _probe_state["gemini_at"] = now
            _probe_state["gemini"] = _check_gemini_key()

    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today = llm_usage.summarize(db, day_start)
    gemini_plan = _gemini_plan(db, today.get("gemini"), day_start)
    week = llm_usage.summarize(db, day_start - timedelta(days=6))
    empty = {"requests": 0, "ok": 0, "errors": 0, "rate_limited": 0, "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0, "models": []}

    return {
        "checked_at": datetime.utcnow().isoformat(),
        "providers": {
            "groq": {
                "configured": bool(os.getenv("GROQ_API_KEY")),
                "model": os.getenv("AI_INTERVIEW_MODEL") or HOSTED_PROVIDERS["groq"]["default_model"],
                "live_limits": limits or None,
                "today": today.get("groq", empty),
                "last_7_days": week.get("groq", empty),
                "note": "Limits are Groq's own live numbers (requests per day, tokens per minute) from its response headers.",
            },
            "gemini": {
                "configured": bool(os.getenv("GEMINI_API_KEY")),
                "model": os.getenv("AI_REALTIME_MODEL") or "",
                "key_check": _probe_state["gemini"],
                "plan": gemini_plan,
                "today": today.get("gemini", empty),
                "last_7_days": week.get("gemini", empty),
                "note": "Google does not publish remaining quota for an API key. These are the requests and tokens THIS platform sent. "
                        "Live voice (Gemini Live) sessions are not token-counted. Check aistudio.google.com for your plan's limits.",
            },
        },
        "history": llm_usage.daily_history(db, 7),
    }


# ------------------------------------------------------------
# LLM MODEL CONTROLS (change the Groq / Gemini models at runtime)
# ------------------------------------------------------------

_model_cache = {"at": 0.0, "groq": [], "gemini": []}
MODEL_LIST_TTL_SECONDS = 300


def _list_available_models():
    """Model names each provider offers this key (cached 5 min; empty on any failure, never blocks the page)."""

    import os
    import time

    if time.monotonic() - _model_cache["at"] < MODEL_LIST_TTL_SECONDS and (_model_cache["groq"] or _model_cache["gemini"]):
        return _model_cache["groq"], _model_cache["gemini"]

    groq_models, gemini_models = [], []

    if os.getenv("GROQ_API_KEY"):
        try:
            import requests
            response = requests.get(
                "https://api.groq.com/openai/v1/models", timeout=10,
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
            )
            ids = [m.get("id", "") for m in (response.json().get("data") or [])] if response.ok else []
            groq_models = sorted(i for i in ids if i and not any(x in i.lower() for x in ("whisper", "tts", "guard", "orpheus")))
        except Exception:
            pass

    if os.getenv("GEMINI_API_KEY"):
        try:
            from google import genai
            client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            names = [(m.name or "").replace("models/", "") for m in client.models.list()]
            gemini_models = sorted(n for n in names if n.startswith("gemini"))
        except Exception:
            pass

    _model_cache.update(at=time.monotonic(), groq=groq_models, gemini=gemini_models)
    return groq_models, gemini_models


def _test_groq_model(model: str) -> str | None:
    """One tiny real request on that model. Returns an error message, or None when it works."""

    import os
    import requests

    from backend.app.services.assessment.interview_llm_provider import HOSTED_PROVIDERS
    from backend.app.services.llm_usage import note_groq_limits

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "GROQ_API_KEY is not set."

    payload = {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 64}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"

    try:
        response = requests.post(
            f"{HOSTED_PROVIDERS['groq']['base_url']}/chat/completions", json=payload, timeout=20,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    except Exception as exc:
        return f"Could not reach Groq ({type(exc).__name__})."

    if response.status_code >= 400:
        try:
            message = (response.json().get("error") or {}).get("message") or ""
        except Exception:
            message = ""
        return f"Groq rejected this model (HTTP {response.status_code}): {message[:160]}"

    note_groq_limits(response.headers, source="live_check")
    return None


def _test_gemini_models(names: list[str]) -> str | None:

    import os

    if not os.getenv("GEMINI_API_KEY"):
        return "GEMINI_API_KEY is not set."

    try:
        from google import genai
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    except Exception as exc:
        return f"Could not start the Gemini client ({type(exc).__name__})."

    for name in names:
        try:
            client.models.get(model=name)
        except Exception as exc:
            return f"Google does not recognise '{name}': {str(exc)[:140]}"
    return None


class LlmSettingChange(BaseModel):
    key: str
    value: str
    skip_test: bool = False


class LlmSettingReset(BaseModel):
    key: str


@router.get("/llm-settings")
def get_llm_settings(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):

    from backend.app.services import admin_settings

    groq_models, gemini_models = _list_available_models()

    return {
        "settings": admin_settings.describe(db),
        "available": {"groq": groq_models, "gemini": gemini_models},
    }


@router.put("/llm-settings")
def change_llm_setting(
    body: LlmSettingChange,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Validates, live-tests (a tiny real request on Groq, a free model lookup on Gemini), saves, and applies at once."""

    import os

    from backend.app.services import admin_settings

    require_csrf(request)

    try:
        value = admin_settings.validate_shape(body.key, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    problem = None

    if not body.skip_test:
        if body.key == "AI_INTERVIEW_MODEL":
            problem = _test_groq_model(value)
        elif body.key in ("GEMINI_TEXT_MODEL", "AI_REALTIME_MODEL"):
            problem = _test_gemini_models([value])
        elif body.key in ("AI_INTERVIEW_GEMINI_MODELS", "AI_TTS_GEMINI_MODELS"):
            problem = _test_gemini_models(value.split(","))

    if body.key == "AI_INTERVIEW_PROVIDER":
        needed = "GROQ_API_KEY" if value == "groq" else "GEMINI_API_KEY"
        if not os.getenv(needed):
            problem = f"{needed} is not set, so the interview brain cannot use {value}."

    if problem:
        raise HTTPException(status_code=400, detail=problem)

    admin_settings.save_setting(db, body.key, value, current_admin.id)

    return {"ok": True, "key": body.key, "value": value, "tested": not body.skip_test}


@router.post("/llm-settings/reset")
def reset_llm_setting(
    body: LlmSettingReset,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):

    from backend.app.services import admin_settings

    require_csrf(request)

    if body.key not in admin_settings.SETTINGS:
        raise HTTPException(status_code=400, detail="That setting cannot be changed here.")

    admin_settings.reset_setting(db, body.key)

    return {"ok": True, "key": body.key, "value": admin_settings.effective_value(body.key) or ""}


# ------------------------------------------------------------
# ADMIN ACCOUNT SETTINGS
# ------------------------------------------------------------

class AdminPasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/change-password")
def change_admin_password(
    body: AdminPasswordChange,
    request: Request,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Lets the logged-in admin change their own password -- same
    verify-current-then-rehash pattern as the company settings page's
    change-password route, just scoped to the admin's own User row."""

    require_csrf(request)

    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")

    user = db.query(User).filter(User.id == current_admin.id).first()

    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    user.hashed_password = hash_password(body.new_password)
    db.commit()

    return {"message": "Password changed successfully."}

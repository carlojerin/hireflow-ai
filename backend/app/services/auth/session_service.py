import hashlib
import os
import secrets
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastapi import Response
from sqlalchemy.orm import Session

from backend.app.models.auth_session import AuthSession


# ============================================================
# JOBPILOT AI - SERVER-SIDE SESSION SERVICE
#
# The session cookie carries exactly one opaque, high-entropy
# token and nothing else — no password, no role, no user or
# company object. Every request looks up SHA-256(token) against
# auth_sessions; identity is never trusted from the browser
# itself. No signing/encryption secret is required anywhere in
# this design, because the cookie is a lookup key, not a
# container of data — there is nothing in it for a secret to
# protect.
#
# This intentionally does NOT replace any existing endpoint's
# ownership checks (those still trust client-supplied ids, as
# before) — it only makes a verified identity available for
# routes that choose to depend on it, starting with
# backend/app/api/routes/auth.py's own /me endpoint. Wiring
# these dependencies into the existing candidate/company/admin
# routers is a separate, later task.
# ============================================================

load_dotenv()

SESSION_COOKIE_NAME = "jobpilot_session"

# A SEPARATE cookie for company sessions (Auth Tab-Switch Hardening
# fix). Root cause of the "switch tabs, click something, suddenly
# logged out" bug: candidate ("user") and company sessions used to
# share this ONE cookie name. Cookies are stored per browser
# origin, not per tab — so logging into a candidate account in one
# tab silently overwrote the company session cookie an HR tab in
# the SAME browser was still relying on (and vice versa), and the
# next authenticated request from that other tab then genuinely,
# correctly failed get_current_company()'s/get_current_user()'s
# subject_type check with a real 401. Giving company sessions their
# own cookie name means logging into one role can never overwrite
# the other's cookie again. Admin sessions intentionally continue
# to share SESSION_COOKIE_NAME with candidate sessions (both are
# rows in the same `users` table, exactly as before) — that
# relationship is untouched; this fix only separates the
# user-vs-company collision that was actually reported/reproduced.
COMPANY_SESSION_COOKIE_NAME = "jobpilot_company_session"

TOKEN_BYTES = 32  # 256 bits of entropy — not a predictable id

SESSION_LIFETIME_HOURS = int(
    os.getenv("SESSION_LIFETIME_HOURS", "24")
)

# Absolute expiration, not idle-based: a session is valid for a
# fixed SESSION_LIFETIME_HOURS from creation regardless of
# activity. last_seen_at is still tracked (see touch_session)
# for audit visibility, but nothing currently extends
# expires_at from it — kept simple on purpose, per this task's
# scope (no refresh tokens, no idle-timeout logic).

SESSION_COOKIE_SECURE = (
    os.getenv("SESSION_COOKIE_SECURE", "false")
    .strip()
    .lower()
    == "true"
)

SESSION_COOKIE_SAMESITE = (
    os.getenv("SESSION_COOKIE_SAMESITE", "lax")
    .strip()
    .lower()
)


def _hash_token(raw_token: str) -> str:

    return hashlib.sha256(
        raw_token.encode()
    ).hexdigest()


def create_session(
    db: Session,
    subject_type: str,
    subject_id: int,
    role_at_login: str | None = None,
) -> tuple[str, AuthSession]:
    """
    Create a new server-side session for ("user", users.id) or
    ("company", companies.id). Returns (raw_token, AuthSession).
    raw_token is the value to put in the cookie — it is
    generated with secrets.token_urlsafe() (cryptographically
    secure) and is never itself persisted, only its hash.
    """

    raw_token = secrets.token_urlsafe(TOKEN_BYTES)

    now = datetime.utcnow()

    session_record = AuthSession(
        token_hash=_hash_token(raw_token),
        subject_type=subject_type,
        subject_id=subject_id,
        role_at_login=role_at_login,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=SESSION_LIFETIME_HOURS),
        revoked=False,
    )

    db.add(session_record)
    db.commit()
    db.refresh(session_record)

    return raw_token, session_record


def get_active_session(
    db: Session,
    raw_token: str | None,
) -> AuthSession | None:
    """
    Resolve a raw cookie token to its AuthSession row, only if
    it exists, is not revoked, and has not expired. Returns
    None for every failure case — this never raises; callers
    decide the HTTP status.
    """

    if not raw_token:
        return None

    session_record = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == _hash_token(raw_token)
        )
        .first()
    )

    if not session_record:
        return None

    if session_record.revoked:
        return None

    if datetime.utcnow() > session_record.expires_at:
        return None

    return session_record


def touch_session(
    db: Session,
    session_record: AuthSession,
) -> None:

    session_record.last_seen_at = datetime.utcnow()

    db.commit()


def revoke_session(
    db: Session,
    raw_token: str | None,
) -> None:
    """
    Revoke whatever session raw_token points to, if any.
    Idempotent — revoking an already-revoked, expired, or
    missing session is a safe no-op, so logout never needs to
    distinguish "was already logged out" from "logged out just
    now".
    """

    if not raw_token:
        return

    session_record = (
        db.query(AuthSession)
        .filter(
            AuthSession.token_hash == _hash_token(raw_token)
        )
        .first()
    )

    if session_record and not session_record.revoked:

        session_record.revoked = True
        session_record.revoked_at = datetime.utcnow()

        db.commit()


def set_session_cookie(
    response: Response,
    raw_token: str,
    cookie_name: str = SESSION_COOKIE_NAME,
) -> None:

    response.set_cookie(
        key=cookie_name,
        value=raw_token,
        max_age=SESSION_LIFETIME_HOURS * 3600,
        httponly=True,
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(
    response: Response,
    cookie_name: str = SESSION_COOKIE_NAME,
) -> None:

    response.delete_cookie(
        key=cookie_name,
        path="/",
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
    )


# ============================================================
# CSRF (double-submit cookie)
#
# jobpilot_session (above) is HttpOnly and never readable by
# JavaScript — that's what makes it a valid identity proof. The
# CSRF token is the opposite on purpose: a second, independent,
# NOT-HttpOnly cookie that the frontend reads and echoes back as
# a request header. A cross-site attacker page can make the
# browser attach the session cookie automatically, but same-
# origin policy stops it from ever reading this cookie's value
# to construct a matching header — so a mismatch or a missing
# header means the request did not originate from this site's
# own JavaScript.
#
# This token is intentionally NOT derived from the session
# token, the user id, or anything predictable — it is its own
# independent secret, and (per the standard double-submit
# design) needs no server-side storage at all: the cookie and
# the header are compared to each other, not looked up anywhere.
# ============================================================

CSRF_COOKIE_NAME = "csrf_token"

CSRF_HEADER_NAME = "X-CSRF-Token"

CSRF_TOKEN_BYTES = 32  # 256 bits — independent of the session token

CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def generate_csrf_token() -> str:

    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


def set_csrf_cookie(
    response: Response,
    csrf_token: str,
) -> None:

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=SESSION_LIFETIME_HOURS * 3600,
        httponly=False,  # JavaScript must read this to send the header
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_csrf_cookie(
    response: Response,
) -> None:

    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        samesite=SESSION_COOKIE_SAMESITE,
        secure=SESSION_COOKIE_SECURE,
    )

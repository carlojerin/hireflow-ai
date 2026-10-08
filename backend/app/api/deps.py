from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.user import User

import secrets

from backend.app.services.auth.session_service import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_SAFE_METHODS,
    SESSION_COOKIE_NAME,
    COMPANY_SESSION_COOKIE_NAME,
    get_active_session,
    touch_session,
)


# ============================================================
# JOBPILOT AI - CURRENT-IDENTITY DEPENDENCIES
#
# Reusable FastAPI dependencies that resolve the caller's real,
# session-backed identity — the first place in this project
# where "who is calling" is verified against the server rather
# than trusted from a client-supplied id.
#
# Scope note: these are not yet wired into any existing
# candidate/company/admin business route. That retrofit
# (replacing "does the URL's user_id match a row" with "does
# the session's identity match this resource") is a separate,
# later task — this module only makes a trustworthy identity
# available for routes that choose to depend on it.
# ============================================================

NOT_AUTHENTICATED_DETAIL = "Not authenticated. Please log in again."


def _get_authenticated_session(
    request: Request,
    db: Session,
    cookie_name: str = SESSION_COOKIE_NAME,
):

    raw_token = request.cookies.get(cookie_name)

    session_record = get_active_session(db, raw_token)

    if not session_record:

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    return session_record


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    """
    Same identity resolution as get_current_user(), but returns
    None instead of raising when there is no valid session.

    For endpoints that must remain usable by a signed-out caller
    (e.g. anonymous job browsing) but that also accept an
    optional, client-supplied user_id to personalize the
    response — that id must never be trusted on its own. The
    caller pattern is:

        current_user = Depends(get_current_user_optional)
        if user_id is not None and (
            current_user is None or current_user.id != user_id
        ):
            user_id = None  # not proven to be this caller's own id

    which degrades gracefully to the same anonymous behavior the
    endpoint already has for user_id=None, rather than trusting
    an unverified id or breaking anonymous access outright.
    """

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)

    session_record = get_active_session(db, raw_token)

    if not session_record or session_record.subject_type != "user":
        return None

    user = (
        db.query(User)
        .filter(User.id == session_record.subject_id)
        .first()
    )

    if not user or not user.is_active:
        return None

    touch_session(db, session_record)

    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Resolves the session cookie to a live, active User row —
    covers both candidate and admin sessions, since both are
    rows in the same users table. Raises 401 if there is no
    valid session, the session does not belong to a user, or
    the underlying account no longer exists / is deactivated.
    """

    session_record = _get_authenticated_session(request, db)

    if session_record.subject_type != "user":

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    user = (
        db.query(User)
        .filter(User.id == session_record.subject_id)
        .first()
    )

    if not user or not user.is_active:

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    touch_session(db, session_record)

    return user


def get_current_company(
    request: Request,
    db: Session = Depends(get_db),
) -> Company:
    """
    Resolves the session cookie to a live, active Company row.
    Raises 401 if there is no valid session, the session does
    not belong to a company, or the company account is no
    longer active.

    Reads its OWN cookie (COMPANY_SESSION_COOKIE_NAME) — separate
    from the candidate/admin session cookie — so logging into a
    candidate account in another browser tab can never overwrite
    (and thus break) an active company session, and vice versa.
    """

    session_record = _get_authenticated_session(
        request, db, cookie_name=COMPANY_SESSION_COOKIE_NAME,
    )

    if session_record.subject_type != "company":

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    company = (
        db.query(Company)
        .filter(Company.id == session_record.subject_id)
        .first()
    )

    if not company or not company.account_active:

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    touch_session(db, session_record)

    return company


def require_csrf(
    request: Request,
) -> None:
    """
    Double-submit CSRF check for state-changing requests.

    Called as a plain function inside a route body — deliberately
    NOT a FastAPI Depends() parameter — so it always runs after
    the route's own get_current_user()/get_current_company()/
    get_current_admin() call and its require_self()/
    require_company_self() authorization check, matching the
    intended order: authentication -> authorization -> CSRF ->
    business logic. A CSRF failure and an authorization failure
    both surface as an opaque 403 either way, so this ordering
    costs nothing in practice while keeping the existing,
    already-tested authorization architecture completely
    untouched.

    Safe methods (GET/HEAD/OPTIONS) are never state-changing and
    are exempt even if this is ever called on one by mistake.
    """

    if request.method in CSRF_SAFE_METHODS:
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)

    if not cookie_token or not header_token:

        raise HTTPException(
            status_code=403,
            detail="Missing CSRF token.",
        )

    if not secrets.compare_digest(cookie_token, header_token):

        raise HTTPException(
            status_code=403,
            detail="Invalid CSRF token.",
        )


def require_self(
    current_user: User,
    requested_user_id: int,
) -> None:
    """
    Raise 403 if the authenticated candidate is not the same
    person as the user_id named in the request's URL/query/body.
    The session's identity is authoritative; a client-supplied
    id is only ever a resource locator, never proof of identity.

    Checked BEFORE any "does this id exist" lookup wherever
    practical, so probing another candidate's id — real or not —
    always gets the same 403, never a distinguishing 404.
    """

    if current_user.id != requested_user_id:

        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource.",
        )


def require_company_self(
    current_company: Company,
    requested_company_id: int,
) -> None:
    """
    Raise 403 if the authenticated company is not the same
    company as the company_id named in the request's
    URL/query/body. Mirrors require_self() for the candidate
    side — the session's identity is authoritative; a
    client-supplied company_id is only ever a resource locator,
    never proof of identity.

    Checked BEFORE any "does this id exist" lookup wherever
    practical, so probing another company's id — real or not —
    always gets the same 403, never a distinguishing 404.
    """

    if current_company.id != requested_company_id:

        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource.",
        )


def get_current_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """
    Resolves the session cookie to a live User row that also
    currently has role == "admin" — the live database role,
    never the session's own role_at_login snapshot, so a role
    change takes effect immediately without waiting for the
    session to expire.

    Deliberately does NOT delegate to get_current_user(): that
    function raises 401 for any non-"user" session (e.g. a
    company session), which is the right behavior for a
    candidate-facing dependency but the wrong one here — a
    company (or a logged-in non-admin candidate) hitting an
    admin route is a real, live, authenticated session that is
    simply the wrong kind of principal for this resource, so it
    gets 403 (authenticated, but not permitted), matching the
    non-admin-user case below. Only a missing/invalid/expired/
    revoked session — no authenticated principal at all — gets
    401.
    """

    session_record = _get_authenticated_session(request, db)

    if session_record.subject_type != "user":

        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )

    user = (
        db.query(User)
        .filter(User.id == session_record.subject_id)
        .first()
    )

    if not user or not user.is_active:

        raise HTTPException(
            status_code=401,
            detail=NOT_AUTHENTICATED_DETAIL,
        )

    if user.role != "admin":

        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )

    touch_session(db, session_record)

    return user

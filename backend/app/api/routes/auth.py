from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.app.database.database import get_db

from backend.app.api.deps import get_current_company, get_current_user, require_csrf

from backend.app.services.auth.session_service import (
    SESSION_COOKIE_NAME,
    COMPANY_SESSION_COOKIE_NAME,
    clear_csrf_cookie,
    clear_session_cookie,
    generate_csrf_token,
    revoke_session,
    set_csrf_cookie,
)


# ============================================================
# JOBPILOT AI - SESSION AUTHENTICATION
#
# Two endpoints only:
#
# GET  /api/auth/me      - reports whichever identity (a
#                           candidate/admin user, or a company)
#                           the current session cookie resolves
#                           to. Also (re)issues the CSRF cookie
#                           on every successful call, so a page
#                           that calls this on load always ends
#                           up with a fresh, valid CSRF cookie
#                           even if the login response's cookie
#                           was somehow lost.
#
# POST /api/auth/logout  - revokes the current server-side
#                           session and clears both cookies.
#                           CSRF-protected like any other
#                           authenticated mutation.
#
# Login itself is NOT here — it stays on the existing
# /api/users/login, /api/companies/login, /api/admin/login
# endpoints, which now additionally create a session, set the
# session cookie, AND issue the CSRF cookie on success. See
# those route files for that change.
# ============================================================

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


@router.get("/me")
def get_current_session_identity(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):

    identity = None

    try:

        user = get_current_user(request, db)

        identity = {
            "subject_type": "user",
            "id": user.id,
            "username": user.username,
            "role": user.role,
        }

    except HTTPException:
        pass

    if identity is None:

        try:

            company = get_current_company(request, db)

            identity = {
                "subject_type": "company",
                "id": company.id,
                "company_name": company.company_name,
            }

        except HTTPException:
            pass

    if identity is None:

        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please log in again.",
        )

    # Refresh the CSRF cookie on every successful identity check —
    # never the session cookie's problem to solve twice.
    set_csrf_cookie(response, generate_csrf_token())

    return identity


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):

    require_csrf(request)

    # Revoke and clear BOTH the candidate/admin ("user") and
    # company session cookies — whichever this browser actually
    # holds. Two separate cookies now exist (see
    # COMPANY_SESSION_COOKIE_NAME), and a full logout must not
    # leave either one behind.
    user_token = request.cookies.get(SESSION_COOKIE_NAME)
    company_token = request.cookies.get(COMPANY_SESSION_COOKIE_NAME)

    revoke_session(db, user_token)
    revoke_session(db, company_token)

    clear_session_cookie(response, cookie_name=SESSION_COOKIE_NAME)
    clear_session_cookie(response, cookie_name=COMPANY_SESSION_COOKIE_NAME)
    clear_csrf_cookie(response)

    return {
        "message": "Logged out successfully."
    }

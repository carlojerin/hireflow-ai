from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.api.routes.assessment_videos import get_owned_application
from backend.app.database.database import get_db
from backend.app.models.assessment_consent import AssessmentConsent
from backend.app.models.user import User


# ============================================================
# JOBPILOT AI - ASSESSMENT CONSENT API
#
# The instructions pop-up ends with a tick box ("I have read the rules and promise to complete this assessment honestly").
# Ticking it and pressing Start records ONE row per (application, stage). Starting is idempotent: a second accept, or a
# page reload after the assessment began, never creates another row and the pop-up is not shown again.
# ============================================================

router = APIRouter(prefix="/api/assessment/consents", tags=["Assessment Consents"])

ALLOWED_STAGES = {"self_intro", "aptitude", "programming", "ai_interview"}
STATEMENT_VERSION = "2026-10-07"


class ConsentRequest(BaseModel):
    statement_version: str | None = None


def _response(row: AssessmentConsent | None) -> dict:

    return {
        "accepted": row is not None,
        "accepted_at": row.accepted_at.isoformat() if row else None,
        "statement_version": row.statement_version if row else None,
    }


@router.get("/{application_id}/{stage}")
def get_consent(
    application_id: int,
    stage: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    if stage not in ALLOWED_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage.")

    application = get_owned_application(db, application_id, current_user.id)

    row = (
        db.query(AssessmentConsent)
        .filter(AssessmentConsent.application_id == application.id, AssessmentConsent.assessment_type == stage)
        .first()
    )

    from backend.app.services.assessment.integrity_service import TERMINATION_LIMIT

    return {**_response(row), "current_statement_version": STATEMENT_VERSION, "max_violations": TERMINATION_LIMIT}


@router.post("/{application_id}/{stage}")
def accept_consent(
    application_id: int,
    stage: str,
    payload: ConsentRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    if stage not in ALLOWED_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage.")

    application = get_owned_application(db, application_id, current_user.id)

    existing = (
        db.query(AssessmentConsent)
        .filter(AssessmentConsent.application_id == application.id, AssessmentConsent.assessment_type == stage)
        .first()
    )

    if existing:
        return _response(existing)

    row = AssessmentConsent(
        application_id=application.id,
        assessment_type=stage,
        statement_version=STATEMENT_VERSION,          # always the server's own version, never what the browser claims
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        accepted_at=datetime.utcnow(),
    )

    db.add(row)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = (
            db.query(AssessmentConsent)
            .filter(AssessmentConsent.application_id == application.id, AssessmentConsent.assessment_type == stage)
            .first()
        )

    return _response(row)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.job_application import JobApplication
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.interview_test import InterviewTest
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.assessment_session import AssessmentSession

from backend.app.api.routes.aptitude_tests import (
    expire_if_due as expire_aptitude_if_due,
)
from backend.app.api.routes.programming_tests import (
    expire_if_due as expire_programming_if_due,
)
from backend.app.api.routes.interview_tests import (
    expire_if_due as expire_interview_if_due,
)
from backend.app.services.assessment.sjt_integration import (
    expire_if_due as expire_sjt_if_due,
)
from backend.app.services.assessment.session_service import (
    get_session_for_application,
)
from backend.app.services.assessment.integrity_service import (
    IntegrityServiceError,
    record_integrity_event,
    get_integrity_state,
)
from backend.app.api.deps import get_current_user, require_csrf, require_self


router = APIRouter(
    prefix="/api/assessment/integrity",
    tags=["Assessment Integrity"],
)


# ============================================================
# JOBPILOT AI - ASSESSMENT INTEGRITY API (Task 4D-STRICT-1)
#
# Thin route layer: authenticate, verify ownership, resolve the
# right underlying test row, verify it is actually active, then
# delegate everything else (dedup, counting, warning level,
# termination) to services/assessment/integrity_service.py — the
# one place that logic lives, reusable by programming/interview
# later without duplicating it here.
# ============================================================

class IntegrityEventRequest(BaseModel):
    user_id: int
    application_id: int
    assessment_type: str
    event_type: str
    # Task 5 - optional, small metadata dict (e.g. phone-detection
    # confidence/source). Never trusted verbatim - integrity_service
    # .record_integrity_event() re-sanitizes it against a fixed
    # per-event-type whitelist before anything reaches the database.
    metadata: dict | None = None


def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication:
    """Same combined id+owner filter, generic 404 either way —
    mirrors aptitude_tests.py's own helper exactly."""

    application = (
        db.query(JobApplication)
        .filter(
            JobApplication.id == application_id,
            JobApplication.user_id == user_id,
        )
        .first()
    )

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    return application


def _verify_aptitude_active(
    db: Session,
    application: JobApplication,
) -> None:
    """
    Raises 409 unless this application's AptitudeTest is
    currently "in_progress" — lazily expiring it first (reusing
    aptitude_tests.py's own expire_if_due(), never duplicating
    that logic) so a test whose exam time has already run out is
    never treated as still active just because no one has hit
    the aptitude-tests GET endpoint yet.
    """

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if not test:

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )

    expire_aptitude_if_due(db, test, application)

    if test.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )


def _verify_programming_active(
    db: Session,
    application: JobApplication,
) -> None:
    """Same shape as _verify_aptitude_active, for the Programming
    stage (Task 4E) — reuses programming_tests.py's own
    expire_if_due(), never duplicating that logic.

    Programming Engine V2, Live Integration task: ADDITIVELY also
    recognizes a ProgrammingAssessmentV2 row when no legacy
    ProgrammingTest exists for this application — the V2 route
    (assessment_sessions.py's /programming/v2/* endpoints) creates
    ProgrammingAssessmentV2, never ProgrammingTest, so without this
    the existing camera/integrity system would reject every
    integrity event for a V2 candidate with a false "not currently
    active" 409. The legacy check is tried FIRST and unchanged; the
    V2 check only runs when the legacy one finds nothing, so a
    legacy candidate's behavior is byte-for-byte identical to
    before this change.
    """

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if test:

        expire_programming_if_due(db, test, application)

        if test.status != "in_progress":

            raise HTTPException(
                status_code=409,
                detail="This assessment is not currently active.",
            )

        return

    from backend.app.services.assessment.programming_integration_v2 import (
        expire_if_due_v2,
    )

    assessment_v2 = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment_v2:

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )

    expire_if_due_v2(db, assessment_v2, application)

    if assessment_v2.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )


def _verify_interview_active(
    db: Session,
    application: JobApplication,
) -> None:
    """Same shape again, for the AI Interview stage (Task 4E) —
    reuses interview_tests.py's own expire_if_due().

    AI Interview V2 (Task 5H): ADDITIVELY also recognizes an
    AIInterviewSessionV2 row when no legacy InterviewTest exists
    for this application — mirrors _verify_programming_active()'s
    own Task 5C fix for exactly the same reason: the V2 route
    (assessment_sessions.py's /interview/v2/* endpoints) creates
    AIInterviewSessionV2, never InterviewTest, so without this the
    existing camera/integrity system rejected every integrity
    event for a V2 candidate with a false "not currently active"
    409 — caught live in this task's own real browser test. The
    legacy check is tried FIRST and unchanged; the V2 check only
    runs when the legacy one finds nothing, so a legacy candidate's
    behavior is byte-for-byte identical to before this change.
    """

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    if test:

        expire_interview_if_due(db, test, application)

        if test.status != "in_progress":

            raise HTTPException(
                status_code=409,
                detail="This assessment is not currently active.",
            )

        return

    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
    from backend.app.services.assessment.interview_integration_v2 import (
        expire_if_due_v2,
    )

    session_v2 = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not session_v2:

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )

    session_record = (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application.id)
        .first()
    )

    expire_if_due_v2(db, session_v2, application, session_record)

    if session_v2.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )


def _verify_situational_judgment_active(
    db: Session,
    application: JobApplication,
) -> None:
    """Same shape as _verify_aptitude_active, for the non-technical
    pipeline's Situational Judgment Test stage (occupies the same
    slot _verify_programming_active covers for a technical
    candidate — see role_classification.py)."""

    assessment = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if not assessment:

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )

    expire_sjt_if_due(db, assessment, application)

    if assessment.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )


def _verify_technical_domain_active(
    db: Session,
    application: JobApplication,
) -> None:
    """Same shape again, for the non-coding technical pipeline's
    Technical Domain Assessment stage (occupies the same slot
    _verify_programming_active covers for a technical candidate
    whose language IS executable — see role_classification.py)."""

    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
    from backend.app.services.assessment.tda_integration import expire_if_due as expire_tda_if_due

    assessment = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    if not assessment:

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )

    expire_tda_if_due(db, assessment, application)

    if assessment.status != "in_progress":

        raise HTTPException(
            status_code=409,
            detail="This assessment is not currently active.",
        )


_ACTIVE_CHECKS = {
    "aptitude": _verify_aptitude_active,
    "programming": _verify_programming_active,
    "ai_interview": _verify_interview_active,
    "situational_judgment": _verify_situational_judgment_active,
    "technical_domain": _verify_technical_domain_active,
}


# The self introduction has no strict mode (it is not a test that can be ended), so it does not use the endpoint below. It gets its own,
# much smaller one: camera attention FLAGS only. They are stored for the recruiter (and feed the "observed behaviour" paragraph under the
# video) and are never counted toward a warning, never shown to the candidate, and can never end the introduction.
SELF_INTRO_CAMERA_EVENTS = {
    "face_missing", "multiple_faces_detected", "looking_away", "gaze_away", "gaze_away_sustained", "camera_covered", "mobile_phone_detected",
    "poor_lighting",
}
SELF_INTRO_MIN_GAP_SECONDS = 5
SELF_INTRO_MAX_EVENTS = 200


class SelfIntroCameraEventRequest(BaseModel):
    application_id: int
    event_type: str


@router.post("/self-intro-event")
def record_self_intro_camera_event(
    payload: SelfIntroCameraEventRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    from datetime import datetime, timedelta

    from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent

    require_csrf(request)

    if payload.event_type not in SELF_INTRO_CAMERA_EVENTS:
        raise HTTPException(status_code=400, detail="Invalid event_type.")

    application = get_owned_application(db, payload.application_id, current_user.id)
    session_record = get_session_for_application(db, application.id)

    if session_record is None:
        raise HTTPException(status_code=409, detail="The assessment has not started.")

    existing = db.query(AssessmentIntegrityEvent).filter(
        AssessmentIntegrityEvent.application_id == application.id, AssessmentIntegrityEvent.assessment_type == "self_intro",
    )

    now = datetime.utcnow()

    if existing.count() >= SELF_INTRO_MAX_EVENTS:
        return {"recorded": False}

    latest_same = existing.filter(AssessmentIntegrityEvent.event_type == payload.event_type).order_by(AssessmentIntegrityEvent.occurred_at.desc()).first()

    if latest_same is not None and now - latest_same.occurred_at < timedelta(seconds=SELF_INTRO_MIN_GAP_SECONDS):
        return {"recorded": False}

    db.add(AssessmentIntegrityEvent(
        application_id=application.id, assessment_session_id=session_record.id, assessment_type="self_intro", event_type=payload.event_type,
        severity="info", counted_as_violation=False, occurred_at=now, created_at=now,
    ))
    db.commit()

    return {"recorded": True}


@router.post("/event")
def report_integrity_event(
    payload: IntegrityEventRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, payload.user_id)
    require_csrf(request)

    application = get_owned_application(
        db, payload.application_id, payload.user_id
    )

    active_check = _ACTIVE_CHECKS.get(payload.assessment_type)

    if not active_check:

        raise HTTPException(
            status_code=400,
            detail=(
                "Strict Assessment Mode is not yet integrated "
                f"for '{payload.assessment_type}'."
            ),
        )

    active_check(db, application)

    session_record = get_session_for_application(db, application.id)

    try:

        result = record_integrity_event(
            db,
            application,
            payload.assessment_type,
            payload.event_type,
            assessment_session_id=(
                session_record.id if session_record else None
            ),
            metadata=payload.metadata,
        )

    except IntegrityServiceError as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return result


@router.get("/state")
def get_current_integrity_state(
    application_id: int = Query(...),
    user_id: int = Query(...),
    assessment_type: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Read-only — lets a page refresh restore the candidate's
    current violation count/warning level without generating a
    new event.
    """

    require_self(current_user, user_id)

    application = get_owned_application(db, application_id, user_id)

    try:

        state = get_integrity_state(db, application, assessment_type)

    except IntegrityServiceError as exc:

        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return state

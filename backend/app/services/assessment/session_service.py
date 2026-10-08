from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.job_application import JobApplication


# ============================================================
# JOBPILOT AI - ASSESSMENT SESSION SERVICE
#
# Deterministic state-machine logic for the candidate's
# assessment journey. Mirrors the existing aptitude system's
# own conventions closely on purpose:
#   - one row per application (AptitudeTest's own shape)
#   - a fixed time limit enforced from a server-recorded
#     timestamp, never a client-supplied duration
#     (services/aptitude, backend/app/api/routes/
#     aptitude_tests.py's expire_if_due() pattern)
#   - a named, independent constant per business rule
#     (MINIMUM_AI_MATCH_TO_APPLY / AI_SHORTLIST_THRESHOLD /
#     APTITUDE_PASS_THRESHOLD already each live this way)
#
# This module never touches AptitudeTest, JobApplication.status,
# or the AI matching/aptitude engines — "aptitude" here is only
# a stage name this session's own state machine can point at,
# not a re-implementation of the real aptitude flow.
# ============================================================

# An application must be at one of these recruitment statuses
# before a brand-new assessment session can be created.
#
# "shortlisted" is the normal case: HR has shortlisted the
# candidate and no assessment stage has been touched yet.
#
# "aptitude_pending" additionally covers a real, still-live gap:
# company_applications.py's assign_aptitude_test() — the OLDER,
# still-live HR "Send Aptitude" action that predates this whole
# AssessmentSession/Self-Introduction architecture — creates an
# AptitudeTest row and advances JobApplication.status straight
# to "aptitude_pending" WITHOUT ever creating an AssessmentSession
# (it does not know this module exists). Before this fix, a
# candidate whose aptitude test was sent this way could never
# create a session at all: this function would refuse (status is
# "aptitude_pending", not "shortlisted"), yet
# aptitude_tests.py's own is_self_intro_completed() gate already
# requires a session to exist before Self Introduction — or
# Aptitude — can ever start. That combination was a genuine dead
# end, not a security boundary: "aptitude_pending" only ever means
# "HR already authorized and sent an assessment for this
# candidate," which is if anything a STRONGER signal than bare
# "shortlisted" that a session should be allowed to start. Self
# Introduction still always becomes current_stage below — this
# only changes which prior HR action is accepted as proof the
# candidate may begin the assessment pipeline, never what stage
# it begins on.
ASSESSMENT_START_STATUSES = {"shortlisted", "aptitude_pending"}

# 2 minutes, per the product spec. A small grace period is added
# only for completion requests (network/processing latency
# between the browser's own auto-stop and the request actually
# arriving) — never for extending what the candidate is told.
SELF_INTRO_MAX_SECONDS = 120
SELF_INTRO_GRACE_SECONDS = 15


class AssessmentSessionError(Exception):
    """
    Raised for any assessment-session business-rule violation.
    Carries an HTTP-style status code so the route layer can
    translate it without this service importing FastAPI.
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ============================================================
# OWNERSHIP LOOKUP
# ============================================================

def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication | None:
    """
    Fetch a JobApplication only if it belongs to user_id — the
    exact same combined id+owner filter already used by
    aptitude_tests.py's get_owned_application(), so an
    application_id belonging to a different candidate can never
    be distinguished from one that doesn't exist at all.
    """

    return (
        db.query(JobApplication)
        .filter(
            JobApplication.id == application_id,
            JobApplication.user_id == user_id,
        )
        .first()
    )


# ============================================================
# SESSION LOOKUP / CREATION
# ============================================================

def get_session_for_application(
    db: Session,
    application_id: int,
) -> AssessmentSession | None:

    return (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application_id)
        .first()
    )


def start_assessment_session(
    db: Session,
    application: JobApplication,
) -> AssessmentSession:
    """
    Create the assessment session for this application, or
    return the existing one unchanged if one already exists —
    idempotent on purpose, so a repeated "start" call (double
    click, page refresh, retried request) can never create a
    second, inconsistent session for the same application.
    """

    existing = get_session_for_application(db, application.id)

    if existing:
        return existing

    if application.status not in ASSESSMENT_START_STATUSES:

        raise AssessmentSessionError(
            status_code=400,
            detail=(
                "This application must be shortlisted, or already "
                "have an assessment sent by the recruiter, before "
                "the assessment can begin. Current status: "
                f"{application.status}."
            ),
        )

    now = datetime.utcnow()

    session_record = AssessmentSession(
        application_id=application.id,
        current_stage="self_intro",
        self_intro_status="not_started",
        created_at=now,
        updated_at=now,
    )

    db.add(session_record)
    db.commit()
    db.refresh(session_record)

    return session_record


# ============================================================
# SELF-INTRODUCTION — LAZY EXPIRY
#
# Mirrors aptitude_tests.py's expire_if_due(): called from
# every read/action so an in-progress self-introduction that
# ran past its time limit is always treated as expired, even
# without a background scheduler.
# ============================================================

def expire_self_intro_if_due(
    db: Session,
    session_record: AssessmentSession,
) -> bool:
    """
    Task 4D-SELF-INTRO-FIX: expiring self-introduction must
    NEVER be a dead end for the rest of the assessment. Before
    this fix, a lazily-expired self-introduction flipped
    self_intro_status to "expired" but left current_stage stuck
    at "self_intro" forever — since Aptitude eligibility
    (aptitude_integration.py's APTITUDE_READY_STAGES check, and
    aptitude_tests.py's own is_self_intro_completed() gate) both
    require current_stage to have moved past "self_intro", a
    candidate whose 2-minute self-introduction simply ran out
    could never reach Aptitude at all. Self-introduction is a
    low-stakes intro step, not a scored gate — it is recorded
    honestly as "expired" (never silently relabeled
    "completed"), but the assessment always moves on.
    """

    if session_record.self_intro_status != "in_progress":
        return session_record.self_intro_status == "expired"

    if not session_record.self_intro_started_at:
        return False

    deadline = session_record.self_intro_started_at + timedelta(
        seconds=SELF_INTRO_MAX_SECONDS + SELF_INTRO_GRACE_SECONDS
    )

    if datetime.utcnow() > deadline:

        session_record.self_intro_status = "expired"
        session_record.current_stage = "aptitude"
        session_record.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(session_record)

        return True

    return False


# ============================================================
# SELF-INTRODUCTION — START
# ============================================================

def start_self_intro(
    db: Session,
    session_record: AssessmentSession,
) -> AssessmentSession:

    if session_record.current_stage != "self_intro":

        raise AssessmentSessionError(
            status_code=400,
            detail=(
                "Self-introduction is not the current stage of "
                "this assessment."
            ),
        )

    expire_self_intro_if_due(db, session_record)

    if session_record.self_intro_status == "completed":

        raise AssessmentSessionError(
            status_code=409,
            detail="Self-introduction has already been completed.",
        )

    if session_record.self_intro_status == "expired":

        raise AssessmentSessionError(
            status_code=410,
            detail=(
                "The self-introduction time limit has already "
                "passed for this assessment."
            ),
        )

    # Idempotent resume: calling start again while already
    # in_progress never resets the server-recorded clock.
    if session_record.self_intro_status == "in_progress":
        return session_record

    now = datetime.utcnow()

    session_record.self_intro_status = "in_progress"
    session_record.self_intro_started_at = now
    session_record.updated_at = now

    db.commit()
    db.refresh(session_record)

    return session_record


# ============================================================
# SELF-INTRODUCTION — COMPLETE
# ============================================================

def complete_self_intro(
    db: Session,
    session_record: AssessmentSession,
    text_introduction: str | None,
) -> AssessmentSession:
    """
    Task 4D-SELF-INTRO-FIX: a completion request that arrives
    late (past the grace window — slow network, a delayed
    auto-submit) no longer discards the candidate's typed
    response and blocks them with a 410. It is graciously
    finalized instead: the response already entered is saved,
    self_intro_status is honestly recorded as "expired" rather
    than "completed", and — exactly like the lazy-expiry path in
    expire_self_intro_if_due() — current_stage still advances so
    Aptitude becomes available. The only two remaining error
    paths are: nothing to complete (never started), or it was
    already finalized by an earlier call (idempotent no-op,
    never a 409 pretending nothing happened).
    """

    if session_record.self_intro_status != "in_progress":

        expire_self_intro_if_due(db, session_record)

        if session_record.self_intro_status in ("completed", "expired"):

            # Already finalized — by this exact call racing a
            # prior one, or by a lazy expiry check that ran
            # first. Either way, current_stage has already
            # advanced; returning the current state is correct
            # and avoids a misleading error for a request that,
            # in effect, already got what it asked for.
            return session_record

        raise AssessmentSessionError(
            status_code=400,
            detail=(
                "Self-introduction must be started before it can "
                "be completed."
            ),
        )

    if not session_record.self_intro_started_at:

        raise AssessmentSessionError(
            status_code=400,
            detail="Self-introduction was never properly started.",
        )

    now = datetime.utcnow()

    elapsed_seconds = (
        now - session_record.self_intro_started_at
    ).total_seconds()

    # The one rule this whole module exists to enforce: the
    # server's own clock decides whether the time limit was
    # respected — never a duration value the client could send.
    # Whether or not it was respected, the response already
    # entered is preserved and the assessment moves on — see the
    # docstring above.
    timed_out = elapsed_seconds > (
        SELF_INTRO_MAX_SECONDS + SELF_INTRO_GRACE_SECONDS
    )

    session_record.self_intro_status = "expired" if timed_out else "completed"
    session_record.self_intro_completed_at = now
    session_record.self_intro_duration_seconds = round(
        min(elapsed_seconds, SELF_INTRO_MAX_SECONDS)
    )

    session_record.self_intro_text = (
        text_introduction.strip()
        if text_introduction and text_introduction.strip()
        else None
    )

    session_record.current_stage = "aptitude"
    session_record.updated_at = now

    db.commit()
    db.refresh(session_record)

    return session_record

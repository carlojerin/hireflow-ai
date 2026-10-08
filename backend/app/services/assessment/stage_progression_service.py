import logging
from urllib.parse import quote

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.interview_test import InterviewTest
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
from backend.app.models.job import Job
from backend.app.services.assessment.assignment_service import create_assignment, get_assignment
from backend.app.services.assessment.role_classification import job_role_shape, job_requires_domain_assessment


logger = logging.getLogger("jobpilot.assessment.stage_progression")


# ============================================================
# JOBPILOT AI - AUTHORITATIVE STAGE PROGRESSION (Task 6A)
#
# The ONE place that decides where an application is in the fully
# automatic pipeline:
#
#   Self Intro -> Aptitude -> Programming/Technical -> AI Interview
#
# It derives everything from the persisted results (AssessmentSession,
# AptitudeTest, ProgrammingAssessmentV2 / legacy ProgrammingTest,
# AIInterviewSessionV2 / legacy InterviewTest, JobApplication.status)
# -- never from anything the frontend claims. The frontend only
# renders what determine_next_stage() returns.
#
# advance_pipeline() is the only writer: it idempotently creates the
# missing system assignment for the next stage once the previous one
# is passed. It never creates a duplicate and never skips a stage.
# HR's manual "assign" endpoints remain as a backward-compatible
# fallback and go through the same create_assignment().
# ============================================================

STAGES = ("self_intro", "aptitude", "programming", "ai_interview")

_LABELS = {
    "self_intro": "Self Introduction",
    "aptitude": "Aptitude",
    "programming": "Programming / Technical",
    "ai_interview": "AI Interview",
}

_PAGES = {
    "self_intro": "assessment.html",
    "aptitude": "aptitude-test.html",
    "programming": "programming-v3.html",
    "ai_interview": "ai-interview-v2.html",
}

# Stage 3's actual page/label depends on which assessment occupies
# it for THIS application (see _stage_3_assessment_type()) — the
# generic STAGES/"programming" key above is never enough on its own,
# otherwise a non-technical or no-executable-language candidate's
# "Continue" link would silently point at the coding assessment page
# they were never assigned (a real bug this module shipped with
# until it was caught here: _stage_url()/_LABELS used to be keyed
# purely by STAGE name, so EVERY stage-3 candidate — including SJT —
# got sent to programming-v3.html regardless of which assessment they
# actually had).
_STAGE_3_LABELS = {
    "programming": "Programming / Technical",
    "situational_judgment": "Situational Judgment Test",
    "technical_domain": "Technical Assessment",
}

_STAGE_3_PAGES = {
    "programming": "programming-v3.html",
    "situational_judgment": "sjt-test.html",
    "technical_domain": "tda-test.html",
}

_FAILED_TEST_STATUSES = {"expired", "terminated"}


def _stage_url(stage: str, application_id: int, page: str | None = None) -> str:
    return f"{page or _PAGES[stage]}?application_id={quote(str(application_id))}"


def _test_state(test) -> str:
    """completed_passed | completed_failed | failed | in_progress | none"""

    if test is None:
        return "none"

    status = getattr(test, "status", None)

    if status == "completed":
        return "completed_passed" if getattr(test, "passed", None) is True else "completed_failed"

    # A timed-out attempt is a finished (not passed) attempt: the candidate moves on and HR decides. Only an
    # integrity TERMINATION still ends the pipeline.
    if status == "expired":
        return "completed_failed"

    if status in _FAILED_TEST_STATUSES:
        return "failed"

    return "in_progress"


def _stage_3_role_shape(db: Session, application: JobApplication) -> str:
    """"technical" | "non_technical" for this application's own job —
    see role_classification.py for the shared heuristic."""

    job = db.query(Job).filter(Job.id == application.job_id).first()

    return job_role_shape(job) if job is not None else "technical"


def _stage_3_job(db: Session, application: JobApplication) -> Job | None:
    return db.query(Job).filter(Job.id == application.job_id).first()


def _stage_3_assessment_type(db: Session, application: JobApplication) -> str:
    """Which assignment/assessment occupies the pipeline's third slot
    for THIS application — "programming" for a technical role whose
    language JobPilot can execute, "technical_domain" for a technical
    role whose language it cannot (SAP, Salesforce, Solidity, RPA,
    embedded, DevOps/infra, networking — see role_classification.py's
    job_requires_domain_assessment()), "situational_judgment" for a
    non-technical one (Digital Marketing, Sales, Finance/Compliance,
    etc. — at ANY company, including a technical one)."""

    job_for_plan = _stage_3_job(db, application)

    if job_for_plan is not None:
        try:
            from backend.app.services.assessment.assessment_planner import practical_spec
            if practical_spec(job_for_plan):
                return "situational_judgment"           # a practical (work-sample) test occupies this slot, even for a hybrid role such as a UI designer
        except Exception:  # noqa: BLE001 - classification must never break progression
            pass

    if _stage_3_role_shape(db, application) == "non_technical":
        return "situational_judgment"

    job = _stage_3_job(db, application)

    return "technical_domain" if (job is not None and job_requires_domain_assessment(job)) else "programming"


def _programming_state(db: Session, application: JobApplication) -> str:

    assessment_type = _stage_3_assessment_type(db, application)

    if assessment_type == "situational_judgment":

        sjt = (
            db.query(SituationalJudgmentTest)
            .filter(SituationalJudgmentTest.application_id == application.id)
            .first()
        )

        return _test_state(sjt)

    if assessment_type == "technical_domain":

        tda = (
            db.query(TechnicalDomainAssessment)
            .filter(TechnicalDomainAssessment.application_id == application.id)
            .first()
        )

        return _test_state(tda)

    v2 = db.query(ProgrammingAssessmentV2).filter(ProgrammingAssessmentV2.application_id == application.id).first()

    if v2 is not None:
        return _test_state(v2)

    legacy = db.query(ProgrammingTest).filter(ProgrammingTest.application_id == application.id).first()

    return _test_state(legacy)


def _interview_state(db: Session, application: JobApplication) -> str:

    v2 = db.query(AIInterviewSessionV2).filter(AIInterviewSessionV2.application_id == application.id).first()

    if v2 is not None:

        if v2.status == "in_progress":
            return "in_progress"

        if v2.status == "expired":
            return "completed_failed"

        if v2.status == "terminated":
            return "failed"

        # completed: pass/fail is what the interview finalizer recorded on the application
        return "completed_failed" if application.status in ("interview_failed", "interview_terminated") else "completed_passed"

    legacy = db.query(InterviewTest).filter(InterviewTest.application_id == application.id).first()

    return _test_state(legacy)


def determine_next_stage(db: Session, application: JobApplication, include_interview: bool = True) -> dict:
    """
    Pure read. Returns every stage's status
    (completed | failed | current | locked), the current stage, the
    next stage, the overall outcome and where the candidate goes next.
    """

    session_record = db.query(AssessmentSession).filter(AssessmentSession.application_id == application.id).first()

    aptitude = db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).first()

    # Same definition as aptitude_tests.is_self_intro_completed(): the stage
    # only ever leaves "self_intro" once the introduction was completed or
    # gracefully expired.
    intro_done = bool(session_record) and (
        session_record.current_stage != "self_intro"
        or session_record.self_intro_status in ("completed", "expired")
    )

    states = {
        "aptitude": _test_state(aptitude),
        "programming": _programming_state(db, application),
        "ai_interview": _interview_state(db, application) if include_interview else "none",
    }

    # Which actual assessment occupies stage 3 for THIS application —
    # "programming" (executable language), "technical_domain"
    # (technical, no executable language), or "situational_judgment"
    # (non-technical). Computed once, used for both this stage's own
    # label/page AND next_action's (see _stage_url() below).
    stage_3_assessment_type = _stage_3_assessment_type(db, application)

    results: dict[str, dict] = {}
    blocked = False

    for stage in STAGES:

        if stage == "self_intro":
            state = "completed_passed" if intro_done else ("in_progress" if session_record else "none")
        else:
            state = states[stage]

        if blocked:
            status = "locked"
        elif state in ("completed_passed", "completed_failed"):
            # Passing is NOT required to move on: HR makes the final decision from the results. A stage that was
            # finished without passing is "completed" here, and its own `passed: False` keeps that visible.
            status = "completed"
        elif state == "failed":
            status = "failed"                     # integrity termination: the one thing that still ends the pipeline
            blocked = True
        else:
            status = "current"
            blocked = True

        label = _STAGE_3_LABELS[stage_3_assessment_type] if stage == "programming" else _LABELS[stage]

        # the AI planner may have renamed a test for this job (for example "Language & Grammar Assessment")
        if stage == "aptitude" or (stage == "programming" and stage_3_assessment_type == "situational_judgment"):
            try:
                from backend.app.services.assessment.assessment_planner import stage_title
                label = stage_title(_stage_3_job(db, application), stage, label)
            except Exception:  # noqa: BLE001 - a label must never break progression
                pass

        results[stage] = {
            "stage": stage,
            "label": label,
            "status": status,
            "passed": (
                True if state == "completed_passed"
                else False if state in ("completed_failed", "failed")
                else None
            ),
        }

    ordered = [results[s] for s in STAGES]
    failed = next((r for r in ordered if r["status"] == "failed"), None)
    current = next((r for r in ordered if r["status"] == "current"), None)

    if failed:
        outcome = "failed"
    elif current is None:
        outcome = "completed"
    else:
        outcome = "in_progress"

    next_stage = None
    if current:
        idx = STAGES.index(current["stage"])
        next_stage = STAGES[idx + 1] if idx + 1 < len(STAGES) else None

    return {
        "application_id": application.id,
        "application_status": application.status,
        "stages": ordered,
        "outcome": outcome,
        "failed_stage": failed["stage"] if failed else None,
        "current_stage": current["stage"] if current else None,
        "next_stage": next_stage,
        "next_action": (
            {
                "stage": current["stage"],
                "label": current["label"],
                "url": _stage_url(
                    current["stage"], application.id,
                    page=_STAGE_3_PAGES.get(stage_3_assessment_type) if current["stage"] == "programming" else None,
                ),
            }
            if current else None
        ),
    }


def advance_pipeline(db: Session, application: JobApplication) -> dict:
    """
    Idempotently creates the missing system assignment for the stage
    that follows a PASSED stage. Best-effort by design (a persisted
    result is never rolled back because an assignment could not be
    created; the next read/advance heals it). NEVER raises: returns
    the determine_next_stage() view, or None if it could not be
    evaluated.
    """

    try:

        # Only the aptitude/programming results decide which assignment is
        # due, so the interview tables are deliberately not read here.
        due = determine_next_stage(db, application, include_interview=False)

    except Exception as exc:

        db.rollback()
        logger.warning("Stage progression could not be evaluated for application %s: %s", application.id, exc)
        return None

    by_stage = {s["stage"]: s for s in due["stages"]}

    wanted = []

    if by_stage["aptitude"]["status"] == "completed":
        # "programming" (executable language), "technical_domain"
        # (technical but no executable language), or
        # "situational_judgment" (non-technical) — see
        # role_classification.py.
        wanted.append(_stage_3_assessment_type(db, application))

    if by_stage["programming"]["status"] == "completed":
        wanted.append("ai_interview")

    blocked = None

    for assessment_type in wanted:

        try:

            if not get_assignment(db, application.id, assessment_type):

                # Shortlist -> blueprint -> capability/content validation -> valid assessment -> candidate assignment:
                # a programming assignment is NEVER created when the blueprint cannot produce a valid assessment.
                # (situational_judgment and technical_domain have no equivalent feasibility gate — their curated
                # banks always have enough content, unlike a job-specific programming blueprint that can genuinely
                # be infeasible; technical_domain was only selected in the first place because
                # job_requires_domain_assessment() already confirmed a matching domain bank exists.)
                if assessment_type == "programming":

                    from backend.app.services.assessment.programming_integration_v2 import programming_assessment_feasible

                    feasible, reason = programming_assessment_feasible(db, application)

                    if not feasible:
                        blocked = {"stage": "programming", "reason": reason}
                        logger.warning(
                            "Programming assignment NOT created for application %s: %s", application.id, reason,
                        )
                        continue

                create_assignment(
                    db, application.id, assessment_type,
                    assigned_by_id=application.user_id, assigned_by_type="system",
                )

        except Exception as exc:

            db.rollback()
            logger.warning(
                "Automatic %s assignment failed for application %s: %s",
                assessment_type, application.id, exc,
            )

    try:

        view = determine_next_stage(db, application)

        if blocked:
            view["blocked"] = blocked                     # why the next assignment was not created (the pipeline stays honest)

        return view

    except Exception as exc:

        db.rollback()
        logger.warning("Stage progression view unavailable for application %s: %s", application.id, exc)
        return None

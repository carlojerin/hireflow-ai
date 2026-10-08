import dataclasses
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2

from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.services.application_status_history import record_status_change
from backend.app.services.assessment.assessment_plan_engine import build_assessment_plan
from backend.app.services.assessment.assignment_service import is_assigned
from backend.app.services.assessment.blueprint_engine import build_assessment_blueprint
from backend.app.services.assessment.programming_generator_v2 import (
    generate_programming_assessment_v2,
)
from backend.app.services.assessment.programming_role_orchestrator import generate_role_based_assessment
from backend.app.services.assessment.programming_hr_report_v2 import build_hr_report_v2
from backend.app.services.assessment.programming_scoring_v2 import score_assessment
from backend.app.services.assessment.programming_submission_v2 import (
    CandidateSubmission,
    LanguageRejectedResult,
    run_public_examples,
    submit_question,
)
from backend.app.schemas.programming_question import REQUIRED_QUESTION_COUNT


logger = logging.getLogger("jobpilot.assessment.programming_integration_v2")


# ============================================================
# JOBPILOT AI - PROGRAMMING V2 <-> LIVE ASSESSMENT BRIDGE
# (Programming Engine V2, Live Integration task)
#
# Mirrors backend/app/services/assessment/programming_
# integration.py's own architecture and every business rule it
# already enforces (eligibility gate, assignment gate, deadline,
# JobApplication.status vocabulary: "programming_pending" /
# "programming_passed" / "programming_failed" / "programming_
# expired" — DELIBERATELY REUSED, not reinvented, so every
# existing HR pipeline view/filter that already queries by these
# status strings keeps working unchanged for a V2 candidate).
#
# NEVER modifies the legacy ProgrammingTest/ProgrammingSubmission
# tables or rows. Persists exclusively into the NEW, additive
# programming_assessments_v2 / programming_submissions_v2 tables.
#
# ADDITIVE, NOT A REPLACEMENT: nothing here is called by the
# existing live route yet — see assessment_sessions.py's new,
# separate /programming/v2/* endpoints, which exist ALONGSIDE the
# original /programming/start endpoint. Per this task's own
# closing instruction, the new route becomes the normal path only
# after it is proven — this module makes that later switch
# possible without needing to be rewritten.
# ============================================================

PROGRAMMING_V2_START_STATUS = "aptitude_passed"
PROGRAMMING_V2_DEADLINE_MINUTES = 45
PROGRAMMING_V2_PASS_THRESHOLD = 70.0

PROGRAMMING_V2_READY_STAGES = ("programming", "ai_interview", "completed")


class ProgrammingIntegrationV2Error(Exception):

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _get_passed_aptitude_test(db: Session, application: JobApplication) -> AptitudeTest | None:

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if test and test.status == "completed":   # finished: passing is not required to continue
        return test

    return None


def _load_question_set(assessment: ProgrammingAssessmentV2) -> ProgrammingQuestionSetV2:

    return ProgrammingQuestionSetV2.model_validate_json(assessment.question_set_json)


# ============================================================
# GENERATE-OR-RETRIEVE
# ============================================================

def start_programming_assessment_v2(
    db: Session,
    session_record,
    application: JobApplication,
    job,
    user,
    profile,
    resume_analysis,
    use_gemini: bool = True,
) -> tuple[ProgrammingAssessmentV2, bool]:
    """
    Returns (assessment, created). Idempotent: an existing row for
    this application is returned unchanged (created=False), never
    regenerated — this is what makes the assessment definition a
    STABLE SNAPSHOT for the candidate's whole attempt, exactly as
    this task requires.
    """

    current_stage = session_record.current_stage

    if current_stage not in PROGRAMMING_V2_READY_STAGES and current_stage != "aptitude":

        raise ProgrammingIntegrationV2Error(
            400,
            "Complete the self-introduction and aptitude stages "
            "before starting the programming assessment.",
        )

    existing = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if existing:
        return existing, False

    passed_aptitude = _get_passed_aptitude_test(db, application)

    if not passed_aptitude:

        raise ProgrammingIntegrationV2Error(
            400, "You must finish the aptitude assessment before starting the programming assessment.",
        )

    if not is_assigned(db, application.id, "programming"):

        raise ProgrammingIntegrationV2Error(
            400, "Programming assessment has not been assigned yet.",
        )

    if application.status not in (PROGRAMMING_V2_START_STATUS, "aptitude_failed"):

        raise ProgrammingIntegrationV2Error(
            400,
            f"This application is not eligible for the programming "
            f"assessment. Current status: {application.status}.",
        )

    blueprint = build_assessment_blueprint(
        user=user, profile=profile, resume_analysis=resume_analysis, job=job,
        total_questions=REQUIRED_QUESTION_COUNT, assessment_type="programming",
    )

    # The plan is the deterministic one: Gemini plays no part in programming assessments any more.
    plan = build_assessment_plan(job, blueprint, use_gemini=False)

    # Role-based: the blueprint allocates the 5 questions; Groq writes and validates the coding ones (pool and
    # role-aware fallbacks below it). `use_gemini` is kept as the parameter name for existing callers: False = no AI.
    result = generate_role_based_assessment(
        job, blueprint, plan, user=user, profile=profile, resume_analysis=resume_analysis, use_ai=use_gemini,
    )

    if result.status == "not_applicable":

        raise ProgrammingIntegrationV2Error(
            409,
            "This job's assessment plan does not include a programming "
            "assessment.",
        )

    if result.status == "capability_unavailable":

        raise ProgrammingIntegrationV2Error(409, result.message)

    question_set = result.question_set

    allowed_languages = sorted({
        lang for q in question_set.main_questions for lang in q.allowed_languages
    })

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=PROGRAMMING_V2_DEADLINE_MINUTES)

    assessment = ProgrammingAssessmentV2(
        application_id=application.id,
        question_set_json=question_set.model_dump_json(),
        allowed_languages_json=json.dumps(allowed_languages),
        generated_by=result.generated_by or "generic_fallback",
        status="in_progress",
        score=None,
        total_score=None,
        passed=None,
        assigned_at=now,
        expires_at=expires_at,
        started_at=now,
        submitted_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(assessment)
    db.flush()

    session_record.current_stage = "programming"
    session_record.updated_at = now

    record_status_change(
        db, application, to_status="programming_pending",
        changed_by_type="candidate", changed_by_id=application.user_id,
        note="Programming assessment (V2, multi-language) generated automatically after passing the personalized aptitude assessment.",
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(session_record)
    db.refresh(application)

    return assessment, True


# ============================================================
# EXPIRY (mirrors programming_tests.py's expire_if_due exactly)
# ============================================================

def expire_if_due_v2(db: Session, assessment: ProgrammingAssessmentV2, application: JobApplication) -> bool:

    if assessment.status == "expired":
        return True

    if assessment.status == "in_progress" and datetime.utcnow() > assessment.expires_at:

        assessment.status = "expired"
        assessment.updated_at = datetime.utcnow()

        record_status_change(
            db, application, to_status="programming_expired",
            changed_by_type="system", changed_by_id=None,
            note="Programming assessment (V2) deadline passed without a final submission.",
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(assessment)
        db.refresh(application)

        return True

    return False


# ============================================================
# CANDIDATE-SAFE VIEW (never includes hidden test data)
# ============================================================

def candidate_view_v2(assessment: ProgrammingAssessmentV2, job=None) -> dict:

    question_set = _load_question_set(assessment)
    allowed_languages = json.loads(assessment.allowed_languages_json)

    questions = []

    for q in question_set.main_questions:

        questions.append({
            "question_id": q.question_id,
            "title": q.title,
            "description": q.description,
            "constraints": q.constraints,
            "examples": q.examples,
            "input_specification": q.input_specification,
            "output_specification": q.output_specification,
            "difficulty": q.difficulty,
            "technology": q.technology,
            "allowed_languages": q.allowed_languages,
            "points": q.points,
            # Task 5G (V3) additive candidate-facing fields — every
            # one is optional/None-or-empty on any question written
            # before this task (fallback bank, or an old persisted
            # snapshot), so the V3 UI must treat them as optional.
            "scenario": q.scenario,
            "requirements": q.requirements,
            "expected_complexity": q.expected_complexity,
            "notes": q.notes,
            "subparts": [
                {"subpart_id": sp.subpart_id, "description": sp.description, "weight": sp.weight}
                for sp in q.subparts
            ],
        })

    metadata = question_set.generation_metadata or {}
    source = assessment.generated_by
    selection = metadata.get("language_selection") or {}

    return {
        "status": assessment.status,
        "assigned_at": assessment.assigned_at.isoformat(),
        "expires_at": assessment.expires_at.isoformat(),
        "allowed_languages": allowed_languages,
        # Where the questions really came from. Only ai_generated / validated_pool / role_aware_fallback are
        # personalized; a generic fallback (and any legacy row) is never presented as personalized.
        "source": source,
        "personalized": source in ("ai_generated", "validated_pool", "role_aware_fallback"),
        "recommended_language": selection.get("selected") or (allowed_languages[0] if allowed_languages else None),
        "questions": questions,
        "score": assessment.score,
        "total_score": assessment.total_score,
        "passed": assessment.passed,
        # Gap found during Task 5D's real browser test: the page's
        # own header always showed the "Job Title" placeholder
        # because this response never carried job info at all.
        # Optional (job=None keeps this backward compatible with
        # any earlier caller) — both live callers (start/get) now
        # pass it.
        "job": (
            {"title": job.title, "company": job.company}
            if job is not None else None
        ),
        # Autosaved (not-yet-run/submitted) code per question, so the
        # candidate's own typed-but-unsaved work survives a refresh
        # or reconnect instead of starting blank while the exam
        # clock keeps running.
        "draft_code": (
            json.loads(assessment.draft_code_json)
            if assessment.draft_code_json
            else {}
        ),
    }


# ============================================================
# PIPELINE GATE: no programming assignment unless the blueprint can produce a valid assessment
#
#   Shortlist -> blueprint -> capability/content validation -> valid assessment creation -> candidate assignment
# ============================================================

def programming_assessment_feasible(db: Session, application: JobApplication) -> tuple[bool, str | None]:
    """
    Builds the candidate's blueprint and plan and checks that a programming assessment can really be produced for
    this job: the plan must call for a programming/SQL/web assessment, and every language it names must be one JobPilot
    can execute. Returns (True, None) or (False, reason). Never raises.
    """

    from backend.app.models.candidate_profile import CandidateProfile
    from backend.app.models.job import Job
    from backend.app.models.resume_analysis import ResumeAnalysis
    from backend.app.models.user import User
    from backend.app.services.assessment.programming_allocation import allocate_questions
    from backend.app.services.assessment.programming_generator_v2 import _classify_programming_items
    from backend.app.services.resume.active_resume import get_active_resume

    try:

        job = db.query(Job).filter(Job.id == application.job_id).first()
        user = db.query(User).filter(User.id == application.user_id).first()

        if job is None or user is None:
            return False, "the job or the candidate no longer exists"

        from backend.app.services.assessment.role_classification import job_role_shape

        if job_role_shape(job) == "non_technical":
            return False, "this role is non-technical — no programming assessment is needed"

        profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == application.user_id).first()
        resume = get_active_resume(db, application.user_id)
        analysis = (
            db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).first() if resume else None
        )

        blueprint = build_assessment_blueprint(
            user=user, profile=profile, resume_analysis=analysis, job=job,
            total_questions=REQUIRED_QUESTION_COUNT, assessment_type="programming",
        )
        plan = build_assessment_plan(job, blueprint, use_gemini=False)

        allocation, explanation = allocate_questions(blueprint, plan, job)

        if not allocation:
            return False, "the assessment plan for this job has no programming, SQL or web assessment"

        _items, unsupported = _classify_programming_items(plan)

        if "coding" in allocation and unsupported:
            return False, f"the job requires {unsupported[0]}, which JobPilot cannot execute yet"

        return True, None

    except Exception as exc:                                           # a gate that cannot decide must not open

        logger.warning("Programming feasibility check failed for application %s: %s", application.id, type(exc).__name__)
        return False, "the programming assessment could not be prepared"


# ============================================================
# RUN CODE (public examples only, never scored, never submits)
# ============================================================

def run_code_v2(
    db: Session,
    assessment: ProgrammingAssessmentV2,
    question_id: int,
    language: str,
    source_code: str,
):

    question_set = _load_question_set(assessment)

    result = run_public_examples(
        question_set, CandidateSubmission(question_id=question_id, language=language, source_code=source_code),
    )

    if isinstance(result, LanguageRejectedResult):

        raise ProgrammingIntegrationV2Error(400, result.reason)

    submission_row = ProgrammingSubmissionV2(
        assessment_id=assessment.id,
        question_id=question_id,
        submission_kind="run",
        language=language,
        source_code=source_code,
        result_json=None,
        earned_points=None,
        submitted_at=datetime.utcnow(),
    )
    db.add(submission_row)
    db.commit()

    return [
        {
            "test_case_id": r.test_case_id,
            "stdin": r.stdin,
            "expected_stdout": r.expected_stdout,
            "actual_stdout": r.execution.stdout,
            "stderr": r.execution.stderr,
            "success": r.execution.success,
            "error_type": r.execution.error_type,
            "execution_time_ms": r.execution.execution_time_ms,
        }
        for r in result
    ]


# ============================================================
# FINAL SUBMISSION (all 5 questions, hidden tests, scoring,
# persistence, progression)
# ============================================================

def submit_assessment_v2(
    db: Session,
    assessment: ProgrammingAssessmentV2,
    application: JobApplication,
    answers: list[dict],
) -> dict:
    """
    answers: [{"question_id": int, "language": str, "source_code": str}, ...]
    """

    question_set = _load_question_set(assessment)

    submissions = [
        CandidateSubmission(
            question_id=a["question_id"], language=a["language"], source_code=a["source_code"],
        )
        for a in answers
    ]

    question_results = []

    for submission in submissions:

        result = submit_question(question_set, submission)

        if isinstance(result, LanguageRejectedResult):

            raise ProgrammingIntegrationV2Error(
                400, f"Question {submission.question_id}: {result.reason}",
            )

        question_results.append(result)

        db.add(ProgrammingSubmissionV2(
            assessment_id=assessment.id,
            question_id=submission.question_id,
            submission_kind="submit",
            language=submission.language,
            source_code=submission.source_code,
            # Full MainQuestionScoreResult, dataclasses.asdict()'d —
            # candidate-safe by construction (see programming_
            # scoring_v2.py's own module header: none of these
            # dataclasses ever carry a hidden test's stdin/expected_
            # stdout), so persisting the WHOLE result here is safe.
            # This is what lets the HR review (Task 5E) show the
            # per-subpart/per-test-case breakdown (hidden test
            # PASS/FAIL counts, compile/runtime error classification,
            # execution time) without re-running the candidate's code
            # or inventing a second scoring path.
            result_json=json.dumps(dataclasses.asdict(result)),
            earned_points=result.earned_points,
            submitted_at=datetime.utcnow(),
        ))

    score_result = score_assessment(question_set, question_results)

    percentage = (
        round(100.0 * score_result.total_earned / score_result.total_possible, 2)
        if score_result.total_possible else 0.0
    )
    passed = percentage >= PROGRAMMING_V2_PASS_THRESHOLD

    now = datetime.utcnow()

    to_status = "programming_passed" if passed else "programming_failed"

    # ----------------------------------------------------
    # ATOMIC COMPLETION GUARD
    #
    # The route's own "already completed" check only catches a
    # clean sequential re-submit. Two near-simultaneous requests
    # (a double-click, or an auto-submit-on-timeout racing a
    # manual click) can both pass that check before either
    # commits. This conditional UPDATE -- WHERE status ==
    # "in_progress" -- makes the in_progress -> completed
    # transition itself the single source of truth: only one of
    # the two requests can ever match the row and flip it, so only
    # one ever proceeds to record_status_change()/advance_pipeline()
    # below. Mirrors the identical fix already applied to the
    # aptitude stage's submit_aptitude_test().
    # ----------------------------------------------------

    updated_rows = (
        db.query(ProgrammingAssessmentV2)
        .filter(
            ProgrammingAssessmentV2.id == assessment.id,
            ProgrammingAssessmentV2.status == "in_progress",
        )
        .update(
            {
                "score": score_result.total_earned,
                "total_score": score_result.total_possible,
                "passed": passed,
                "status": "completed",
                "submitted_at": now,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )

    if updated_rows == 0:

        db.rollback()

        raise ProgrammingIntegrationV2Error(
            409, "This programming assessment has already been submitted.",
        )

    record_status_change(
        db, application, to_status=to_status,
        changed_by_type="system", changed_by_id=None,
        note=f"Programming assessment (V2) score: {score_result.total_earned}/{score_result.total_possible} ({percentage}%)",
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(application)

    _advance_after_programming_v2_result(db, application, passed=passed)

    hr_report = build_hr_report_v2(question_set, score_result)

    return {
        "status": assessment.status,
        "score": assessment.score,
        "total_score": assessment.total_score,
        "percentage": percentage,
        "passed": passed,
        "report": hr_report,
    }


# ------------------------------------------------------------
# AUTOMATIC PROGRESSION (Task 6A: single authoritative service)
# ------------------------------------------------------------

def _advance_after_programming_v2_result(db: Session, application: JobApplication, passed: bool) -> None:
    """Task 6A: delegates to the one authoritative progression service."""

    # pass or fail: the candidate moves on (HR decides from the results)
    from backend.app.services.assessment.stage_progression_service import advance_pipeline

    advance_pipeline(db, application)

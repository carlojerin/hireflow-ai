import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job_application import JobApplication
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.services.application_status_history import record_status_change
from backend.app.services.assessment.assignment_service import is_assigned
from backend.app.services.sjt.scoring import score_sjt
from backend.app.services.sjt.sjt_question_service import build_sjt_questions


# ============================================================
# JOBPILOT AI - SITUATIONAL JUDGMENT TEST (non-technical pipeline
# stage) INTEGRATION
#
# The non-technical candidate's equivalent of
# programming_integration_v2.py — occupies the EXACT same pipeline
# slot (stage_progression_service.py's "programming" stage; see
# role_classification.py for which candidates get which). Mirrors
# that file's start/expire/candidate_view/submit shape closely on
# purpose, including the atomic-completion-guard fix already proven
# for both the aptitude and programming submit routes (a double-
# click or auto-submit-on-timeout racing a manual click must never
# both succeed).
# ============================================================

SJT_START_STATUS = "aptitude_passed"
SJT_DEADLINE_MINUTES = 20
PRACTICAL_DEADLINE_MINUTES = 25
PRACTICAL_PASS_PERCENT = 60
MAX_WRITTEN_CHARS = 6000
MAX_DESIGN_CHARS = 90000


class SJTIntegrationError(Exception):

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _get_passed_aptitude_test(db: Session, application: JobApplication) -> AptitudeTest | None:

    test = db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).first()

    if test and test.status == "completed":   # finished: passing is not required to continue
        return test

    return None


def start_sjt_assessment(
    db: Session,
    session_record,
    application: JobApplication,
) -> tuple[SituationalJudgmentTest, bool]:
    """
    Returns (assessment, created). Idempotent: an existing row for
    this application is returned unchanged (created=False) — a
    stable snapshot for the candidate's whole attempt, same
    guarantee programming's own start function makes.
    """

    current_stage = session_record.current_stage

    if current_stage not in ("programming", "ai_interview", "completed") and current_stage != "aptitude":

        raise SJTIntegrationError(
            400,
            "Complete the self-introduction and aptitude stages "
            "before starting this assessment.",
        )

    existing = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if existing:
        return existing, False

    if not _get_passed_aptitude_test(db, application):

        raise SJTIntegrationError(
            400, "You must finish the aptitude assessment before starting this assessment.",
        )

    if not is_assigned(db, application.id, "situational_judgment"):

        raise SJTIntegrationError(400, "This assessment has not been assigned yet.")

    if application.status not in (SJT_START_STATUS, "aptitude_failed"):

        raise SJTIntegrationError(
            400,
            f"This application is not eligible for this assessment. "
            f"Current status: {application.status}.",
        )

    from backend.app.models.job import Job
    job = db.query(Job).filter(Job.id == application.job_id).first()

    # a practical (work-sample) test when the job's plan says so; any failure to build one falls back to the situational questions
    practical = None
    try:
        from backend.app.services.assessment.assessment_planner import practical_spec
        from backend.app.services.sjt.sjt_question_service import build_practical_test

        spec = practical_spec(job)
        practical = build_practical_test(job, spec, seed=f"{application.id}:{application.user_id}") if spec else None
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("jobpilot.sjt").warning("Practical test could not be built for application %s: using the situational questions.", application.id, exc_info=True)

    if practical:
        questions, source = practical["questions"], practical["source"]
    else:
        questions, source = build_sjt_questions(job=job)

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=(practical["payload"].get("minutes") or PRACTICAL_DEADLINE_MINUTES) if practical else SJT_DEADLINE_MINUTES)

    assessment = SituationalJudgmentTest(
        application_id=application.id,
        questions=json.dumps(questions),
        answers=None,
        kind="practical" if practical else "mcq",
        payload_json=json.dumps(practical["payload"]) if practical else None,
        total_questions=100 if practical else len(questions),          # practical: the score is a percentage out of 100
        passed=None,
        status="in_progress",
        question_source=source,
        assigned_at=now,
        expires_at=expires_at,
        exam_expires_at=expires_at,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(assessment)
    db.flush()

    if practical and practical.get("roleplay_scenario"):
        from backend.app.services.sjt.practical_roleplay import save_scenario
        save_scenario(db, application.id, practical["roleplay_scenario"])

    # Same generic stage-3 slot name programming_integration_v2.py sets for its
    # own start -- every assessment occupying this pipeline slot (programming/
    # situational_judgment/technical_domain) uses "programming" here, which is
    # exactly what interview_integration_v2.py's start gate checks for. Leaving
    # this unset (as it was before this fix) silently blocked every SJT
    # candidate from ever starting the AI interview afterwards.
    session_record.current_stage = "programming"
    session_record.updated_at = now

    record_status_change(
        db, application, to_status="sjt_pending",
        changed_by_type="system", changed_by_id=None,
        note="Situational Judgment Test generated automatically after passing aptitude.",
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(application)

    return assessment, True


def expire_if_due(db: Session, assessment: SituationalJudgmentTest, application: JobApplication) -> bool:

    if assessment.status == "expired":
        return True

    if assessment.status == "in_progress" and datetime.utcnow() > assessment.exam_expires_at:

        assessment.status = "expired"
        assessment.updated_at = datetime.utcnow()

        record_status_change(
            db, application, to_status="sjt_expired",
            changed_by_type="system", changed_by_id=None,
            note="Situational Judgment Test deadline passed without a final submission.",
        )

        application.updated_date = datetime.utcnow()

        db.commit()
        db.refresh(assessment)
        db.refresh(application)

        return True

    return False


def _sanitize_questions(questions: list[dict]) -> list[dict]:
    """Strips correct_answer before any response reaches the candidate."""

    safe = []

    for q in questions:

        item = {"id": q["id"], "type": q.get("type", "mcq"), "category": q.get("category"), "question": q["question"], "options": q.get("options") or []}

        if item["type"] == "written":
            item.update({"scenario": q.get("scenario"), "instructions": q.get("instructions"), "min_words": q.get("min_words"), "max_words": q.get("max_words")})
        elif item["type"] in ("data", "lab"):
            item.update({"answer_kind": q.get("answer_kind"), "unit": q.get("unit"), "hint": q.get("hint")})

        safe.append(item)

    return safe


def candidate_view(assessment: SituationalJudgmentTest, job=None) -> dict:

    questions = json.loads(assessment.questions)

    remaining_seconds = None

    if assessment.exam_expires_at and assessment.status == "in_progress":
        remaining_seconds = max(0, int((assessment.exam_expires_at - datetime.utcnow()).total_seconds()))

    return {
        "status": assessment.status,
        "assigned_at": assessment.assigned_at.isoformat() if assessment.assigned_at else None,
        "expires_at": assessment.expires_at.isoformat() if assessment.expires_at else None,
        "exam_remaining_seconds": remaining_seconds,
        "questions": _sanitize_questions(questions) if assessment.status == "in_progress" else [],
        "total_questions": assessment.total_questions,
        "score": assessment.score,
        "passed": assessment.passed,
        "draft_answers": json.loads(assessment.draft_answers) if assessment.draft_answers else [],
        "job": ({"title": job.title, "company": job.company} if job is not None else None),
        "kind": getattr(assessment, "kind", None) or "mcq",
        "practical": json.loads(assessment.payload_json) if getattr(assessment, "payload_json", None) and assessment.status == "in_progress" else None,
        "percentage": assessment.score if getattr(assessment, "kind", "mcq") == "practical" else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
    }


def submit_sjt_assessment(
    db: Session,
    assessment: SituationalJudgmentTest,
    application: JobApplication,
    answers: list[dict],
) -> dict:

    questions = json.loads(assessment.questions)

    practical = (getattr(assessment, "kind", None) or "mcq") == "practical"

    if practical:
        result = score_practical(questions, answers, db=db, application_id=application.id)
    else:
        result = score_sjt(questions, answers)

    now = datetime.utcnow()

    to_status = "sjt_passed" if result["passed"] else "sjt_failed"

    # ATOMIC COMPLETION GUARD — same fix already applied to aptitude
    # and programming's own submit routes: makes the in_progress ->
    # completed transition itself the single source of truth, so a
    # double-click/auto-submit-on-timeout race can never both score.
    updated_rows = (
        db.query(SituationalJudgmentTest)
        .filter(
            SituationalJudgmentTest.id == assessment.id,
            SituationalJudgmentTest.status == "in_progress",
        )
        .update(
            {
                "answers": json.dumps(answers),
                "result_json": json.dumps(result["details"]) if practical else None,
                "score": result["percentage"] if practical else result["correct_count"],
                "passed": result["passed"],
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
            },
            synchronize_session=False,
        )
    )

    if updated_rows == 0:

        db.rollback()

        raise SJTIntegrationError(409, "This assessment has already been submitted.")

    record_status_change(
        db, application, to_status=to_status,
        changed_by_type="system", changed_by_id=None,
        note=(f"Practical assessment score: {result['percentage']}%" if practical
              else f"Situational Judgment Test score: {result['correct_count']}/{result['total_questions']} ({result['percentage']}%)"),
    )

    application.updated_date = now

    db.commit()
    db.refresh(assessment)
    db.refresh(application)

    # pass or fail: the candidate moves on (HR decides from the results)
    from backend.app.services.assessment.stage_progression_service import advance_pipeline
    advance_pipeline(db, application)

    return {
        "status": assessment.status,
        "score": assessment.score,
        "total_questions": assessment.total_questions,
        "percentage": result["percentage"],
        "passed": result["passed"],
        "kind": "practical" if practical else "mcq",
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
    }


def score_practical(questions: list[dict], answers: list[dict], db=None, application_id=None) -> dict:
    """
    Scores a practical test. data tasks: every answer is checked against the code-computed key. written tasks: AI-assisted rubric grading
    (see practical_written_tasks.grade_written; the candidate's text and the AI's notes are both kept for HR).
    Returns {"percentage", "passed", "correct_count", "total_questions", "details"} where details is what result_json stores.
    """

    from backend.app.services.sjt.practical_data_templates import check_answer
    from backend.app.services.sjt.practical_written_tasks import grade_written

    limit = MAX_DESIGN_CHARS if questions and questions[0].get("type") == "design" else MAX_WRITTEN_CHARS
    given = {a.get("question_id"): (a.get("selected_answer") or "")[:limit] for a in answers if isinstance(a, dict)}

    details = {"items": []}

    if questions and questions[0].get("type") == "design":

        from backend.app.services.sjt.practical_design import PASS_PERCENT as DESIGN_PASS, score_design

        graded = score_design(given.get(questions[0]["id"]), questions[0]["brief"])
        item = {"id": questions[0]["id"], "type": "design", **{k: graded[k] for k in ("percent", "source", "needs_review", "summary", "criteria", "metrics", "layout", "problems")}}

        return {"percentage": graded["percent"], "passed": graded["percent"] >= DESIGN_PASS, "correct_count": 1 if graded["percent"] >= DESIGN_PASS else 0,
                "total_questions": 1, "details": {"items": [item], "needs_review": graded["needs_review"]}}

    if questions and questions[0].get("type") == "roleplay":

        from backend.app.services.sjt.practical_roleplay import score_roleplay

        scored = score_roleplay(db, application_id)

        return {"percentage": scored["percentage"], "passed": scored["passed"], "correct_count": 1 if scored["passed"] else 0,
                "total_questions": 1, "details": scored["details"]}

    if questions and questions[0].get("type") == "written":

        graded = grade_written(questions, given)

        percents = []
        for q in questions:
            g = graded[q["id"]]
            percents.append(g["percent"])
            details["items"].append({"id": q["id"], "type": "written", "percent": g["percent"], "source": g["source"], "needs_review": g["needs_review"],
                                     "summary": g["summary"], "criteria": g["criteria"]})

        percentage = round(sum(percents) / len(percents)) if percents else 0
        details["needs_review"] = any(i["needs_review"] for i in details["items"])
        correct = sum(1 for p in percents if p >= PRACTICAL_PASS_PERCENT)

    else:

        correct = 0
        for q in questions:
            ok = check_answer(q, given.get(q["id"]))
            correct += 1 if ok else 0
            details["items"].append({"id": q["id"], "type": q.get("type", "data"), "correct": ok})

        percentage = round(100 * correct / len(questions)) if questions else 0
        details["needs_review"] = False

    return {
        "percentage": percentage, "passed": percentage >= PRACTICAL_PASS_PERCENT, "correct_count": correct,
        "total_questions": len(questions), "details": details,
    }

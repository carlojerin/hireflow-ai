import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.job_application import JobApplication
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_submission import ProgrammingSubmission
from backend.app.models.interview_test import InterviewTest
from backend.app.models.interview_response import InterviewResponse
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.assessment_consent import AssessmentConsent
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2

from backend.app.api.deps import get_current_company, require_company_self
from backend.app.api.routes.company_applications import (
    get_company_application,
    get_verified_company,
    ai_result_response,
    aptitude_summary_response,
    programming_summary_response,
    interview_summary_response,
    assessment_summary_response,
    profile_response,
)
from backend.app.api.routes.programming_tests import (
    sanitize_programming_question,
)
from backend.app.services.assessment.integrity_service import (
    VALID_ASSESSMENT_TYPES,
    get_integrity_state,
)
from backend.app.services.ai_candidate_matching import parse_json_list
from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2
from backend.app.services.assessment.programming_hr_report_v2 import build_hr_report_v2
from backend.app.services.assessment.programming_assessment_evidence_v2 import (
    build_programming_evidence_v2,
)
from backend.app.services.assessment.programming_scoring_v2 import (
    MainQuestionScoreResult,
    SubpartScoreResult,
    TestCaseScoreResult,
    score_assessment,
)


# ============================================================
# JOBPILOT AI - HR ASSESSMENT REVIEW (Task 4D-STRICT-2)
#
# Dedicated HR/company-authorized endpoints for the detailed,
# per-question review the candidate-facing endpoints must never
# expose (correct aptitude answers, an interview's AI evaluation,
# an aggregated integrity summary). Deliberately separate from
# company_applications.py's own application_summary_response()
# (used for the applicant list and the lightweight detail view)
# so that heavy per-question payloads are only ever fetched when
# HR explicitly opens a review panel, not on every list render.
#
# Every endpoint reuses the exact same ownership/authorization
# pattern already established in company_applications.py:
# get_current_company -> require_company_self -> get_verified_
# company -> get_company_application (Job.company_id ==
# company_id, generic 404 otherwise). No new authentication or
# authorization mechanism is introduced.
#
# Nothing here creates, scores, or re-evaluates anything — every
# endpoint is read-only.
# ============================================================

from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.services.assessment.stage_progression_service import determine_next_stage

router = APIRouter(
    prefix="/api/company-applications",
    tags=["HR Assessment Review"],
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _duration_seconds(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> int | None:

    if not started_at or not ended_at:
        return None

    return max(0, int((ended_at - started_at).total_seconds()))


def _require_company(
    company_id: int,
    current_company: Company,
    db: Session,
) -> None:

    require_company_self(current_company, company_id)
    get_verified_company(company_id, db)


# ============================================================
# SELF INTRODUCTION REVIEW
#
# _self_intro_review_data() is the ONE place this shape is built
# — reused by both the standalone review endpoint below AND the
# consolidated Candidate Intelligence Report (Task 4F), so
# neither can silently drift from the other.
# ============================================================

def _self_intro_review_data(
    application: JobApplication,
    session_record: AssessmentSession | None,
) -> dict:

    if not session_record:

        return {
            "application_id": application.id,
            "exists": False,
            "status": "not_started",
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "text": None,
        }

    return {
        "application_id": application.id,
        "exists": True,
        "status": session_record.self_intro_status,
        "started_at": _iso(session_record.self_intro_started_at),
        "completed_at": _iso(session_record.self_intro_completed_at),
        "duration_seconds": session_record.self_intro_duration_seconds,
        "text": session_record.self_intro_text,
    }


@router.get("/{company_id}/{application_id}/review/self-intro")
def review_self_introduction(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    session_record = (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application.id)
        .first()
    )

    return _self_intro_review_data(application, session_record)


# ============================================================
# APTITUDE REVIEW — the ONE place correct answers are ever
# returned in an HTTP response. Never reachable by a candidate
# session (get_current_company only accepts a company session).
# ============================================================

@router.get("/{company_id}/{application_id}/review/aptitude")
def review_aptitude(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
            "exists": False,
            "questions": [],
        }

    stored_questions = json.loads(test.questions)

    submitted_answers = (
        json.loads(test.answers) if test.answers else []
    )

    selected_by_question_id = {
        answer["question_id"]: answer.get("selected_answer")
        for answer in submitted_answers
    }

    questions_review = []

    for question in stored_questions:

        correct_answer = question.get("correct_answer")
        selected_answer = selected_by_question_id.get(question["id"])

        questions_review.append(
            {
                "question_id": question["id"],
                "category": question.get("category"),
                "question": question["question"],
                "options": question["options"],
                "correct_answer": correct_answer,
                "selected_answer": selected_answer,
                "is_correct": (
                    selected_answer is not None
                    and selected_answer == correct_answer
                ),
            }
        )

    correct_count = sum(1 for q in questions_review if q["is_correct"])
    incorrect_count = sum(
        1 for q in questions_review if q["selected_answer"] is not None and not q["is_correct"]
    )

    percentage = None

    if test.score is not None and test.total_questions:

        percentage = round((test.score / test.total_questions) * 100, 2)

    return {
        "application_id": application.id,
        "exists": True,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "score": test.score,
        "percentage": percentage,
        "passed": test.passed,
        "started_at": _iso(test.started_at),
        "completed_at": _iso(test.completed_at),
        "duration_seconds": _duration_seconds(test.started_at, test.completed_at),
        "questions": questions_review,
    }


# ============================================================
# SITUATIONAL JUDGMENT TEST / TECHNICAL DOMAIN ASSESSMENT REVIEW
# — same MCQ shape as aptitude (stored question+correct_answer,
# submitted answers JSON), so this mirrors review_aptitude()
# exactly rather than introducing a new pattern.
# ============================================================

def _mcq_review_data(test, extra_fields: dict | None = None) -> dict:

    if not test:
        return {"exists": False, "questions": []}

    stored_questions = json.loads(test.questions)

    submitted_answers = (
        json.loads(test.answers) if test.answers else []
    )

    selected_by_question_id = {
        answer["question_id"]: answer.get("selected_answer")
        for answer in submitted_answers
    }

    questions_review = []

    for question in stored_questions:

        correct_answer = question.get("correct_answer")
        selected_answer = selected_by_question_id.get(question["id"])

        questions_review.append(
            {
                "question_id": question["id"],
                "category": question.get("category"),
                "question": question["question"],
                "options": question["options"],
                "correct_answer": correct_answer,
                "selected_answer": selected_answer,
                "is_correct": (
                    selected_answer is not None
                    and selected_answer == correct_answer
                ),
            }
        )

    correct_count = sum(1 for q in questions_review if q["is_correct"])
    incorrect_count = sum(
        1 for q in questions_review if q["selected_answer"] is not None and not q["is_correct"]
    )

    percentage = None

    if test.score is not None and test.total_questions:
        percentage = round((test.score / test.total_questions) * 100, 2)

    result = {
        "exists": True,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "score": test.score,
        "percentage": percentage,
        "passed": test.passed,
        "started_at": _iso(test.started_at),
        "completed_at": _iso(test.completed_at),
        "duration_seconds": _duration_seconds(test.started_at, test.completed_at),
        "questions": questions_review,
    }

    if extra_fields:
        result.update(extra_fields)

    return result


def _sjt_review_data(db: Session, application: JobApplication) -> dict:

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest

    test = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if test is not None and (getattr(test, "kind", None) or "mcq") == "practical":
        return _practical_review_data(test, db)

    return _mcq_review_data(test)


def _practical_verdict(review: dict | None) -> dict | None:
    """
    The practical (work-sample) stage as the intelligent report states it: the AI's marks, a short verdict, and what was strong and weak, so
    HR can read the verdict and open the candidate's actual work only if they want to.
    """

    if not review or not review.get("exists") or review.get("kind") != "practical" or review.get("status") not in ("completed", "expired"):
        return None                                            # nothing to judge until the candidate has finished (or run out of time)

    items = review.get("practical_items") or []
    marks, strengths, gaps, summaries = [], [], [], []
    source = "auto"

    for item in items:

        if item.get("type") in ("written", "roleplay", "design"):

            result = item.get("result") or {}
            source = result.get("source") or source

            for criterion in result.get("criteria") or []:

                if criterion.get("score") is None:
                    continue

                marks.append({"label": criterion["criterion"], "score": criterion["score"], "max": criterion.get("max", 10), "task": item.get("question")})

                if criterion["score"] >= 8:
                    strengths.append(criterion["criterion"])
                elif criterion["score"] <= 4:
                    gaps.append(criterion["criterion"])

            if result.get("summary"):
                summaries.append(result["summary"])

            for problem in result.get("problems") or []:
                gaps.append("Design: " + problem)

        else:

            if item.get("is_correct"):
                continue

            gaps.append("Wrong answer: " + str(item.get("question") or "")[:110])

    if items and items[0].get("type") in ("data", "lab"):

        correct, total = review.get("correct_count") or 0, len(items)
        what = "lab" if items[0].get("type") == "lab" else "data"
        summaries.append(f"Answered {correct} of {total} {what} questions correctly (checked automatically against the answer key).")

        if correct == total:
            strengths.append(f"Answered every {what} question correctly")
        elif correct >= (total + 1) // 2:
            strengths.append(f"Answered {correct} of {total} {what} questions correctly")

    return {
        "title": review.get("title"), "environment": review.get("environment"),
        "percentage": review.get("percentage"), "passed": review.get("passed"),
        "marked_by": source,                                   # ai | rules | auto
        "needs_review": bool(review.get("needs_review")),
        "verdict": " ".join(dict.fromkeys(summaries)).strip(),
        "marks": marks, "strengths": strengths[:6], "gaps": gaps[:6],
    }


def _practical_review_data(test, db=None) -> dict:
    """A practical (work-sample) test for HR: the candidate's own answers next to the answer key (data) or the AI's rubric notes (written)."""

    questions = json.loads(test.questions)
    answers = json.loads(test.answers) if test.answers else []
    given = {a["question_id"]: a.get("selected_answer") for a in answers if isinstance(a, dict) and "question_id" in a}
    result = json.loads(test.result_json) if test.result_json else {"items": []}
    by_id = {item["id"]: item for item in result.get("items", [])}
    payload = json.loads(test.payload_json) if test.payload_json else {}

    items = []

    for q in questions:

        entry = {
            "question_id": q["id"], "type": q.get("type"), "category": q.get("category"), "question": q["question"],
            "answer": given.get(q["id"]), "result": by_id.get(q["id"]),
        }

        if q.get("type") == "written":
            entry.update({"scenario": q.get("scenario"), "instructions": q.get("instructions")})
        elif q.get("type") == "design":
            from backend.app.services.sjt.practical_design import sanitize_layout

            entry.update({"scenario": (q.get("brief") or {}).get("brief"), "layout": sanitize_layout(given.get(q["id"])),
                          "brand_color": ((q.get("brief") or {}).get("brand") or {}).get("primary")})
        elif q.get("type") == "roleplay":
            from backend.app.services.sjt.practical_roleplay import load_row

            row = load_row(db, test.application_id) if db is not None else None
            entry.update({
                "scenario": (json.loads(row.scenario_json).get("candidate_brief") if row else None),
                "transcript": (json.loads(row.transcript_json) if row and row.transcript_json else []),
            })
        else:
            entry.update({"correct_answer": q.get("correct_answer"), "unit": q.get("unit"),
                          "is_correct": bool((by_id.get(q["id"]) or {}).get("correct"))})

        items.append(entry)

    return {
        "exists": True, "kind": "practical", "environment": payload.get("environment"), "title": payload.get("title"),
        "status": test.status, "question_source": test.question_source, "total_questions": len(questions),
        "score": test.score, "percentage": test.score if test.score is not None else None, "passed": test.passed,
        "needs_review": bool(result.get("needs_review")), "dataset": payload.get("dataset"),
        "started_at": _iso(test.started_at), "completed_at": _iso(test.completed_at),
        "duration_seconds": _duration_seconds(test.started_at, test.completed_at),
        "correct_count": sum(1 for i in items if i.get("is_correct")), "incorrect_count": sum(1 for i in items if i.get("type") in ("data", "lab") and not i.get("is_correct")),
        "questions": [],
        "practical_items": items,
    }


def _tda_review_data(db: Session, application: JobApplication) -> dict:

    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    test = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    return _mcq_review_data(test, extra_fields={"domain": test.domain} if test else None)


@router.get("/{company_id}/{application_id}/review/sjt")
def review_sjt(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    return {"application_id": application.id, **_sjt_review_data(db, application)}


@router.get("/{company_id}/{application_id}/review/technical-domain")
def review_technical_domain(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    return {"application_id": application.id, **_tda_review_data(db, application)}


# ============================================================
# PROGRAMMING REVIEW — submitted code/result only. Hidden tests,
# evaluation_notes, and any reference-solution content are
# stripped by the SAME sanitize_programming_question() the
# candidate-facing endpoints already use — reused, not
# duplicated, so this can never accidentally drift into leaking
# more than the candidate's own view once that function is
# updated.
# ============================================================

@router.get("/{company_id}/{application_id}/review/programming")
def review_programming(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
            "exists": False,
            "questions": [],
        }

    stored_questions = json.loads(test.questions)

    final_submissions = (
        db.query(ProgrammingSubmission)
        .filter(
            ProgrammingSubmission.programming_test_id == test.id,
            ProgrammingSubmission.submission_kind == "final",
        )
        .all()
    )

    submission_by_question_id = {
        submission.question_id: submission
        for submission in final_submissions
    }

    questions_review = []

    for question in stored_questions:

        sanitized = sanitize_programming_question(question)
        submission = submission_by_question_id.get(question["question_id"])

        sanitized["submission"] = (
            {
                "language": submission.language,
                "source_code": submission.source_code,
                "execution_status": submission.execution_status,
                "score": submission.score,
                "execution_time_ms": submission.execution_time_ms,
                "submitted_at": _iso(submission.submitted_at),
            }
            if submission
            else None
        )

        questions_review.append(sanitized)

    percentage = None

    if test.score is not None and test.total_score:

        percentage = round((test.score / test.total_score) * 100, 2)

    return {
        "application_id": application.id,
        "exists": True,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "score": test.score,
        "total_score": test.total_score,
        "percentage": percentage,
        "passed": test.passed,
        "started_at": _iso(test.started_at),
        "submitted_at": _iso(test.submitted_at),
        "duration_seconds": _duration_seconds(test.started_at, test.submitted_at),
        "questions": questions_review,
    }


# ============================================================
# PROGRAMMING V2 (MULTI-LANGUAGE) REVIEW (Task 5E)
#
# Same "one place this shape is built" pattern as every other
# review section in this file, and reuses the EXISTING Programming
# V2 report architecture end to end — never a second scoring/
# reporting system:
#
#   ProgrammingSubmissionV2.result_json (a persisted, candidate-
#   safe MainQuestionScoreResult — see programming_integration_v2.
#   py's submit_assessment_v2()) is deserialized back into the
#   SAME programming_scoring_v2 dataclasses, re-aggregated with the
#   SAME score_assessment() used at submission time, and handed to
#   the SAME build_hr_report_v2() the candidate's own submit
#   response already used. Nothing here re-executes candidate code,
#   re-scores anything, or invents a new redaction step — the
#   candidate-safety guarantee is inherited unchanged from
#   programming_scoring_v2.py's dataclasses (no hidden stdin/
#   expected_stdout ever reaches any of them).
#
#   assessment.score/total_score/passed/status — the authoritative,
#   already-persisted outcome — are always read directly off the
#   ProgrammingAssessmentV2 row, never recomputed here; the
#   reconstructed score_result exists only to recover the
#   per-question/per-subpart/per-test-case DISPLAY detail (hidden
#   vs public pass counts, compile/runtime/timeout classification,
#   execution time) that a flat score alone cannot show HR.
# ============================================================

def _reconstruct_question_result(
    question_id: int,
    max_points: float,
    data: dict,
) -> MainQuestionScoreResult:

    subpart_results = []

    for subpart_data in (data.get("subpart_results") or []):

        test_case_results = [
            TestCaseScoreResult(
                test_case_id=tc["test_case_id"],
                is_hidden=tc["is_hidden"],
                passed=tc["passed"],
                weight=tc["weight"],
                execution_time_ms=tc["execution_time_ms"],
                error_type=tc["error_type"],
            )
            for tc in (subpart_data.get("test_case_results") or [])
        ]

        subpart_results.append(SubpartScoreResult(
            subpart_id=subpart_data["subpart_id"],
            earned_fraction=subpart_data["earned_fraction"],
            test_case_results=test_case_results,
            platform_failure=subpart_data["platform_failure"],
        ))

    return MainQuestionScoreResult(
        question_id=question_id,
        earned_points=data.get("earned_points") or 0.0,
        max_points=data.get("max_points") or max_points,
        language_used=data.get("language_used") or "",
        subpart_results=subpart_results,
        platform_failure=bool(data.get("platform_failure")),
    )


def _programming_v2_review_data(
    db: Session,
    application: JobApplication,
) -> dict:

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment:

        return {
            "application_id": application.id,
            "exists": False,
            "questions": [],
        }

    question_set = ProgrammingQuestionSetV2.model_validate_json(assessment.question_set_json)

    submission_rows = (
        db.query(ProgrammingSubmissionV2)
        .filter(
            ProgrammingSubmissionV2.assessment_id == assessment.id,
            ProgrammingSubmissionV2.submission_kind == "submit",
        )
        .all()
    )

    submission_by_question_id = {
        row.question_id: row for row in submission_rows
    }

    question_results = []

    for question in question_set.main_questions:

        row = submission_by_question_id.get(question.question_id)

        if row and row.result_json:

            question_results.append(
                _reconstruct_question_result(
                    question.question_id, question.points, json.loads(row.result_json),
                )
            )

        else:

            # Never submitted (candidate ran out of time / left the
            # question blank) — a real, zero-score result, not an
            # error.
            question_results.append(MainQuestionScoreResult(
                question_id=question.question_id,
                earned_points=0.0,
                max_points=question.points,
                language_used="",
                subpart_results=[],
                platform_failure=False,
            ))

    score_result = score_assessment(question_set, question_results)
    hr_report = build_hr_report_v2(question_set, score_result)
    evidence = build_programming_evidence_v2(question_set, score_result)

    for question_report in hr_report["questions"]:

        row = submission_by_question_id.get(question_report["question_id"])

        question_report["source_code"] = row.source_code if row else None
        question_report["submitted_at"] = _iso(row.submitted_at) if row else None
        question_report["answered"] = row is not None

    percentage = (
        round(100.0 * assessment.score / assessment.total_score, 2)
        if assessment.score is not None and assessment.total_score
        else None
    )

    return {
        "application_id": application.id,
        "exists": True,
        "status": assessment.status,
        "generated_by": assessment.generated_by,
        "allowed_languages": json.loads(assessment.allowed_languages_json),
        "score": assessment.score,
        "total_score": assessment.total_score,
        "percentage": percentage,
        "passed": assessment.passed,
        "assigned_at": _iso(assessment.assigned_at),
        "started_at": _iso(assessment.started_at),
        "submitted_at": _iso(assessment.submitted_at),
        "expires_at": _iso(assessment.expires_at),
        "duration_seconds": _duration_seconds(assessment.started_at, assessment.submitted_at),
        "languages_used": hr_report["languages_used"],
        "skill_breakdown": hr_report["skill_breakdown"],
        "questions": hr_report["questions"],
        # Task 5G evidence foundation — deterministic, derived only
        # from real execution results (see programming_assessment_
        # evidence_v2.py's own module header). Not yet consumed by
        # any AI Interview logic; exposed here so HR can preview it
        # and so this foundation is exercised against real data.
        "evidence": {
            "strengths": evidence.strengths,
            "weaknesses": evidence.weaknesses,
            "insufficient_evidence": evidence.insufficient_evidence,
            "technologies_demonstrated": evidence.technologies_demonstrated,
            "languages_used": evidence.languages_used,
            "highest_difficulty_passed": evidence.highest_difficulty_passed,
        },
    }


@router.get("/{company_id}/{application_id}/review/programming-v2")
def review_programming_v2(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    return _programming_v2_review_data(db, application)


# ============================================================
# AI INTERVIEW REVIEW — per-question candidate answer + the
# REAL AI evaluation already computed by services/assessment/
# interview_evaluator.py at answer time (never re-evaluated,
# never a new algorithm). expected_answer_points/follow_up_focus
# remain internal-only, exactly as for the candidate — Part 11
# of the task spec asks for "Question / Candidate Answer / AI
# Evaluation", not the grading rubric itself.
# ============================================================

@router.get("/{company_id}/{application_id}/review/interview")
def review_interview(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    if not test:

        return {
            "application_id": application.id,
            "exists": False,
            "questions": [],
        }

    stored_questions = json.loads(test.questions)

    responses = (
        db.query(InterviewResponse)
        .filter(InterviewResponse.interview_test_id == test.id)
        .all()
    )

    response_by_question_id = {
        response.question_id: response
        for response in responses
    }

    questions_review = []
    skills_assessed = []

    for question in stored_questions:

        response = response_by_question_id.get(question["question_id"])

        evaluation = (
            json.loads(response.evaluation_data)
            if response and response.evaluation_data
            else None
        )

        primary_skill = question.get("primary_skill")

        if primary_skill and primary_skill not in skills_assessed:
            skills_assessed.append(primary_skill)

        questions_review.append(
            {
                "question_id": question["question_id"],
                "question": question["question"],
                "category": question.get("category"),
                "difficulty": question.get("difficulty"),
                "primary_skill": primary_skill,
                "what_it_assesses": question.get("what_it_assesses"),
                "candidate_answer": response.answer_text if response else None,
                "score": response.score if response else None,
                "evaluation": evaluation,
                "evaluation_source": (
                    response.evaluation_source if response else None
                ),
                "answered": response is not None,
            }
        )

    evaluation_summary = (
        json.loads(test.evaluation_summary)
        if test.evaluation_summary
        else None
    )

    return {
        "application_id": application.id,
        "exists": True,
        "status": test.status,
        "question_source": test.question_source,
        "total_questions": test.total_questions,
        "current_question_index": test.current_question_index,
        "overall_score": test.overall_score,
        "passed": test.passed,
        "recommendation": test.recommendation,
        "evaluation_summary": evaluation_summary,
        "skills_assessed": skills_assessed,
        "started_at": _iso(test.started_at),
        "submitted_at": _iso(test.submitted_at),
        "duration_seconds": _duration_seconds(test.started_at, test.submitted_at),
        "questions": questions_review,
    }


# ============================================================
# AI INTERVIEW V2 REVIEW (Task 5H)
#
# Shows HR the actual conversation timeline plus a competency-
# level evidence summary (Section 25) -- reconstructed entirely
# from AIInterviewTurnV2 rows (question/candidate_answer/
# answer_analysis_json, all already candidate-safe by
# construction — see ai_interview_turn_v2.py's own module header)
# and AIInterviewSessionV2.final_evaluation_json (written once at
# completion by interview_integration_v2._finalize_session()).
# Never exposes a raw Gemini prompt, the model's internal "reason"
# field (HR-internal only per the turn contract's own docstring —
# even THIS endpoint omits it, since it can reference the model's
# own reasoning about the decision, not evidence), or any chain-
# of-thought. No new scoring path — `final_evaluation_json` is the
# one place this is computed, read here unchanged.
# ============================================================

def _interview_v2_review_data(db: Session, application: JobApplication) -> dict:

    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
    from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
    from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2

    session = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not session:

        return {"application_id": application.id, "exists": False, "timeline": []}

    turns = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .all()
    )

    timeline = []

    for t in turns:

        analysis = json.loads(t.answer_analysis_json) if t.answer_analysis_json else None

        timeline.append({
            "turn_number": t.turn_number,
            "question": t.question,
            "question_type": t.question_type,
            "competency": t.competency,
            "difficulty": t.difficulty,
            "candidate_answer": t.candidate_answer,
            "next_action": t.next_action,
            "analysis": (
                {
                    "strengths": analysis["strengths"],
                    "weaknesses": analysis["weaknesses"],
                    "evidence": analysis["evidence"],
                    "claims": analysis["claims"],
                    "missing_depth": analysis["missing_depth"],
                    "strength_level": analysis["strength_level"],
                }
                if analysis else None
            ),
            "answered": t.candidate_answer is not None,
        })

    blueprint = InterviewBlueprintV2.model_validate_json(session.blueprint_json)

    final_evaluation = json.loads(session.final_evaluation_json) if session.final_evaluation_json else None

    return {
        "application_id": application.id,
        "exists": True,
        "status": session.status,
        "generated_by": session.generated_by,
        "blueprint": [
            {
                "name": c.name, "priority": c.priority,
                "known_strength": c.known_strength, "known_weakness": c.known_weakness,
                "evidence_status": c.evidence_status, "source": c.source,
            }
            for c in blueprint.competencies
        ],
        "assigned_at": _iso(session.assigned_at),
        "started_at": _iso(session.started_at),
        "completed_at": _iso(session.completed_at),
        "expires_at": _iso(session.expires_at),
        "duration_seconds": _duration_seconds(session.started_at, session.completed_at),
        "total_turns": len(turns),
        "final_evaluation": final_evaluation,
        "timeline": timeline,
    }


@router.get("/{company_id}/{application_id}/review/interview-v2")
def review_interview_v2(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    return _interview_v2_review_data(db, application)


# ============================================================
# ASSESSMENT INTEGRITY SUMMARY — reuses services/assessment/
# integrity_service.py's own read-only state function exactly;
# no new counting/policy logic. Returns a per-assessment-type
# breakdown across all three now-integrated stages (Task 4E:
# aptitude, programming, ai_interview all share the same
# violation counter/policy — see integrity_service.py), plus a
# chronological event timeline and a neutral-language camera/
# face/tab/fullscreen count breakdown for the HR-facing
# "Assessment Integrity" report section. Every field here is
# read directly from AssessmentIntegrityEvent rows already
# written by the real event-reporting pipeline — nothing is
# fabricated or estimated.
# ============================================================

# Groups the fixed, narrow event_type vocabulary into neutral,
# HR-readable categories. Never implies a verdict ("cheated") —
# only ever describes what signal fired.
_EVENT_CATEGORY = {
    "camera_connected": "camera",
    "camera_reconnected": "camera",
    "camera_disconnected": "camera",
    "camera_permission_denied": "camera",
    "camera_unsupported": "camera",
    "face_present": "face_presence",
    "face_missing": "face_presence",
    "multiple_faces_detected": "face_presence",
    "looking_away": "face_presence",
    "gaze_away": "face_presence",
    "gaze_away_sustained": "face_presence",
    "looking_away_repeated": "face_presence",
    "voice_detected": "audio",
    "possible_third_party_voice": "audio",
    "camera_covered": "camera",
    "paste_detected": "clipboard",
    "automated_typing_suspected": "browser_guard",
    "poor_lighting": "camera",
    "screen_share_started": "screen_share",
    "screen_share_stopped": "screen_share",
    "screen_share_rejected": "screen_share",
    "screen_share_unsupported": "screen_share",
    "copy_detected": "clipboard",
    "cut_detected": "clipboard",
    "blocked_shortcut": "browser_guard",
    "manipulation_attempt": "browser_guard",
    "devtools_opened": "browser_guard",
    "multiple_monitors_detected": "browser_guard",
    "fullscreen_entered": "fullscreen",
    "fullscreen_exited": "fullscreen",
    "tab_hidden": "tab_focus",
    "window_blur": "tab_focus",
    "window_focus": "tab_focus",
    "assessment_page_left": "tab_focus",
    "assessment_page_returned": "tab_focus",
    "violation_warning": "system",
    "assessment_auto_terminated": "system",
    # Task 5 - browser-side object detection observation, kept in
    # its own category rather than lumped into "camera" since it
    # is a distinct signal (visible device presence, not camera
    # health or face presence).
    "mobile_phone_detected": "device_detection",
}

_EVENT_LABEL = {
    "camera_connected": "Camera connected",
    "camera_reconnected": "Camera reconnected",
    "camera_disconnected": "Camera disconnected",
    "camera_permission_denied": "Camera permission not granted",
    "camera_unsupported": "Camera not supported in candidate's browser",
    "face_present": "Face detected",
    "face_missing": "Potential Integrity Event — no face detected",
    "multiple_faces_detected": "Potential Integrity Event — multiple faces detected",
    "looking_away": "Flag — head turned away from the screen for several seconds",
    "gaze_away": "Flag — eyes off the screen for several seconds",
    "gaze_away_sustained": "Potential Integrity Event — eyes off the screen continuously for 20+ seconds",
    "looking_away_repeated": "Warning — looked away from the screen for 10+ seconds again after a spoken warning",
    "voice_detected": "Potential Integrity Event — speech-level sound during a silent assessment (audio not recorded)",
    "possible_third_party_voice": "Flag — a sustained voice did not match the candidate's calibrated pitch range (heuristic, not verified identity; review the recording)",
    "camera_covered": "Potential Integrity Event — camera covered / picture black",
    "paste_detected": "Flag — text pasted into the assessment",
    "poor_lighting": "Information: the camera picture stayed too dark, too bright or backlit for a stretch, although the candidate was told how to fix it. Camera flags during that time may be caused by the lighting",
    "screen_share_started": "Screen sharing started: the entire screen is recorded for this stage",
    "screen_share_stopped": "Potential Integrity Event: the candidate stopped sharing their screen during the test",
    "screen_share_rejected": "The candidate chose a window or tab instead of the entire screen and was asked to share again",
    "screen_share_unsupported": "This browser cannot share a screen, so the test could not start",
    "automated_typing_suspected": "Flag — keystrokes arrived faster than a person can type (a browser agent or automation tool may have typed this)",
    "copy_detected": "Flag — copy attempted (blocked outside the code editor)",
    "manipulation_attempt": "Flag — told the AI interviewer to ignore its rules, or asked it for the answers or a score",
    "cut_detected": "Flag — cut attempted",
    "blocked_shortcut": "Flag — blocked browser shortcut (developer tools / save / print)",
    "devtools_opened": "Flag — browser developer tools appear to be open",
    "multiple_monitors_detected": "Flag — more than one screen is connected",
    "fullscreen_entered": "Entered fullscreen",
    "fullscreen_exited": "Potential Integrity Event — exited fullscreen",
    "tab_hidden": "Potential Integrity Event — tab switched away",
    "window_blur": "Potential Integrity Event — window lost focus",
    "window_focus": "Window regained focus",
    "assessment_page_left": "Left assessment screen",
    "assessment_page_returned": "Returned to assessment screen",
    "violation_warning": "Integrity warning issued",
    "assessment_auto_terminated": "Assessment automatically terminated",
    "mobile_phone_detected": "Potential Integrity Event — mobile phone detected",
}


def _overall_percentage_for_report(aptitude_summary, stage_3_percentage):
    from backend.app.services.assessment.integrity_digest import overall_percentage

    return overall_percentage([aptitude_summary["percentage"] if aptitude_summary else None, stage_3_percentage])


def _integrity_review_data(
    db: Session,
    application: JobApplication,
) -> dict:
    """
    The ONE place the integrity summary/counts/timeline shape is
    built — reused by the standalone /review/integrity endpoint
    AND the consolidated Candidate Intelligence Report (Task 4F).
    """

    summary = {}

    for assessment_type in sorted(VALID_ASSESSMENT_TYPES):

        summary[assessment_type] = get_integrity_state(
            db, application, assessment_type,
        )

    events = (
        db.query(AssessmentIntegrityEvent)
        .filter(AssessmentIntegrityEvent.application_id == application.id)
        .order_by(AssessmentIntegrityEvent.occurred_at.asc())
        .all()
    )

    timeline = [
        {
            "assessment_type": event.assessment_type,
            "event_type": event.event_type,
            "category": _EVENT_CATEGORY.get(event.event_type, "other"),
            "label": _EVENT_LABEL.get(event.event_type, event.event_type),
            "severity": event.severity,
            "counted_as_violation": event.counted_as_violation,
            "occurred_at": _iso(event.occurred_at),
            # Task 5 - already sanitized server-side at write time
            # (see integrity_service._sanitize_metadata) — never
            # raw client content, never a frame/screenshot.
            "metadata": (
                json.loads(event.metadata_json)
                if event.metadata_json else None
            ),
        }
        for event in events
    ]

    counts = {
        "camera_disconnect_count": sum(
            1 for e in events if e.event_type == "camera_disconnected"
        ),
        "camera_permission_denied": any(
            e.event_type == "camera_permission_denied" for e in events
        ),
        "camera_active_observed": any(
            e.event_type in ("camera_connected", "camera_reconnected")
            for e in events
        ),
        "face_missing_count": sum(
            1 for e in events if e.event_type == "face_missing"
        ),
        "multiple_faces_count": sum(
            1 for e in events if e.event_type == "multiple_faces_detected"
        ),
        "looking_away_count": sum(
            1 for e in events if e.event_type in ("looking_away", "gaze_away")
        ),
        "paste_count": sum(1 for e in events if e.event_type == "paste_detected"),
        "automated_typing_count": sum(1 for e in events if e.event_type == "automated_typing_suspected"),
        "copy_count": sum(1 for e in events if e.event_type in ("copy_detected", "cut_detected")),
        "browser_guard_count": sum(
            1 for e in events if e.event_type in ("blocked_shortcut", "devtools_opened", "multiple_monitors_detected")
        ),
        "snapshot_count": db.query(AssessmentSnapshot).filter(AssessmentSnapshot.application_id == application.id).count(),
        "voice_detected_count": sum(1 for e in events if e.event_type == "voice_detected"),
        "camera_covered_count": sum(
            1 for e in events if e.event_type == "camera_covered"
        ),
        "mobile_phone_detected_count": sum(
            1 for e in events if e.event_type == "mobile_phone_detected"
        ),
        "tab_focus_violation_count": sum(
            1 for e in events
            if e.event_type in ("tab_hidden", "window_blur")
            and e.counted_as_violation
        ),
        "fullscreen_violation_count": sum(
            1 for e in events
            if e.event_type == "fullscreen_exited" and e.counted_as_violation
        ),
        "total_events": len(events),
    }

    any_terminated = any(
        state["warning_level"] == "terminated"
        for state in summary.values()
    )

    from backend.app.services.assessment.integrity_digest import digest as integrity_digest

    return {
        "application_id": application.id,
        "summary": summary,
        "counts": counts,
        "digest": integrity_digest(events),
        "terminated": any_terminated,
        # who accepted the instructions / monitoring notice before which stage, and when
        "consents": [
            {"assessment_type": c.assessment_type, "accepted_at": _iso(c.accepted_at), "statement_version": c.statement_version}
            for c in (
                db.query(AssessmentConsent)
                .filter(AssessmentConsent.application_id == application.id)
                .order_by(AssessmentConsent.accepted_at.asc())
                .all()
            )
        ],
        "timeline": timeline,
        # camera evidence: identity reference at the start of each stage, periodic and flag-time snapshots (urls need the
        # company/admin session cookie; images are only ever shown to the recruiter)
        "snapshots": [
            {
                "id": s.id,
                "assessment_type": s.assessment_type,
                "reason": s.reason,
                "created_at": _iso(s.created_at),
                "url": f"/api/assessment/snapshots/file/{s.id}",
            }
            for s in (
                db.query(AssessmentSnapshot)
                .filter(AssessmentSnapshot.application_id == application.id)
                .order_by(AssessmentSnapshot.created_at.asc())
                .all()
            )
        ],
    }


@router.get("/{company_id}/{application_id}/review/integrity")
def review_integrity(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    return _integrity_review_data(db, application)


# ============================================================
# CANDIDATE INTELLIGENCE / FINAL ASSESSMENT REPORT (Task 4F)
#
# A single consolidated, HR-facing view assembled ENTIRELY from
# data this project already computes elsewhere:
#   - AI Match + its component breakdown: JobApplication's own
#     ai_* columns, written by services/ai_candidate_matching.py
#     (a deterministic weighted-matching model — NEVER a Gemini/
#     LLM score; labeled "AI Match" here, matching the existing
#     column/label convention used everywhere else in this
#     project).
#   - Self Intro / Aptitude / Programming / Interview summaries:
#     the exact same *_summary_response() functions
#     company_applications.py's own application-detail endpoint
#     already uses — never a second scoring/aggregation path.
#   - Integrity: the exact same _integrity_review_data() the
#     standalone /review/integrity endpoint uses (Task 4E).
#   - Self Intro's actual saved text: _self_intro_review_data()
#     (extracted above so this and the standalone endpoint can
#     never drift apart).
#
# Deliberately light: full per-question detail (aptitude
# correct/incorrect breakdown, submitted code, per-question
# interview transcripts, the integrity event timeline) stays
# behind the EXISTING dedicated review endpoints/modals — this
# endpoint returns only the same summary-level fields the main
# detail view already computes, so opening one report never
# pulls a candidate's complete answer history (see Part 23 of
# the task spec).
#
# No new scoring engine, no new "overall score" — Part 12/13 of
# the task spec explicitly require leaving that pending unless a
# formal formula already exists (it does not, confirmed by
# auditing this codebase; Task 4D-STRICT-2 already established
# the "Overall Candidate Intelligence: Pending" wording this
# reuses verbatim).
# ============================================================

def _alignment_label(score: float | None) -> str:

    if score is None:
        return "Insufficient data"

    if score >= 80:
        return "Strong"

    if score >= 50:
        return "Moderate"

    return "Weak"


def _stage_state(status_label: str) -> str:
    """
    Maps the existing _aptitude_status_label()/_programming_
    status_label()/_interview_status_label()/self_intro_status
    vocabulary (already computed by assessment_summary_response())
    into a 3-state progress-stepper state. "done" means the stage
    was REACHED and FINISHED — passed or failed — mirroring the
    task spec's own example (a candidate who failed the interview
    still gets a checkmark on every stage they completed).
    """

    if status_label in ("completed", "expired", "terminated"):
        return "done"

    if status_label == "in_progress":
        return "active"

    return "locked"


def _compute_strengths(
    application: JobApplication,
    aptitude_test: AptitudeTest | None,
    programming_test: ProgrammingTest | None,
    interview_test: InterviewTest | None,
) -> list[str]:
    """
    Deterministic, template-based strengths derived ONLY from
    real stored fields — never a free-text generation call, never
    an invented skill. Each line is gated on an actual boolean/
    threshold from data that already exists.
    """

    strengths: list[str] = []

    matched_skills = parse_json_list(application.ai_matched_skills)

    if matched_skills:

        strengths.append(
            "Strong alignment on " + ", ".join(matched_skills[:3])
        )

    if application.ai_recommendation in (
        "Strongly Recommended", "Recommended",
    ):

        strengths.append(
            f"{application.ai_recommendation} by the AI candidate-job "
            "matching model"
        )

    if aptitude_test and aptitude_test.passed:
        strengths.append("Passed the aptitude assessment")

    if programming_test and programming_test.passed:
        strengths.append("Strong programming assessment performance")

    if interview_test and interview_test.evaluation_summary:

        eval_summary = json.loads(interview_test.evaluation_summary)

        for item in eval_summary.get("strengths", []):

            if item and item not in strengths:
                strengths.append(item)

    return strengths[:8]


def _compute_skill_gaps(
    application: JobApplication,
    interview_test: InterviewTest | None,
) -> list[str]:
    """
    Reuses the AI matching engine's own ai_missing_skills column
    (required skills not found on the candidate) plus, if the
    interview was completed, the real per-answer "gaps" already
    produced by services/assessment/interview_scoring.py. Never
    invents a skill that doesn't come from one of these two real
    sources.
    """

    gaps = parse_json_list(application.ai_missing_skills)

    if interview_test and interview_test.evaluation_summary:

        eval_summary = json.loads(interview_test.evaluation_summary)

        for item in eval_summary.get("gaps", []):

            if item and item not in gaps:
                gaps.append(item)

    return gaps[:10]


# camera flags whose snapshot HR should look at (the picture taken the moment the flag fired)
_FLAG_SNAPSHOT_LABELS = {
    "face_missing": "No face in the picture",
    "multiple_faces_detected": "More than one person",
    "mobile_phone_detected": "A phone was visible",
    "camera_covered": "Camera covered",
    "looking_away": "Looked away from the screen",
    "looking_away_repeated": "Looked away again after a warning",
    "gaze_away": "Eyes off the screen",
}

_STAGE_PHOTO_LABELS = {
    "self_intro": "Self introduction", "aptitude": "Aptitude", "programming": "Programming / technical",
    "situational_judgment": "Situational judgment", "technical_domain": "Technical assessment", "ai_interview": "AI interview",
}


def _identity_photos(db, application) -> dict:
    """
    The candidate's photo for the report: the picture taken when the camera first came on, before the self introduction (the
    reference), the face at the START of every later stage (so the recruiter can see it is the same person throughout), and the
    pictures taken the moment a camera flag fired. Image URLs need the company / admin session cookie.
    """

    rows = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id == application.id)
        .order_by(AssessmentSnapshot.created_at.asc(), AssessmentSnapshot.id.asc())
        .all()
    )

    def _photo(row, label):
        return {
            "id": row.id, "stage": row.assessment_type, "stage_label": _STAGE_PHOTO_LABELS.get(row.assessment_type, row.assessment_type),
            "reason": row.reason, "label": label, "taken_at": _iso(row.created_at), "url": f"/api/assessment/snapshots/file/{row.id}",
        }

    starts = [r for r in rows if r.reason == "identity_reference"]
    reference = next((r for r in starts if r.assessment_type == "self_intro"), starts[0] if starts else None)

    seen_stages = set()
    per_stage = []
    for row in starts:
        if row.assessment_type not in seen_stages:
            seen_stages.add(row.assessment_type)
            per_stage.append(_photo(row, "Start of " + _STAGE_PHOTO_LABELS.get(row.assessment_type, row.assessment_type)))

    flagged = [_photo(r, _FLAG_SNAPSHOT_LABELS[r.reason]) for r in rows if r.reason in _FLAG_SNAPSHOT_LABELS][:24]

    return {
        "reference": _photo(reference, "Before the self introduction") if reference else None,
        "per_stage": per_stage,
        "flagged": flagged,
        "total_snapshots": len(rows),
    }


def _face_match_view(db: Session, application_id: int) -> dict:
    from backend.app.services.assessment.face_match import pick_photos, stored_results, summarise

    rows = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id == application_id)
        .order_by(AssessmentSnapshot.created_at.asc(), AssessmentSnapshot.id.asc())
        .all()
    )

    reference, later = pick_photos(rows)
    results = stored_results(db, application_id)

    labels = {r.id: _STAGE_PHOTO_LABELS.get(r.assessment_type, r.assessment_type) for r in later}

    for result in results:
        result["stage_label"] = labels.get(result["snapshot_id"]) or _STAGE_PHOTO_LABELS.get(result["stage"], result["stage"])

    return {
        "photos_available": (1 if reference else 0) + len(later),
        "compared": len(results),
        "pending": len([r for r in later if r.id not in {x["snapshot_id"] for x in results}]),
        "results": results,
        "summary": summarise(results),
        "note": "A computer comparison of the camera photos to help you review them. It is not an identity verification: you decide.",
    }


@router.get("/{company_id}/{application_id}/face-match")
def get_face_match(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(company_id, application_id, db)

    return _face_match_view(db, application.id)


@router.post("/{company_id}/{application_id}/face-match/run")
async def run_face_match(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    """Compare the candidate's later photos with the reference photo (only the photos not compared yet), store the answers and return the view."""

    from starlette.concurrency import run_in_threadpool

    from backend.app.api.deps import require_csrf
    from backend.app.services.assessment.face_match import run_for_application

    require_csrf(request)

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(company_id, application_id, db)

    application_pk = application.id

    def _work():
        from backend.app.database.database import SessionLocal

        worker_db = SessionLocal()
        try:
            return run_for_application(worker_db, application_pk)
        finally:
            worker_db.close()

    ran = await run_in_threadpool(_work)

    db.expire_all()

    return {**_face_match_view(db, application_pk), "run": ran}


@router.get("/{company_id}/{application_id}/recordings/{recording_id}/behavior")
async def get_recording_behavior_summary(
    company_id: int,
    application_id: int,
    recording_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    """The 'observed behaviour' paragraph for one recorded video (written once from recorded signals, then cached). It describes what was
    measured; it never judges emotions, confidence or personality (see services/assessment/recording_behavior_summary.py)."""

    from starlette.concurrency import run_in_threadpool

    from backend.app.services.assessment.recording_behavior_summary import get_or_create_behavior_summary

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(company_id, application_id, db)

    recording = (
        db.query(AssessmentVideoRecording)
        .filter(AssessmentVideoRecording.id == recording_id, AssessmentVideoRecording.application_id == application.id)
        .first()
    )

    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")

    return await run_in_threadpool(lambda: get_or_create_behavior_summary(db, recording, application))


def _assessment_plan_for_report(job) -> dict | None:

    try:
        from backend.app.services.assessment.assessment_planner import get_job_plan

        plan = get_job_plan(job, allow_llm=False, store=False)
        return {
            "source": plan.get("source"), "summary": plan.get("summary"),
            "stages": [{"slot": s["slot"], "kind": s["kind"], "title": s.get("title") or "", "reason": s.get("reason") or ""} for s in plan["stages"]],
        }
    except Exception:  # noqa: BLE001
        return None


@router.get("/{company_id}/{application_id}/candidate-intelligence-report")
def get_candidate_intelligence_report(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    _require_company(company_id, current_company, db)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    # a candidate who left a stage early leaves spooled video chunks: turn the stale ones into a "partial" recording first
    try:
        from backend.app.api.routes.assessment_videos import assemble_stale_partials
        assemble_stale_partials(db, application.id)
    except Exception:  # noqa: BLE001 - never blocks the report
        pass

    aptitude_test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    programming_test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    interview_test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    session_record = (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application.id)
        .first()
    )

    assessment_summary = assessment_summary_response(
        session_record, aptitude_test, programming_test, interview_test,
    )

    aptitude_summary = aptitude_summary_response(aptitude_test)
    programming_summary = programming_summary_response(programming_test)
    interview_summary = interview_summary_response(interview_test)
    self_intro = _self_intro_review_data(application, session_record)
    integrity = _integrity_review_data(db, application)

    # Programming V2 (multi-language) — a candidate takes EITHER the
    # legacy programming stage OR the V2 one, never both, so this is
    # only ever a fallback: the legacy programming_status is used
    # whenever a legacy ProgrammingTest row exists (unchanged
    # behavior for every existing candidate), and V2's own status
    # is used only when no legacy row exists at all.
    programming_v2_review = _programming_v2_review_data(db, application)

    # Non-technical / no-executable-language candidates take SJT or
    # TDA instead of programming — occupies the exact same pipeline
    # slot (stage_progression_service.py), so HR's report must cover
    # it the same way it covers programming. A candidate only ever
    # has ONE of these two non-empty, and only when neither
    # programming path above exists either.
    sjt_review = _sjt_review_data(db, application)
    tda_review = _tda_review_data(db, application)

    # Stage 3 is EXACTLY ONE of: legacy programming, programming V2,
    # SJT, or TDA — checked in that order, mirroring the "legacy
    # first, V2 fallback" precedent already established above.
    if programming_test:
        programming_status_for_progress = (assessment_summary or {}).get("programming_status", "not_started")
        stage_3_label = "Programming"
    elif programming_v2_review["exists"]:
        programming_status_for_progress = programming_v2_review["status"]
        stage_3_label = "Programming"
    elif sjt_review["exists"]:
        programming_status_for_progress = sjt_review["status"]
        stage_3_label = "Situational Judgment Test"
    elif tda_review["exists"]:
        programming_status_for_progress = tda_review["status"]
        stage_3_label = "Technical Assessment"
    else:
        programming_status_for_progress = "not_started"
        stage_3_label = "Programming"

    # AI Interview V2 (adaptive) — same "legacy first, V2 fallback"
    # pattern as programming above (Task 5G/5F precedent): a
    # candidate takes EITHER the legacy fixed-question interview OR
    # the V2 adaptive one, never both.
    interview_v2_review = _interview_v2_review_data(db, application)

    interview_v2_status_map = {"in_progress": "in_progress", "completed": "completed", "expired": "expired"}

    interview_status_for_progress = (
        (assessment_summary or {}).get("interview_status", "not_started")
        if interview_test
        else (
            interview_v2_status_map.get(interview_v2_review["status"], "not_started")
            if interview_v2_review["exists"]
            else "not_started"
        )
    )

    stage_defs = [
        ("self_intro", "Self Introduction",
         (assessment_summary or {}).get("self_intro_status", "not_started")),
        ("aptitude", "Aptitude",
         (assessment_summary or {}).get("aptitude_status", "not_started")),
        ("programming", stage_3_label,
         programming_status_for_progress),
        ("ai_interview", "AI Interview",
         interview_status_for_progress),
    ]

    progress = [
        {
            "stage": key,
            "label": label,
            "status": status_label,
            "state": _stage_state(status_label),
        }
        for key, label, status_label in stage_defs
    ]

    completed_stage_count = sum(
        1 for step in progress if step["state"] == "done"
    )

    stage_3_percentage = (
        programming_summary["percentage"] if programming_summary and programming_summary["percentage"] is not None
        else programming_v2_review["percentage"] if programming_v2_review["exists"] and programming_v2_review["percentage"] is not None
        else sjt_review["percentage"] if sjt_review["exists"] and sjt_review["percentage"] is not None
        else tda_review["percentage"] if tda_review["exists"] and tda_review["percentage"] is not None
        else None
    )

    role_alignment = {
        "skills": _alignment_label(application.ai_skill_score),
        "experience": _alignment_label(application.ai_experience_score),
        "education": _alignment_label(application.ai_education_score),
        "programming": (
            _alignment_label(stage_3_percentage)
            if stage_3_percentage is not None
            else "Insufficient data"
        ),
        "interview": (
            _alignment_label(interview_summary["overall_score"])
            if interview_summary
            and interview_summary["overall_score"] is not None
            else "Insufficient data"
        ),
    }

    # Plain-language synthesis across whichever stages actually
    # happened -- never a hire/reject verdict (confirmed with the
    # user before this was built; see candidate_narrative_summary.py's
    # own header). Only built from facts already computed above.
    from backend.app.services.assessment.candidate_narrative_summary import (
        generate_candidate_narrative_summary,
    )

    stage_facts = {}

    if self_intro["exists"] and self_intro["status"] == "completed":
        stage_facts["Self Introduction"] = "Completed"

    if aptitude_summary:
        stage_facts["Aptitude"] = (
            f"{aptitude_summary['percentage']}% "
            f"({'passed' if aptitude_summary['passed'] else 'did not pass'})"
        )

    if programming_summary:
        stage_facts["Programming"] = (
            f"{programming_summary['percentage']}% "
            f"({'passed' if programming_summary['passed'] else 'did not pass'})"
        )
    elif programming_v2_review["exists"]:
        evidence = programming_v2_review.get("evidence") or {}
        parts = [f"{programming_v2_review['percentage']}%"]
        if evidence.get("strengths"):
            parts.append("strengths: " + ", ".join(evidence["strengths"][:4]))
        if evidence.get("weaknesses"):
            parts.append("gaps: " + ", ".join(evidence["weaknesses"][:4]))
        stage_facts["Programming"] = "; ".join(parts)
    elif sjt_review["exists"] and sjt_review.get("kind") == "practical":
        verdict = _practical_verdict(sjt_review) or {}
        parts = [f"{sjt_review['percentage']}% ({'passed' if sjt_review['passed'] else 'did not pass'})"]
        if verdict.get("verdict"):
            parts.append("AI verdict: " + verdict["verdict"])
        if verdict.get("strengths"):
            parts.append("strengths: " + ", ".join(verdict["strengths"][:3]))
        if verdict.get("gaps"):
            parts.append("gaps: " + ", ".join(verdict["gaps"][:3]))
        stage_facts[f"Practical assessment ({sjt_review.get('environment') or 'work sample'})"] = "; ".join(parts)
    elif sjt_review["exists"]:
        stage_facts["Situational Judgment Test"] = (
            f"{sjt_review['percentage']}% ({'passed' if sjt_review['passed'] else 'did not pass'})"
        )
    elif tda_review["exists"]:
        stage_facts[f"Technical Assessment ({tda_review.get('domain', '')})"] = (
            f"{tda_review['percentage']}% ({'passed' if tda_review['passed'] else 'did not pass'})"
        )

    if interview_summary and interview_summary.get("overall_score") is not None:
        stage_facts["AI Interview"] = f"Overall score {interview_summary['overall_score']}"
    elif interview_v2_review["exists"] and interview_v2_review.get("final_evaluation"):
        final_eval = interview_v2_review["final_evaluation"]
        parts = []
        if final_eval.get("strengths"):
            parts.append("strengths: " + ", ".join(final_eval["strengths"][:4]))
        if final_eval.get("weaknesses"):
            parts.append("gaps: " + ", ".join(final_eval["weaknesses"][:4]))

        detailed = final_eval.get("candidate_report") or {}
        if detailed.get("generated_by") == "ai":
            stated = detailed.get("stated_by_candidate") or {}
            for label, value in (
                ("overview", detailed.get("candidate_overview")),
                ("communication", detailed.get("communication")),
                ("fit with resume", detailed.get("resume_consistency")),
                ("concerns", " ".join(detailed.get("concerns") or [])),
                ("AI tools", (detailed.get("ai_tools") or {}).get("assessment")),
                ("expected package, in their words", (stated.get("expected_package") or {}).get("answer")),
                ("their final answer on why they fit the role, in their words", (stated.get("work_in_company") or {}).get("answer")),
            ):
                if value:
                    parts.append(f"{label}: {str(value)[:600]}")

        if parts:
            stage_facts["AI Interview"] = "; ".join(parts)

    narrative = generate_candidate_narrative_summary(stage_facts)

    return {
        "application_id": application.id,

        "narrative_summary": narrative,

        "candidate": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "mobile": user.mobile,
            "location": user.location,
            "current_role": user.current_role,
            "industry": user.industry,
            "experience_level": user.experience_level,
        },

        "profile": profile_response(profile),

        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "work_mode": job.work_mode,
            "employment_type": job.employment_type,
        },

        "application": {
            "id": application.id,
            "status": application.status,
            "recruiter_shortlisted": application.recruiter_shortlisted,
            "applied_date": _iso(application.applied_date),
        },

        "ai_match": ai_result_response(application),

        "assessment": {
            "current_stage": (
                session_record.current_stage if session_record else None
            ),
            "progress": progress,
            "completed_stage_count": completed_stage_count,
            "total_stage_count": len(progress),
            "self_intro": self_intro,
            "aptitude": aptitude_summary,
            "programming": programming_summary,
            "programming_v2": (
                programming_v2_review if programming_v2_review["exists"] else None
            ),
            "situational_judgment_test": (
                sjt_review if sjt_review["exists"] else None
            ),
            "technical_domain_assessment": (
                tda_review if tda_review["exists"] else None
            ),
            "interview": interview_summary,
            "interview_v2": (
                interview_v2_review if interview_v2_review["exists"] else None
            ),
            "integrity": {
                "summary": integrity["summary"],
                "counts": integrity["counts"],
                "digest": integrity["digest"],
                "terminated": integrity["terminated"],
            },
        },

        "practical_verdict": _practical_verdict(sjt_review),

        "strengths": (
            [f"Practical task: {s}" for s in ((_practical_verdict(sjt_review) or {}).get("strengths") or [])[:2]]
            + _compute_strengths(application, aptitude_test, programming_test, interview_test)
        )[:8],

        "skill_gaps": (
            [f"Practical task: {g}" for g in ((_practical_verdict(sjt_review) or {}).get("gaps") or [])[:3]]
            + _compute_skill_gaps(application, interview_test)
        )[:10],

        "role_alignment": role_alignment,

        "overall_intelligence": {
            "ai_match_score": application.ai_match_score,
            "aptitude_percentage": (
                aptitude_summary["percentage"] if aptitude_summary else None
            ),
            "programming_percentage": (
                programming_summary["percentage"]
                if programming_summary
                else (
                    programming_v2_review["percentage"]
                    if programming_v2_review["exists"] else None
                )
            ),
            "interview_score": (
                interview_summary["overall_score"]
                if interview_summary else None
            ),
            "integrity_event_count": integrity["counts"]["total_events"],
            "integrity_risk": integrity["digest"]["risk"],
            "integrity_strikes": integrity["digest"]["strikes"],
            "overall_percentage": _overall_percentage_for_report(aptitude_summary, stage_3_percentage),
            "status": "pending",
            "note": (
                "Overall percentage is the plain average of the scored stages (quiz and the practical / coding / platform stage). "
                "The AI interview is shown separately as pass or fail."
            ),
        },

        # Task 6A: the single HR view of the automatic pipeline.
        "pipeline": determine_next_stage(db, application),

        # what the AI planner called this job's tests, and why it chose them
        "assessment_plan": _assessment_plan_for_report(job),

        # the candidate's photo and the camera pictures that show it is the same person throughout
        "identity_photos": _identity_photos(db, application),

        "status_history": [
            {
                "from_status": h.from_status,
                "to_status": h.to_status,
                "changed_by_type": h.changed_by_type,
                "note": h.note,
                "created_at": _iso(h.created_at),
            }
            for h in (
                db.query(ApplicationStatusHistory)
                .filter(ApplicationStatusHistory.application_id == application.id)
                .order_by(ApplicationStatusHistory.id.asc())
                .all()
            )
        ],

        # Metadata only -- never the server file path. Playback goes
        # through the existing authorized stream endpoint.
        "recordings": [
            {
                "id": r.id,
                "stage": r.stage,
                "status": r.status,
                "mime_type": r.mime_type,
                "file_size": r.file_size,
                "duration_seconds": r.duration_seconds,
                "created_at": _iso(r.created_at),
                "stream_url": f"/api/assessment/videos/{r.id}/stream",
            }
            for r in (
                db.query(AssessmentVideoRecording)
                .filter(AssessmentVideoRecording.application_id == application.id)
                .order_by(AssessmentVideoRecording.id.asc())
                .all()
            )
        ],

        "final_status": application.status,
    }

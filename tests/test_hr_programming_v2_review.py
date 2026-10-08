"""
Isolated-database tests for the HR Programming V2 review (Task 5E).

Same convention as test_programming_v2_live_integration.py: a fresh,
temporary in-memory SQLite database per test (never touches the real
jobpilot.db), operating at the service/data layer (_programming_v2_
review_data() called directly) rather than through a full FastAPI
TestClient -- HTTP-level authorization (candidate rejected, wrong
company rejected, authorized company allowed) for this endpoint was
additionally verified live against the running app during Task 5E's
manual browser/security test pass, using the exact same get_current_
company / require_company_self / get_company_application dependency
chain every other HR review endpoint in this file already uses and
is not re-proven here at the unit level, matching this project's
existing testing convention for that concern.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.database import Base

from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.password_reset import PasswordResetOTP
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user_activity import UserActivity
from backend.app.models.resume import Resume
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.job import Job
from backend.app.models.job_question import JobQuestion
from backend.app.models.job_application import JobApplication
from backend.app.models.application_answer import ApplicationAnswer
from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.auth_session import AuthSession
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_submission import ProgrammingSubmission
from backend.app.models.interview_test import InterviewTest
from backend.app.models.interview_response import InterviewResponse
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2

from backend.app.services.assessment.programming_integration_v2 import (
    start_programming_assessment_v2,
    submit_assessment_v2,
)
from backend.app.api.routes.hr_assessment_review import _programming_v2_review_data


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed(db, job_title="Backend Developer", required_skills="Python"):

    user = User(
        full_name="Test Candidate", username="testcand_5e", email="cand5e@example.com",
        hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    job = Job(title=job_title, company="TestCo", required_skills=required_skills)
    db.add(job)
    db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status="aptitude_passed")
    db.add(application)
    db.flush()

    session_record = AssessmentSession(application_id=application.id, current_stage="aptitude")
    db.add(session_record)

    db.add(AssessmentAssignment(
        application_id=application.id, assessment_type="programming",
        status="assigned", assigned_by_type="system", assigned_by_id=user.id,
    ))

    db.add(AptitudeTest(
        application_id=application.id, questions="[]", total_questions=10,
        passed=True, status="completed",
    ))

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    return user, job, application, session_record


_PYTHON_SOLUTIONS = {
    1: "a, b = map(int, input().split())\nprint(a + b)",
    2: "words = input().split()\ncounts = {}\nfor w in words:\n    counts[w] = counts.get(w, 0) + 1\nbest = words[0]\nfor w in words:\n    if counts[w] > counts[best]:\n        best = w\nprint(best)",
    3: "s = input()\nstack = []\npairs = {')':'(', ']':'[', '}':'{'}\nok = True\nfor c in s:\n    if c in '([{':\n        stack.append(c)\n    elif c in ')]}':\n        if not stack or stack.pop() != pairs[c]:\n            ok = False\n            break\nif stack:\n    ok = False\nprint('YES' if ok else 'NO')",
    4: "nums = list(map(int, input().split()))\nseen = set()\nresult = -1\nfor n in nums:\n    if n in seen:\n        result = n\n        break\n    seen.add(n)\nprint(result)",
    5: "s = input()\nprint('YES' if s == s[::-1] else 'NO')",
}


def _lookup_solution(question) -> str:
    """
    A Python solution that answers every test input of this question correctly. Questions are generated per
    candidate now (random data), so the correct answers are taken from the stored test cases themselves.
    """

    table = {}

    for subpart in question["subparts"]:
        for case in subpart["test_cases"]:
            table[case["stdin"].rstrip("\n")] = case["expected_stdout"]

    return (
        "lines = []\nwhile True:\n    try:\n        lines.append(input())\n    except EOFError:\n        break\n"
        f"TABLE = {table!r}\nprint(TABLE['\\n'.join(lines)])\n"
    )


def _submit_all_correct(db, assessment, application):

    import json as _json

    questions = _json.loads(assessment.question_set_json)["main_questions"]

    answers = [
        {"question_id": q["question_id"], "language": "python", "source_code": _lookup_solution(q)}
        for q in questions
    ]

    return submit_assessment_v2(db, assessment, application, answers)


# No V2 assessment exists at all -- must return exists: False, never
# a crash or a fabricated report.
def test_review_reports_not_exists_when_no_v2_assessment():
    db = _make_db()
    _, _, application, _ = _seed(db)

    review = _programming_v2_review_data(db, application)

    assert review == {
        "application_id": application.id, "exists": False, "questions": [],
    }


# Core shape: reuses build_hr_report_v2 end to end via the persisted
# result_json, and the authoritative score/passed/status come from
# the ProgrammingAssessmentV2 row itself, not a re-derived value.
def test_review_reflects_real_submission_after_full_correct_solve():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )

    result = _submit_all_correct(db, assessment, application)
    assert result["passed"] is True

    review = _programming_v2_review_data(db, application)

    assert review["exists"] is True
    assert review["status"] == "completed"
    assert review["passed"] is True
    assert review["score"] == 100.0
    assert review["total_score"] == 100.0
    assert review["percentage"] == 100.0
    assert review["languages_used"] == ["python"]
    assert len(review["questions"]) == 5

    for question in review["questions"]:
        assert question["answered"] is True
        assert question["source_code"]
        assert question["earned_points"] == question["max_points"]
        assert question["hidden_test_summary"]["total"] >= 1
        assert (
            question["hidden_test_summary"]["passed"]
            == question["hidden_test_summary"]["total"]
        )
        assert (
            question["public_test_summary"]["passed"]
            == question["public_test_summary"]["total"]
        )


# HIDDEN-TEST-LEAKAGE CHECK: every test_case dict inside every
# subpart, for every question, must contain ONLY the candidate-safe
# fields -- never stdin, expected_stdout, or any evaluator detail.
# This is the same guarantee programming_scoring_v2.py's dataclasses
# already provide by construction; this test proves the HR review
# layer built on top of them does not accidentally add a leaking
# field of its own.
def test_review_never_exposes_hidden_test_input_or_expected_output():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )

    _submit_all_correct(db, assessment, application)

    review = _programming_v2_review_data(db, application)

    allowed_test_case_keys = {
        "test_case_id", "is_hidden", "passed", "weight",
        "execution_time_ms", "error_type",
    }
    forbidden_substrings = ("stdin", "expected_stdout", "expected_output")

    for question in review["questions"]:

        # top-level question dict itself must never contain a
        # forbidden key
        for key in question.keys():
            for bad in forbidden_substrings:
                assert bad not in key.lower()

        for subpart in question["subparts"]:

            for test_case in subpart["test_cases"]:

                assert set(test_case.keys()) == allowed_test_case_keys

                # Also scan values, not just keys, in case a stray
                # field ever got serialized as a nested string.
                serialized = str(test_case)
                for bad in forbidden_substrings:
                    assert bad not in serialized.lower()


# A candidate who submits an incomplete/incorrect set of answers
# still gets a real, honest (not error-raising) review -- the
# question(s) they never answered show answered: False, 0 points,
# never a fabricated pass.
def test_review_handles_partial_and_wrong_submission_honestly():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )

    # Only answer question 1, and answer it WRONG.
    wrong_answers = [
        {"question_id": 1, "language": "python", "source_code": "print('nope')"},
    ]

    result = submit_assessment_v2(db, assessment, application, wrong_answers)
    assert result["passed"] is False

    review = _programming_v2_review_data(db, application)

    q1 = next(q for q in review["questions"] if q["question_id"] == 1)
    assert q1["answered"] is True
    assert q1["earned_points"] == 0.0

    unanswered = [q for q in review["questions"] if q["question_id"] != 1]
    assert len(unanswered) == 4
    for q in unanswered:
        assert q["answered"] is False
        assert q["earned_points"] == 0.0
        assert q["source_code"] is None

"""
Isolated-database integration tests for AI Interview V2 (Task 5H).
Same convention as test_programming_v2_live_integration.py: every
test creates its own fresh, temporary in-memory SQLite database
(never D:\\jobpilot-ai\\jobpilot.db) and operates at the SERVICE
layer (interview_integration_v2.py functions called directly).

Covers:
  G. Interview bounds (minimum/maximum turns, completion)
  D/E again, but against REAL persisted turns (not isolated units)
  F. Conversation memory -- no duplicate question text persisted
  J. Database safety -- in-memory DB, nothing to clean up destructively

use_llm=False throughout -- deterministic, no network dependency
for the regression suite; the fallback engine is a real, separate
code path (see interview_brain_v2.py), not a mock of the real one.
"""

from datetime import datetime, timedelta

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
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

from backend.app.services.assessment.interview_integration_v2 import (
    INTERVIEW_V2_MAX_TURNS,
    INTERVIEW_V2_MIN_TURNS,
    InterviewIntegrationV2Error,
    start_interview_v2,
    submit_answer_v2,
)
from backend.app.services.assessment.assignment_service import create_assignment


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed(db, required_skills="Java, Debugging", programming_passed=True):

    user = User(
        full_name="Test Candidate", username="testcand_5h", email="cand5h@example.com",
        hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    job = Job(title="Backend Developer", company="TestCo", required_skills=required_skills)
    db.add(job)
    db.flush()

    application = JobApplication(
        job_id=job.id, user_id=user.id,
        status="programming_passed" if programming_passed else "aptitude_passed",
    )
    db.add(application)
    db.flush()

    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)

    db.add(AptitudeTest(
        application_id=application.id, questions="[]", total_questions=10,
        passed=True, status="completed", score=9,
    ))

    if programming_passed:

        db.add(ProgrammingTest(
            application_id=application.id, questions="[]", total_questions=5,
            passed=True, status="completed", score=90, total_score=100,
            assigned_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
            started_at=datetime.utcnow(),
        ))

        create_assignment(db, application.id, "ai_interview", assigned_by_id=user.id, assigned_by_type="system")

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    return user, job, application, session_record


# 1: starting creates a session + exactly one (unanswered) opening turn
def test_start_creates_session_and_opening_turn():

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, created = start_interview_v2(
        db, session_record, application, job, user, None, None, use_llm=False,
    )

    assert created is True
    assert session.status == "in_progress"
    assert session.current_turn == 1

    turns = db.query(AIInterviewTurnV2).filter(AIInterviewTurnV2.session_id == session.id).all()
    assert len(turns) == 1
    assert turns[0].candidate_answer is None
    assert turns[0].question


# 2: starting again returns the SAME session unchanged (stable snapshot)
def test_start_is_idempotent():

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session1, created1 = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)
    session2, created2 = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    assert created1 is True
    assert created2 is False
    assert session1.id == session2.id


# 3: cannot start before passing programming
def test_cannot_start_before_programming_passed():

    db = _make_db()
    user, job, application, session_record = _seed(db, programming_passed=False)

    try:
        start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)
        assert False, "expected InterviewIntegrationV2Error"
    except InterviewIntegrationV2Error as exc:
        assert exc.status_code == 400


# 4: minimum turns -- the fallback engine never ends before min_turns
# even if every early answer looks weak/empty.
def test_interview_never_ends_before_minimum_turns():

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, _ = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    turn_count = 0

    for _ in range(INTERVIEW_V2_MIN_TURNS + 4):

        db.refresh(session)
        if session.status != "in_progress":
            break

        result = submit_answer_v2(db, session, application, session_record, "", use_llm=False)
        turn_count += 1

        if result["interview_complete"]:
            break

    assert turn_count >= INTERVIEW_V2_MIN_TURNS - 1  # -1: the turn that completes it doesn't need a NEW turn row


# 5: maximum turns -- the interview is bounded, never infinite
def test_interview_stops_at_maximum_turns():

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, _ = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    turn_count = 0
    completed = False

    for _ in range(INTERVIEW_V2_MAX_TURNS + 3):

        db.refresh(session)
        if session.status != "in_progress":
            completed = True
            break

        result = submit_answer_v2(
            db, session, application, session_record,
            "I built a REST API using Spring Boot and PostgreSQL.",
            use_llm=False,
        )
        turn_count += 1

        if result["interview_complete"]:
            completed = True
            break

    assert completed is True
    assert turn_count <= INTERVIEW_V2_MAX_TURNS


# 6: completion writes a real, non-fabricated final evaluation and
# advances application status / session stage correctly.
def test_completion_persists_final_evaluation_and_advances_pipeline():

    import json

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, _ = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    for _ in range(INTERVIEW_V2_MAX_TURNS + 3):

        db.refresh(session)
        if session.status != "in_progress":
            break

        result = submit_answer_v2(
            db, session, application, session_record,
            "I designed the schema and used indexes to speed up queries.",
            use_llm=False,
        )

        if result["interview_complete"]:
            break

    db.refresh(session)
    db.refresh(application)
    db.refresh(session_record)

    assert session.status == "completed"
    assert session.final_evaluation_json is not None

    final_eval = json.loads(session.final_evaluation_json)
    assert "competency_evaluations" in final_eval
    assert len(final_eval["competency_evaluations"]) >= 1

    assert application.status in ("interview_passed", "interview_failed")
    assert session_record.current_stage == "completed"


# 7: conversation memory -- no two turns in the same interview ask the
# EXACT same question text (the fallback engine's own no-repeat rule).
def test_no_duplicate_question_text_across_turns():

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, _ = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    for _ in range(INTERVIEW_V2_MAX_TURNS + 3):

        db.refresh(session)
        if session.status != "in_progress":
            break

        result = submit_answer_v2(
            db, session, application, session_record,
            "We used a message queue to decouple the services.",
            use_llm=False,
        )

        if result["interview_complete"]:
            break

    turns = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .all()
    )

    questions = [t.question for t in turns]
    assert len(questions) == len(set(questions)), "a question was repeated verbatim"


# 8: answering an empty/very-short answer is real, honest "none"
# evidence -- never silently upgraded to look like a real answer.
def test_weak_answer_is_recorded_honestly():

    import json

    db = _make_db()
    user, job, application, session_record = _seed(db)

    session, _ = start_interview_v2(db, session_record, application, job, user, None, None, use_llm=False)

    submit_answer_v2(db, session, application, session_record, "idk", use_llm=False)

    first_turn = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .first()
    )

    analysis = json.loads(first_turn.answer_analysis_json)
    assert analysis["strength_level"] == "none"


# 9: _get_programming_evidence() picks ONE representative code snippet --
# the candidate's own submitted code for their highest-scoring answered
# question -- so the interview can ground a follow-up in real code (Gap 2
# of the "AI interview to 100%" plan), never the whole submission history.
def test_programming_evidence_includes_highest_scoring_answered_snippet():

    import json as _json

    from backend.app.schemas.programming_question_v2 import (
        ExecutableTestCase,
        ProgrammingMainQuestionV2,
        ProgrammingQuestionSetV2,
        ProgrammingSubpartV2,
    )
    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
    from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
    from backend.app.services.assessment.interview_integration_v2 import _get_programming_evidence

    def _q(question_id, title, points=20.0):
        return ProgrammingMainQuestionV2(
            question_id=question_id, title=title, description="A task.",
            input_specification="input", output_specification="output",
            difficulty="medium", technology="general", skills_assessed=["Java"],
            allowed_languages=["java"], points=points,
            subparts=[
                ProgrammingSubpartV2(
                    subpart_id="sp1", description="basic", weight=0.5,
                    test_cases=[ExecutableTestCase(test_case_id="tc1", stdin="x", expected_stdout="y", weight=1.0, is_hidden=False)],
                ),
                ProgrammingSubpartV2(
                    subpart_id="sp2", description="edge case", weight=0.5,
                    test_cases=[ExecutableTestCase(test_case_id="tc2", stdin="z", expected_stdout="w", weight=1.0, is_hidden=True)],
                ),
            ],
        )

    question_set = ProgrammingQuestionSetV2(
        main_questions=[_q(1, "Order log summarizer"), _q(2, "Password checker"), _q(3, "f3"), _q(4, "f4"), _q(5, "f5")],
        generated_by="fallback",
    )

    db = _make_db()
    user, job, application, session_record = _seed(db, required_skills="Java")

    assessment = ProgrammingAssessmentV2(
        application_id=application.id,
        question_set_json=question_set.model_dump_json(),
        allowed_languages_json=_json.dumps(["java"]),
        generated_by="fallback", status="completed", score=90.0, total_score=100.0, passed=True,
        assigned_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
    )
    db.add(assessment)
    db.flush()

    # question 1: fully passed, contains a real TreeMap usage
    db.add(ProgrammingSubmissionV2(
        assessment_id=assessment.id, question_id=1, submission_kind="submit", language="java",
        source_code="Map<String, Integer> count = new TreeMap<>();",
        result_json=_json.dumps({"question_id": 1, "earned_points": 20.0, "max_points": 20.0, "language_used": "java", "subpart_results": [], "platform_failure": False}),
        earned_points=20.0, submitted_at=datetime.utcnow(),
    ))
    # question 2: only partially passed -- lower score, must NOT be picked over question 1
    db.add(ProgrammingSubmissionV2(
        assessment_id=assessment.id, question_id=2, submission_kind="submit", language="java",
        source_code="if (pwd.length() < min) return false;",
        result_json=_json.dumps({"question_id": 2, "earned_points": 5.0, "max_points": 20.0, "language_used": "java", "subpart_results": [], "platform_failure": False}),
        earned_points=5.0, submitted_at=datetime.utcnow(),
    ))
    db.commit()

    evidence = _get_programming_evidence(db, application)

    assert evidence is not None
    assert evidence.representative_snippet is not None
    assert evidence.representative_snippet["question_title"] == "Order log summarizer"
    assert "TreeMap" in evidence.representative_snippet["code_excerpt"]
    assert evidence.representative_snippet["language"] == "java"


def test_programming_evidence_snippet_is_none_when_nothing_was_answered():

    import json as _json

    from backend.app.schemas.programming_question_v2 import (
        ExecutableTestCase,
        ProgrammingMainQuestionV2,
        ProgrammingQuestionSetV2,
        ProgrammingSubpartV2,
    )
    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
    from backend.app.services.assessment.interview_integration_v2 import _get_programming_evidence

    def _q(question_id, title):
        return ProgrammingMainQuestionV2(
            question_id=question_id, title=title, description="A task.",
            input_specification="input", output_specification="output",
            difficulty="medium", technology="general", skills_assessed=["Java"],
            allowed_languages=["java"], points=20.0,
            subparts=[
                ProgrammingSubpartV2(
                    subpart_id="sp1", description="basic", weight=0.5,
                    test_cases=[ExecutableTestCase(test_case_id="tc1", stdin="x", expected_stdout="y", weight=1.0, is_hidden=False)],
                ),
                ProgrammingSubpartV2(
                    subpart_id="sp2", description="edge case", weight=0.5,
                    test_cases=[ExecutableTestCase(test_case_id="tc2", stdin="z", expected_stdout="w", weight=1.0, is_hidden=True)],
                ),
            ],
        )

    question_set = ProgrammingQuestionSetV2(
        main_questions=[_q(i, f"q{i}") for i in range(1, 6)], generated_by="fallback",
    )

    db = _make_db()
    user, job, application, session_record = _seed(db, required_skills="Java")

    assessment = ProgrammingAssessmentV2(
        application_id=application.id,
        question_set_json=question_set.model_dump_json(),
        allowed_languages_json=_json.dumps(["java"]),
        generated_by="fallback", status="completed", score=0.0, total_score=100.0, passed=False,
        assigned_at=datetime.utcnow(), expires_at=datetime.utcnow() + timedelta(hours=1),
        started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
    )
    db.add(assessment)
    db.commit()

    evidence = _get_programming_evidence(db, application)

    assert evidence is not None
    assert evidence.representative_snippet is None


# 10: regression test for the bug this fix closed -- an SJT (non-technical)
# candidate could never actually START the AI interview before this fix, even
# though the assignment existed: application.status was "sjt_passed" (never
# "programming_passed"), _programming_passed() only ever checked the two
# programming tables, and session_record.current_stage was never advanced
# past "aptitude" by the SJT start flow. All three gates are exercised here.
def _seed_sjt(db, passed=True):

    user = User(
        full_name="SJT Candidate", username="testcand_sjt", email="candsjt@example.com",
        hashed_password="x", role="user",
    )
    db.add(user)
    db.flush()

    job = Job(title="Digital Marketing Executive", company="TestCo", required_skills="SEO, Content Writing")
    db.add(job)
    db.flush()

    application = JobApplication(job_id=job.id, user_id=user.id, status="sjt_passed" if passed else "aptitude_passed")
    db.add(application)
    db.flush()

    # Set by start_sjt_assessment() in sjt_integration.py (the fix under test).
    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)

    db.add(AptitudeTest(
        application_id=application.id, questions="[]", total_questions=10,
        passed=True, status="completed", score=9,
    ))

    if passed:

        import json as _json

        questions = [
            {"id": 1, "category": "communication", "question": "q1", "options": ["a", "b"], "correct_answer": "a"},
            {"id": 2, "category": "teamwork", "question": "q2", "options": ["a", "b"], "correct_answer": "b"},
        ]
        answers = [
            {"question_id": 1, "selected_answer": "a"},
            {"question_id": 2, "selected_answer": "a"},   # wrong -> teamwork is a weakness
        ]

        db.add(SituationalJudgmentTest(
            application_id=application.id, questions=_json.dumps(questions), answers=_json.dumps(answers),
            total_questions=2, score=1, passed=True, status="completed",
            started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        ))

        create_assignment(db, application.id, "ai_interview", assigned_by_id=user.id, assigned_by_type="system")

    db.commit()
    db.refresh(application)
    db.refresh(session_record)

    return user, job, application, session_record


def test_sjt_candidate_can_start_the_interview_after_passing():

    db = _make_db()
    user, job, application, session_record = _seed_sjt(db)

    session, created = start_interview_v2(
        db, session_record, application, job, user, profile=None, resume_analysis=None, use_llm=False,
    )

    assert created is True
    assert session.status == "in_progress"


def test_sjt_candidate_cannot_start_before_passing():

    db = _make_db()
    user, job, application, session_record = _seed_sjt(db, passed=False)

    try:
        start_interview_v2(
            db, session_record, application, job, user, profile=None, resume_analysis=None, use_llm=False,
        )
        assert False, "expected InterviewIntegrationV2Error"
    except InterviewIntegrationV2Error:
        pass


def test_sjt_evidence_feeds_the_interview_blueprint():

    from backend.app.services.assessment.interview_integration_v2 import _get_sjt_evidence

    db = _make_db()
    user, job, application, session_record = _seed_sjt(db)

    evidence = _get_sjt_evidence(db, application)

    assert evidence is not None
    assert "communication" in evidence.strengths
    assert "teamwork" in evidence.weaknesses

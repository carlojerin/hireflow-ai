"""
Task 6A: the single authoritative stage-progression service.
In-memory DB, no network. Proves: derived-from-results stage states,
automatic (system) assignments, idempotency, no stage skipping, and
terminal failure states.
"""

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.database.database import Base
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
from backend.app.models.user import User
from backend.app.services.assessment.stage_progression_service import advance_pipeline, determine_next_stage


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _app(db, status="shortlisted"):
    user = User(full_name="T", username="tsp", email="tsp@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(title="Java Backend Developer", company="Co", required_skills="Java, SQL")
    db.add(job); db.flush()
    app = JobApplication(job_id=job.id, user_id=user.id, status=status)
    db.add(app); db.commit()
    return app


def _session(db, app, intro="completed", stage="aptitude"):
    s = AssessmentSession(application_id=app.id, current_stage=stage, self_intro_status=intro)
    db.add(s); db.commit()
    return s


def _apt(db, app, passed, status="completed"):
    db.add(AptitudeTest(application_id=app.id, questions="[]", total_questions=10, passed=passed, status=status, score=9 if passed else 2))
    db.commit()


def _prog(db, app, passed, status="completed"):
    now = datetime.utcnow()
    db.add(ProgrammingTest(application_id=app.id, questions="[]", total_questions=5, passed=passed, status=status, score=90 if passed else 10,
                           total_score=100, assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now))
    db.commit()


def _interview(db, app, status="completed"):
    now = datetime.utcnow()
    db.add(AIInterviewSessionV2(application_id=app.id, status=status, current_turn=1, blueprint_json="{}", conversation_summary="",
                                generated_by="groq", assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now,
                                created_at=now, updated_at=now))
    db.commit()


def _by(view):
    return {s["stage"]: s["status"] for s in view["stages"]}


def _assignments(db, app):
    return sorted((a.assessment_type, a.assigned_by_type) for a in db.query(AssessmentAssignment).filter(AssessmentAssignment.application_id == app.id))


def _non_technical_app(db, status="shortlisted"):
    user = User(full_name="T", username="tspnt", email="tspnt@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(
        title="Digital Marketing Executive", company="Co",
        required_skills="SEO, Content Writing, Social Media Management",
        description="Plan and run marketing campaigns across social channels.",
    )
    db.add(job); db.flush()
    app = JobApplication(job_id=job.id, user_id=user.id, status=status)
    db.add(app); db.commit()
    return app


def test_fresh_shortlisted_candidate_starts_at_self_intro_everything_else_locked():
    db = _db(); app = _app(db)
    v = determine_next_stage(db, app)
    assert _by(v) == {"self_intro": "current", "aptitude": "locked", "programming": "locked", "ai_interview": "locked"}
    assert v["current_stage"] == "self_intro" and v["outcome"] == "in_progress"
    assert v["next_action"]["url"].startswith("assessment.html?application_id=")


def test_self_intro_done_unlocks_aptitude_automatically():
    db = _db(); app = _app(db); _session(db, app)
    v = determine_next_stage(db, app)
    assert _by(v)["self_intro"] == "completed" and v["current_stage"] == "aptitude" and v["next_stage"] == "programming"
    assert _assignments(db, app) == []


def test_aptitude_pass_auto_assigns_programming_once_by_system():
    db = _db(); app = _app(db, "aptitude_passed"); _session(db, app, stage="programming"); _apt(db, app, True)
    v = advance_pipeline(db, app)
    assert v["current_stage"] == "programming" and v["next_action"]["url"].startswith("programming-v3.html")
    advance_pipeline(db, app); advance_pipeline(db, app)
    assert _assignments(db, app) == [("programming", "system")]


def _sjt(db, app, passed, status="completed"):
    now = datetime.utcnow()
    db.add(SituationalJudgmentTest(
        application_id=app.id, questions="[]", total_questions=10, passed=passed, status=status,
        score=9 if passed else 2, assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now,
    ))
    db.commit()


def test_non_technical_role_gets_an_sjt_assignment_instead_of_programming():
    # Digital Marketing — a non-technical role, classified by
    # role_classification.py's shared heuristic (same one the AI
    # interview blueprint uses for role_shape). It gets a REAL
    # Situational Judgment Test assignment in place of programming
    # -- not a silent skip straight to the interview.
    db = _db(); app = _non_technical_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True)

    v = advance_pipeline(db, app)

    assert _by(v)["programming"] == "current"  # the stage-3 slot, still awaiting the SJT attempt
    assert v["current_stage"] == "programming"
    assert _assignments(db, app) == [("situational_judgment", "system")]

    advance_pipeline(db, app); advance_pipeline(db, app)
    assert _assignments(db, app) == [("situational_judgment", "system")]  # never duplicated


def test_non_technical_role_sjt_pass_auto_assigns_interview():
    db = _db(); app = _non_technical_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True); _sjt(db, app, True)

    v = advance_pipeline(db, app)

    assert _by(v)["programming"] == "completed"
    assert v["current_stage"] == "ai_interview"
    assert v["next_action"]["url"].startswith("ai-interview-v2.html")
    assert _assignments(db, app) == [("ai_interview", "system"), ("situational_judgment", "system")]


def test_non_technical_role_sjt_fail_still_moves_on_to_the_interview():
    db = _db(); app = _non_technical_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True); _sjt(db, app, False)

    v = advance_pipeline(db, app)

    assert v["outcome"] == "in_progress" and v["failed_stage"] is None and v["current_stage"] == "ai_interview"
    assert _by(v)["programming"] == "completed" and _by(v)["ai_interview"] == "current"
    assert ("ai_interview", "system") in _assignments(db, app)


def _domain_app(db, status="shortlisted"):
    # Salesforce Developer: technical (role_classification.py), but
    # no executable language -- the technical_domain path, not
    # programming or situational_judgment.
    user = User(full_name="T", username="tspdomain", email="tspdomain@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(
        title="Salesforce Developer", company="Co",
        required_skills="Salesforce, Apex, Lightning",
        description="Build and configure solutions on the platform.",
    )
    db.add(job); db.flush()
    app = JobApplication(job_id=job.id, user_id=user.id, status=status)
    db.add(app); db.commit()
    return app


def _tda(db, app, passed, status="completed"):
    now = datetime.utcnow()
    db.add(TechnicalDomainAssessment(
        application_id=app.id, domain="salesforce", questions="[]", total_questions=10, passed=passed, status=status,
        score=9 if passed else 2, assigned_at=now, expires_at=now + timedelta(hours=1), started_at=now,
    ))
    db.commit()


def test_technical_role_with_no_executable_language_gets_a_domain_assignment():
    # Salesforce Developer -- technical, but Apex isn't a language
    # JobPilot's Docker sandbox can run; gets a REAL Technical Domain
    # Assessment assignment instead of being blocked with no test.
    db = _db(); app = _domain_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True)

    v = advance_pipeline(db, app)

    assert _by(v)["programming"] == "current"  # the stage-3 slot, still awaiting the TDA attempt
    assert v["current_stage"] == "programming"
    assert _assignments(db, app) == [("technical_domain", "system")]
    assert not v.get("blocked")

    advance_pipeline(db, app); advance_pipeline(db, app)
    assert _assignments(db, app) == [("technical_domain", "system")]  # never duplicated


def test_domain_assessment_pass_auto_assigns_interview():
    db = _db(); app = _domain_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True); _tda(db, app, True)

    v = advance_pipeline(db, app)

    assert _by(v)["programming"] == "completed"
    assert v["current_stage"] == "ai_interview"
    assert v["next_action"]["url"].startswith("ai-interview-v2.html")
    assert _assignments(db, app) == [("ai_interview", "system"), ("technical_domain", "system")]


def test_domain_assessment_fail_still_moves_on_to_the_interview():
    db = _db(); app = _domain_app(db, "aptitude_passed")
    _session(db, app, stage="programming"); _apt(db, app, True); _tda(db, app, False)

    v = advance_pipeline(db, app)

    assert v["outcome"] == "in_progress" and v["current_stage"] == "ai_interview"
    assert ("ai_interview", "system") in _assignments(db, app)


def test_aptitude_fail_still_unlocks_the_next_assessment():
    db = _db(); app = _app(db, "aptitude_failed"); _session(db, app); _apt(db, app, False)
    v = advance_pipeline(db, app)
    assert v["outcome"] == "in_progress" and v["failed_stage"] is None and v["current_stage"] == "programming"
    assert _by(v)["aptitude"] == "completed" and ("programming", "system") in _assignments(db, app)
    assert next(st for st in v["stages"] if st["stage"] == "aptitude")["passed"] is False      # HR still sees it was not passed


def test_programming_pass_auto_assigns_interview_once_by_system():
    db = _db(); app = _app(db, "programming_passed"); _session(db, app, stage="ai_interview"); _apt(db, app, True); _prog(db, app, True)
    v = advance_pipeline(db, app); advance_pipeline(db, app)
    assert v["current_stage"] == "ai_interview" and v["next_action"]["url"].startswith("ai-interview-v2.html")
    assert _assignments(db, app) == [("ai_interview", "system"), ("programming", "system")]


def test_programming_fail_still_moves_on_to_the_interview():
    db = _db(); app = _app(db, "programming_failed"); _session(db, app); _apt(db, app, True); _prog(db, app, False)
    v = advance_pipeline(db, app)
    assert v["outcome"] == "in_progress" and v["current_stage"] == "ai_interview"
    assert ("ai_interview", "system") in _assignments(db, app)


def test_stages_cannot_be_skipped():
    db = _db(); app = _app(db); _session(db, app)
    _prog(db, app, True)          # a programming pass with NO aptitude result must not unlock anything
    v = advance_pipeline(db, app)
    assert v["current_stage"] == "aptitude" and _by(v)["programming"] == "locked"
    assert _assignments(db, app) == []


def test_timed_out_stage_counts_as_finished_and_the_candidate_moves_on():
    db = _db(); app = _app(db, "aptitude_expired"); _session(db, app); _apt(db, app, None, status="expired")
    v = determine_next_stage(db, app)
    assert v["failed_stage"] is None and v["current_stage"] == "programming"


def test_an_integrity_termination_still_ends_the_pipeline():
    db = _db(); app = _app(db, "aptitude_terminated"); _session(db, app); _apt(db, app, False, status="terminated")
    v = determine_next_stage(db, app)
    assert v["outcome"] == "failed" and v["failed_stage"] == "aptitude" and _by(v)["programming"] == "locked"


def test_interview_in_progress_then_completed_passed():
    db = _db(); app = _app(db, "interview_pending"); _session(db, app, stage="ai_interview"); _apt(db, app, True); _prog(db, app, True)
    _interview(db, app, status="in_progress")
    assert determine_next_stage(db, app)["current_stage"] == "ai_interview"
    s = db.query(AIInterviewSessionV2).first(); s.status = "completed"; app.status = "interview_passed"; db.commit()
    v = determine_next_stage(db, app)
    assert v["outcome"] == "completed" and v["current_stage"] is None and v["next_action"] is None
    assert all(x["status"] == "completed" for x in v["stages"])


def test_interview_completed_but_not_passed_still_completes_the_pipeline():
    db = _db(); app = _app(db, "interview_failed"); _session(db, app, stage="completed"); _apt(db, app, True); _prog(db, app, True)
    _interview(db, app, status="completed")
    v = determine_next_stage(db, app)
    assert v["outcome"] == "completed" and v["failed_stage"] is None and v["current_stage"] is None


def test_advance_never_creates_a_duplicate_when_hr_already_assigned_manually():
    db = _db(); app = _app(db, "aptitude_passed"); _session(db, app, stage="programming"); _apt(db, app, True)
    from backend.app.services.assessment.assignment_service import create_assignment
    create_assignment(db, app.id, "programming", assigned_by_id=1, assigned_by_type="hr")
    advance_pipeline(db, app)
    assert _assignments(db, app) == [("programming", "hr")]


def test_advance_pipeline_never_raises_even_if_stage_state_cannot_be_read():
    db = _db(); app = _app(db, "aptitude_passed"); _session(db, app, stage="programming"); _apt(db, app, True)
    db.execute(__import__("sqlalchemy").text("drop table ai_interview_sessions_v2"))
    assert advance_pipeline(db, app) is None      # logged + rolled back, the caller's committed result is untouched

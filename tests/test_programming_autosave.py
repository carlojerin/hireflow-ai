"""
Programming assessment (V2) code autosave: a candidate's typed-but-
not-yet-run/submitted code survives a refresh/reconnect instead of
being lost while the exam clock keeps running -- same class of fix
already applied to the aptitude stage.

Calls the route function directly (not through TestClient) --
in-memory SQLite connections can't cross TestClient's own request
thread, the same reason test_programming_submit_race_guard.py calls
the service layer directly instead of going through HTTP.
"""

from backend.app.api.routes import assessment_sessions
from backend.app.api.routes.assessment_sessions import (
    AutosaveCodeV2Request,
    autosave_code_v2_endpoint,
)
from backend.app.services.assessment.programming_integration_v2 import (
    candidate_view_v2,
    start_programming_assessment_v2,
)

from test_programming_v2_live_integration import _make_db, _seed

assessment_sessions.require_csrf = lambda request: None


def test_autosave_persists_a_draft_and_is_returned_in_candidate_view():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, created = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )
    assert created is True

    question_id = candidate_view_v2(assessment)["questions"][0]["question_id"]

    result = autosave_code_v2_endpoint(
        application.id,
        AutosaveCodeV2Request(question_id=question_id, language="python", source_code="print(1)"),
        request=None,
        current_user=user,
        db=db,
    )
    assert result == {"saved": True}

    db.refresh(assessment)
    view = candidate_view_v2(assessment)
    assert view["draft_code"][str(question_id)] == {
        "language": "python", "source_code": "print(1)",
    }


def test_autosave_overwrites_only_its_own_question():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )

    questions = candidate_view_v2(assessment)["questions"]
    assert len(questions) >= 2
    q1, q2 = questions[0]["question_id"], questions[1]["question_id"]

    autosave_code_v2_endpoint(
        application.id,
        AutosaveCodeV2Request(question_id=q1, language="python", source_code="a = 1"),
        request=None, current_user=user, db=db,
    )
    autosave_code_v2_endpoint(
        application.id,
        AutosaveCodeV2Request(question_id=q2, language="java", source_code="b = 2"),
        request=None, current_user=user, db=db,
    )

    db.refresh(assessment)
    draft_code = candidate_view_v2(assessment)["draft_code"]
    assert draft_code[str(q1)]["source_code"] == "a = 1"
    assert draft_code[str(q2)]["source_code"] == "b = 2"


def test_autosave_on_a_completed_assessment_is_a_no_op_not_an_error():
    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, _ = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )
    assessment.status = "completed"
    db.commit()

    question_id = candidate_view_v2(assessment)["questions"][0]["question_id"]

    result = autosave_code_v2_endpoint(
        application.id,
        AutosaveCodeV2Request(question_id=question_id, language="python", source_code="print(1)"),
        request=None, current_user=user, db=db,
    )
    assert result == {"saved": False}

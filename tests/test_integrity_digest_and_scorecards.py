"""The HR dashboard filters (score, integrity) and the report's integrity breakdown: what the candidate did, how many strikes, in which stage."""

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace as N

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_practical_assessments as PA

import backend.app.main  # noqa: F401
from backend.app.api.routes.company_applications import _scorecards_by_application
from backend.app.api.routes.hr_assessment_review import _integrity_review_data
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.services.assessment.integrity_digest import digest, overall_percentage
from backend.app.services.assessment.sjt_integration import start_sjt_assessment, submit_sjt_assessment


def ev(event_type, stage="aptitude", counted=True):
    return N(event_type=event_type, assessment_type=stage, counted_as_violation=counted)


def test_nothing_recorded_is_clean():
    d = digest([ev("fullscreen_entered"), ev("window_focus"), ev("camera_connected"), ev("face_present")])
    assert d["risk"] == "clean" and d["strikes"] == 0 and d["flags"] == 0 and d["what_happened"] == []        # harmless events never inflate the count


def test_strikes_and_flags_are_counted_separately_and_a_cooldown_repeat_is_not_a_second_strike():
    d = digest([ev("fullscreen_exited"), ev("fullscreen_exited", counted=False), ev("tab_hidden"), ev("paste_detected")])
    assert d["strikes"] == 2 and d["flags"] == 1 and d["risk"] == "low"
    assert {w["label"]: w["count"] for w in d["what_happened"]} == {"Left fullscreen": 1, "Switched to another tab": 1, "Pasted text": 1}


@pytest.mark.parametrize("events,risk", [
    ([ev("fullscreen_exited")] * 2, "low"),
    ([ev("fullscreen_exited")] * 3, "medium"),
    ([ev("paste_detected")] * 3, "medium"),
    ([ev("fullscreen_exited")] * 5, "high"),
    ([ev("mobile_phone_detected")], "high"),                      # a serious signal is high on its own
    ([ev("multiple_faces_detected", "ai_interview")], "high"),
    ([ev("assessment_auto_terminated"), ev("tab_hidden")], "high"),
])
def test_the_risk_level_follows_the_published_rules(events, risk):
    assert digest(events)["risk"] == risk


def test_the_breakdown_names_the_stage_and_a_termination():
    d = digest([ev("tab_hidden", "aptitude"), ev("tab_hidden", "aptitude"), ev("face_missing", "ai_interview"), ev("assessment_auto_terminated", "ai_interview")])
    by_stage = {s["stage"]: s for s in d["by_stage"]}
    assert by_stage["aptitude"]["strikes"] == 2 and by_stage["aptitude"]["stage_label"] == "Quiz"
    assert by_stage["ai_interview"]["strikes"] == 1 and by_stage["ai_interview"]["terminated"] is True and d["terminated"] is True


def test_overall_percentage_is_the_plain_average_of_the_scored_stages():
    assert overall_percentage([90, 70]) == 80.0 and overall_percentage([None, 65]) == 65.0 and overall_percentage([None, None]) is None


def test_the_hr_list_gets_a_score_and_integrity_per_candidate_and_the_report_gets_the_same_digest(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_CATALOGUE", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")

    db = PA._db()
    user, job, application, session_record = PA._seed(db, "DevOps Engineer", "")

    assessment, _ = start_sjt_assessment(db, session_record, application)
    answers = [{"question_id": q["id"], "selected_answer": str(q["correct_answer"])} for q in json.loads(assessment.questions)]
    submit_sjt_assessment(db, assessment, application, answers)

    for event_type, counted in (("fullscreen_exited", True), ("tab_hidden", True), ("paste_detected", False), ("fullscreen_entered", False)):
        db.add(AssessmentIntegrityEvent(application_id=application.id, assessment_type="situational_judgment", event_type=event_type,
                                        severity="info", counted_as_violation=counted, occurred_at=datetime.utcnow()))
    db.commit()

    card = _scorecards_by_application(db, [application.id])[application.id]
    assert card["overall_percentage"] == 100.0 and card["stage3_percentage"] == 100 and card["aptitude_percentage"] is None
    assert card["integrity"]["strikes"] == 2 and card["integrity"]["flags"] == 1 and card["integrity"]["risk"] == "low"

    report = _integrity_review_data(db, application)
    assert report["digest"] == card["integrity"]                                                       # the dashboard and the report can never disagree

    assert _scorecards_by_application(db, []) == {}

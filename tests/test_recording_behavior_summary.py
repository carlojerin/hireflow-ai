"""The 'observed behaviour' paragraph under each recorded video: written only from recorded signals, never a judgement of emotions,
confidence or personality; cached; falls back to a plain paragraph. In-memory DB, no real model."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.database.database import Base
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.recording_behavior_summary import RecordingBehaviorSummary
from backend.app.services.assessment import recording_behavior_summary as rbs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = SimpleNamespace(id=10)


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def _recording(db, stage, duration=300.0, status="uploaded"):
    row = AssessmentVideoRecording(application_id=10, stage=stage, file_path=f"10/{stage}/x.webm", mime_type="video/webm", file_size=1000,
                                   duration_seconds=duration, status=status, created_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def _event(db, assessment_type, event_type, n=1):
    for _ in range(n):
        db.add(AssessmentIntegrityEvent(application_id=10, assessment_type=assessment_type, event_type=event_type, severity="info", counted_as_violation=False, occurred_at=datetime.utcnow()))
    db.flush()


class Model:
    """Stands in for the language model."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def generate_json(self, prompt, system=None, **kwargs):
        self.prompts.append((prompt, system))
        return self.reply


def _use_model(monkeypatch, reply):
    model = Model(reply)
    monkeypatch.setattr(rbs, "HostedChatProvider", lambda *a, **k: model)
    return model


# ---------------------------------------------------------------- the facts

def test_facts_count_only_this_stages_signals_and_list_them_in_plain_words():
    db = _db()
    recording = _recording(db, "aptitude", 584)
    _event(db, "aptitude", "looking_away", 3)
    _event(db, "aptitude", "tab_hidden", 2)
    _event(db, "aptitude", "fullscreen_entered", 1)                  # baseline information: not a behaviour signal
    _event(db, "programming", "mobile_phone_detected", 4)            # another stage: not counted here
    db.commit()

    facts = rbs.gather_facts(db, recording, APP)

    assert {s["label"]: s["count"] for s in facts["signals"]} == {
        "The head turned away from the screen for several seconds": 3, "The test tab was left": 2,
    }
    text = rbs.facts_paragraph(facts)
    assert "camera recording of the test, 9 min 44 s long" in text and "(3×)" in text and "(2×)" in text
    assert "phone" not in text


def test_a_clean_stage_and_a_clean_self_introduction_both_say_no_signals_were_recorded():
    db = _db()
    clean = rbs.facts_paragraph(rbs.gather_facts(db, _recording(db, "aptitude", 120), APP))
    intro = rbs.facts_paragraph(rbs.gather_facts(db, _recording(db, "self_intro", 40), APP))

    assert "No attention or conduct signals were recorded" in clean
    assert "No attention or conduct signals were recorded" in intro


def test_speech_measurements_come_from_the_self_introduction_and_the_interview_answers():
    db = _db()
    db.add(AssessmentSession(application_id=10, current_stage="aptitude",
                             self_intro_text="Hi I am Asha um I have three years of experience and I built an API you know with FastAPI"))
    intro = _recording(db, "self_intro", 20)
    db.commit()

    speech = rbs.gather_facts(db, intro, APP)["speech"]
    assert speech["answers"] == 1 and speech["words"] >= 18 and speech["fillers"] >= 2 and speech["wpm"] is not None

    session = AIInterviewSessionV2(application_id=10, status="completed", blueprint_json="{}", conversation_summary="", assigned_at=datetime.utcnow(), expires_at=datetime.utcnow(), started_at=datetime.utcnow())
    db.add(session)
    db.flush()
    for n, answer in enumerate(["I built the billing service and cut latency by 40 percent", "um I think maybe we used Redis"], start=1):
        db.add(AIInterviewTurnV2(session_id=session.id, turn_number=n, question="q", question_type="EXPERIENCE", competency="x", difficulty="basic",
                                 candidate_answer=answer, asked_at=datetime.utcnow(), answered_at=datetime.utcnow()))
    interview = _recording(db, "ai_interview", 600)
    db.commit()

    spoken = rbs.gather_facts(db, interview, APP)
    assert spoken["spoken_turns"] == 2 and spoken["speech"]["answers"] == 2 and spoken["speech"]["wpm"] is None      # pace needs one timed answer
    assert "Speaking: " in rbs.facts_paragraph(spoken)


def test_a_screen_recording_and_a_partial_recording_are_described_as_such():
    db = _db()
    screen = rbs.facts_paragraph(rbs.gather_facts(db, _recording(db, "aptitude_screen", 300), APP))
    partial = rbs.facts_paragraph(rbs.gather_facts(db, _recording(db, "programming", 90, status="partial"), APP))

    assert "screen recording of the test" in screen
    assert "covers only part of the stage" in partial


# ---------------------------------------------------------------- the model's paragraph is trusted only when it keeps to the facts

def test_a_faithful_model_paragraph_is_used_and_cached_so_the_second_open_costs_nothing(monkeypatch):
    db = _db()
    recording = _recording(db, "aptitude", 300)
    _event(db, "aptitude", "looking_away", 3)
    db.commit()

    model = _use_model(monkeypatch, "The camera recording lasted 5 min 00 s. The head turned away from the screen for several seconds 3 times.")
    first = rbs.get_or_create_behavior_summary(db, recording, APP)
    second = rbs.get_or_create_behavior_summary(db, recording, APP)

    assert first["source"] == "ai" and "5 min 00 s" in first["summary"] and first["cached"] is False
    assert second["cached"] is True and second["summary"] == first["summary"] and len(model.prompts) == 1
    assert "do NOT infer or mention emotions, confidence" in model.prompts[0][1]
    assert db.query(RecordingBehaviorSummary).count() == 1


@pytest.mark.parametrize("bad_reply", [
    "The candidate looked nervous and lacked confidence during the test.",                      # reads emotion / confidence
    "Their personality seems evasive and they may be dishonest.",
    "I recommend hiring this candidate.",
    "The camera recording lasted 5 min 00 s. The head turned away 47 times.",                    # a number that is not in the facts
    "",
])
def test_a_model_that_judges_the_person_or_invents_numbers_is_not_shown(monkeypatch, bad_reply):
    db = _db()
    recording = _recording(db, "aptitude", 300)
    _event(db, "aptitude", "looking_away", 3)
    db.commit()

    _use_model(monkeypatch, bad_reply)
    result = rbs.get_or_create_behavior_summary(db, recording, APP)

    assert result["source"] == "facts"                                                           # the plain, factual paragraph instead
    assert "nervous" not in result["summary"].lower() and "47" not in result["summary"]
    assert "(3×)" in result["summary"]


def test_an_unavailable_model_falls_back_to_the_plain_paragraph(monkeypatch):
    db = _db()
    recording = _recording(db, "aptitude", 300)

    def broken(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(rbs, "HostedChatProvider", broken)
    result = rbs.get_or_create_behavior_summary(db, recording, APP)

    assert result["source"] == "facts" and "camera recording of the test" in result["summary"]
    assert rbs.get_or_create_behavior_summary(db, recording, APP, use_model=False)["cached"] is True


# ---------------------------------------------------------------- wiring

def test_the_route_and_the_page_show_the_paragraph_under_every_video_with_an_honest_note():
    with open(os.path.join(ROOT, "backend", "app", "api", "routes", "hr_assessment_review.py"), encoding="utf-8") as f:
        py = f.read()
    with open(os.path.join(ROOT, "frontend", "js", "company-candidate-report.js"), encoding="utf-8") as f:
        js = f.read()

    assert '"/{company_id}/{application_id}/recordings/{recording_id}/behavior"' in py
    assert "AssessmentVideoRecording.application_id == application.id" in py                       # only this candidate's recordings
    assert 'id="behaviorBox"' in js and js.count("showBehavior(") >= 3                             # first video, and every video selected after
    assert "It does not judge emotions, confidence or personality" in js

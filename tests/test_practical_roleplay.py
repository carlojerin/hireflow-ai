"""
Practical voice role-play: the AI plays a customer / prospect / client, the candidate is the agent. The scenario's private facts and rubric stay on
the server, the call runs on the live interview engine, and an AI grades the transcript. In-memory database, no network (the AI and the live
connection are fakes).
"""

import asyncio
import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_practical_assessments as PA
import test_realtime_interview_v2 as base

import backend.app.main  # noqa: F401
from backend.app.api.deps import get_current_user
from backend.app.api.routes import roleplay_v2 as route
from backend.app.database.database import Base, get_db
from backend.app.models.practical_roleplay import PracticalRoleplay
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.services.assessment import realtime_interview_v2 as rt
from backend.app.services.assessment.sjt_integration import candidate_view, start_sjt_assessment, submit_sjt_assessment
from backend.app.services.sjt import practical_roleplay as rp
from backend.app.services.sjt import sjt_question_service


@pytest.fixture(autouse=True)
def roleplay_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _fallback_scenario(monkeypatch, voice=True):
    monkeypatch.setattr(rp, "voice_available", lambda: voice)
    monkeypatch.setattr(rp, "generate_roleplay_scenario", lambda job, family=None, focus=None: (rp.fallback_scenario("support"), "fallback"))


# ------------------------------------------------------------------ planning
def test_support_and_sales_roles_get_a_call_simulation():
    for title in ("Customer Support Executive", "Sales Development Representative", "Receptionist"):
        stage = PA.planner.rules_plan(PA._job(title))["stages"][1]
        assert stage["kind"] == "practical" and stage["environment"] == "roleplay"


def test_the_switch_turns_the_call_back_into_a_written_task(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "off")
    assert PA.planner.rules_plan(PA._job("Customer Support Executive"))["stages"][1]["environment"] == "written"


def test_the_curated_scenarios_are_complete():
    for family in ("support", "sales", "client", "hr", "reception"):
        s = rp.fallback_scenario(family)
        p = s["persona"]
        assert len(s["candidate_brief"]) > 100 and p["name"] and p["opening_line"] and len(s["rubric"]) >= 3 and p["hidden"]


def test_the_ai_scenario_is_validated_and_a_bad_one_falls_back(monkeypatch):
    good = rp.fallback_scenario("support")
    assert rp._normalise_scenario(good, "support")["title"] == good["title"]
    assert rp._normalise_scenario({"title": "x", "persona": {}}, "support") is None

    monkeypatch.setattr(rp, "_provider", lambda timeout: (_ for _ in ()).throw(RuntimeError("down")))
    scenario, source = rp.generate_roleplay_scenario(PA._job("Customer Support Executive"), family="support")
    assert source == "fallback" and scenario["persona"]["name"]


def test_the_character_instruction_hides_nothing_it_needs_and_never_carries_the_rubric():
    scenario = rp.fallback_scenario("support")
    text = rp.build_instruction(scenario)
    assert scenario["persona"]["hidden"] in text and scenario["persona"]["situation"] in text
    assert "Never mention being an AI" in text and "PRIVATE NOTE" in text
    assert scenario["rubric"][0]["criterion"] not in text                                   # the grading rubric is not given to the character


# ------------------------------------------------------------------ the practical test
def test_the_call_is_built_and_only_the_candidates_brief_reaches_the_browser(monkeypatch):
    _fallback_scenario(monkeypatch)
    db = PA._db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert assessment.kind == "practical" and view["practical"]["environment"] == "roleplay"
    assert view["questions"][0]["type"] == "roleplay"
    assert "company rules" in view["practical"]["brief"].lower() or "rules" in view["practical"]["brief"].lower()

    shown = json.dumps(view)
    scenario = rp.fallback_scenario("support")
    for secret in (scenario["persona"]["hidden"], scenario["persona"]["knows"], scenario["rubric"][0]["criterion"], "Order number 48213"):
        assert secret not in shown

    row = db.query(PracticalRoleplay).filter(PracticalRoleplay.application_id == application.id).one()
    assert scenario["persona"]["hidden"] in json.loads(row.scenario_json)["persona"]["hidden"] and row.status == "created"


def test_without_the_voice_stack_the_candidate_gets_a_written_task_instead(monkeypatch):
    monkeypatch.setattr(rp, "voice_available", lambda: False)
    from backend.app.services.sjt import practical_written_tasks as written
    monkeypatch.setattr(written, "generate_written_tasks", lambda job, focus=None, family=None: (written.fallback_written_tasks("support"), "fallback"))

    db = PA._db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")
    assessment, _ = start_sjt_assessment(db, session_record, application)

    assert assessment.kind == "practical" and json.loads(assessment.payload_json)["environment"] == "written"
    assert db.query(PracticalRoleplay).count() == 0


# ------------------------------------------------------------------ the work adapter + grading
def _factory_with_row():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)

    db = factory()
    from backend.app.models.job import Job
    from backend.app.models.job_application import JobApplication
    from backend.app.models.user import User

    user = User(full_name="T", username="rp1", email="rp1@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(title="Support", company="Co", description="x")
    db.add(job); db.flush()
    application = JobApplication(job_id=job.id, user_id=user.id, status="aptitude_passed")
    db.add(application); db.flush()
    db.add(PracticalRoleplay(application_id=application.id, scenario_json=json.dumps(rp.fallback_scenario("support")), status="created"))
    db.commit()
    app_id = application.id
    db.close()

    return factory, app_id


def test_the_work_adapter_plans_the_call_and_keeps_the_transcript():
    factory, app_id = _factory_with_row()
    work = rp.RolePlayWork(app_id, session_factory=factory)

    plan = work.load_plan()
    assert plan.kind == "roleplay" and "Mr. Kumar" in plan.instruction and plan.opening_note.startswith("PRIVATE NOTE: The call connects")

    work.record_ai_question("Hello? Yes, finally. I ordered a blender nine days ago.")
    first = work.record_answer("Good morning, thank you for calling. May I have your name and order number please?")
    assert first["interview_complete"] is False and "Stay in character" in first["note"]

    db = factory()
    row = db.query(PracticalRoleplay).one()
    transcript = json.loads(row.transcript_json)
    assert [t["role"] for t in transcript] == ["persona", "agent"] and row.status == "in_call"
    db.close()


def test_the_call_ends_on_a_goodbye_after_enough_turns_on_the_turn_limit_and_on_silence():
    factory, app_id = _factory_with_row()
    work = rp.RolePlayWork(app_id, session_factory=factory)
    work.load_plan()

    assert work.record_answer("Goodbye, thank you for calling!")["interview_complete"] is False          # too early to count as the end
    work.record_answer("I understand, let me check that for you."); work.record_answer("I will refund the express fee today.")
    done = work.record_answer("Is there anything else? Have a great day, goodbye!")
    assert done["interview_complete"] is True and "ONE brief, natural closing line" in done["note"]

    factory2, app2 = _factory_with_row()
    work2 = rp.RolePlayWork(app2, session_factory=factory2)
    work2.load_plan()
    for i in range(rp.MAX_AGENT_TURNS - 1):
        assert work2.record_answer(f"Let me help with that, part {i}.")["interview_complete"] is False
    assert work2.record_answer("And that is all from me.")["interview_complete"] is True

    factory3, app3 = _factory_with_row()
    work3 = rp.RolePlayWork(app3, session_factory=factory3)
    work3.load_plan()
    assert work3.record_answer("(The agent stayed silent.)")["interview_complete"] is True


def test_a_finished_call_cannot_be_started_again():
    factory, app_id = _factory_with_row()
    work = rp.RolePlayWork(app_id, session_factory=factory)
    work.load_plan()
    work.record_answer("(The agent stayed silent.)")

    with pytest.raises(rt.LiveConnectionError):
        rp.RolePlayWork(app_id, session_factory=factory).load_plan()


def test_the_call_is_graded_against_the_rubric_and_the_transcript_is_kept(monkeypatch):
    scenario = rp.fallback_scenario("support")
    transcript = [{"role": "persona", "text": "Hello, where is my order?"}, {"role": "agent", "text": "I am very sorry, may I have your order number?"}]

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            assert "AGENT: I am very sorry" in json.loads(prompt)["transcript"]
            return json.dumps({"summary": "Warm and clear.", "criteria": [{"criterion": c["criterion"], "score": 9, "comment": "good"} for c in scenario["rubric"]]})

    monkeypatch.setattr(rp, "_provider", lambda timeout: Provider())
    graded = rp.grade_transcript(scenario, transcript)
    assert graded["source"] == "ai" and graded["percent"] == 90 and graded["needs_review"] is False


def test_no_speech_scores_zero_and_an_ai_failure_uses_a_flagged_participation_score(monkeypatch):
    scenario = rp.fallback_scenario("support")
    assert rp.grade_transcript(scenario, [{"role": "persona", "text": "Hello?"}])["percent"] == 0

    monkeypatch.setattr(rp, "_provider", lambda timeout: (_ for _ in ()).throw(RuntimeError("down")))
    graded = rp.grade_transcript(scenario, [{"role": "agent", "text": "Hello, how can I help?"}, {"role": "agent", "text": "I will check that."}])
    assert graded["source"] == "rules" and graded["needs_review"] is True and 0 < graded["percent"] <= 60


def test_submitting_the_practical_scores_the_call(monkeypatch):
    _fallback_scenario(monkeypatch)
    db = PA._db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")
    assessment, _ = start_sjt_assessment(db, session_record, application)

    row = db.query(PracticalRoleplay).one()
    row.transcript_json = json.dumps([{"role": "persona", "text": "Hello?"}, {"role": "agent", "text": "Good morning, how can I help you today?"}])
    db.commit()

    scenario = rp.fallback_scenario("support")

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            return json.dumps({"summary": "ok", "criteria": [{"criterion": c["criterion"], "score": 8, "comment": ""} for c in scenario["rubric"]]})

    monkeypatch.setattr(rp, "_provider", lambda timeout: Provider())

    result = submit_sjt_assessment(db, assessment, application, [])
    assert result["kind"] == "practical" and result["percentage"] == 80 and result["passed"] is True

    db.refresh(assessment)
    assert json.loads(assessment.result_json)["items"][0]["type"] == "roleplay" and db.query(PracticalRoleplay).one().status == "done"


# ------------------------------------------------------------------ the live session in role-play mode
def test_the_live_session_opens_in_character_does_not_swap_questions_and_ends_with_the_closing_line():
    async def go():
        factory, app_id = _factory_with_row()
        work = rp.RolePlayWork(app_id, session_factory=factory)
        session, conn, pub, *_ = await base._make(work=work, lead=0.0)
        await session.start()
        await asyncio.sleep(0.15)

        opening = conn.notes[0][0]
        assert "you are the one who called" in opening and "finally. I ordered a blender" in opening          # the persona's opening, not an interview greeting

        before = len(conn.notes)
        await session._record_answer("I don't know")                                                         # an agent saying this is just an answer in a call
        assert session.stats["swapped_questions"] == 0 and len(conn.notes) == before + 1 and "Stay in character" in conn.notes[-1][0]

        await session._record_answer("Goodbye and thank you, have a great day!")                             # early goodbye: not the end yet
        await session._record_answer("Let me look into the order for you now.")
        await session._record_answer("I will refund the express fee today.")
        await session._record_answer("Anything else? Thank you for calling, goodbye!")
        assert session._closing_sent and "ONE brief, natural closing line" in conn.notes[-1][0]

        conn.feed("audio", b"AAAA"); conn.feed("output_text", "Okay, thank you, bye."); conn.feed("turn_complete")
        await asyncio.sleep(1.8)
        assert ("state", "complete") in pub.log
    asyncio.run(go())


def test_a_silent_agent_gets_in_character_prompts_then_the_call_ends():
    async def go():
        factory, app_id = _factory_with_row()
        session, conn, pub, *_ = await base._make(work=rp.RolePlayWork(app_id, session_factory=factory), lead=0.0)
        await session.start()
        session._opening_spoken = True

        await session._nudge()
        assert "Are you still there?" in conn.notes[-1][0] and "kind" not in conn.notes[-1][0].lower()
        await session._nudge()
        assert "a little impatient" in conn.notes[-1][0]
        await session._nudge()
        assert session._closing_sent and "ONE brief, natural closing line" in conn.notes[-1][0]
        await session.close()
    asyncio.run(go())


def test_a_roleplay_session_and_the_interview_never_share_a_live_session():
    async def go():
        factory, app_id = _factory_with_row()
        rt._sessions.clear()
        pub = base.FakePublisher()
        conn = base.FakeConn()

        async def conn_factory(instruction, handle):
            return conn

        call = await rt.start_realtime_session(app_id, connection_factory=conn_factory, publisher=pub, work=rp.RolePlayWork(app_id, session_factory=factory))
        assert call.state == "ready"

        interview = await rt.start_realtime_session(app_id, connection_factory=conn_factory, publisher=base.FakePublisher(), work=base.FakeWork())
        assert interview is not call and call.state == "closed"
        await interview.close()
        rt._sessions.clear()
    asyncio.run(go())


# ------------------------------------------------------------------ routes
def _threadsafe_db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _client(db, user):
    api = FastAPI()
    api.include_router(route.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: user
    return TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})


def test_the_routes_need_an_active_call_simulation_and_the_owner(monkeypatch):
    _fallback_scenario(monkeypatch)
    db = _threadsafe_db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")
    client = _client(db, user)

    assert client.post(f"/api/assessment/sessions/{application.id}/roleplay/start").status_code == 409        # the practical test has not started
    assert client.get(f"/api/assessment/sessions/{application.id}/roleplay/status").json()["state"] == "idle"

    start_sjt_assessment(db, session_record, application)

    assert client.post(f"/api/assessment/sessions/{application.id}/roleplay/v2/avatar/start").json()["state"] == "disabled"
    assert client.post(f"/api/assessment/sessions/{application.id + 99}/roleplay/start").status_code == 404

    started = []

    async def fake_start(application_id, work=None, **kw):
        started.append((application_id, type(work).__name__))
        return type("S", (), {"status": lambda self: {"state": "ready", "error": None}})()

    monkeypatch.setattr(rt, "start_realtime_session", fake_start)
    monkeypatch.setattr(rt, "is_realtime_configured", lambda: True)
    monkeypatch.setattr(rt, "get_realtime_provider", lambda: rt.PROVIDER_REALTIME)
    monkeypatch.setattr(route, "is_livekit_configured", lambda: True)

    response = client.post(f"/api/assessment/sessions/{application.id}/roleplay/start")
    assert response.status_code == 200 and started == [(application.id, "RolePlayWork")]


def test_the_routes_refuse_without_a_csrf_token(monkeypatch):
    _fallback_scenario(monkeypatch)
    db = _threadsafe_db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")
    start_sjt_assessment(db, session_record, application)

    api = FastAPI()
    api.include_router(route.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_user] = lambda: user

    assert TestClient(api).post(f"/api/assessment/sessions/{application.id}/roleplay/start").status_code == 403


def test_the_page_script_and_demo_are_wired():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "frontend", "sjt-test.html"), encoding="utf-8").read()
    assert "practical-roleplay.js" in html and "livekit-client" in html
    script = open(os.path.join(root, "frontend", "js", "practical-roleplay.js"), encoding="utf-8").read()
    assert 'post("start")' in script and 'post("text"' in script and 'apiPath: "roleplay/v2/avatar"' in script
    assert "config.apiPath" in open(os.path.join(root, "frontend", "js", "interview-avatar-v2.js"), encoding="utf-8").read()


def test_hr_sees_the_call_transcript_next_to_the_grading(monkeypatch):
    from backend.app.api.routes.hr_assessment_review import _sjt_review_data

    _fallback_scenario(monkeypatch)
    db = PA._db()
    user, job, application, session_record = PA._seed(db, "Customer Support Executive")
    assessment, _ = start_sjt_assessment(db, session_record, application)

    row = db.query(PracticalRoleplay).one()
    row.transcript_json = json.dumps([{"role": "persona", "text": "Hello?"}, {"role": "agent", "text": "Good morning, how can I help you today?"}])
    db.commit()

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            return json.dumps({"summary": "ok", "criteria": []})

    monkeypatch.setattr(rp, "_provider", lambda timeout: Provider())
    submit_sjt_assessment(db, assessment, application, [])

    review = _sjt_review_data(db, application)
    item = review["practical_items"][0]

    assert review["kind"] == "practical" and review["environment"] == "roleplay"
    assert item["type"] == "roleplay" and [t["role"] for t in item["transcript"]] == ["persona", "agent"] and item["scenario"]
    assert rp.fallback_scenario("support")["persona"]["hidden"] not in json.dumps(item)                 # HR sees the transcript, never the character's private script

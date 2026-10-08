"""
Practical (work-sample) assessments for non-technical roles: the planner chooses an environment per role, the data environment computes its own
answer key from the table the candidate sees, the written environment is graded against a rubric, and the whole thing runs through the existing
stage-3 pipeline (same table, same endpoints, same proctoring). In-memory database, no network (the AI is stubbed).
"""

import json
from types import SimpleNamespace as N

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.database.database import Base
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User
from backend.app.services.assessment import assessment_planner as planner
from backend.app.services.assessment.sjt_integration import candidate_view, start_sjt_assessment, submit_sjt_assessment
from backend.app.services.sjt import practical_data_templates as data
from backend.app.services.sjt import practical_written_tasks as written


@pytest.fixture(autouse=True)
def practical_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _job(title, **extra):
    return N(id=None, title=title, required_skills=extra.get("skills", ""), technologies="", requirements="", description=extra.get("description", ""), responsibilities="")


# ------------------------------------------------------------------ the planner picks an environment per role
@pytest.mark.parametrize("title,environment,template", [
    ("MIS Executive - Fintech Operations", "data", "sales_report"),
    ("Accountant", "data", "bank_reconciliation"),
    ("Operations Coordinator", "data", "inventory_orders"),
    ("Customer Support Executive", "written", None),
    ("Sales Development Representative", "written", None),
    ("HR Recruiter", "written", None),
    ("Digital Marketing Manager", "written", None),
    ("Content Writer", "written", None),
    ("Executive Assistant", "written", None),
    ("Office Administrator", "written", None),
    ("Legal Executive", "written", None),
    ("Business Analyst", "written", None),
    ("L&D Specialist", "written", None),
    ("Project Manager", "written", None),
    ("Implementation Specialist", "written", None),
    ("Receptionist", "written", None),                                                                  # a call simulation when the voice stack is on (see test_practical_roleplay)
])
def test_the_rules_choose_a_practical_environment_for_the_role(title, environment, template):
    stage = planner.rules_plan(_job(title))["stages"][1]

    assert stage["kind"] == "practical" and stage["environment"] == environment and stage.get("template") == template
    assert stage["title"]


def test_design_roles_and_unknown_roles_keep_the_situational_test():
    assert planner.rules_plan(_job("Graphic Designer"))["stages"][1]["kind"] == "situational_judgment"
    assert planner.rules_plan(_job("Warehouse Driver"))["stages"][1]["kind"] == "situational_judgment"


def test_technical_roles_are_never_given_a_practical_stage():
    job = _job("Python Developer", skills="Python, Django, SQL", description="Build REST APIs")
    stage = planner.rules_plan(job)["stages"][1]
    assert stage["kind"] in ("programming", "technical_domain")


def test_the_switch_puts_every_job_back_on_the_situational_test(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "off")
    assert planner.rules_plan(_job("Accountant"))["stages"][1]["kind"] == "situational_judgment"


def test_an_ai_plan_with_a_practical_stage_is_validated():
    job = _job("Customer Support Executive")
    ok = planner.normalise_plan({"stages": [{"slot": "aptitude", "kind": "aptitude"}, {"slot": "programming", "kind": "practical", "environment": "written", "title": "Support practical", "focus": ["empathy", "clarity"]}]}, job, "ai")
    assert ok["stages"][1]["environment"] == "written" and ok["stages"][1]["focus"] == ["empathy", "clarity"]

    bad_template = planner.normalise_plan({"stages": [{"slot": "aptitude", "kind": "aptitude"}, {"slot": "programming", "kind": "practical", "environment": "data", "template": "made_up"}]}, job, "ai")
    assert bad_template["stages"][1]["template"] == "sales_report"                                     # an unknown template falls back to a known one

    with pytest.raises(ValueError):                                                                     # nothing usable in the AI's plan: the rules plan the job instead
        planner.normalise_plan({"stages": [{"slot": "aptitude", "kind": "aptitude"}, {"slot": "programming", "kind": "practical", "environment": "voice"}]}, job, "ai", strict=False)

    mixed = planner.normalise_plan({"stages": [{"slot": "aptitude", "kind": "skills", "title": "Support skills", "areas": [{"label": "Tone", "topics": ["empathy"]}, {"label": "Clarity", "topics": ["brevity"]}]},
                                               {"slot": "programming", "kind": "practical", "environment": "voice"}]}, job, "ai", strict=False)
    assert mixed["stages"][1]["kind"] == "situational_judgment"                                       # one bad stage falls back to the standard test, the good one stays


def test_hr_can_put_the_standard_test_back():
    plan = planner.rules_plan(_job("Accountant"))
    overridden = planner.apply_override(plan, _job("Accountant"), stage3="standard")
    assert overridden["stages"][1]["kind"] == "situational_judgment"


# ------------------------------------------------------------------ the data environment computes its own answer key
@pytest.mark.parametrize("template", sorted(data.TEMPLATES))
def test_every_data_template_builds_a_consistent_task_for_many_candidates(template):
    for seed in range(120):
        task = data.build_data_task(template, seed)
        assert len(task["questions"]) == 5 and len(task["rows"]) >= 10
        assert all(len(row) == len(task["columns"]) for row in task["rows"])
        for q in task["questions"]:
            assert data.check_answer(q, q["correct_answer"])                                            # the key accepts itself


def test_two_candidates_get_different_tables_and_the_same_seed_the_same_table():
    a, b = data.build_data_task("sales_report", "1:10"), data.build_data_task("sales_report", "2:11")
    assert a["rows"] != b["rows"] and data.build_data_task("sales_report", "1:10")["rows"] == a["rows"]


def test_answers_are_checked_with_a_sensible_tolerance_and_format_forgiveness():
    q = {"answer_kind": "number", "correct_answer": 123456.5, "tolerance": 0.5}
    assert data.check_answer(q, "Rs 1,23,456.50") and data.check_answer(q, "123456") and not data.check_answer(q, "123400") and not data.check_answer(q, "")
    assert data.parse_number("(450)") == -450 and data.parse_number("12.5%") == 12.5 and data.parse_number("abc") is None
    text = {"answer_kind": "text", "correct_answer": "North"}
    assert data.check_answer(text, "  north ") and not data.check_answer(text, "South")


def test_the_bank_reconciliation_answers_really_follow_from_the_table():
    task = data.build_data_task("bank_reconciliation", 5)
    rows = task["rows"]
    cash = 50000 + sum(r[3] for r in rows if r[3] != "")
    bank = 50000 + sum(r[4] for r in rows if r[4] != "")
    answers = [q["correct_answer"] for q in task["questions"]]
    assert answers[0] == cash and answers[1] == bank
    assert answers[2] == sum(1 for r in rows if r[3] != "" and r[4] == "")
    assert answers[4] == cash + answers[3]


# ------------------------------------------------------------------ the written environment
def test_the_curated_bank_has_two_usable_tasks_for_every_family():
    for family in ("support", "sales", "hr", "finance", "marketing", "writing", "operations", "mis", "design", "admin", "legal", "product", "training", "management", "implementation", "reception", None):
        tasks = written.fallback_written_tasks(family)
        assert len(tasks) == 2
        for t in tasks:
            assert t["type"] == "written" and len(t["scenario"]) > 60 and len(t["rubric"]) >= 3 and t["min_words"] < t["max_words"]


def test_a_blank_answer_scores_zero_without_calling_the_ai(monkeypatch):
    monkeypatch.setattr(written, "_provider", lambda timeout: (_ for _ in ()).throw(AssertionError("the AI must not be called for a blank answer")))
    tasks = written.fallback_written_tasks("support")
    result = written.grade_written(tasks, {1: "", 2: "  "})
    assert result[1]["percent"] == 0 and result[2]["percent"] == 0


def test_ai_grading_is_weighted_by_the_rubric(monkeypatch):
    tasks = written.fallback_written_tasks("support")
    weights = [c["weight"] for c in tasks[0]["rubric"]]

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            return json.dumps({"tasks": [{"id": t["id"], "summary": "ok", "criteria": [{"criterion": c["criterion"], "score": 10 if i == 0 else 0, "comment": "x"} for i, c in enumerate(t["rubric"])]} for t in tasks]})

    monkeypatch.setattr(written, "_provider", lambda timeout: Provider())
    result = written.grade_written(tasks, {1: "I am very sorry for the delay and I will refund your express fee today.", 2: "Three customers were charged twice this morning, please approve refunds."})
    assert result[1]["source"] == "ai" and result[1]["percent"] == round(100 * weights[0] / sum(weights))


def test_when_the_ai_is_down_a_length_check_is_used_and_flagged_for_human_review(monkeypatch):
    monkeypatch.setattr(written, "_provider", lambda timeout: (_ for _ in ()).throw(RuntimeError("down")))
    tasks = written.fallback_written_tasks("support")
    long_answer = "I am sorry about the delay with your order and I would like to make it right for you. " * 6
    result = written.grade_written(tasks, {1: long_answer, 2: "ok"})
    assert result[1]["source"] == "rules" and result[1]["needs_review"] is True and result[1]["percent"] >= 60
    assert result[2]["percent"] < 60                                                                      # a one-word reply is not a pass


# ------------------------------------------------------------------ through the real stage-3 service
def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _seed(db, title, skills=""):
    user = User(full_name="T", username="prac1", email="prac1@example.com", hashed_password="x", role="user")
    db.add(user); db.flush()
    job = Job(title=title, company="Co", required_skills=skills, description="Work in the team.")
    db.add(job); db.flush()
    application = JobApplication(job_id=job.id, user_id=user.id, status="aptitude_passed")
    db.add(application); db.flush()
    session_record = AssessmentSession(application_id=application.id, current_stage="programming")
    db.add(session_record)
    db.add(AptitudeTest(application_id=application.id, questions="[]", total_questions=10, passed=True, status="completed"))
    db.add(AssessmentAssignment(application_id=application.id, assessment_type="situational_judgment", status="assigned", assigned_by_type="system", assigned_by_id=user.id))
    db.commit()
    db.refresh(application); db.refresh(session_record)
    return user, job, application, session_record


def test_a_data_practical_runs_end_to_end_with_the_answer_key_hidden_from_the_candidate():
    db = _db()
    user, job, application, session_record = _seed(db, "Accountant")

    assessment, created = start_sjt_assessment(db, session_record, application)
    assert created and assessment.kind == "practical" and assessment.question_source == "procedural"

    view = candidate_view(assessment, job=job)
    assert view["kind"] == "practical" and view["practical"]["environment"] == "data" and len(view["practical"]["dataset"]["rows"]) >= 10
    assert len(view["questions"]) == 5 and all(q["type"] == "data" for q in view["questions"])
    assert all("correct_answer" not in q and "tolerance" not in q for q in view["questions"])
    assert "correct_answer" not in json.dumps(view)

    stored = json.loads(assessment.questions)
    answers = [{"question_id": q["id"], "selected_answer": str(q["correct_answer"])} for q in stored]
    answers[0]["selected_answer"] = "999999"                                                            # one wrong answer: 4 of 5

    result = submit_sjt_assessment(db, assessment, application, answers)
    assert result["kind"] == "practical" and result["percentage"] == 80 and result["passed"] is True

    db.refresh(assessment)
    assert assessment.score == 80 and assessment.status == "completed" and json.loads(assessment.result_json)["items"][0]["correct"] is False


def test_a_written_practical_stores_the_text_and_the_graded_feedback(monkeypatch):
    db = _db()
    user, job, application, session_record = _seed(db, "Customer Support Executive")

    monkeypatch.setattr(written, "generate_written_tasks", lambda job, focus=None, family=None: (written.fallback_written_tasks("support"), "fallback"))

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            payload = json.loads(prompt)
            return json.dumps({"tasks": [{"id": t["id"], "summary": "Good", "criteria": [{"criterion": c["criterion"], "score": 8, "comment": "fine"} for c in t["rubric"]]} for t in payload["tasks"]]})

    monkeypatch.setattr(written, "_provider", lambda timeout: Provider())

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert assessment.kind == "practical" and view["practical"]["environment"] == "written"
    assert all(q["type"] == "written" and q["scenario"] and q["instructions"] and "rubric" not in q for q in view["questions"])

    answers = [{"question_id": q["id"], "selected_answer": "Dear customer, I am so sorry for the delay. I have refunded your express fee and the parcel will reach you on Friday."} for q in view["questions"]]
    result = submit_sjt_assessment(db, assessment, application, answers)

    assert result["percentage"] == 80 and result["passed"] is True
    db.refresh(assessment)
    items = json.loads(assessment.result_json)["items"]
    assert items[0]["source"] == "ai" and items[0]["criteria"][0]["score"] == 8
    assert json.loads(assessment.answers)[0]["selected_answer"].startswith("Dear customer")              # HR sees the candidate's own text


def test_a_job_without_a_practical_stage_still_gets_the_situational_questions():
    db = _db()
    user, job, application, session_record = _seed(db, "Warehouse Driver")

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert assessment.kind == "mcq" and view["kind"] == "mcq" and view["practical"] is None and len(view["questions"]) == 10


def test_if_the_practical_cannot_be_built_the_candidate_still_gets_a_test(monkeypatch):
    from backend.app.services.sjt import sjt_question_service

    monkeypatch.setattr(sjt_question_service, "build_practical_test", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    db = _db()
    user, job, application, session_record = _seed(db, "Accountant")

    assessment, _ = start_sjt_assessment(db, session_record, application)
    assert assessment.kind == "mcq" and assessment.total_questions == 10


def test_the_stage_label_and_titles_use_the_practical_name():
    job = _job("Accountant")
    assert planner.stage_title(job, "programming", "Situational Judgment Test") == "Finance Practical: Bank Reconciliation"


def test_written_grading_asks_the_ai_once_more_before_using_the_length_check(monkeypatch):
    from backend.app.services.sjt import practical_written_tasks as pw

    calls = []

    class Flaky:
        def generate_json(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("groq request failed (HTTP 400: json_validate_failed)")
            return '{"tasks": [{"id": 1, "summary": "Good.", "criteria": [{"criterion": "x", "score": 8, "comment": "ok"}]}]}'

    monkeypatch.setattr(pw, "_provider", lambda timeout: Flaky())
    task = {"id": 1, "scenario": "s", "instructions": "i", "rubric": [{"criterion": "x", "weight": 1}] * 3, "min_words": 5, "max_words": 50}
    out = pw.grade_written([task], {1: "a reasonable written answer of several words here"})
    assert out[1]["source"] == "ai" and len(calls) == 2

    class Broken:
        def generate_json(self, *args, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(pw, "_provider", lambda timeout: Broken())
    assert pw.grade_written([task], {1: "a reasonable written answer of several words here"})[1]["source"] == "rules"

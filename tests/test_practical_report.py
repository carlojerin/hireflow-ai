"""
The intelligent report states the practical assessment as a verdict: the AI's marks, a short judgment, strengths and gaps (HR reads that, and
opens the candidate's own work only if they want to). In-memory database, no network (the AI is stubbed).
"""

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

import backend.app.main  # noqa: F401
from backend.app.api.deps import get_current_company
from backend.app.api.routes import hr_assessment_review as review_routes
from backend.app.api.routes.hr_assessment_review import _practical_verdict
from backend.app.database.database import Base, get_db
from backend.app.models.company import Company
from backend.app.services.assessment import candidate_narrative_summary as narrative
from backend.app.services.assessment.sjt_integration import start_sjt_assessment, submit_sjt_assessment
from backend.app.services.sjt import practical_written_tasks as written


@pytest.fixture(autouse=True)
def practical_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "off")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _world(title):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    company = Company(id=1, company_name="Acme", legal_name="Acme Ltd", email="hr@acme.test", mobile="123", hashed_password="x",
                      account_active=True, verification_status="verified", admin_verified=True, role="company")
    db.add(company)
    db.commit()

    user, job, application, session_record = PA._seed(db, title)
    job.company_id = 1
    db.commit()

    return db, company, user, job, application, session_record


def _client(db, company):
    api = FastAPI()
    api.include_router(review_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company
    return TestClient(api)


def test_the_report_states_a_data_practical_as_a_verdict_with_the_answers_one_level_down(monkeypatch):
    seen = {}
    monkeypatch.setattr(narrative, "generate_candidate_narrative_summary", lambda facts: seen.update(facts) or {"summary": "ok", "generated_by": "test"})

    db, company, user, job, application, session_record = _world("Accountant")
    assessment, _ = start_sjt_assessment(db, session_record, application)

    stored = json.loads(assessment.questions)
    answers = [{"question_id": q["id"], "selected_answer": str(q["correct_answer"])} for q in stored]
    answers[0]["selected_answer"] = "1"
    answers[1]["selected_answer"] = "1"                                                                  # 3 of 5
    submit_sjt_assessment(db, assessment, application, answers)

    data = _client(db, company).get(f"/api/company-applications/1/{application.id}/candidate-intelligence-report").json()

    verdict = data["practical_verdict"]
    assert verdict["percentage"] == 60 and verdict["passed"] is True and verdict["marked_by"] == "auto"
    assert "3 of 5 data questions correctly" in verdict["verdict"]
    assert len(verdict["gaps"]) == 2 and all(g.startswith("Wrong answer:") for g in verdict["gaps"])

    assert any(s.startswith("Practical task:") for s in data["strengths"]) and any(g.startswith("Practical task:") for g in data["skill_gaps"])
    fact = next(v for k, v in seen.items() if k.startswith("Practical assessment (data)"))
    assert "60%" in fact and "AI verdict" in fact                                                        # the narrative summary is built from the verdict too

    assert data["assessment"]["situational_judgment_test"]["practical_items"]                          # the actual work is still there for the one who wants it


def test_the_report_states_a_written_practical_with_the_ais_marks_strengths_and_gaps(monkeypatch):
    monkeypatch.setattr(narrative, "generate_candidate_narrative_summary", lambda facts: {"summary": "ok", "generated_by": "test"})
    monkeypatch.setattr(written, "generate_written_tasks", lambda job, focus=None, family=None: (written.fallback_written_tasks("hr"), "fallback"))

    class Provider:
        def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
            tasks = json.loads(prompt)["tasks"]
            return json.dumps({"tasks": [{"id": t["id"], "summary": "Clear and kind, but light on next steps.",
                                          "criteria": [{"criterion": c["criterion"], "score": 9 if i == 0 else 3 if i == 1 else 6, "comment": "x"} for i, c in enumerate(t["rubric"])]} for t in tasks]})

    monkeypatch.setattr(written, "_provider", lambda timeout: Provider())

    db, company, user, job, application, session_record = _world("HR Recruiter")
    assessment, _ = start_sjt_assessment(db, session_record, application)
    answers = [{"question_id": q["id"], "selected_answer": "Dear candidate, thank you for your time and the effort you put into preparing for the interview with our team."} for q in json.loads(assessment.questions)]
    submit_sjt_assessment(db, assessment, application, answers)

    verdict = _client(db, company).get(f"/api/company-applications/1/{application.id}/candidate-intelligence-report").json()["practical_verdict"]

    assert verdict["marked_by"] == "ai" and verdict["environment"] == "written" and "Clear and kind" in verdict["verdict"]
    assert len(verdict["marks"]) >= 6 and all(0 <= m["score"] <= 10 for m in verdict["marks"])
    assert verdict["strengths"] and verdict["gaps"] and verdict["percentage"] is not None


def test_a_job_without_a_practical_stage_has_no_practical_verdict(monkeypatch):
    monkeypatch.setattr(narrative, "generate_candidate_narrative_summary", lambda facts: {"summary": "ok", "generated_by": "test"})
    db, company, user, job, application, session_record = _world("Graphic Designer")
    start_sjt_assessment(db, session_record, application)

    data = _client(db, company).get(f"/api/company-applications/1/{application.id}/candidate-intelligence-report").json()
    assert data["practical_verdict"] is None


def test_the_verdict_marks_a_provisional_score_when_the_ai_was_unavailable():
    review = {"exists": True, "kind": "practical", "status": "completed", "environment": "written", "title": "t", "percentage": 55, "passed": False, "needs_review": True,
              "practical_items": [{"type": "written", "question": "Reply", "result": {"source": "rules", "summary": "Automatic grading was unavailable.", "criteria": [{"criterion": "Tone", "score": None, "max": 10}]}}]}
    verdict = _practical_verdict(review)
    assert verdict["marked_by"] == "rules" and verdict["needs_review"] is True and verdict["marks"] == []


def test_the_report_page_shows_the_verdict_first_and_the_work_behind_a_click():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = open(os.path.join(root, "frontend", "js", "company-candidate-report.js"), encoding="utf-8").read()
    assert "renderPracticalVerdict(data.practical_verdict)" in script and "<details>" in script and "Practical assessment: AI verdict" in script

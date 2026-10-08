"""
The invite form with the role catalogue: HR types a title and a one-line requirement; the plan the form previews and the job the invite creates are right for the
ROLE, not only for the words of the requirement.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_candidate_invite_flow as base

from backend.app.api.routes import company_invites as company_invites_routes
from backend.app.models.job import Job


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "on")
    monkeypatch.setenv("ASSESSMENT_CATALOGUE", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


ONE_LINE = "Strong experience required."


@pytest.mark.parametrize("title,shape,kind,detail", [
    ("Java Developer", "technical", "programming", None),
    ("Backend Developer", "technical", "programming", None),
    ("DevOps Engineer", "technical", "practical", ("environment", "lab")),
    ("Network Engineer", "technical", "practical", ("lab", "network_math")),
    ("Manual QA Tester", "technical", "practical", ("family", "qa")),
    ("Customer Support Executive", "non_technical", "practical", ("environment", "roleplay")),
    ("Help Desk Analyst", "technical", "practical", ("family", "itsupport")),
    ("Accountant", "non_technical", "practical", ("title", "Finance Practical: Bank Reconciliation")),
    ("Product Manager", "non_technical", "practical", ("family", "product")),
    ("UX Designer", "non_technical", "practical", ("environment", "design")),
    ("SAP Consultant", "technical", "technical_domain", None),
])
def test_the_form_previews_the_right_assessment_for_the_role_from_a_one_line_requirement(title, shape, kind, detail):
    db, company = base._db()
    client = base._app(db, company)

    body = base._plan_preview(client, title, ONE_LINE).json()
    stage3 = body["stages"][1]

    assert stage3["kind"] == kind, (title, stage3)

    if detail:
        key, value = detail
        assert stage3.get(key) == value, (title, stage3)

    assert db.query(Job).count() == 0                                                                  # previewing creates nothing


def test_an_invite_for_a_coding_role_with_a_one_line_text_creates_a_job_the_coding_engine_can_plan_from(monkeypatch, tmp_path):
    db, company = base._db()
    client = base._app(db, company)
    base._patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    response = base._upload(client, job_title="Backend Developer", job_requirement=ONE_LINE)
    assert response.status_code == 200, response.text

    job = db.query(Job).filter(Job.title == "Backend Developer").first()
    assert job.required_skills == "Python" and job.technologies == "Python"                            # the usual language of the role, because HR named none

    stored = json.loads(job.assessment_plan_json)
    assert stored["stages"][1]["kind"] == "programming"


def test_what_hr_wrote_is_never_replaced_by_the_catalogue(monkeypatch, tmp_path):
    db, company = base._db()
    client = base._app(db, company)
    base._patch_analysis(monkeypatch, [])
    monkeypatch.setattr(company_invites_routes, "UPLOAD_FOLDER", str(tmp_path))

    response = base._upload(client, job_title="Backend Developer", job_requirement="Strong Java and Spring experience")
    assert response.status_code == 200, response.text

    job = db.query(Job).filter(Job.title == "Backend Developer").first()
    assert "Python" not in (job.technologies or "") and "Java" in (job.required_skills + (job.requirements or ""))   # the catalogue only fills in what HR left out


def test_the_plan_names_the_environment_in_words_hr_understands():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = open(os.path.join(root, "frontend", "js", "company-invite-candidate.js"), encoding="utf-8").read()
    for words in ("practical technical lab", "practical call simulation", "practical web design task", "practical written task", "practical data task"):
        assert words in script

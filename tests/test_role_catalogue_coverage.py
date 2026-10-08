"""
COVERAGE: every domain of an IT company and every role in it gets the right assessment, and that assessment can really be produced and marked.

For each of the roles in the role catalogue, from a short job text the way HR types it ("We are hiring a Java Developer. Strong experience required."):
  * the role is classified (technical / non-technical) correctly from its TITLE,
  * the planner chooses the intended stage-3 assessment,
  * a coding role has a language the coding engine can plan from (and really can: the engine's own feasibility check passes), or a domain-test bank,
  * every other role's assessment is built (the AI and the voice stack are off, so the fallbacks are what is proven) and marked correctly.
In-memory database, no network.
"""

import json
import os
import sys
from types import SimpleNamespace as N

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backend.app.main  # noqa: F401
from backend.app.database.database import Base
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.user import User
from backend.app.services.assessment import assessment_planner as planner
from backend.app.services.assessment import role_catalogue as catalogue
from backend.app.services.assessment.role_classification import job_has_executable_language, job_role_shape, job_technical_domain
from backend.app.services.sjt import practical_data_templates as data
from backend.app.services.sjt import practical_design as design
from backend.app.services.sjt import practical_roleplay as roleplay
from backend.app.services.sjt import practical_written_tasks as written
from backend.app.services.sjt.sjt_question_service import build_practical_test
from backend.app.services.tda.tda_question_service import build_tda_questions

ROLES = catalogue.all_roles()
CODE_ROLES = [r for r in ROLES if r["category"] == catalogue.CODE]
PRACTICAL_ROLES = [r for r in ROLES if r["category"] != catalogue.CODE]


@pytest.fixture(autouse=True)
def everything_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "on")
    monkeypatch.setenv("ASSESSMENT_CATALOGUE", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "off")


def _job(title, **extra):
    text = f"We are hiring a {title}. Strong experience required."
    return Job(title=title, company="Co", description=text, requirements=text, source="invite_only", status="active", **extra)


def _no_ai(monkeypatch):
    """The AI is down everywhere: the built-in banks, scenarios and measured marks are what runs."""

    def down(timeout):
        raise RuntimeError("AI is down")

    monkeypatch.setattr(written, "_provider", down)
    monkeypatch.setattr(roleplay, "_provider", down)
    monkeypatch.setattr(design, "_provider", down)


# ------------------------------------------------------------------ the catalogue itself
def test_the_catalogue_covers_every_domain_of_an_it_company():
    wanted = {
        "Software Development", "Quality Assurance & Testing", "DevOps, Cloud & Infrastructure", "Data, AI & Machine Learning", "Cybersecurity",
        "IT Support & Service Desk", "Enterprise Applications & Automation", "Product & Project Management", "UI/UX & Design", "Sales & Business Development",
        "Marketing & Content", "Customer Success & Support", "Human Resources & Talent", "Finance & Accounting", "Operations, Admin & Legal", "Engineering Leadership",
    }
    assert wanted == set(catalogue.domains()) and len(ROLES) >= 90


def test_every_role_is_complete_and_names_things_that_exist():
    titles = set()

    for r in ROLES:
        assert r["title"] not in titles and r["patterns"] and all(p == p.lower() for p in r["patterns"]), r["title"]
        titles.add(r["title"])
        assert r["category"] in (catalogue.CODE, catalogue.LAB, catalogue.DATA, catalogue.WRITTEN, catalogue.ROLEPLAY, catalogue.DESIGN) and r["shape"] in ("technical", "non_technical")

        if r["category"] == catalogue.DATA:
            assert r["template"] in data.TEMPLATES, r["title"]
        if r["category"] == catalogue.LAB:
            from backend.app.services.sjt.practical_lab_templates import TEMPLATES as LABS
            assert r["lab"] in LABS and r["shape"] == "technical", r["title"]
        if r["category"] == catalogue.WRITTEN:
            assert r["family"] in planner.WRITTEN_FAMILIES and r["family"] in written._BANK, r["title"]
        if r["category"] == catalogue.ROLEPLAY:
            assert r["family"] in planner.ROLEPLAY_FAMILIES, r["title"]
            assert roleplay.fallback_scenario(r["family"])["persona"]["opening_line"]
        if r["category"] == catalogue.CODE:
            assert r.get("languages") is not None and r["shape"] == "technical"


def test_every_title_pattern_and_its_seniority_variants_find_their_own_role():
    for r in ROLES:
        for p in r["patterns"]:
            for variant in (p, f"Senior {p}", f"{p} (Remote)", f"Jr. {p} - Bangalore", p.upper(), f"{p} II", f"  {p}  "):
                found = catalogue.lookup(variant)
                assert found and found["title"] == r["title"], (variant, r["title"], found and found["title"])


@pytest.mark.parametrize("title,expected", [
    ("Senior Java Developer (Remote)", "Java Developer"), ("Sr. QA Engineer", "Manual QA Tester"), ("Lead DevOps Engineer", "DevOps Engineer"),
    ("Jr Data Analyst", "Data Analyst"), ("Customer Support Executive - Night Shift", "Customer Support Executive"), ("SAP FICO Consultant", "SAP Consultant"),
    ("Associate Product Manager", "Product Manager"), ("UI/UX Designer", "UX Designer"), ("Pen Tester", "Penetration Tester"), ("SRE", "Site Reliability Engineer"),
    ("Principal Engineer", "Software Architect"), ("L&D Specialist", "Learning & Development Specialist"), ("Developer", "Software Engineer"),
])
def test_real_world_titles_are_recognised(title, expected):
    assert catalogue.lookup(title)["title"] == expected


def test_a_title_that_is_not_in_the_catalogue_is_simply_not_matched():
    for title in ("Chief of Staff", "Beekeeper", "", None, "x"):
        assert catalogue.lookup(title) is None


def test_the_catalogue_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_CATALOGUE", "off")
    assert catalogue.lookup("Java Developer") is None


# ------------------------------------------------------------------ every role is classified and planned
@pytest.mark.parametrize("role", ROLES, ids=lambda r: r["title"])
def test_every_role_is_classified_from_its_title_and_gets_the_intended_assessment(role):
    job = _job(role["title"])
    catalogue.enrich_job(job)

    assert job_role_shape(job) == role["shape"]

    stage = planner.rules_plan(job)["stages"][1]

    if role["category"] == catalogue.CODE:
        assert stage["kind"] in ("programming", "technical_domain")
        platform_only = bool(role.get("domain")) and not role.get("languages")          # SAP, Salesforce, RPA...: a platform test; embedded has C/C++ to code in
        assert stage["kind"] == ("technical_domain" if platform_only else "programming")
    else:
        assert stage["kind"] == "practical"
        expected = {catalogue.LAB: "lab", catalogue.DATA: "data", catalogue.WRITTEN: "written", catalogue.ROLEPLAY: "roleplay", catalogue.DESIGN: "design"}[role["category"]]
        assert stage["environment"] == expected and stage["title"]

        if role["category"] == catalogue.LAB:
            assert stage["lab"] == role["lab"]
        if role["category"] == catalogue.DATA:
            assert stage["template"] == role["template"]
        if role["category"] in (catalogue.WRITTEN, catalogue.ROLEPLAY):
            assert stage["family"] == role["family"]


@pytest.mark.parametrize("role", CODE_ROLES, ids=lambda r: r["title"])
def test_a_coding_role_always_has_something_the_engines_can_run(role):
    job = _job(role["title"])
    catalogue.enrich_job(job)

    if role.get("domain") and not role.get("languages"):
        assert job_technical_domain(job) == role["domain"]
        questions, _source = build_tda_questions(role["domain"])
        assert len(questions) >= 4 and all(q["options"] and q["correct_answer"] in q["options"] for q in questions)
    else:
        assert job_has_executable_language(job.required_skills or "", job.technologies or ""), (role["title"], job.technologies)


def test_every_coding_role_passes_the_coding_engines_own_feasibility_check():
    from backend.app.services.assessment.programming_integration_v2 import programming_assessment_feasible

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    user = User(full_name="T", username="cov1", email="cov1@example.com", hashed_password="x", role="user")
    db.add(user)
    db.flush()

    blocked = []

    for role in CODE_ROLES:

        if role.get("domain") and not role.get("languages"):
            continue

        job = _job(role["title"])
        catalogue.enrich_job(job)
        db.add(job)
        db.flush()
        application = JobApplication(job_id=job.id, user_id=user.id, status="aptitude_passed")
        db.add(application)
        db.flush()

        feasible, reason = programming_assessment_feasible(db, application)
        if not feasible:
            blocked.append((role["title"], reason))

    assert blocked == []


# ------------------------------------------------------------------ every other role's assessment is built and marked
def _given_correct(questions):
    return [{"question_id": q["id"], "selected_answer": str(q["correct_answer"])} for q in questions]


@pytest.mark.parametrize("role", PRACTICAL_ROLES, ids=lambda r: r["title"])
def test_every_practical_role_gets_a_real_assessment_that_can_be_built_and_marked(role, monkeypatch):
    from backend.app.services.assessment.sjt_integration import score_practical

    _no_ai(monkeypatch)

    job = _job(role["title"])
    catalogue.enrich_job(job)
    spec = planner.rules_plan(job)["stages"][1]

    for voice in (True, False):
        monkeypatch.setattr(roleplay, "voice_available", lambda v=voice: v)

        built = build_practical_test(job, spec, seed=f"cov:{role['title']}")
        questions, payload = built["questions"], built["payload"]

        assert questions and payload["title"] and payload["environment"] in ("data", "lab", "written", "roleplay", "design")
        assert all(q["id"] and q["question"] for q in questions)

        env = payload["environment"]

        if env in ("data", "lab"):
            assert len(questions) == 5 and (payload.get("dataset") or payload.get("artifact"))
            result = score_practical(questions, _given_correct(questions))
            assert result["percentage"] == 100 and result["passed"] is True
            wrong = score_practical(questions, [{"question_id": q["id"], "selected_answer": "zzz"} for q in questions])
            assert wrong["percentage"] == 0

        elif env == "written":
            assert len(questions) == 2 and all(q["scenario"] and q["instructions"] and len(q["rubric"]) >= 3 for q in questions)
            nothing = score_practical(questions, [])
            assert nothing["percentage"] == 0

        elif env == "roleplay":
            scenario = built["roleplay_scenario"]
            assert scenario["persona"]["opening_line"] and scenario["candidate_brief"] and len(scenario["rubric"]) >= 3 and voice is True

        elif env == "design":
            assert payload["design"]["brief"] and payload["design"]["checklist"]

        # a role-play role without a voice stack is given a written task instead: still a real test
        if role["category"] == catalogue.ROLEPLAY and voice is False:
            assert env == "written" and len(questions) == 2


@pytest.mark.parametrize("role", [r for r in ROLES if r["category"] == catalogue.LAB], ids=lambda r: r["title"])
def test_a_lab_role_gets_the_lab_the_role_calls_for(role, monkeypatch):
    job = _job(role["title"])
    spec = planner.rules_plan(job)["stages"][1]
    built = build_practical_test(job, spec, seed="x")
    assert built["payload"]["lab"] == role["lab"] and built["payload"]["artifact"]["lines"]


# ------------------------------------------------------------------ titles the catalogue does not know
@pytest.mark.parametrize("title,skills", [
    ("Chief of Staff", ""), ("Beekeeper", ""), ("Release Manager", "Jenkins, Git"), ("Kubernetes Specialist", "Kubernetes, Helm"),
    ("Growth Hacker II", ""), ("Compliance Officer", "ISO 27001"), ("Technical Operations Lead", ""), ("", ""),
])
def test_an_unknown_title_still_gets_a_valid_plan(title, skills):
    job = _job(title or "Role", required_skills=skills)
    plan = planner.rules_plan(job)
    assert [s["slot"] for s in plan["stages"]] == ["aptitude", "programming"]
    assert plan["stages"][1]["kind"] in ("situational_judgment", "skills", "practical", "programming", "technical_domain")


def test_a_technical_role_with_nothing_runnable_gets_a_lab_not_a_blocked_coding_test():
    job = _job("Release Manager", required_skills="Git, Jira, Release management")
    assert job_role_shape(job) == "technical" and planner.stage3_fixed_kind(job) is None
    stage = planner.rules_plan(job)["stages"][1]
    assert stage["kind"] == "practical" and stage["environment"] == "lab"


def test_hr_can_still_override_what_the_catalogue_decides():
    job = _job("Java Developer", role_shape_override="non_technical")
    assert job_role_shape(job) == "non_technical"


# ------------------------------------------------------------------ the invite path
def test_the_invite_job_of_a_coding_role_with_a_one_line_text_is_given_a_language():
    job = _job("Backend Developer")
    assert not job_has_executable_language(job.required_skills or "", job.technologies or "")
    catalogue.enrich_job(job)
    assert job.technologies == "Python" and job.required_skills == "Python"

    explicit = _job("Backend Developer", required_skills="Go, PostgreSQL")
    catalogue.enrich_job(explicit)
    assert explicit.required_skills == "Go, PostgreSQL" and not explicit.technologies                      # what HR wrote is never touched

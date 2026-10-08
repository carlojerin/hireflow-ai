"""AI assessment planner: per-job plans (AI with a rules fallback), validation, storage, HR override, and the content generator
for AI-designed tests. No network: the LLM is stubbed."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from types import SimpleNamespace

import pytest

import backend.app.main  # noqa: F401  (registers every model)
from backend.app.services.assessment import assessment_planner as planner
from backend.app.services.assessment import skills_question_generator as skills
from backend.app.services.aptitude import ai_question_generator as gen


def _job(title, text="", **extra):
    base = dict(id=None, title=title, required_skills="", technologies="", requirements=text, description=text,
                responsibilities="", assessment_plan_json=None, company="Acme")
    base.update(extra)
    return SimpleNamespace(**base)


COPYWRITER = _job("Copywriter", "Write marketing copy, blog posts and ads for our brand. Excellent grammar and tone.")
ACCOUNTANT = _job("Accountant", "Maintain ledgers, prepare GST returns and bank reconciliations.")
BACKEND = _job("Backend Developer", "Build APIs", required_skills="Python, FastAPI, SQL")


def _kinds(plan):
    return [(s["slot"], s["kind"]) for s in plan["stages"]]


# ------------------------------------------------------------------ the rules fallback (no LLM)

def test_a_copywriter_gets_language_and_grammar_not_general_aptitude():
    plan = planner.rules_plan(COPYWRITER)
    aptitude, stage3 = plan["stages"]
    assert aptitude["kind"] == "skills" and aptitude["title"] == "Language & Grammar Assessment"
    assert sum(a["count"] for a in aptitude["areas"]) == aptitude["questions"] == 10
    assert stage3["kind"] == "situational_judgment"
    assert plan["source"] == "rules"


def test_an_accountant_gets_numerical_and_accounting_knowledge():
    aptitude = planner.rules_plan(ACCOUNTANT)["stages"][0]
    assert aptitude["kind"] == "skills" and "Finance" in aptitude["title"]


def test_a_technical_role_keeps_general_aptitude_and_its_fixed_programming_stage():
    plan = planner.rules_plan(BACKEND)
    assert _kinds(plan) == [("aptitude", "aptitude"), ("programming", "programming")]


def test_an_unrecognised_role_falls_back_to_the_standard_tests():
    assert _kinds(planner.rules_plan(_job("Barista", "Make coffee"))) == [("aptitude", "aptitude"), ("programming", "situational_judgment")]


# ------------------------------------------------------------------ validation of whatever the LLM (or HR) sends

def _skills_stage(**over):
    stage = {"slot": "aptitude", "kind": "skills", "title": "Brand Voice Assessment", "intro": "Tests tone.", "questions": 10,
             "areas": [{"label": "Tone", "topics": ["formal", "casual", "persuasive"], "count": 5},
                       {"label": "Clarity", "topics": ["wordiness", "jargon"], "count": 5}]}
    stage.update(over)
    return stage


def test_a_valid_ai_plan_is_accepted_and_questions_are_distributed_over_the_areas():
    plan = planner.normalise_plan({"summary": "x", "stages": [_skills_stage(questions=12), {"slot": "programming", "kind": "situational_judgment"}]}, COPYWRITER, "ai")
    stage = plan["stages"][0]
    assert stage["questions"] == 12 and sum(a["count"] for a in stage["areas"]) == 12 and all(a["count"] >= 2 for a in stage["areas"])
    assert {a["key"] for a in stage["areas"]} == {"tone", "clarity"}


@pytest.mark.parametrize("bad", [
    _skills_stage(title=""),                                                       # a custom test needs a title
    _skills_stage(areas=[{"label": "Only one", "topics": ["a", "b"], "count": 10}]),   # at least two areas
    {"slot": "aptitude", "kind": "programming"},                                    # not allowed in this slot
])
def test_unusable_stages_are_rejected(bad):
    with pytest.raises(ValueError):
        planner.normalise_plan({"stages": [bad]}, COPYWRITER, "ai")


def test_a_technical_role_can_never_be_given_a_non_technical_third_stage():
    plan = planner.normalise_plan({"stages": [{"slot": "aptitude", "kind": "aptitude"}, {"slot": "programming", "kind": "skills", "title": "x", "areas": []}]}, BACKEND, "ai")
    assert plan["stages"][1]["kind"] == "programming" and "areas" not in plan["stages"][1]


def test_missing_stages_default_to_standard_ones():
    plan = planner.normalise_plan({}, COPYWRITER, "ai")
    assert _kinds(plan) == [("aptitude", "aptitude"), ("programming", "situational_judgment")]


# ------------------------------------------------------------------ AI first, rules when the AI fails; stored once

def test_the_ai_plan_is_used_when_valid_and_the_rules_when_it_fails(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "on")

    monkeypatch.setattr(planner, "_ask_llm", lambda job: {"summary": "AI says", "stages": [_skills_stage(), {"slot": "programming", "kind": "situational_judgment"}]})
    plan = planner.get_job_plan(_job("Brand Strategist", "x"), store=False)
    assert plan["source"] == "ai" and plan["stages"][0]["title"] == "Brand Voice Assessment" and plan["summary"] == "AI says"

    def boom(job):
        raise RuntimeError("Groq down")

    monkeypatch.setattr(planner, "_ask_llm", boom)
    plan = planner.get_job_plan(COPYWRITER, store=False)
    assert plan["source"] == "rules" and plan["stages"][0]["title"] == "Language & Grammar Assessment"

    monkeypatch.setattr(planner, "_ask_llm", lambda job: {"stages": [{"slot": "aptitude", "kind": "nonsense"}]})
    assert planner.get_job_plan(COPYWRITER, store=False)["source"] == "rules"          # a bad AI answer is not trusted


def test_the_llm_is_not_called_when_switched_off_or_when_a_plan_is_already_stored(monkeypatch):
    calls = []
    monkeypatch.setattr(planner, "_ask_llm", lambda job: calls.append(1) or {})

    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")
    planner.get_job_plan(COPYWRITER, store=False)
    assert calls == []

    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "on")
    job = _job("Copywriter", "x", assessment_plan_json=json.dumps(planner.rules_plan(COPYWRITER)))
    assert planner.get_job_plan(job)["source"] == "rules" and calls == []               # the stored plan wins, no new call


def test_stage_for_slot_and_titles_never_call_the_llm(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "on")
    monkeypatch.setattr(planner, "_ask_llm", lambda job: pytest.fail("a read path made an LLM call"))

    assert planner.stage_for_slot(COPYWRITER, "aptitude")["title"] == "Language & Grammar Assessment"
    assert planner.stage_title(COPYWRITER, "aptitude", "Aptitude Test") == "Language & Grammar Assessment"
    assert planner.stage_title(BACKEND, "aptitude", "Aptitude Test") == "Aptitude Test"
    assert planner.stage_for_slot(BACKEND, "programming") is None


def test_hr_can_put_the_standard_tests_back():
    plan = planner.rules_plan(COPYWRITER)
    changed = planner.apply_override(plan, COPYWRITER, aptitude="standard")
    assert changed["stages"][0]["kind"] == "aptitude" and changed["source"] == "hr"
    assert planner.apply_override(plan, COPYWRITER)["stages"][0]["kind"] == "skills"


# ------------------------------------------------------------------ the content generator for an AI-designed test

class FakeProvider:
    """Answers each request with one valid question per slot it was asked for (parsed from the prompt)."""

    model = "fake"

    def __init__(self, break_first_for=None):
        self.calls, self.break_first_for = 0, break_first_for

    def generate_json(self, prompt, **kwargs):
        self.calls += 1
        slots = re.findall(r"^\d+\. category=(\w+) \(", prompt, re.M)
        questions = []
        for n, category in enumerate(slots):
            questions.append({"category": category, "difficulty": "intermediate", "question": f"{category} question {n} {self.calls}?",
                              "options": ["right", "wrong one", "wrong two", "wrong three"], "correct_answer": "right", "explanation": "Because."})
        if self.break_first_for and self.calls == 1:
            questions[0]["category"] = "not_a_real_area"                                # one bad question: only it is regenerated
        return json.dumps({"questions": questions})


@pytest.fixture
def fake_ai(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(gen, "_make_provider", lambda: provider)
    monkeypatch.setattr(gen, "_disputed_slots", lambda p, items: ([], True))
    return provider


def test_generator_writes_one_valid_question_per_slot_in_the_planned_areas(fake_ai):
    stage = planner.rules_plan(COPYWRITER)["stages"][0]
    questions = skills.generate_skills_questions(COPYWRITER, stage)

    assert len(questions) == 10 and [q["id"] for q in questions] == list(range(1, 11))
    counts = {}
    for q in questions:
        counts[q["category"]] = counts.get(q["category"], 0) + 1
        assert q["correct_answer"] in q["options"] and len(q["options"]) == 4
    assert counts == {a["key"]: a["count"] for a in stage["areas"]}


def test_a_bad_question_is_regenerated_alone(monkeypatch):
    provider = FakeProvider(break_first_for=True)
    monkeypatch.setattr(gen, "_make_provider", lambda: provider)
    monkeypatch.setattr(gen, "_disputed_slots", lambda p, items: ([], True))

    questions = skills.generate_skills_questions(COPYWRITER, planner.rules_plan(COPYWRITER)["stages"][0])

    assert len(questions) == 10 and provider.calls == 2


def test_generator_raises_so_callers_can_fall_back_when_it_cannot_finish(monkeypatch):
    class Broken:
        model = "fake"
        def generate_json(self, *a, **k):
            raise RuntimeError("model down")

    monkeypatch.setattr(gen, "_make_provider", lambda: Broken())
    monkeypatch.setattr(gen, "GENERATION_ATTEMPTS", 1)

    with pytest.raises(RuntimeError):
        skills.generate_skills_questions(COPYWRITER, planner.rules_plan(COPYWRITER)["stages"][0])


# ------------------------------------------------------------------ the two question builders use the plan, and fall back

def test_aptitude_builder_gives_a_copywriter_the_planned_test_and_falls_back_to_aptitude_on_failure(monkeypatch, fake_ai):
    from backend.app.services.aptitude import aptitude_question_service as aq

    user = SimpleNamespace(experience_level="junior")
    questions, source = aq.build_aptitude_questions(COPYWRITER, user, None, None)
    assert source == "ai" and {q["category"] for q in questions} == {"grammar_usage", "vocabulary_word_choice", "editing_style"}

    monkeypatch.setattr(skills, "generate_skills_questions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(aq, "generate_ai_aptitude_questions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("also down")))
    questions, source = aq.build_aptitude_questions(COPYWRITER, user, None, None)
    assert source in ("pool", "parametric") and len(questions) == 10                     # the candidate still gets a test


def test_a_technical_job_still_gets_the_standard_aptitude_generation(monkeypatch):
    from backend.app.services.aptitude import aptitude_question_service as aq

    seen = []
    monkeypatch.setattr(aq, "generate_ai_aptitude_questions", lambda *a, **k: seen.append("standard") or [{"id": 1, "category": "x"}])
    monkeypatch.setattr(aq.question_pool, "add", lambda *a, **k: None)

    aq.build_aptitude_questions(BACKEND, SimpleNamespace(experience_level="junior"), None, None)
    assert seen == ["standard"]


def test_sjt_builder_uses_role_specific_ai_scenarios_when_planned_else_the_standard_bank(monkeypatch, fake_ai):
    from backend.app.services.sjt import sjt_question_service as sq

    job = _job("Customer Support Agent", "x", assessment_plan_json=json.dumps(planner.normalise_plan(
        {"stages": [{"slot": "aptitude", "kind": "aptitude"},
                    {"slot": "programming", "kind": "situational_judgment", "title": "Support Scenarios", "style": "scenario",
                     "areas": [{"label": "Angry customers", "topics": ["refund demand", "delay"], "count": 5}, {"label": "Escalation", "topics": ["policy", "urgency"], "count": 5}]}]},
        _job("Customer Support Agent", "x"), "ai")))

    questions, source = sq.build_sjt_questions(job=job)
    assert source == "ai" and len(questions) == 10

    questions, source = sq.build_sjt_questions(job=_job("Barista", "Make coffee"))          # no areas planned: the standard bank
    assert source == "fallback" and len(questions) == 10

    monkeypatch.setattr(skills, "generate_skills_questions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert sq.build_sjt_questions(job=job)[1] == "fallback"                                # AI failure: the bank, never no test


def test_areas_without_topics_and_typographic_dashes_are_handled():
    plan = planner.normalise_plan({"stages": [
        {"slot": "aptitude", "kind": "aptitude"},
        {"slot": "programming", "kind": "situational_judgment", "title": "Pharmacy Judgement \u2011 Scenarios",
         "areas": [{"label": "Patient counseling"}, {"label": "Inventory \u2013 compliance"}]},
    ]}, _job("Pharmacist", "x"), "ai")
    stage3 = plan["stages"][1]
    assert stage3["title"] == "Pharmacy Judgement - Scenarios" and [a["key"] for a in stage3["areas"]] == ["patient_counseling", "inventory_compliance"]
    assert stage3["areas"][0]["topics"] == ["Patient counseling"] and sum(a["count"] for a in stage3["areas"]) == 10


def test_one_bad_stage_in_the_ai_plan_falls_back_to_standard_without_losing_the_good_stage(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "on")
    monkeypatch.setattr(planner, "_ask_llm", lambda job: {"stages": [_skills_stage(), {"slot": "programming", "kind": "skills", "title": "x", "areas": [{"label": "only one"}]}]})

    plan = planner.get_job_plan(COPYWRITER, store=False)

    assert plan["source"] == "ai" and plan["stages"][0]["title"] == "Brand Voice Assessment"
    assert plan["stages"][1]["kind"] == "situational_judgment" and "areas" not in plan["stages"][1]

    with pytest.raises(ValueError):                                   # a plan from a browser is still all-or-nothing
        planner.normalise_plan({"stages": [_skills_stage(), {"slot": "programming", "kind": "skills", "title": "x", "areas": [{"label": "only one"}]}]}, COPYWRITER, "ai")


# ------------------------------------------------------------------ the candidate / HR pages use the planned names

def _frontend(*parts):
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", *parts), encoding="utf-8") as f:
        return f.read()


def test_candidate_pages_load_the_title_script_and_it_cannot_loop_on_names_that_contain_the_default():
    for page in ("aptitude-test.html", "sjt-test.html", "assessment.html"):
        assert "assessment-titles.js" in _frontend(page)

    script = _frontend("js", "assessment-titles.js")
    assert "function swap(" in script and "text.split(pair[1])" in script         # text already holding the planned name is left alone
    assert "requestAnimationFrame" not in script                                   # background tabs pause animation frames


def test_the_invite_page_previews_the_plan_and_sends_it_back_with_the_choice():
    page, script = _frontend("company-invite-candidate.html"), _frontend("js", "company-invite-candidate.js")
    assert "icPlanCard" in page
    assert "/assessment-plan" in script and 'formData.append("assessment_plan"' in script and "aptitude_choice" in script and "stage3_choice" in script


def test_the_hr_report_shows_the_plan_and_uses_planned_names():
    report = _frontend("js", "company-candidate-report.js")
    assert "renderAssessmentPlan(data)" in report and "planTitle(data, \"aptitude\"" in report

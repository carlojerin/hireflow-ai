"""
Practical web design: the candidate builds a page by dragging components; code measures it and an AI judges it. In-memory database, no network
(the AI is stubbed).
"""

import json
import os
import sys
from types import SimpleNamespace as N

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_practical_assessments as PA

import backend.app.main  # noqa: F401
from backend.app.api.routes.hr_assessment_review import _practical_verdict, _sjt_review_data
from backend.app.services.assessment import assessment_planner as planner
from backend.app.services.assessment.sjt_integration import candidate_view, start_sjt_assessment, submit_sjt_assessment
from backend.app.services.assessment.stage_progression_service import _stage_3_assessment_type
from backend.app.services.sjt import practical_design as design


@pytest.fixture(autouse=True)
def design_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "off")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _el(i, type, x, y, w, h, text="", **extra):
    base = {"id": i, "type": type, "x": x, "y": y, "w": w, "h": h, "text": text, "fontSize": 16, "fontWeight": 400, "color": "#111827", "bg": "", "radius": 0, "align": "left"}
    base.update(extra)
    return base


def _good_layout():
    return {"elements": [
        _el(1, "nav", 0, 0, 1200, 72, "Tasklane   Features   Pricing", color="#ffffff", bg="#1f2937", fontWeight=700),
        _el(2, "section", 0, 72, 1200, 420, bg="#eef1ff"),
        _el(3, "heading", 80, 130, 640, 64, "Plan work. Ship on time.", fontSize=48, fontWeight=700),
        _el(4, "text", 80, 210, 560, 70, "Tasklane keeps your team's tasks, deadlines and files in one calm place.", fontSize=18, color="#374151"),
        _el(5, "button", 80, 310, 200, 52, "Start free trial", color="#ffffff", bg="#4f5bff", fontWeight=700, align="center", radius=10),
        _el(6, "image", 700, 120, 400, 300, "Product screenshot", bg="#d1d5db", color="#374151", align="center"),
        _el(7, "card", 80, 540, 340, 200, "Simple boards", bg="#ffffff"),
        _el(8, "card", 430, 540, 340, 200, "Deadline reminders", bg="#ffffff"),
        _el(9, "card", 780, 540, 340, 200, "Team chat", bg="#ffffff"),
        _el(10, "text", 80, 800, 700, 60, "\"We cut our status meetings in half.\" Meera, Operations Lead", fontSize=20, color="#374151"),
        _el(11, "footer", 0, 1000, 1200, 120, "Tasklane 2026   Contact   Privacy", color="#e5e7eb", bg="#111827", align="center", fontSize=14),
    ]}


BRIEF = design._BRIEFS[0]


# ------------------------------------------------------------------ planning
@pytest.mark.parametrize("title", ["UI Designer", "UX Designer", "Product Designer", "Web Designer", "UI/UX Designer", "Senior Visual Designer"])
def test_web_design_roles_get_the_page_design_task(title):
    stage = planner.rules_plan(PA._job(title))["stages"][1]
    assert stage["kind"] == "practical" and stage["environment"] == "design" and "Web Design" in stage["title"]


def test_a_ui_designer_with_a_technical_looking_skills_list_still_gets_it():
    job = PA._job("UI Designer", skills="HTML, CSS, JavaScript, React, Figma, Git", description="Design and build interfaces")
    assert planner.stage3_fixed_kind(job) is None
    assert planner.rules_plan(job)["stages"][1]["environment"] == "design"


def test_other_roles_are_not_pulled_into_it():
    assert planner.rules_plan(PA._job("Digital Marketing Manager", description="Works with the web design team on landing pages"))["stages"][1]["environment"] == "written"
    assert planner.rules_plan(PA._job("Video Editor"))["stages"][1]["kind"] == "situational_judgment"           # no video task: this is an IT company
    assert planner.rules_plan(PA._job("Graphic Designer"))["stages"][1]["kind"] == "situational_judgment"
    developer = PA._job("Frontend Developer", skills="React, JavaScript, CSS, HTML")
    assert planner.rules_plan(developer)["stages"][1]["kind"] in ("programming", "technical_domain")             # a developer still gets code


def test_the_switch_turns_the_design_task_off(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "off")
    assert planner.rules_plan(PA._job("UI Designer"))["stages"][1]["kind"] != "practical"


# ------------------------------------------------------------------ what the browser sends is validated
def test_the_layout_is_sanitised():
    raw = {"elements": [
        {"id": 1, "type": "heading", "x": "40", "y": 9999999, "w": 99999, "h": -5, "text": "  Hello   world  " + "x" * 500, "fontSize": 500, "color": "red", "bg": "#ABCDEF", "align": "diagonal"},
        {"id": 2, "type": "script", "x": 0, "y": 0, "w": 10, "h": 10},
        "junk", None,
    ] + [{"id": 100 + i, "type": "text", "x": 0, "y": 0, "w": 50, "h": 20, "text": "t"} for i in range(200)]}
    clean = design.sanitize_layout(json.dumps(raw))
    first = clean["elements"][0]
    assert len(clean["elements"]) == design.MAX_ELEMENTS and all(e["type"] != "script" for e in clean["elements"])
    assert first["fontSize"] == 96 and first["color"] == "#111827" and first["bg"] == "#ABCDEF" and first["align"] == "left" and len(first["text"]) == 300
    assert first["w"] <= design.CANVAS_W + 200 and first["h"] >= 2 and first["text"].startswith("Hello world")
    assert design.sanitize_layout("not json") == {"elements": []} and design.sanitize_layout(None) == {"elements": []}


# ------------------------------------------------------------------ what code measures
def test_contrast_follows_wcag():
    assert round(design.contrast_ratio("#000000", "#ffffff")) == 21
    assert design.contrast_ratio("#777777", "#ffffff") < 4.6 and design.contrast_ratio("#767676", "#ffffff") >= 4.5
    assert design.contrast_ratio("#ffffff", "#ffffff") == 1


def test_a_complete_well_built_page_scores_well_on_everything_code_can_measure():
    layout = design.sanitize_layout(_good_layout())
    m = design.measure(layout, BRIEF)
    scores = {c["criterion"]: c["score"] for c in design.auto_scores(m)}

    assert all(item["met"] for item in m["checklist"]), [c for c in m["checklist"] if not c["met"]]
    assert m["low_contrast_count"] == 0 and m["off_page"] == 0 and m["overlaps"] == 0 and m["brand_color_used"] is True
    assert scores["Completeness against the brief"] == 10 and scores["Readable colours and text size"] == 10 and scores["Typographic hierarchy"] == 10
    assert scores["Alignment and tidy layout"] >= 8


def test_missing_parts_are_named_in_the_marks():
    layout = design.sanitize_layout({"elements": [e for e in _good_layout()["elements"] if e["type"] not in ("footer", "button")]})
    m = design.measure(layout, BRIEF)
    completeness = next(c for c in design.auto_scores(m) if c["criterion"].startswith("Completeness"))

    assert not next(c for c in m["checklist"] if c["key"] == "cta")["met"] and not next(c for c in m["checklist"] if c["key"] == "footer")["met"]
    assert completeness["score"] < 10 and "call-to-action" in completeness["comment"] and "footer" in completeness["comment"]


def test_unreadable_text_overlaps_and_left_over_placeholders_cost_marks():
    messy = [
        _el(1, "nav", 0, 0, 1200, 72, "Navigation", color="#cccccc", bg="#ffffff"),
        _el(2, "heading", 100, 100, 400, 60, "Heading text", color="#dddddd", fontSize=20),
        _el(3, "text", 110, 110, 400, 60, "Paragraph text", color="#dddddd", fontSize=20),
        _el(4, "button", 900, 100, 150, 40, "Button", color="#f0f0f0", bg="#ffffff", fontSize=9),
        _el(5, "image", 1100, 100, 400, 200, "Image"),
    ]
    m = design.measure(design.sanitize_layout({"elements": messy}), BRIEF)
    scores = {c["criterion"]: c["score"] for c in design.auto_scores(m)}

    assert m["low_contrast_count"] >= 3 and m["overlaps"] >= 1 and m["off_page"] >= 1 and m["placeholder_text"] >= 3
    assert scores["Readable colours and text size"] <= 4 and scores["Typographic hierarchy"] <= 4 and scores["Completeness against the brief"] <= 5


def test_text_on_a_coloured_container_uses_the_containers_colour_not_the_white_page():
    layout = design.sanitize_layout({"elements": [
        _el(1, "section", 0, 0, 1200, 400, bg="#111827"),
        _el(2, "heading", 80, 100, 500, 60, "White on dark", color="#ffffff", fontSize=40),
        _el(3, "heading", 80, 200, 500, 60, "Dark on dark", color="#1f2937", fontSize=40),
    ]})
    m = design.measure(layout, BRIEF)
    assert m["low_contrast_count"] == 1 and m["low_contrast"][0]["text"] == "Dark on dark"


# ------------------------------------------------------------------ marking
class _Provider:
    def __init__(self, scores=(8, 7, 6)):
        self.scores, self.prompts = scores, []

    def generate_json(self, prompt, system=None, max_tokens=0, temperature=0):
        self.prompts.append(json.loads(prompt))
        names = [c for c in json.loads(prompt)["criteria"]]
        return json.dumps({"summary": "A clear, well-ordered page.", "criteria": [{"criterion": n, "score": s, "comment": "ok"} for n, s in zip(names, self.scores)]})


def test_the_final_mark_combines_the_measured_marks_and_the_ais(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(design, "_provider", lambda timeout: provider)

    graded = design.score_design(json.dumps(_good_layout()), BRIEF)

    assert graded["source"] == "ai" and graded["needs_review"] is False and len(graded["criteria"]) == 7
    code = [c for c in graded["criteria"] if c["kind"] == "code"]
    ai = [c for c in graded["criteria"] if c["kind"] == "ai"]
    assert len(code) == 4 and len(ai) == 3
    expected = round(100 * sum(c["score"] * c["weight"] for c in graded["criteria"]) / sum(c["max"] * c["weight"] for c in graded["criteria"]))
    assert graded["percent"] == expected and 80 <= graded["percent"] <= 100

    sent = provider.prompts[0]
    assert "[nav]" in sent["page"] and "Plan work. Ship on time." in sent["page"] and sent["measurements"]["overlaps"] == 0
    assert BRIEF["checklist"][0]["rule"] is not None and "rule" not in json.dumps(sent)                    # the AI gets the measurements, not our rule internals


def test_when_the_ai_is_unavailable_the_measured_marks_stand_and_are_flagged(monkeypatch):
    monkeypatch.setattr(design, "_provider", lambda timeout: (_ for _ in ()).throw(RuntimeError("down")))
    graded = design.score_design(json.dumps(_good_layout()), BRIEF)
    assert graded["source"] == "rules" and graded["needs_review"] is True and "Please look at the design" in graded["summary"]
    assert len(graded["criteria"]) == 4 and graded["percent"] >= 80


def test_an_empty_page_scores_zero_without_asking_the_ai(monkeypatch):
    monkeypatch.setattr(design, "_provider", lambda timeout: (_ for _ in ()).throw(AssertionError("the AI must not be called for an empty page")))
    for answer in (None, "", "garbage", json.dumps({"elements": [_el(1, "heading", 0, 0, 100, 40, "Hi")]})):
        graded = design.score_design(answer, BRIEF)
        assert graded["percent"] == 0 and graded["needs_review"] is False


# ------------------------------------------------------------------ through the stage-3 service
def _seed(db, title="UI Designer", skills="Figma, HTML, CSS, JavaScript, React"):
    return PA._seed(db, title, skills)


def test_the_design_test_is_built_for_a_web_designer_without_leaking_the_marking(monkeypatch):
    db = PA._db()
    user, job, application, session_record = _seed(db)

    assert _stage_3_assessment_type(db, application) == "situational_judgment"                              # rides the situational slot, not the coding one

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert assessment.kind == "practical" and view["practical"]["environment"] == "design"
    spec = view["practical"]["design"]
    assert spec["brief"] and spec["checklist"] and spec["canvas"] == {"w": 1200, "h": 1500} and spec["brand"]["primary"].startswith("#")
    assert view["questions"][0]["type"] == "design"

    shown = json.dumps(view)
    for secret in ('"rule"', "keywords", "min_size", "max_y"):
        assert secret not in shown

    minutes = (assessment.exam_expires_at - assessment.started_at).total_seconds() / 60
    assert 39 <= minutes <= 41


def test_submitting_a_page_is_scored_and_stored_for_hr(monkeypatch):
    monkeypatch.setattr(design, "_provider", lambda timeout: _Provider())
    db = PA._db()
    user, job, application, session_record = _seed(db)
    assessment, _ = start_sjt_assessment(db, session_record, application)

    brief = json.loads(assessment.questions)[0]["brief"]
    layout = _good_layout()
    result = submit_sjt_assessment(db, assessment, application, [{"question_id": 1, "selected_answer": json.dumps(layout)}])

    assert result["kind"] == "practical" and result["percentage"] > 0
    db.refresh(assessment)
    stored = json.loads(assessment.result_json)["items"][0]
    assert stored["type"] == "design" and stored["layout"]["elements"] and stored["criteria"] and "checklist" in stored["metrics"]

    review = _sjt_review_data(db, application)
    item = review["practical_items"][0]
    assert item["type"] == "design" and len(item["layout"]["elements"]) == len(layout["elements"]) and item["scenario"] == brief["brief"]

    verdict = _practical_verdict(review)
    assert verdict["marked_by"] == "ai" and verdict["marks"] and verdict["environment"] == "design"


def test_a_large_layout_survives_the_answer_size_limit(monkeypatch):
    monkeypatch.setattr(design, "_provider", lambda timeout: _Provider())
    db = PA._db()
    user, job, application, session_record = _seed(db)
    assessment, _ = start_sjt_assessment(db, session_record, application)

    elements = [_el(i + 1, "text", 40 + (i % 20) * 8, 40 + (i // 20) * 60, 200, 40, "Some text for this element " + str(i)) for i in range(60)]
    text = json.dumps({"elements": elements})
    assert len(text) > 6000                                                                                # bigger than a written answer may be

    submit_sjt_assessment(db, assessment, application, [{"question_id": 1, "selected_answer": text}])
    db.refresh(assessment)
    assert len(json.loads(assessment.result_json)["items"][0]["layout"]["elements"]) == 60


def test_the_briefs_are_complete_and_the_pick_fits_the_job():
    for brief in design._BRIEFS:
        assert brief["title"] and brief["brief"] and brief["copy"]["headline"] and len(brief["checklist"]) >= 5 and brief["brand"]["primary"].startswith("#")
    cafe = design.pick_brief(N(title="Web Designer", description="Websites for restaurants and cafe owners", requirements="", responsibilities="", required_skills="food"), "1")
    assert cafe["key"] == "cafe"
    assert design.pick_brief(N(title="UI Designer"), "7")["key"] == design.pick_brief(N(title="UI Designer"), "7")["key"]          # stable for a candidate


def test_the_candidate_view_of_a_brief_never_carries_the_rules():
    view = design.candidate_view_of(BRIEF)
    assert view["checklist"] == [c["label"] for c in BRIEF["checklist"]] and "rule" not in json.dumps(view)

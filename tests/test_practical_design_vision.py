"""
The design test is marked by a VISION model that looks at a picture of the page (drawn on the server from the validated layout), averaged over two
looks, with a chain of models and fallbacks (text-only AI, then the measured marks). No network: the model and the Gemini client are stubs.
"""

import json
import os
import sys
from io import BytesIO

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_practical_assessments as PA
import test_practical_design as D

import backend.app.main  # noqa: F401
from backend.app.api.routes.hr_assessment_review import _practical_verdict, _sjt_review_data
from backend.app.services.assessment.sjt_integration import start_sjt_assessment, submit_sjt_assessment
from backend.app.services.sjt import practical_design as design
from backend.app.services.sjt.design_renderer import render_png

BRIEF = D.BRIEF


@pytest.fixture(autouse=True)
def design_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "off")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _look(scores=(7, 6, 8, 6), summary="A tidy page with a clear call to action.", problems=("The cards are empty.",), model="stub-vision"):
    def fake(png, brief, m):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"                                                   # the model is given a real picture
        return {"criteria": [{"criterion": name, "score": score, "comment": f"{name} ok"} for (name, _w), score in zip(design._VISION_CRITERIA, scores)],
                "summary": summary, "problems": list(problems), "_model": model}
    return fake


def test_the_vision_model_marks_the_picture_and_two_looks_are_averaged(monkeypatch):
    calls = []
    scores = iter([(8, 8, 8, 8), (6, 6, 6, 6)])

    def look(png, brief, m):
        calls.append(png)
        return _look(next(scores))(png, brief, m)

    monkeypatch.setattr(design, "_one_look", look)
    monkeypatch.setattr(design, "_provider", lambda timeout: (_ for _ in ()).throw(AssertionError("the text AI must not be needed")))

    graded = design.score_design(json.dumps(D._good_layout()), BRIEF)

    assert graded["source"] == "vision" and graded["needs_review"] is False and len(calls) == design.VISION_LOOKS == 2
    vision = [c for c in graded["criteria"] if c["kind"] == "vision"]
    assert [c["score"] for c in vision] == [7, 7, 7, 7] and all(c["spread"] == 2.0 for c in vision)       # (8 + 6) / 2, and the disagreement is recorded
    assert len(graded["criteria"]) == 8 and graded["problems"] == ["The cards are empty."]
    assert graded["summary"].startswith("A tidy page")


def test_one_failed_look_does_not_stop_the_mark(monkeypatch):
    outcomes = iter([True, False])

    def look(png, brief, m):
        if next(outcomes):
            raise RuntimeError("503 UNAVAILABLE")
        return _look((9, 9, 9, 9))(png, brief, m)

    monkeypatch.setattr(design, "_one_look", look)
    graded = design.score_design(json.dumps(D._good_layout()), BRIEF)
    assert graded["source"] == "vision" and [c["score"] for c in graded["criteria"] if c["kind"] == "vision"] == [9, 9, 9, 9]


def test_if_every_look_fails_the_text_only_ai_marks_and_then_the_measurements(monkeypatch):
    monkeypatch.setattr(design, "_one_look", lambda png, brief, m: (_ for _ in ()).throw(RuntimeError("429 quota")))
    monkeypatch.setattr(design, "_provider", lambda timeout: D._Provider())
    text_ai = design.score_design(json.dumps(D._good_layout()), BRIEF)
    assert text_ai["source"] == "ai" and len([c for c in text_ai["criteria"] if c["kind"] == "ai"]) == 3

    monkeypatch.setattr(design, "_provider", lambda timeout: (_ for _ in ()).throw(RuntimeError("down")))
    rules = design.score_design(json.dumps(D._good_layout()), BRIEF)
    assert rules["source"] == "rules" and rules["needs_review"] is True


class _FakeGenai:
    """A stand-in for google.genai.Client that records what it was sent."""

    def __init__(self, outcomes):
        self.outcomes, self.calls = outcomes, []
        fake = self

        class Models:
            def generate_content(self, model, contents, config):
                fake.calls.append({"model": model, "image": contents[0], "text": contents[1], "system": config.system_instruction})
                outcome = fake.outcomes(model)
                if isinstance(outcome, Exception):
                    raise outcome
                return type("R", (), {"text": outcome, "usage_metadata": None})()

        class Client:
            def __init__(self, **kw):
                self.models = Models()

            def close(self):
                pass

        self.Client = Client


def _answer(score=7):
    return json.dumps({"criteria": [{"criterion": n, "score": score, "comment": ""} for n, _w in design._VISION_CRITERIA], "summary": "ok", "problems": []})


def test_the_vision_prompt_carries_the_brief_and_the_measurements_but_never_our_rules(monkeypatch):
    import google.genai

    fake = _FakeGenai(lambda model: _answer())
    monkeypatch.setattr(google.genai, "Client", fake.Client)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "model-a,model-b")

    m = design.measure(design.sanitize_layout(D._good_layout()), BRIEF)
    result = design._one_look(b"\x89PNG-bytes", BRIEF, m)

    sent = fake.calls[0]
    assert result["_model"] == "model-a" and sent["model"] == "model-a"
    payload = json.loads(sent["text"])
    assert payload["brief"] == BRIEF["brief"] and payload["measurements"]["overlaps"] == 0 and payload["the_required_parts_missing"] == []
    assert "rule" not in sent["text"] and "min_size" not in sent["text"]
    assert "PICTURE" in sent["system"]


def test_the_next_vision_model_is_tried_when_one_is_busy_or_over_quota(monkeypatch):
    import google.genai

    def outcome(model):
        return {"model-a": RuntimeError("503 UNAVAILABLE"), "model-b": RuntimeError("429 RESOURCE_EXHAUSTED")}.get(model, _answer())

    fake = _FakeGenai(outcome)
    monkeypatch.setattr(google.genai, "Client", fake.Client)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "model-a,model-b,model-c")

    m = design.measure(design.sanitize_layout(D._good_layout()), BRIEF)
    assert design._one_look(b"png", BRIEF, m)["_model"] == "model-c" and [c["model"] for c in fake.calls] == ["model-a", "model-b", "model-c"]


def test_an_answer_without_criteria_counts_as_a_failure_and_the_next_model_is_tried(monkeypatch):
    import google.genai

    fake = _FakeGenai(lambda model: "{}" if model == "model-a" else _answer())
    monkeypatch.setattr(google.genai, "Client", fake.Client)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "model-a,model-b")

    m = design.measure(design.sanitize_layout(D._good_layout()), BRIEF)
    assert design._one_look(b"png", BRIEF, m)["_model"] == "model-b"


def test_a_second_round_over_the_models_is_tried_when_the_first_failed_quickly(monkeypatch):
    import google.genai

    fake = _FakeGenai(lambda model: RuntimeError("503 UNAVAILABLE"))
    monkeypatch.setattr(google.genai, "Client", fake.Client)
    monkeypatch.setattr(design.time, "sleep", lambda s: None)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "model-a,model-b")

    m = design.measure(design.sanitize_layout(D._good_layout()), BRIEF)
    with pytest.raises(RuntimeError):
        design._one_look(b"png", BRIEF, m)
    assert [c["model"] for c in fake.calls] == ["model-a", "model-b", "model-a", "model-b"]


def test_no_api_key_is_a_clean_failure_not_a_crash(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    m = design.measure(design.sanitize_layout(D._good_layout()), BRIEF)
    with pytest.raises(RuntimeError):
        design._one_look(b"png", BRIEF, m)


def test_the_vision_model_can_be_switched_off_and_the_list_is_read_in_order(monkeypatch):
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "off")
    assert design.vision_models() == []
    monkeypatch.setenv("AI_DESIGN_VISION_MODELS", "a, b ,c")
    assert design.vision_models() == ["a", "b", "c"]
    monkeypatch.delenv("AI_DESIGN_VISION_MODELS")
    assert design.vision_models()[0] == "gemini-3-flash-preview"


# ------------------------------------------------------------------ the picture the model looks at
def test_the_page_is_drawn_as_a_real_picture_from_the_layout():
    layout = design.sanitize_layout(D._good_layout())
    image = Image.open(BytesIO(render_png(layout, scale=1.0))).convert("RGB")

    assert image.size[0] == 1200 and 500 <= image.size[1] <= 1700
    assert image.getpixel((600, 10)) == (31, 41, 55)                                                      # the navigation bar's dark fill
    assert image.getpixel((600, 100)) == (238, 241, 255)                                                  # the hero section behind the headline
    assert image.getpixel((150, 1050)) == (17, 24, 39)                                                    # the footer
    assert image.getpixel((1190, 600)) == (255, 255, 255)                                                 # the white page
    assert image.getpixel((100, 330)) == (79, 91, 255)                                                    # the call-to-action button

    heading_area = image.crop((80, 130, 700, 190))
    assert len({heading_area.getpixel((x, y)) for x in range(0, 620, 3) for y in range(0, 60, 3)}) > 3     # there is text drawn in it


def test_an_element_is_clipped_to_its_own_box_and_the_page_never_grows_without_limit():
    layout = design.sanitize_layout({"elements": [
        D._el(1, "button", 100, 100, 100, 30, "A very long label that cannot possibly fit inside this little button box", color="#ffffff", bg="#000000", align="center"),
        D._el(2, "heading", 0, 99999, 200, 50, "Far below"),
    ]})
    image = Image.open(BytesIO(render_png(layout, scale=1.0))).convert("RGB")

    assert image.getpixel((300, 115)) == (255, 255, 255)                                                  # nothing spilled out to the right of the button
    assert image.size[1] <= 1700


def test_an_empty_page_is_a_plain_white_picture():
    image = Image.open(BytesIO(render_png({"elements": []}))).convert("RGB")
    assert {image.getpixel((x, y)) for x in range(0, image.size[0], 40) for y in range(0, image.size[1], 40)} == {(255, 255, 255)}


def test_the_same_layout_always_draws_the_same_picture():
    layout = design.sanitize_layout(D._good_layout())
    assert render_png(layout) == render_png(layout)


# ------------------------------------------------------------------ what HR sees
def test_the_review_shows_the_vision_source_and_the_visual_problems(monkeypatch):
    monkeypatch.setattr(design, "_one_look", _look(problems=("The hero image is a grey box.", "The cards have no content.")))
    db = PA._db()
    user, job, application, session_record = D._seed(db)
    assessment, _ = start_sjt_assessment(db, session_record, application)
    submit_sjt_assessment(db, assessment, application, [{"question_id": 1, "selected_answer": json.dumps(D._good_layout())}])

    verdict = _practical_verdict(_sjt_review_data(db, application))
    assert verdict["marked_by"] == "vision" and "Design: The hero image is a grey box." in verdict["gaps"]
    assert any(m["label"].startswith("Visual polish") for m in verdict["marks"])

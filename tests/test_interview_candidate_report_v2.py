"""Detailed HR interview report: shape cleaning, one review per real turn, verbatim closing answers, safe fallback."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from types import SimpleNamespace

from backend.app.services.assessment import interview_candidate_report_v2 as report_module
from backend.app.services.assessment.interview_candidate_report_v2 import generate_interview_candidate_report
from backend.app.services.assessment.interview_llm_provider import InterviewLLMUnavailableError


def _turn(n, competency, question, answer):
    return SimpleNamespace(turn_number=n, competency=competency, question=question, candidate_answer=answer)


TURNS = [
    _turn(1, "Python", "Tell me about a Python project.", "I built an invoicing API with FastAPI and PostgreSQL."),
    _turn(2, "AI Tool Fluency", "Which AI tools do you use?", "Mostly Cursor and some ChatGPT for debugging."),
    _turn(3, "Salary Expectations", "What package do you expect?", "Around 12 LPA, a bit flexible."),
    _turn(4, "Closing Thoughts & Fit", "Anything we have not covered?", "I plan weekly, pair often and flag risks early."),
]
JOB = SimpleNamespace(title="Backend Developer", company="Acme", required_skills="Python, SQL", description="Build APIs.")


class _Provider:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def generate_json(self, prompt, system=None, **kwargs):
        self.prompt = prompt
        if self.error:
            raise self.error
        return self.reply


def _use(monkeypatch, provider):
    monkeypatch.setattr(report_module, "get_evaluator_llm_provider", lambda: provider)


def test_report_has_a_review_for_every_turn_with_the_real_question_and_answer(monkeypatch):
    model_reply = {
        "candidate_overview": "A backend developer with an API project and sensible AI habits.",
        "answer_reviews": [
            {"turn": 1, "answer_summary": "Built an invoicing API.", "assessment": "Concrete, names the stack.", "quality": "strong"},
            {"turn": 3, "answer_summary": "12 LPA.", "assessment": "Clear.", "quality": "banana"},          # turn 2 and 4 missing, bad quality
            {"turn": 99, "answer_summary": "invented turn", "assessment": "x", "quality": "weak"},          # not a real turn: dropped
        ],
        "strengths": ["Names the real stack."], "concerns": [], "communication": "Clear.", "resume_consistency": "Matches.",
        "ai_tools": {"tools": ["Cursor", "ChatGPT"], "assessment": "Real use."}, "compensation": "12 LPA, flexible.",
        "work_style_and_fit": "Plans weekly.", "suggested_follow_ups": ["Ask about testing."], "overall_impression": "Solid.",
    }
    provider = _Provider(reply=json.dumps(model_reply))
    _use(monkeypatch, provider)

    report = generate_interview_candidate_report(TURNS, JOB, resume_summary="5 yrs Python", stage_facts={"Aptitude": "6/10, did not pass"})

    assert report["generated_by"] == "ai"
    assert [r["turn"] for r in report["answer_reviews"]] == [1, 2, 3, 4]                                     # one per real turn, in order
    assert report["answer_reviews"][0]["question"] == "Tell me about a Python project."
    assert report["answer_reviews"][0]["answer"].startswith("I built an invoicing API")                      # the real words, from our data
    assert report["answer_reviews"][0]["quality"] == "strong" and report["answer_reviews"][2]["quality"] == "adequate"
    assert report["answer_reviews"][1]["assessment"] == ""                                                    # model skipped it: left empty, never invented
    assert report["ai_tools"]["tools"] == ["Cursor", "ChatGPT"]
    assert "6/10, did not pass" in provider.prompt and "5 yrs Python" in provider.prompt                      # earlier results and resume reach the model


def test_the_closing_answers_are_copied_verbatim_by_code_not_by_the_model(monkeypatch):
    _use(monkeypatch, _Provider(reply=json.dumps({"candidate_overview": "x", "compensation": "the model paraphrase"})))

    stated = generate_interview_candidate_report(TURNS, JOB)["stated_by_candidate"]

    assert stated["expected_package"]["answer"] == "Around 12 LPA, a bit flexible."
    assert stated["work_in_company"]["answer"] == "I plan weekly, pair often and flag risks early."
    assert stated["ai_tools"][0]["answer"] == "Mostly Cursor and some ChatGPT for debugging."


def test_any_failure_falls_back_to_a_plain_transcript_report(monkeypatch):
    for provider in (_Provider(error=InterviewLLMUnavailableError("down")), _Provider(reply="not json"), _Provider(reply="{}")):
        _use(monkeypatch, provider)
        report = generate_interview_candidate_report(TURNS, JOB)
        assert report["generated_by"] == "fallback"
        assert len(report["answer_reviews"]) == 4 and report["answer_reviews"][0]["answer"]
        assert report["stated_by_candidate"]["expected_package"]["answer"].startswith("Around 12 LPA")


def test_no_answered_turns_never_calls_the_model(monkeypatch):
    provider = _Provider(error=AssertionError("must not be called"))
    _use(monkeypatch, provider)
    assert generate_interview_candidate_report([], JOB)["generated_by"] == "fallback"

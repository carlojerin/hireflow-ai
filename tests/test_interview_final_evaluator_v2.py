"""
Tests for interview_final_evaluator_v2.py -- the independent, second-opinion
final evaluator pass (Gap 1 of the "AI interview to 100%" plan). Covers:
  - a real, valid LLM rubric response is accepted and used as-is
  - a fabricated key_evidence quote (not an actual substring of any real
    candidate answer) is silently dropped, never trusted
  - an unknown competency name in the reply is rejected and retried, then
    falls back
  - any InterviewLLMUnavailableError falls back immediately (no retry)
  - malformed JSON after the one retry falls back
  - no answered turns at all falls back without ever calling the LLM

Gemini/Groq-backed paths are isolated behind monkeypatched generate_json,
exactly like this project's existing interview_brain_v2 tests.
"""

import json
from types import SimpleNamespace

from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2
from backend.app.services.assessment import interview_final_evaluator_v2 as evaluator
from backend.app.services.assessment.interview_llm_provider import InterviewLLMUnavailableError


def _turn(turn_number, competency, question, answer):
    return SimpleNamespace(
        turn_number=turn_number, competency=competency, question=question, candidate_answer=answer,
    )


def _analysis(competency, strength_level="moderate", evidence=None):
    return AnswerAnalysisV2(competency=competency, evidence=evidence or [], strength_level=strength_level)


def _turns_and_analyses():
    turns = [
        _turn(1, "Java", "Tell me about a Java project.", "I built a REST API with Spring Boot using HikariCP for pooling."),
        _turn(2, "Java", "Why HikariCP?", "It was faster under load than the default pool and easy to configure."),
    ]
    analyses = [_analysis("Java", "moderate", ["mentioned HikariCP"]), _analysis("Java", "strong", ["faster under load"])]
    return turns, analyses


def test_valid_rubric_response_is_accepted_and_labeled_llm_rubric(monkeypatch):

    def _good(*a, **k):
        return json.dumps({
            "competency_evaluations": [
                {
                    "competency": "Java", "label": "Strong evidence", "turns_covering": 2,
                    "key_evidence": ["It was faster under load than the default pool"],
                    "key_weaknesses": [], "veracity_flag": None,
                },
            ],
            "strengths": ["Java"], "weaknesses": [], "insufficient_evidence": [],
            "overall_summary": "Candidate showed solid, evidenced Java backend experience.",
        })

    monkeypatch.setattr(evaluator, "_ask_llm", _good)

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java, Spring")

    assert result.evaluated_by == "llm_rubric"
    assert result.total_turns == 2
    assert result.competency_evaluations[0].label == "Strong evidence"
    assert result.competency_evaluations[0].key_evidence == ["It was faster under load than the default pool"]


def test_fabricated_quote_is_silently_dropped_not_trusted(monkeypatch):

    def _fabricated(*a, **k):
        return json.dumps({
            "competency_evaluations": [
                {
                    "competency": "Java", "label": "Strong evidence", "turns_covering": 2,
                    "key_evidence": ["I have ten years of Kubernetes experience"],  # never actually said
                    "key_weaknesses": [], "veracity_flag": None,
                },
            ],
            "strengths": ["Java"], "weaknesses": [], "insufficient_evidence": [],
            "overall_summary": "x",
        })

    monkeypatch.setattr(evaluator, "_ask_llm", _fabricated)

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "llm_rubric"
    assert result.competency_evaluations[0].key_evidence == []


def test_unknown_competency_name_is_rejected_then_falls_back(monkeypatch):

    def _unknown_competency(*a, **k):
        return json.dumps({
            "competency_evaluations": [
                {"competency": "Rust", "label": "Strong evidence", "turns_covering": 2, "key_evidence": [], "key_weaknesses": [], "veracity_flag": None},
            ],
            "strengths": [], "weaknesses": [], "insufficient_evidence": [], "overall_summary": "x",
        })

    monkeypatch.setattr(evaluator, "_ask_llm", _unknown_competency)

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "fallback_aggregation"


def test_llm_unavailable_falls_back_immediately_without_retry(monkeypatch):

    calls = []

    def _unavailable(*a, **k):
        calls.append(1)
        raise InterviewLLMUnavailableError("groq request failed (HTTP 503)")

    monkeypatch.setattr(evaluator, "_ask_llm", _unavailable)

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "fallback_aggregation"
    assert len(calls) == 1


def test_malformed_json_after_retry_falls_back(monkeypatch):

    monkeypatch.setattr(evaluator, "_ask_llm", lambda *a, **k: "{not valid json")

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "fallback_aggregation"


def test_no_answered_turns_falls_back_without_calling_the_llm(monkeypatch):

    def _should_not_be_called(*a, **k):
        raise AssertionError("the LLM must never be called when there are no answered turns")

    monkeypatch.setattr(evaluator, "_ask_llm", _should_not_be_called)

    result = evaluator.evaluate_interview_final_v2([], [], ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "fallback_aggregation"
    assert result.total_turns == 0


def test_veracity_flag_is_surfaced_in_flattened_veracity_concerns(monkeypatch):

    def _flagged(*a, **k):
        return json.dumps({
            "competency_evaluations": [
                {
                    "competency": "Java", "label": "Moderate evidence", "turns_covering": 2,
                    "key_evidence": [], "key_weaknesses": [],
                    "veracity_flag": "claimed a 90% latency reduction with no numbers given earlier in the answer",
                },
            ],
            "strengths": [], "weaknesses": ["Java"], "insufficient_evidence": [], "overall_summary": "x",
        })

    monkeypatch.setattr(evaluator, "_ask_llm", _flagged)

    turns, analyses = _turns_and_analyses()
    result = evaluator.evaluate_interview_final_v2(turns, analyses, ["Java"], "Backend Developer", "Java")

    assert result.evaluated_by == "llm_rubric"
    assert len(result.veracity_concerns) == 1
    assert "latency reduction" in result.veracity_concerns[0]

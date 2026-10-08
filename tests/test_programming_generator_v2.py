import os

import pytest

from backend.app.schemas.assessment_blueprint import AssessmentBlueprint, DifficultyDistribution
from backend.app.schemas.assessment_plan import AssessmentPlan, AssessmentPlanItem
from backend.app.services.assessment import gemini_client
from backend.app.services.assessment.programming_generator_v2 import (
    generate_programming_assessment_v2,
)


class _FakeJob:
    def __init__(self, title="Backend Developer", description="Build APIs.", required_skills="Python"):
        self.title = title
        self.description = description
        self.required_skills = required_skills


def _blueprint(level="intermediate"):
    return AssessmentBlueprint(
        candidate_id=1, job_id=1, job_title="Backend Developer",
        total_questions=10, candidate_experience_level=level,
        difficulty_distribution=DifficultyDistribution(basic=2, intermediate=6, advanced=2),
    )


def _plan(technology=None, extra_types=None):
    items = [
        AssessmentPlanItem(
            assessment_type="programming", technology=technology,
            execution_supported=False, reason="Job requires this.",
            priority="high", difficulty="intermediate", question_count=5,
        )
    ]
    if extra_types:
        items.extend(extra_types)
    return AssessmentPlan(role_type="Backend Developer", assessment_types=items, generated_by="deterministic_fallback")


def _plan_no_programming():
    return AssessmentPlan(
        role_type="HR Executive",
        assessment_types=[
            AssessmentPlanItem(
                assessment_type="hr_scenario", technology=None,
                execution_supported=False, reason="Role is HR-focused.",
                priority="high", difficulty="intermediate", question_count=5,
            )
        ],
        generated_by="deterministic_fallback",
    )


# D, E, F: fallback always produces a valid, executable 5-question set
def test_fallback_produces_exactly_5_questions_with_2_to_4_subparts_each():
    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(), use_gemini=False)
    assert result.status == "generated"
    assert result.generated_by == "fallback"
    qs = result.question_set
    assert len(qs.main_questions) == 5
    for q in qs.main_questions:
        assert 2 <= len(q.subparts) <= 4
        for sp in q.subparts:
            for tc in sp.test_cases:
                assert tc.stdin is not None
                assert tc.expected_stdout


# C: Gemini unavailable -> deterministic fallback, platform stays usable
def test_gemini_unavailable_falls_back(monkeypatch):
    def _raise(*a, **k):
        raise gemini_client.GeminiUnavailableError("no key")

    monkeypatch.setattr(gemini_client, "generate_json_content", _raise)
    import backend.app.services.assessment.programming_generator_v2 as gen_mod
    monkeypatch.setattr(gen_mod, "generate_json_content", _raise)

    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(), use_gemini=True)
    assert result.status == "generated"
    assert result.generated_by == "fallback"
    assert len(result.question_set.main_questions) == 5


# B: malformed Gemini output is rejected by the validator, falls back
def test_malformed_gemini_output_is_rejected_and_falls_back(monkeypatch):
    import backend.app.services.assessment.programming_generator_v2 as gen_mod

    def _bad_json(*a, **k):
        return '{"main_questions": [], "generated_by": "gemini"}'

    monkeypatch.setattr(gen_mod, "generate_json_content", _bad_json)

    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(), use_gemini=True)
    assert result.status == "generated"
    assert result.generated_by == "fallback"  # never trusted the malformed output


# V: no Python substitution for a required technology JobPilot can't execute
#
# Task 5C, Complete Multi-Language Execution Engine: "java" and
# "go" are now genuinely execution_supported (real runners,
# real toolchains) and no longer demonstrate this case -- "swift"
# is the current genuine example (registered runner architecture,
# but not execution_supported; see swift_runner.py's docstring
# for the confirmed root cause).
def test_unsupported_required_language_is_not_substituted_with_python():
    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(technology="swift"), use_gemini=False)
    assert result.status == "capability_unavailable"
    assert result.question_set is None
    assert result.required_technology == "swift"
    assert "python" not in result.message.lower() or "cannot yet execute" in result.message.lower()


def test_unsupported_language_guard_fires_consistently_across_calls():
    # a second, independent call with the same unsupported
    # technology confirms this isn't a one-off/cached fluke
    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(technology="swift"), use_gemini=False)
    assert result.status == "capability_unavailable"
    assert result.question_set is None


# MBA HR: no programming item in plan at all -> not_applicable, no assessment forced
def test_non_technical_role_produces_not_applicable():
    result = generate_programming_assessment_v2(_FakeJob(title="HR Executive"), _blueprint(), _plan_no_programming(), use_gemini=False)
    assert result.status == "not_applicable"
    assert result.question_set is None


def test_pinned_javascript_technology_narrows_allowed_languages():
    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(technology="javascript"), use_gemini=False)
    assert result.status == "generated"
    for q in result.question_set.main_questions:
        assert q.allowed_languages == ["javascript"]


def test_generic_technology_allows_every_execution_supported_language():
    # Task 5C, Complete Multi-Language Execution Engine: the
    # unpinned default now dynamically reflects every genuinely
    # execution_supported language (12, as of this task -- swift
    # excluded since it is not execution_supported), not a
    # hardcoded 3-language literal -- see programming_generator_v2
    # .py's _general_purpose_languages() for why a hardcoded set
    # was a real bug this task hit and fixed.
    from backend.app.services.assessment.language_capability_registry import (
        list_all_known_languages,
    )

    expected = {
        c.canonical_name for c in list_all_known_languages()
        if c.execution_supported and c.canonical_name not in ("sql", "html")
    }

    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(technology=None), use_gemini=False)
    assert result.status == "generated"
    for q in result.question_set.main_questions:
        assert set(q.allowed_languages) == expected
        assert "swift" not in q.allowed_languages


# A: real end-to-end Gemini generation -- only runs if a real key is configured
@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not configured")
def test_real_gemini_generates_a_valid_question_set():
    result = generate_programming_assessment_v2(
        _FakeJob(title="Python Backend Developer", required_skills="Python, FastAPI, PostgreSQL"),
        _blueprint(), _plan(technology="python"), use_gemini=True,
    )
    assert result.status == "generated"
    assert result.question_set is not None
    assert len(result.question_set.main_questions) == 5


# ============================================================
# TASK 5G (Programming V3): duplicate avoidance + richer blueprint
# context in the prompt.
# ============================================================

def _blueprint_with_skills():
    from backend.app.schemas.assessment_blueprint import SkillAssessmentItem

    return AssessmentBlueprint(
        candidate_id=1, job_id=1, job_title="Backend Developer",
        total_questions=10, candidate_experience_level="senior",
        difficulty_distribution=DifficultyDistribution(basic=0, intermediate=3, advanced=7),
        skills_to_assess=[
            SkillAssessmentItem(
                skill="Java", priority="critical", match_status="strong_match",
                question_count=3, reason="Core job requirement, candidate has direct experience.",
            ),
            SkillAssessmentItem(
                skill="Concurrency", priority="high", match_status="partial_match",
                question_count=2, reason="Job requires it; resume shows limited evidence.",
            ),
        ],
        job_focus_areas=["API design", "concurrency"],
        candidate_focus_areas=["Spring Boot", "REST APIs"],
        skill_gaps=["Kafka"],
    )


# Gemini prompt must actually carry the ALREADY-COMPUTED blueprint
# signal (skills_to_assess/focus areas/skill_gaps), not just
# experience level -- this is the real "candidate + job context"
# requirement from Task 5G Section 12, satisfied without any new
# plumbing since build_assessment_blueprint() already derives all
# of it from the candidate's real resume/profile.
def test_gemini_prompt_carries_blueprint_skill_and_focus_context():
    from backend.app.services.assessment.programming_generator_v2 import _build_gemini_prompt

    prompt = _build_gemini_prompt(
        _FakeJob(), _blueprint_with_skills(), _plan(technology="java"), ["java"],
    )

    assert "Java (priority: critical" in prompt
    assert "Concurrency (priority: high" in prompt
    assert "API design" in prompt
    assert "Spring Boot" in prompt
    assert "Kafka" in prompt
    assert "senior" in prompt.lower() or "advanced" in prompt.lower()


# Two near-identical questions (same technology, overlapping title
# vocabulary) must be rejected -- generator falls back to the
# deterministic bank rather than presenting a candidate with two
# variations of the same trivial task.
def test_duplicate_questions_in_gemini_output_trigger_fallback(monkeypatch):
    import backend.app.services.assessment.programming_generator_v2 as gen_mod
    import json as json_mod

    def _make_question(qid, title, technology):
        return {
            "question_id": qid, "title": title,
            "description": "Read a line and print the most frequent word.",
            "constraints": "", "examples": [{"input": "a a b", "output": "a"}],
            "input_specification": "one line", "output_specification": "one word",
            "difficulty": "medium", "technology": technology,
            "required_technology": None, "allowed_languages": ["python"],
            "execution_mode": "console_coding", "points": 20.0,
            # Gemini must now supply a reference solution; the server runs it to get the expected outputs
            "reference_solution_python": "s = input()\nprint('a' if s == 'a a b' else 'b')\n",
            "subparts": [
                {
                    "subpart_id": "sp1", "description": "basic", "weight": 0.5,
                    "test_cases": [{
                        "test_case_id": "tc1", "stdin": "a a b", "expected_stdout": "a",
                        "weight": 1.0, "is_hidden": False,
                    }],
                },
                {
                    "subpart_id": "sp2", "description": "edge case", "weight": 0.5,
                    "test_cases": [{
                        "test_case_id": "tc2", "stdin": "b b a", "expected_stdout": "b",
                        "weight": 1.0, "is_hidden": True,
                    }],
                },
            ],
        }

    duplicate_set = {
        "main_questions": [
            _make_question(1, "Word Frequency Counter", "text processing"),
            _make_question(2, "Word Frequency Analyzer", "text processing"),
            _make_question(3, "Question Three", "unrelated topic"),
            _make_question(4, "Question Four", "another topic"),
            _make_question(5, "Question Five", "yet another topic"),
        ],
        "generated_by": "gemini",
    }

    def _fake_generate(*a, **k):
        return json_mod.dumps(duplicate_set)

    monkeypatch.setattr(gen_mod, "generate_json_content", _fake_generate)

    result = generate_programming_assessment_v2(
        _FakeJob(), _blueprint(), _plan(technology="python"), use_gemini=True,
    )

    # rejected as duplicate -> falls through to the deterministic bank
    assert result.status == "generated"
    assert result.generated_by == "fallback"


def test_non_duplicate_questions_are_not_falsely_rejected(monkeypatch):
    import backend.app.services.assessment.programming_generator_v2 as gen_mod
    import json as json_mod

    def _make_question(qid, title, technology):
        return {
            "question_id": qid, "title": title,
            "description": "Read a line and print a single word result.",
            "constraints": "", "examples": [{"input": "a a b", "output": "a"}],
            "input_specification": "one line", "output_specification": "one word",
            "difficulty": "medium", "technology": technology,
            "required_technology": None, "allowed_languages": ["python"],
            "execution_mode": "console_coding", "points": 20.0,
            # Gemini must now supply a reference solution; the server runs it to get the expected outputs
            "reference_solution_python": "s = input()\nprint('a' if s == 'a a b' else 'b')\n",
            "subparts": [
                {
                    "subpart_id": "sp1", "description": "basic", "weight": 0.5,
                    "test_cases": [{
                        "test_case_id": "tc1", "stdin": "a a b", "expected_stdout": "a",
                        "weight": 1.0, "is_hidden": False,
                    }],
                },
                {
                    "subpart_id": "sp2", "description": "edge case", "weight": 0.5,
                    "test_cases": [{
                        "test_case_id": "tc2", "stdin": "b b a", "expected_stdout": "b",
                        "weight": 1.0, "is_hidden": True,
                    }],
                },
            ],
        }

    distinct_set = {
        "main_questions": [
            _make_question(1, "API Rate Limiter", "backend systems"),
            _make_question(2, "Transaction Log Aggregator", "data processing"),
            _make_question(3, "Balanced Brackets Validator", "data structures"),
            _make_question(4, "Cache Eviction Policy", "systems design"),
            _make_question(5, "Duplicate Order Detector", "data processing"),
        ],
        "generated_by": "gemini",
    }

    def _fake_generate(*a, **k):
        return json_mod.dumps(distinct_set)

    monkeypatch.setattr(gen_mod, "generate_json_content", _fake_generate)

    result = generate_programming_assessment_v2(
        _FakeJob(), _blueprint(), _plan(technology="python"), use_gemini=True,
    )

    assert result.status == "generated"
    assert result.generated_by == "gemini"  # NOT falsely rejected as duplicate


# V3 additive schema fields (scenario/requirements/skills_assessed/
# expected_complexity/notes) must round-trip through the fallback
# bank (which leaves them at defaults) without breaking anything.
def test_v3_additive_fields_default_safely_on_fallback_questions():
    result = generate_programming_assessment_v2(_FakeJob(), _blueprint(), _plan(), use_gemini=False)
    # The identical generic bank is gone: fallback questions now come from the parametric generator and
    # carry a scenario and skills, so the additive fields only have to stay valid (never a broken type).
    for q in result.question_set.main_questions:
        assert q.scenario is None or isinstance(q.scenario, str)
        assert isinstance(q.requirements, list)
        assert isinstance(q.skills_assessed, list)
        assert q.expected_complexity is None or isinstance(q.expected_complexity, str)
        assert q.notes is None or isinstance(q.notes, str)

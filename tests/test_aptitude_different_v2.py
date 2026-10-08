"""
Every candidate gets a DIFFERENT aptitude set, whichever source serves it:
Groq -> pool of earlier Groq questions -> parametric questions (computed answers).
No network, no keys, temporary pool file, no jobpilot.db.
"""

import os
import random
import re
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["APTITUDE_POOL_PATH"] = os.path.join(tempfile.mkdtemp(), "pool.json")

import backend.app.services.aptitude.ai_question_generator as gen
import backend.app.services.aptitude.aptitude_question_service as service
import backend.app.services.aptitude.question_pool as pool
from backend.app.services.aptitude.parametric_bank import _run, generate_parametric_questions

gen.RATE_LIMIT_BACKOFF_SECONDS = 0.0


def _reset_pool():
    path = os.environ["APTITUDE_POOL_PATH"]
    if os.path.exists(path):
        os.unlink(path)


def _candidate(level="Fresher", summary=""):
    job = types.SimpleNamespace(title="Python Backend Developer", required_skills="Python", technologies="FastAPI")
    user = types.SimpleNamespace(experience_level=None)
    analysis = types.SimpleNamespace(experience_level=level, summary=summary, skills="[]")
    return job, user, None, analysis


def _fake_ai(seed_counter=[0]):
    """A generate_ai_aptitude_questions stand-in that returns a distinct valid set per call."""

    def fake(job, user, profile, resume_analysis, blueprint=None):
        band, plan = gen.plan_for_candidate(user, profile, resume_analysis, blueprint)
        seed_counter[0] += 1
        questions = generate_parametric_questions(band, plan, random.Random(seed_counter[0] * 7919))
        for item in questions:
            item["question"] = f"AI#{seed_counter[0]} " + item["question"]
            item["_verified"] = True
        return questions

    return fake


def test_parametric_sets_are_valid_and_never_identical():
    for band in ("fresher", "junior", "senior"):
        plan = gen.BAND_PLANS[band]
        sets = []
        for _ in range(150):
            questions = generate_parametric_questions(band, plan, random.Random())
            assert len(questions) == 10 and [q["id"] for q in questions] == list(range(1, 11))
            for q in questions:
                assert len(set(q["options"])) == 4 and q["correct_answer"] in q["options"]
            assert len({q["question"] for q in questions}) == 10
            sets.append(frozenset(q["question"] for q in questions))
        assert len(set(sets)) == len(sets), f"identical sets for band {band}"
        overlaps = [len(sets[i] & sets[i + 1]) for i in range(len(sets) - 1)]
        assert sum(overlaps) / len(overlaps) < 2, "neighbouring candidates share too many questions"


def test_parametric_mix_follows_the_experience_band():
    def mix(band):
        counts = {}
        for q in generate_parametric_questions(band, gen.BAND_PLANS[band], random.Random(3)):
            counts[q["category"]] = counts.get(q["category"], 0) + 1
        return counts

    assert mix("fresher") == {"quantitative_aptitude": 4, "logical_reasoning": 3, "verbal_reasoning": 3}
    assert mix("junior")["technical_logic"] == 4
    assert mix("senior")["technical_logic"] == 7


def test_parametric_answer_keys_are_correct():
    checked = 0

    for _ in range(80):

        for q in generate_parametric_questions("senior", gen.BAND_PLANS["senior"], random.Random()) + \
                generate_parametric_questions("junior", gen.BAND_PLANS["junior"], random.Random()):

            text = q["question"]

            if text.startswith("What does this code print?"):
                code = text.split("\n\n", 1)[1]
                assert _run(code) == q["correct_answer"], text
                checked += 1

            m = re.match(r"What is (\d+)% of (\d+)\?", text)
            if m:
                assert float(q["correct_answer"]) == int(m.group(1)) * int(m.group(2)) / 100
                checked += 1

            m = re.match(r"What is the remainder when (\d+) is divided by (\d+)\?", text)
            if m:
                assert int(q["correct_answer"]) == int(m.group(1)) % int(m.group(2))
                checked += 1

            m = re.match(r"In how many ways can (\d+) people be chosen from a group of (\d+)\?", text)
            if m:
                import math
                assert int(q["correct_answer"]) == math.comb(int(m.group(2)), int(m.group(1)))
                checked += 1

    assert checked > 100


def test_groq_success_is_used_first_and_fed_into_the_pool():
    _reset_pool()
    service.generate_ai_aptitude_questions = _fake_ai()
    questions, source = service.build_aptitude_questions(*_candidate())
    assert source == "ai" and len(questions) == 10
    assert all("_verified" not in q for q in questions)               # internal marker never leaves the service
    assert pool.size("fresher") == 10


def test_when_groq_fails_the_pool_serves_a_different_set_each_time():
    _reset_pool()
    service.generate_ai_aptitude_questions = _fake_ai()

    for _ in range(4):                                                # four Groq sets -> 40 pooled questions
        service.build_aptitude_questions(*_candidate())

    def down(*args, **kwargs):
        raise RuntimeError("groq request failed (HTTP 429)")

    service.generate_ai_aptitude_questions = down

    sets = []
    for _ in range(6):
        questions, source = service.build_aptitude_questions(*_candidate())
        assert source == "pool" and len(questions) == 10
        assert [q["id"] for q in questions] == list(range(1, 11))
        sets.append(tuple(q["question"] for q in questions))

    assert len({frozenset(s) for s in sets}) >= 3, "pool draws must vary between candidates"


def test_pool_serves_least_used_questions_first():
    _reset_pool()
    plan = {"quantitative_aptitude": 2}
    items = generate_parametric_questions("fresher", {"quantitative_aptitude": 6}, random.Random(1))
    pool.add("fresher", items)
    first = {q["question"] for q in pool.draw("fresher", plan)}
    second = {q["question"] for q in pool.draw("fresher", plan)}
    third = {q["question"] for q in pool.draw("fresher", plan)}
    assert len(first | second | third) == 6, "unused questions must be served before any is repeated"


def test_empty_pool_falls_through_to_parametric_and_still_differs():
    _reset_pool()

    def down(*args, **kwargs):
        raise RuntimeError("groq request failed (HTTP 500)")

    service.generate_ai_aptitude_questions = down

    sets = []
    for level in ("Fresher", "Mid Level", "Senior Level"):
        for _ in range(10):
            questions, source = service.build_aptitude_questions(*_candidate(level, "3 years" if "Senior" in level else "2 years" if "Mid" in level else ""))
            assert source == "parametric" and len(questions) == 10
            sets.append(frozenset(q["question"] for q in questions))

    assert len(set(sets)) == len(sets)


def test_generation_stops_at_its_time_budget_and_the_chain_continues():
    _reset_pool()
    calls = []

    class Slow:
        model = "openai/gpt-oss-120b"

        def generate_json(self, *args, **kwargs):
            calls.append(1)
            raise RuntimeError("groq request failed (HTTP 429: rate_limit_exceeded)")

    import importlib
    real = importlib.reload(service)                                   # restore the real generator wiring
    gen._make_provider = lambda: Slow()
    original_deadline = gen.GENERATION_DEADLINE_SECONDS
    gen.GENERATION_DEADLINE_SECONDS = 0.0
    try:
        questions, source = real.build_aptitude_questions(*_candidate())
    finally:
        gen.GENERATION_DEADLINE_SECONDS = original_deadline

    assert source == "parametric" and len(questions) == 10
    assert len(calls) <= 1


def test_both_aptitude_entry_points_use_the_single_source():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    for relative in ("backend/app/services/assessment/aptitude_integration.py", "backend/app/api/routes/company_applications.py"):
        source = open(os.path.join(root, relative), encoding="utf-8").read()
        assert "build_aptitude_questions(" in source, relative
        assert "questions = get_aptitude_questions()" not in source, relative     # the identical fixed bank is no longer the fallback
        assert "generate_personalized_aptitude_questions(" not in source, relative  # and Gemini is no longer used for aptitude


# Regression: a Talent Acquisition Coordinator (non-technical) candidate was shown Python
# recursion/conditional-logic/time-complexity/"find the bug" questions, purely because
# technical_logic was keyed off EXPERIENCE level (junior/senior), never off whether the role
# itself is technical. Confirmed live via real production logs the user shared.

def _non_technical_candidate(level="Junior"):
    job = types.SimpleNamespace(
        title="Talent Acquisition Coordinator", required_skills=None,
        technologies=None, description="Assist with sourcing applicants, screening resumes, scheduling interviews.",
        requirements=None,
    )
    user = types.SimpleNamespace(experience_level=None)
    analysis = types.SimpleNamespace(experience_level=level, summary="", skills="[]")
    return job, user, None, analysis


def test_non_technical_role_never_gets_technical_logic_questions():
    for level in ("Fresher", "Junior", "Senior"):
        job, user, profile, analysis = _non_technical_candidate(level)
        band, plan = gen.plan_for_candidate(user, profile, analysis, job=job)
        assert "technical_logic" not in plan, (level, plan)
        assert sum(plan.values()) == 10

        questions = generate_parametric_questions(band, plan, random.Random())
        assert all(q["category"] != "technical_logic" for q in questions)


def test_technical_role_still_gets_technical_logic_questions_for_junior_and_senior():
    job, user, profile, analysis = _candidate(level="Senior")
    band, plan = gen.plan_for_candidate(user, profile, analysis, job=job)
    assert "technical_logic" in plan
    assert plan == gen.BAND_PLANS["senior"]


def test_plan_for_candidate_with_no_job_defaults_to_technical_unchanged_behaviour():
    """job=None (some caller that genuinely has no job object) must never silently lose a
    category a pre-fix caller used to get -- backward compatible by default."""
    user = types.SimpleNamespace(experience_level=None)
    analysis = types.SimpleNamespace(experience_level="Senior", summary="", skills="[]")
    band, plan = gen.plan_for_candidate(user, None, analysis, job=None)
    assert plan == gen.BAND_PLANS["senior"]


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failures += 1
                print("FAIL", name, type(exc).__name__, exc)
    sys.exit(1 if failures else 0)

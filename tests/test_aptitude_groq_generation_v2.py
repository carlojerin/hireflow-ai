"""
Aptitude generation with Groq: per-candidate, experience-band aware, validated and answer-key verified.
A fake provider stands in for Groq: no network, no keys, no jobpilot.db.
"""

import json
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.services.aptitude.ai_question_generator as gen
from backend.app.services.aptitude.scoring import score_aptitude_test

gen.RATE_LIMIT_BACKOFF_SECONDS = 0.0

SLOT = re.compile(r"^(\d+)\. category=(\w+); subtopic=(.+)$", re.M)


class FakeGroq:
    """Writes one valid question per requested slot; can be told to misbehave."""

    def __init__(self, bad_first_n=0, dispute_slot_text=None, fail_times=0, fail_message="groq request failed (HTTP 429: rate_limit_exceeded)"):
        self.calls = []
        self.model = "openai/gpt-oss-120b"
        self.bad_first_n = bad_first_n
        self.dispute_slot_text = dispute_slot_text
        self.fail_times = fail_times
        self.fail_message = fail_message
        self.counter = 0
        self.disputed_done = False

    def generate_json(self, prompt, system=None, json_schema=None, max_tokens=None, temperature=None):

        self.calls.append({"prompt": prompt, "schema": json_schema, "temperature": temperature})

        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError(self.fail_message)

        if "Solve each multiple-choice question" in prompt:
            answers = []
            for block in prompt.split("\n\n")[1:]:
                m = re.match(r"ID (\d+)\n(.+?)\nOptions:\n(.+)", block, re.S)
                if not m:
                    continue
                options = [o[2:] for o in m.group(3).splitlines() if o.startswith("- ")]
                key = next(o for o in options if o.endswith("-correct"))
                choice = key
                if self.dispute_slot_text and self.dispute_slot_text in m.group(2) and not self.disputed_done:
                    choice = next(o for o in options if o != key)
                answers.append({"id": int(m.group(1)), "choice": choice})
            if self.dispute_slot_text and any(self.dispute_slot_text in b for b in prompt.split("\n\n")):
                self.disputed_done = True
            return json.dumps({"answers": answers})

        questions = []
        for _index, category, topic in SLOT.findall(prompt):
            self.counter += 1
            n = self.counter
            question = {
                "category": category,
                "difficulty": "intermediate",
                "question": f"{category} about {topic} number {n}",
                "options": [f"opt{n}a", f"opt{n}b", f"opt{n}c", f"opt{n}-correct"],
                "correct_answer": f"opt{n}-correct",
                "explanation": "because",
            }
            questions.append(question)

        for i in range(min(self.bad_first_n, len(questions))):
            questions[i]["options"] = ["same", "same", "x", "y"]            # duplicate options -> rejected
        self.bad_first_n = 0

        return json.dumps({"questions": questions})


def _generate(fake, level="Fresher", summary="", total=None):

    gen._make_provider = lambda: fake

    job = types.SimpleNamespace(title="Python Backend Developer", required_skills="Python, FastAPI", technologies="PostgreSQL")
    user = types.SimpleNamespace(experience_level=None)
    resume_analysis = types.SimpleNamespace(experience_level=level, summary=summary, skills=json.dumps(["Python"]))
    blueprint = types.SimpleNamespace(total_questions=total, candidate_experience_level=level) if total else None

    return gen.generate_ai_aptitude_questions(job, user, None, resume_analysis, blueprint=blueprint)


def _mix(questions):
    counts = {}
    for q in questions:
        counts[q["category"]] = counts.get(q["category"], 0) + 1
    return counts


def test_experience_band_resolution():
    assert gen.resolve_experience_band("Fresher") == "fresher"
    assert gen.resolve_experience_band("Entry Level") == "fresher"
    assert gen.resolve_experience_band("Mid Level", "Developer with 2 years of experience") == "junior"
    assert gen.resolve_experience_band("Mid Level") == "junior"
    assert gen.resolve_experience_band("Senior Level", "6 years building APIs") == "senior"
    assert gen.resolve_experience_band("Fresher", "0.5 years internship") == "fresher"
    assert gen.resolve_experience_band(None, None) == "fresher"


def test_fresher_gets_general_aptitude_only_medium_high():
    fake = FakeGroq()
    questions = _generate(fake, "Fresher")
    assert len(questions) == 10 and [q["id"] for q in questions] == list(range(1, 11))
    assert _mix(questions) == {"quantitative_aptitude": 4, "logical_reasoning": 3, "verbal_reasoning": 3}
    assert "MEDIUM TO HIGH" in fake.calls[0]["prompt"] and "No programming questions" in fake.calls[0]["prompt"]


def test_junior_gets_easier_general_plus_technical_logic():
    fake = FakeGroq()
    questions = _generate(fake, "Mid Level", "2 years of experience")
    assert _mix(questions) == {"quantitative_aptitude": 2, "logical_reasoning": 2, "verbal_reasoning": 2, "technical_logic": 4}
    prompt = fake.calls[0]["prompt"]
    assert "JUNIOR PROFESSIONAL" in prompt and "pseudo-code" in prompt and "Python Backend Developer" in prompt


def test_senior_general_aptitude_mostly_waived():
    fake = FakeGroq()
    questions = _generate(fake, "Senior Level", "6 years")
    assert _mix(questions) == {"quantitative_aptitude": 1, "logical_reasoning": 1, "verbal_reasoning": 1, "technical_logic": 7}
    assert "largely waived" in fake.calls[0]["prompt"]


def test_every_candidate_gets_a_different_set():
    sets = set()
    for _ in range(12):
        questions = _generate(FakeGroq(), "Fresher")
        sets.add(tuple(re.sub(r" number \d+$", "", q["question"]) for q in questions))
    assert len(sets) > 6, "subtopic slots must vary per candidate"


def test_groq_is_the_provider_and_gemini_is_not_used():
    source = open(gen.__file__, encoding="utf-8").read()
    assert "genai" not in source and "GEMINI" not in source
    assert '"groq"' in source and gen.FALLBACK_MODEL == "openai/gpt-oss-120b"
    fake = FakeGroq()
    _generate(fake)
    assert fake.calls[0]["schema"]["required"] == ["questions"]           # structured JSON output requested
    assert fake.calls[0]["temperature"] == gen.GENERATION_TEMPERATURE


def test_invalid_questions_are_regenerated_not_the_whole_set():
    fake = FakeGroq(bad_first_n=3)
    questions = _generate(fake)
    assert len(questions) == 10
    generation_calls = [c for c in fake.calls if "Solve each" not in c["prompt"]]
    assert len(generation_calls) == 2
    assert len(SLOT.findall(generation_calls[1]["prompt"])) == 3           # only the 3 rejected slots asked again


def test_disputed_answer_key_is_regenerated():
    fake = FakeGroq(dispute_slot_text="quantitative_aptitude")
    questions = _generate(fake)
    assert len(questions) == 10
    assert len([c for c in fake.calls if "Solve each" in c["prompt"]]) >= 2      # a second verification round ran
    assert len({q["question"] for q in questions}) == 10


def test_rate_limit_is_retried_then_succeeds():
    fake = FakeGroq(fail_times=1)
    questions = _generate(fake)
    assert len(questions) == 10


def test_total_failure_raises_so_the_bank_fallback_can_be_used():
    fake = FakeGroq(fail_times=99, fail_message="groq request failed (HTTP 500)")
    try:
        _generate(fake)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "0 of 10" in str(exc)


def test_answer_key_matching_is_tolerant_but_exact_after_normalisation():
    options = ["$96", "$100", "$104", "Cannot be determined"]
    assert gen._match_answer("$100", options) == "$100"
    assert gen._match_answer("cannot be determined.", options) == "Cannot be determined"
    assert gen._match_answer("B", options) == "$100"
    assert gen._match_answer("(c)", options) == "$104"
    assert gen._match_answer("nope", options) is None


def test_generated_set_is_scored_by_the_existing_scorer():
    questions = _generate(FakeGroq())
    answers = [{"question_id": q["id"], "selected_answer": q["correct_answer"]} for q in questions]
    result = score_aptitude_test(questions, answers)
    assert result["correct_count"] == 10 and result["passed"] is True
    wrong = [{"question_id": q["id"], "selected_answer": q["options"][0]} for q in questions]
    assert score_aptitude_test(questions, wrong)["passed"] is False


def test_code_fences_and_language_labels_are_stripped():
    cleaned = gen._clean_question_text("Consider:\n```python\ndef f(n):\n    return n\n```\nWhat is f(2)?")
    assert "```" not in cleaned and "python" not in cleaned.lower() and "def f(n):" in cleaned
    assert gen._clean_question_text("Consider:\npython\n\ndef f(): pass") == "Consider:\n\ndef f(): pass"


def test_scaled_plan_for_other_totals():
    plan = gen._scaled_plan("fresher", 5)
    assert sum(plan.values()) == 5
    plan = gen._scaled_plan("senior", 20)
    assert sum(plan.values()) == 20 and plan["technical_logic"] == 14


def test_experience_years_come_from_the_resume_date_ranges():
    from datetime import date

    exp = json.dumps([
        "Java Full Stack Developer - Nimbus (June 2024 - Present): Spring Boot APIs",
        "Java Developer Intern - Brightpath (January 2024 - May 2024)",
    ])

    assert round(gen.years_from_experience_text(exp, date(2026, 9, 25)), 1) == 2.6
    # an "Entry Level" label no longer hides ~2.6 years of work
    assert gen.resolve_experience_band("Entry Level", experience_text=exp) == "junior"
    assert gen.resolve_experience_band("Fresher", experience_text="Intern (2025 - 2025)") == "fresher"
    assert gen.resolve_experience_band(experience_text="Dev (2015 - 2021), Lead (2021 - Present)") == "senior"
    # overlapping ranges are not double counted
    assert gen.years_from_experience_text("2020 - 2022 and 2021 - 2022") <= 3.0


def test_ambiguous_seating_and_validity_questions_are_rejected():
    for text, options in (
        ("Five candidates are seated in a row. Who sits in the middle?", ["A", "B", "C", "D"]),
        ("All A are B. Which judgment is correct?", ["Valid", "Invalid but possible", "Undetermined", "None"]),
    ):
        try:
            gen._reject_ambiguous(text, options)
        except RuntimeError:
            continue
        raise AssertionError("ambiguous question was accepted: " + text)

    gen._reject_ambiguous("A train covers 120 km in 2 hours. What is its speed?", ["40", "60", "80", "100"])


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

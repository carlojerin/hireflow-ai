"""
Programming assessment, backend/general phase: Groq generation + validation, language ranking, four-tier fallback,
blueprint allocation and the pipeline gate.

Groq is replaced by a scripted provider (no network, no keys); the Python sandbox, the scoring and the blueprint/plan engines are real.
Gemini is booby-trapped: any call to it fails the test. No jobpilot.db.
"""

import importlib.util
import json
import os
import random
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PROGRAMMING_POOL_PATH"] = os.path.join(tempfile.mkdtemp(), "programming_pool.json")

from backend.app.services.assessment import gemini_client
from backend.app.services.assessment import programming_groq_generator as gg
from backend.app.services.assessment import programming_question_pool as pool
from backend.app.services.assessment import programming_role_orchestrator as orch
from backend.app.services.assessment.blueprint_engine import build_assessment_blueprint
from backend.app.services.assessment.assessment_plan_engine import build_assessment_plan
from backend.app.services.assessment.candidate_language_ranker import rank_languages
from backend.app.services.assessment.programming_allocation import allocate_questions
from backend.app.services.assessment.programming_question_builders import assemble_question_set
from backend.app.services.assessment.programming_submission_v2 import CandidateSubmission, submit_question

GEMINI_CALLS = []


def _gemini_trap(*args, **kwargs):
    GEMINI_CALLS.append(1)
    raise AssertionError("Gemini must not be used for programming generation")


_real_generate_json_content = gemini_client.generate_json_content


def setup_module(module):
    # Scoped to this module's own test run (not at import time): patching gemini_client at import time would leak
    # into every other test file in the same pytest process, since pytest imports all test modules before running any.
    gemini_client.generate_json_content = _gemini_trap


def teardown_module(module):
    gemini_client.generate_json_content = _real_generate_json_content


gg.RATE_LIMIT_BACKOFF_SECONDS = 0.0


# ------------------------------------------------------------ a small library of valid backend questions

def _range_tests(gen, sizes=(4, 6, 2, 0, 3, 30)):
    groups = ["typical", "typical", "edge", "edge", "edge", "stress"]
    tests, seen = [], set()
    for index, (group, n) in enumerate(zip(groups, sizes)):
        seed = 5 + index
        stdin = gen(random.Random(seed), n)
        while stdin in seen:                                    # every test input of a question is different
            seed += 100
            stdin = gen(random.Random(seed), n + 1)
        seen.add(stdin)
        tests.append((group, stdin))
    return tests


def _status_stdin(rng, n):
    return "\n".join([str(n)] + [f"{rng.choice(['GET', 'POST'])} /p{rng.randint(1, 4)} {rng.choice([200, 201, 404, 500])}" for _ in range(n)])


def _lat_stdin(rng, n):
    return "\n".join([str(n)] + [f"/e{rng.randint(1, 3)} {rng.randint(5, 90)}" for _ in range(n)])


def _paths_stdin(rng, n):
    return "\n".join([str(n)] + [f"/r{rng.randint(1, 5)}" for _ in range(n)])


def _cache_stdin(rng, n):
    return "\n".join([str(rng.randint(1, 3))] + [str(rng.randint(1, 5)) for _ in range(n)]) if n else "2\n1"


def _rate_stdin(rng, n):
    t, out = 0, []
    for _ in range(n):
        t += rng.choice([0, 1, 3])
        out.append(str(t))
    return "\n".join(["2"] + out) if out else "2\n0"



def _token_stdin(rng, n):
    return "\n".join([str(rng.randint(50, 200))] + [f"{rng.randint(0, 150)} {rng.randint(10, 80)}" for _ in range(n)])


def _page_stdin(rng, n):
    return "\n".join([str(n)] + [f"{rng.randint(0, 40)} {rng.randint(1, 3)} {rng.randint(1, 10)}" for _ in range(n)])


READ_N = "n = int(input())\nrows = [input() for _ in range(n)]\n"

LIBRARY = [
    dict(title="Status class counter", difficulty="easy", gen=_status_stdin,
         text="Read N access-log lines 'METHOD PATH STATUS'. Count how many responses are successful (status below 400) and how many are errors (400 or above). Print 'ok E' and 'errors X' on two lines.",
         ref=READ_N + "ok = sum(1 for r in rows if int(r.split()[2]) < 400)\nprint('ok', ok)\nprint('errors', n - ok)\n",
         alt="lines = []\nwhile True:\n    try:\n        lines.append(input())\n    except EOFError:\n        break\ncodes = [int(l.split()[2]) for l in lines[1:]]\nprint('ok', len([c for c in codes if c < 400]))\nprint('errors', len([c for c in codes if c >= 400]))\n"),
    dict(title="Distinct endpoints", difficulty="easy", gen=_paths_stdin,
         text="Read N request paths, one per line. Print how many DISTINCT paths were requested, then the most requested path (ties: alphabetically first) on the next line. For N = 0 print 0 and NONE.",
         ref="n = int(input())\nrows = [input() for _ in range(n)]\nfrom collections import Counter\nc = Counter(rows)\nprint(len(c))\nprint(sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else 'NONE')\n",
         alt="import collections\nn = int(input())\nseen = collections.Counter()\nfor _ in range(n):\n    seen[input()] += 1\nprint(len(seen))\nbest = None\nfor path in sorted(seen):\n    if best is None or seen[path] > seen[best]:\n        best = path\nprint(best if best else 'NONE')\n"),
    dict(title="Slowest endpoint", difficulty="medium", gen=_lat_stdin,
         text="Read N lines 'PATH MILLISECONDS'. For every path compute the average latency using integer division. Print the path with the highest average (ties: alphabetically first), then that average. For N = 0 print NONE and 0.",
         ref="n = int(input())\nrows = [input().split() for _ in range(n)]\nd = {}\nfor p, ms in rows:\n    d.setdefault(p, []).append(int(ms))\navg = {p: sum(v) // len(v) for p, v in d.items()}\nif not avg:\n    print('NONE')\n    print(0)\nelse:\n    best = sorted(avg.items(), key=lambda kv: (-kv[1], kv[0]))[0]\n    print(best[0])\n    print(best[1])\n",
         alt="n = int(input())\ntotals = {}\ncounts = {}\nfor _ in range(n):\n    path, ms = input().split()\n    totals[path] = totals.get(path, 0) + int(ms)\n    counts[path] = counts.get(path, 0) + 1\nbest, best_avg = 'NONE', 0\nfor path in sorted(totals):\n    a = totals[path] // counts[path]\n    if best == 'NONE' or a > best_avg:\n        best, best_avg = path, a\nprint(best)\nprint(best_avg)\n"),
    dict(title="Request throttling", difficulty="medium", gen=_rate_stdin,
         text="The first line is LIMIT. Then one integer timestamp (seconds, non-decreasing) per request. A request is allowed when fewer than LIMIT requests were ALLOWED in the previous 5 seconds (timestamps t' with t - 5 < t' <= t). Print the number of allowed requests.",
         ref="limit = int(input())\nallowed = []\nwhile True:\n    try:\n        t = int(input())\n    except EOFError:\n        break\n    if len([a for a in allowed if a > t - 5]) < limit:\n        allowed.append(t)\nprint(len(allowed))\n",
         alt="limit = int(input())\nstamps = []\ntry:\n    while True:\n        stamps.append(int(input()))\nexcept EOFError:\n    pass\nkept = []\nfor t in stamps:\n    window = [k for k in kept if t - k < 5]\n    if len(window) < limit:\n        kept.append(t)\nprint(len(kept))\n"),
    dict(title="Token expiry sweep", difficulty="medium", gen=_token_stdin,
         text="The first line is the current time NOW. Then N lines 'ISSUED TTL' (one issued token each). A token is valid when ISSUED + TTL is greater than NOW. Print the number of valid tokens, then the number of expired ones; the number of lines N is not given: read until the end of the input.",
         ref="now = int(input())\nvalid = expired = 0\nwhile True:\n    try:\n        issued, ttl = map(int, input().split())\n    except EOFError:\n        break\n    if issued + ttl > now:\n        valid += 1\n    else:\n        expired += 1\nprint(valid)\nprint(expired)\n",
         alt="lines = []\ntry:\n    while True:\n        lines.append(input())\nexcept EOFError:\n    pass\nnow = int(lines[0])\nvalid = 0\nrest = lines[1:]\nfor row in rest:\n    a, b = row.split()\n    if int(a) + int(b) > now:\n        valid += 1\nprint(valid)\nprint(len(rest) - valid)\n"),
    dict(title="Paginated slices", difficulty="medium", gen=_page_stdin,
         text="Read N lines 'TOTAL PAGE SIZE' (1-based page numbers). For every line print the first and last item number shown on that page as 'first last', or 'EMPTY' when the page is beyond the last item. Items are numbered from 1.",
         ref="n = int(input())\nfor _ in range(n):\n    total, page, size = map(int, input().split())\n    first = (page - 1) * size + 1\n    if first > total:\n        print('EMPTY')\n    else:\n        print(first, min(total, page * size))\n",
         alt="count = int(input())\nfor _ in range(count):\n    t, p, s = [int(x) for x in input().split()]\n    lo = (p - 1) * s + 1\n    hi = p * s\n    if lo > t:\n        print('EMPTY')\n    else:\n        print(lo, hi if hi <= t else t)\n"),
    dict(title="Cache hit counter", difficulty="hard", gen=_cache_stdin,
         text="The first line is the cache capacity. Then one integer key per line is requested. The cache evicts the least recently used key when full; a request for a cached key is a hit and refreshes it. Print the number of hits.",
         ref="cap = int(input())\ncache = []\nhits = 0\nwhile True:\n    try:\n        k = int(input())\n    except EOFError:\n        break\n    if k in cache:\n        hits += 1\n        cache.remove(k)\n    elif len(cache) >= cap:\n        cache.pop(0)\n    cache.append(k)\nprint(hits)\n",
         alt="from collections import OrderedDict\ncap = int(input())\nc = OrderedDict()\nhits = 0\ntry:\n    while True:\n        k = int(input())\n        if k in c:\n            hits += 1\n            c.move_to_end(k)\n        else:\n            if len(c) >= cap:\n                c.popitem(last=False)\n            c[k] = 1\nexcept EOFError:\n    pass\nprint(hits)\n"),
    dict(title="Bulk status audit", difficulty="hard", gen=_status_stdin,
         text="Read N access-log lines 'METHOD PATH STATUS'. For every PATH print the path, the number of its requests and the number of its failed requests (status 400 or above), one path per line as 'path total failed', paths in alphabetical order. For N = 0 print NONE.",
         ref=READ_N + "d = {}\nfor r in rows:\n    m, p, s = r.split()\n    t, f = d.get(p, (0, 0))\n    d[p] = (t + 1, f + (1 if int(s) >= 400 else 0))\nif not d:\n    print('NONE')\nfor p in sorted(d):\n    print(p, d[p][0], d[p][1])\n",
         alt="n = int(input())\ntot, bad = {}, {}\nfor _ in range(n):\n    _m, path, code = input().split()\n    tot[path] = tot.get(path, 0) + 1\n    bad[path] = bad.get(path, 0) + (1 if int(code) >= 400 else 0)\nif not tot:\n    print('NONE')\nfor path in sorted(tot):\n    print(path, tot[path], bad[path])\n"),
]


def _render(item, covered):
    tests = _range_tests(item["gen"])
    lines = [
        f"=== QUESTION 1 ===", f"TITLE: {item['title']}", f"DIFFICULTY: {item['difficulty']}", "SCENARIO: A production API service.",
        "DESCRIPTION:", item["text"], "REQUIREMENTS:", "- read the input exactly as described", "- print exactly the requested lines",
        "CONSTRAINTS: N is at most 100", "INPUT_SPEC: the format above", "OUTPUT_SPEC: the lines above",
        "SKILLS_ASSESSED: parsing, aggregation", f"JOB_SKILLS_COVERED: {covered}", "COMPLEXITY: O(n)",
    ]
    for group, stdin in tests:
        lines += [f"--- TEST {group} | {group} input", stdin, "--- END TEST"]
    lines += ["--- REFERENCE (python)", item["ref"], "--- END REFERENCE", "=== END QUESTION ==="]
    return "\n".join(lines)


class ScriptedGroq:
    """Behaves like HostedChatProvider('groq'); every reply is plain text in the generation protocol."""

    name = "groq"
    model = "openai/gpt-oss-120b"

    def __init__(self, covered="Python, REST APIs", tamper=None, fail=False):
        self.calls, self.used, self.covered, self.tamper, self.fail = [], [], covered, tamper, fail

    def generate_json(self, prompt, system=None, json_schema=None, max_tokens=None, temperature=None, raw_text=False):

        assert raw_text is True
        self.calls.append(prompt)

        if self.fail:
            raise RuntimeError("groq request failed (HTTP 429: rate_limit_exceeded)")

        if "Solve each programming problem" in prompt:
            out = []
            for i, chunk in enumerate(prompt.split("QUESTION ")[1:]):
                title = chunk.split("Title: ", 1)[1].split("\n", 1)[0]
                item = next(l for l in LIBRARY if l["title"] == title)
                code = (self.tamper or {}).get("solution", item["alt"])
                out.append(f"=== SOLUTION {i} ===\n{code}\n=== END SOLUTION ===")
            return "\n".join(out)

        difficulty = prompt.split("difficulty=", 1)[1].split(";", 1)[0]
        item = next(l for l in LIBRARY if l["difficulty"] == difficulty and l["title"] not in self.used)
        self.used.append(item["title"])
        text = _render(item, self.covered)
        for old, new in (self.tamper or {}).get("replace", []):
            text = text.replace(old, new)
        return text


def _job(title="Python Backend Developer", skills="Python, REST APIs, FastAPI, Git", description="Build and maintain REST APIs and services.", tech="Python, FastAPI"):
    return types.SimpleNamespace(id=1, title=title, description=description, responsibilities="", requirements="", required_skills=skills,
                                 technologies=tech, experience_required="1-3 years", experience_level="", job_type="Full-time",
                                 work_mode="Remote", location="x", company="Acme", salary_min=None, salary_max=None,
                                 preferred_skills="", skills="", department="")


def _candidate(skills, level="Mid Level", summary="2 years of experience"):
    return types.SimpleNamespace(experience_level=level, summary=summary, skills=json.dumps(skills), job_roles="[]", education="[]",
                                 experience="[]", raw_text="")


def _context(job, skills, level="Mid Level", summary="2 years of experience"):
    analysis = _candidate(skills, level, summary)
    user = types.SimpleNamespace(experience_level=level, id=1)
    blueprint = build_assessment_blueprint(user=user, profile=None, resume_analysis=analysis, job=job, total_questions=5, assessment_type="programming")
    plan = build_assessment_plan(job, blueprint, use_gemini=False)
    return blueprint, plan, analysis, user


def _reset_pool():
    path = os.environ["PROGRAMMING_POOL_PATH"]
    if os.path.exists(path):
        os.unlink(path)


def _generate(job, skills, provider=None, level="Mid Level", summary="2 years of experience", use_ai=True):
    blueprint, plan, analysis, user = _context(job, skills, level, summary)
    return orch.generate_role_based_assessment(job, blueprint, plan, user=user, profile=None, resume_analysis=analysis,
                                               use_ai=use_ai, rng=random.Random(3), provider=provider)


# ------------------------------------------------------------ tests

def test_groq_is_the_provider_gemini_is_never_called_and_all_five_are_ai_generated():
    _reset_pool()
    del GEMINI_CALLS[:]
    provider = ScriptedGroq()
    result = _generate(_job(), ["Python", "FastAPI", "Git"], provider)

    assert result.status == "generated" and result.generated_by == "ai_generated"
    assert result.question_set.generation_metadata["provider"] == "groq" and result.question_set.generation_metadata["model"] == "openai/gpt-oss-120b"
    assert result.question_set.generation_metadata["question_sources"] == ["ai_generated"] * 5
    assert len(result.question_set.main_questions) == 5 and GEMINI_CALLS == []
    assert all(call for call in provider.calls)

    provider_class = gg.make_provider()
    assert provider_class.name == "groq" and provider_class.model == "openai/gpt-oss-120b"
    from backend.app.services.assessment.interview_llm_provider import HostedChatProvider
    assert isinstance(provider_class, HostedChatProvider)


def test_python_backend_questions_are_backend_relevant_and_carry_python_languages():
    _reset_pool()
    result = _generate(_job(), ["Python", "FastAPI"], ScriptedGroq(covered="Python, REST APIs"))
    for question in result.question_set.main_questions:
        assert question.allowed_languages == ["python"]
        assert "Python" in (question.technology or "") or "REST" in (question.technology or "")
        assert question.scenario and 2 <= len(question.subparts) <= 4
        assert any(not tc.is_hidden for sp in question.subparts for tc in sp.test_cases)               # a public example
        assert sum(1 for sp in question.subparts for tc in sp.test_cases if tc.is_hidden) >= 3          # and hidden tests


def test_java_job_gets_java_questions_and_language():
    _reset_pool()
    job = _job("Java Backend Developer", "Java, Spring Boot, REST APIs", "Spring Boot microservices and REST APIs.", "Java, Spring Boot")
    result = _generate(job, ["Java", "Spring", "Kotlin"], ScriptedGroq(covered="Java, Spring Boot"))
    assert result.generated_by == "ai_generated"
    for question in result.question_set.main_questions:
        assert question.allowed_languages == ["java"] and question.technology in ("Java", "Spring Boot")
    assert result.question_set.generation_metadata["language_selection"]["selected"] == "java"


def test_multi_language_job_selects_the_strongest_resume_language_never_outside_the_job():
    items = [types.SimpleNamespace(assessment_type="programming", technology=t) for t in ("Java", "Python")]
    python_person = _candidate(["Git", "Python", "Django", "Go", "Rust"])
    allowed, selection = orch.select_languages(items, python_person, None)
    assert allowed[0] == "python" and set(allowed) == {"java", "python"} and selection["selected"] == "python"
    assert "go" not in allowed and "rust" not in allowed                                        # strong elsewhere, but the job does not allow it

    java_person = _candidate(["Java", "Spring Boot", "Hibernate", "Python"])
    assert orch.select_languages(items, java_person, None)[0][0] == "java"

    nobody = _candidate(["Excel", "Communication"])
    ranked, reasons = rank_languages(["java", "python"], nobody, None)
    assert ranked == ["java", "python"] and "no resume evidence" in reasons[0]["evidence"][0]   # the job's own order is kept

    open_job = orch.select_languages([types.SimpleNamespace(assessment_type="programming", technology=None)], _candidate(["Kotlin", "Java"]), None)[0]
    assert open_job[0] == "kotlin"                                                              # an open job offers the candidate's own languages first


def test_difficulty_adapts_to_experience_and_blueprint():
    blueprint, _plan, _analysis, _user = _context(_job(), ["Python"])
    assert gg.difficulty_slots("fresher", blueprint, 5) == ["easy", "easy", "medium", "medium", "medium"]
    assert gg.difficulty_slots("senior", blueprint, 5)[-1] == "hard"
    shifted = types.SimpleNamespace(difficulty_distribution=types.SimpleNamespace(basic=0, intermediate=1, advanced=4))
    assert gg.difficulty_slots("fresher", shifted, 5) == ["medium", "medium", "hard", "hard", "hard"]
    easier = types.SimpleNamespace(difficulty_distribution=types.SimpleNamespace(basic=4, intermediate=1, advanced=0))
    assert gg.difficulty_slots("senior", easier, 5) == ["easy", "easy", "medium", "medium", "medium"]

    _reset_pool()
    wrong = ScriptedGroq(tamper={"replace": [("DIFFICULTY: easy", "DIFFICULTY: hard")]})
    result = _generate(_job(), ["Python"], wrong, level="Fresher", summary="")
    assert result.generated_by != "ai_generated"                                                # a question of the wrong difficulty is rejected


def test_role_irrelevant_questions_are_rejected():
    _reset_pool()
    result = _generate(_job(), ["Python"], ScriptedGroq(covered="Baking, Gardening"))
    assert result.generated_by in ("role_aware_fallback", "generic_fallback")
    assert any("role relevance" in r["reason"] for r in result.question_set.generation_metadata["validation"]["rejected"])


def test_duplicate_questions_are_rejected():
    provider = ScriptedGroq()
    original = provider.generate_json

    def same_every_time(prompt, **kwargs):
        if "Solve each programming problem" not in prompt:
            provider.used.clear()
            difficulty = prompt.split("difficulty=", 1)[1].split(";", 1)[0]
            provider.used.extend(l["title"] for l in LIBRARY if l["difficulty"] == difficulty and l["title"] != LIBRARY[0]["title"] and difficulty == "easy")
        return original(prompt, **kwargs)

    _reset_pool()
    provider.generate_json = same_every_time
    result = _generate(_job(), ["Python"], provider, level="Fresher", summary="")
    titles = [q.title for q in result.question_set.main_questions if q.title in {l["title"] for l in LIBRARY}]
    assert len(titles) == len(set(titles)), "no two questions of a set may be the same task"

    seen = {gg.__dict__["validate_structure"](
        gg.parse_questions(_render(LIBRARY[0], "Python"))[0], "easy", ["Python"], {"python", "rest", "apis"}, set())["fingerprint"]}
    try:
        gg.validate_structure(gg.parse_questions(_render(LIBRARY[0], "Python"))[0], "easy", ["Python"], {"python", "rest", "apis"}, seen)
        raise AssertionError("expected a duplicate rejection")
    except gg.Rejected as exc:
        assert "duplicate" in str(exc)


def test_a_bad_reference_solution_rejects_generation_and_never_reaches_a_candidate():
    _reset_pool()
    crash = ScriptedGroq(tamper={"replace": [("n = int(input())\nrows", "n = int(input())\nboom = 1 / 0\nrows")]})
    result = _generate(_job(), ["Python"], crash)
    assert result.generated_by != "ai_generated"
    assert any("reference solution failed" in r["reason"] for r in result.question_set.generation_metadata["validation"]["rejected"])

    _reset_pool()
    wrong_but_running = ScriptedGroq(tamper={"replace": [("print('ok', ok)", "print('ok', ok + 1)")]})           # reference disagrees with the independent solver
    result = _generate(_job(), ["Python"], wrong_but_running, level="Fresher", summary="")
    rejected = result.question_set.generation_metadata["validation"]["rejected"]
    assert any("disagrees" in r["reason"] for r in rejected)
    assert "Status class counter" not in [q.title for q in result.question_set.main_questions]


def test_expected_outputs_come_from_running_the_reference_and_hidden_tests_are_correct():
    _reset_pool()
    result = _generate(_job(), ["Python"], ScriptedGroq())
    question_set = result.question_set

    for index, question in enumerate(question_set.main_questions):
        item = next(l for l in LIBRARY if l["title"] == question.title)
        for sp in question.subparts:
            for tc in sp.test_cases:
                ok, output, _why = gg.run_python(item["ref"], tc.stdin)
                assert ok and gg._normalize_output(output) == gg._normalize_output(tc.expected_stdout)

        good = submit_question(question_set, CandidateSubmission(index + 1, "python", item["alt"]))
        bad = submit_question(question_set, CandidateSubmission(index + 1, "python", "print('nope')"))
        assert good.earned_points == 20.0 and bad.earned_points < 20.0


def test_fallback_hierarchy_pool_then_role_aware_then_generic():
    # tier 2: a validated pool question is served when Groq is down (its reference is re-run first)
    _reset_pool()
    _generate(_job(), ["Python"], ScriptedGroq())                                                     # fills the pool with 5 validated questions
    assert pool.size() >= 5
    down = ScriptedGroq(fail=True)
    from_pool = _generate(_job(), ["Python"], down)
    assert from_pool.generated_by == "validated_pool"
    assert from_pool.question_set.generation_metadata["question_sources"].count("validated_pool") == 5
    assert "groq" in from_pool.question_set.generation_metadata["tiers_tried"]

    # tier 3: no pool -> role-aware parametric questions for a backend role
    _reset_pool()
    role_aware = _generate(_job(), ["Python"], ScriptedGroq(fail=True))
    assert role_aware.generated_by == "role_aware_fallback"
    assert set(role_aware.question_set.generation_metadata["question_sources"]) <= {"role_aware_fallback", "generic_fallback"}

    # tier 4: a job with no domain at all can only get generic questions, and says so
    _reset_pool()
    vague = _job("Software Engineer", "Programming, Problem solving, Rust", "General software work.", "Rust")
    generic = _generate(vague, ["Rust"], ScriptedGroq(fail=True))
    assert generic.generated_by == "generic_fallback"
    assert generic.question_set.generation_metadata["personalized"] is False

    # the accurate label travels with the set to the candidate view
    from backend.app.services.assessment.programming_integration_v2 import candidate_view_v2
    fake_row = types.SimpleNamespace(
        generated_by=generic.generated_by, allowed_languages_json=json.dumps(["rust"]), status="in_progress", score=None, total_score=None,
        passed=None, question_set_json=generic.question_set.model_dump_json(),
        assigned_at=__import__("datetime").datetime.utcnow(), expires_at=__import__("datetime").datetime.utcnow(),
    )
    view = candidate_view_v2(fake_row)
    assert view["source"] == "generic_fallback" and view["personalized"] is False and view["recommended_language"] == "rust"


def test_no_gemini_call_anywhere_in_the_generation_paths():
    del GEMINI_CALLS[:]
    _reset_pool()
    _generate(_job(), ["Python"], ScriptedGroq())
    _generate(_job(), ["Python"], ScriptedGroq(fail=True))
    _generate(_job("SQL Developer", "SQL, PostgreSQL, Database design", "SQL queries.", "SQL"), ["SQL"], ScriptedGroq(fail=True))
    _generate(_job("Frontend Developer", "HTML, CSS, JavaScript", "UI pages.", "HTML, CSS"), ["HTML", "CSS"], ScriptedGroq(fail=True))
    assert GEMINI_CALLS == []
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("programming_groq_generator.py", "programming_role_orchestrator.py", "programming_question_pool.py", "programming_allocation.py"):
        source = open(os.path.join(root, "backend", "app", "services", "assessment", name), encoding="utf-8").read()
        assert "gemini" not in source.lower().replace("no gemini", "") or "Gemini and" in source, name


def test_blueprint_decides_the_allocation_not_a_fixed_split():
    backend_job = _job("Python Backend Developer", "Python, FastAPI, REST APIs, PostgreSQL, SQL, Git", "APIs on PostgreSQL.", "Python, FastAPI")
    blueprint, plan, _a, _u = _context(backend_job, ["Python", "SQL"])
    counts, why = allocate_questions(blueprint, plan, backend_job)
    assert counts == {"coding": 4, "sql": 1}, (counts, why)

    analyst = _job("Data Analyst", "SQL, PostgreSQL, MySQL, Database queries, Python, Excel", "Analyse data with SQL and Python.", "SQL, Python")
    blueprint, plan, _a, _u = _context(analyst, ["SQL", "Python"])
    counts, why = allocate_questions(blueprint, plan, analyst)
    assert counts.get("sql", 0) >= 3 and counts.get("coding", 0) >= 1 and sum(counts.values()) == 5, (counts, why)

    only_python = _job("Python Developer", "Python, Django, Flask", "Python services.", "Python")
    blueprint, plan, _a, _u = _context(only_python, ["Python"])
    assert allocate_questions(blueprint, plan, only_python)[0] == {"coding": 5}

    no_tracks = types.SimpleNamespace(assessment_types=[types.SimpleNamespace(assessment_type="aptitude", technology=None)])
    assert allocate_questions(blueprint, no_tracks, only_python)[0] == {}


def test_no_programming_assignment_when_the_blueprint_cannot_produce_an_assessment():
    spec = importlib.util.spec_from_file_location("hrhelpers", os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_hr_programming_v2_review.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)

    from backend.app.models.assessment_assignment import AssessmentAssignment
    from backend.app.services.assessment.programming_integration_v2 import programming_assessment_feasible
    from backend.app.services.assessment.stage_progression_service import advance_pipeline

    def assigned(db, application, assessment_type="programming"):
        return db.query(AssessmentAssignment).filter(AssessmentAssignment.application_id == application.id,
                                                     AssessmentAssignment.assessment_type == assessment_type).count()

    for title, skills, expect_assignment in (
        ("Backend Developer", "Python", True),                     # feasible: assignment is created
        ("Mainframe Developer", "COBOL, Mainframe", False),         # a genuinely technical role, but the language cannot be executed, and it's not a Technical Domain Assessment bank either: no programming assignment, no substitute
        ("Office Coordinator", "Communication, Scheduling", False),  # non-technical (see role_classification.py): no PROGRAMMING assignment, but a real situational_judgment one instead -- never silently blocked
    ):
        db = helpers._make_db()
        _user, _job_row, application, _session = helpers._seed(db, job_title=title, required_skills=skills)
        db.query(AssessmentAssignment).filter(AssessmentAssignment.application_id == application.id).delete()
        db.commit()

        feasible, reason = programming_assessment_feasible(db, application)
        view = advance_pipeline(db, application)

        assert feasible is expect_assignment, (title, reason)
        assert (assigned(db, application) == 1) is expect_assignment, title

        if title == "Office Coordinator":
            # Non-technical: occupies the same pipeline slot with a
            # REAL Situational Judgment Test assignment instead of
            # being blocked (see stage_progression_service.py /
            # role_classification.py) -- the one case that is NOT a
            # dead end even though no "programming" assignment exists.
            assert assigned(db, application, "situational_judgment") == 1, title
            assert not (view and view.get("blocked"))

        elif not expect_assignment:
            # A genuinely technical role whose required language
            # JobPilot cannot execute: a real feasibility gap, still
            # correctly blocked with no substitute assessment.
            assert reason and view and view.get("blocked", {}).get("stage") == "programming"


def test_ranking_and_parsing_helpers():
    assert gg.normalize_reference_source("import sys\nprint(sys.stdin.readline())").count("input()") == 1
    assert "sys." in gg.normalize_reference_source("import sys\nsys.exit(0)")                    # other sys use is kept, so it gets rejected
    block = gg.parse_questions(_render(LIBRARY[0], "Python"))[0]
    assert len(block["tests"]) == 6 and block["reference_solution_python"].startswith("n = int(input())")


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name, flush=True)
            except Exception as exc:
                failures += 1
                import traceback
                print("FAIL", name, type(exc).__name__, exc, flush=True)
                traceback.print_exc(limit=4)
    sys.exit(1 if failures else 0)

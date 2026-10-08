"""
Role-based programming assessment: coding / SQL / web questions, different for every candidate.

Real SQLite, real headless browser and the real Python sandbox are used; Gemini is faked (no network, no keys).
No jobpilot.db.
"""

import json
import os
import random
import sys
import types
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.services.assessment.coding_question_generator as cg
import backend.app.services.assessment.frontend_question_generator as fg
import backend.app.services.assessment.programming_generator_v2 as v2
import backend.app.services.assessment.sql_question_generator as sg
from backend.app.services.assessment import gemini_client
from backend.app.services.assessment.language_capability_registry import get_language_capability
from backend.app.services.assessment.language_runner import execute_code
from backend.app.services.assessment.programming_question_builders import assemble_question_set
from backend.app.services.assessment.programming_submission_v2 import CandidateSubmission, submit_question
from backend.app.services.assessment.sql_runner import ORDERED_DIRECTIVE, run_sql_query

SETUP = "CREATE TABLE emp(id INTEGER, name TEXT, dept TEXT, salary REAL);INSERT INTO emp VALUES (1,'A','x',100),(2,'B','x',300),(3,'C','y',200.5),(4,'D','y',NULL);"


def _plan(*kinds):
    return types.SimpleNamespace(assessment_types=[types.SimpleNamespace(assessment_type=k, technology=None) for k in kinds])


def _job(title):
    return types.SimpleNamespace(title=title, description="", required_skills="")


def _score(questions, index, language, code):
    question_set = assemble_question_set(questions, generated_by="fallback")
    result = submit_question(question_set, CandidateSubmission(question_id=index + 1, language=language, source_code=code))
    return result.earned_points / result.max_points if hasattr(result, "earned_points") else result


# ------------------------------------------------------------ SQL runner

def test_sql_and_html_are_supported_languages_now():
    assert get_language_capability("sql").execution_supported
    assert get_language_capability("html").execution_supported
    assert get_language_capability("css").canonical_name == "html"


def test_sql_runner_results_and_formatting():
    ok = execute_code("sql", "SELECT dept, AVG(salary) FROM emp GROUP BY dept", SETUP)
    assert ok.success and ok.stdout == "x|200\ny|200.5"
    assert run_sql_query(SETUP, "SELECT name FROM emp WHERE salary > 1000") == "(no rows)"
    assert run_sql_query(SETUP, "SELECT name, salary FROM emp WHERE dept='y' ORDER BY name") == "C|200.5\nD|NULL"
    ordered = run_sql_query(ORDERED_DIRECTIVE + "\n" + SETUP, "SELECT name FROM emp ORDER BY name DESC")
    unordered = run_sql_query(SETUP, "SELECT name FROM emp ORDER BY name DESC")
    assert ordered == "D\nC\nB\nA" and unordered == "A\nB\nC\nD"          # row order only matters when the test says so


def test_sql_runner_is_read_only_and_bounded():
    for bad in ("DROP TABLE emp", "DELETE FROM emp", "UPDATE emp SET salary = 1", "PRAGMA table_info(emp)",
                "ATTACH DATABASE 'x.db' AS x", "SELECT 1; DROP TABLE emp", "INSERT INTO emp VALUES (9,'z','z',1)"):
        result = execute_code("sql", bad, SETUP)
        assert not result.success and result.error_type in ("runtime_error", "compile_error"), bad
    loop = execute_code("sql", "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c) SELECT count(*) FROM c", SETUP)
    assert loop.error_type == "timeout"
    assert execute_code("sql", "SELECT * FROM missing_table", SETUP).error_type == "compile_error"


# ------------------------------------------------------------ SQL questions

def test_sql_parametric_sets_are_valid_and_all_different():
    signatures = set()
    for band in ("fresher", "junior", "senior"):
        for _ in range(40):
            rng, used = random.Random(), set()
            questions = [sg.parametric_sql_question(d, rng, used) for d in sg.difficulty_slots(band, 5)]
            assert len(questions) == 5
            for q in questions:
                assert q.execution_mode == "sql" and q.allowed_languages == ["sql"]
                assert all(tc.expected_stdout for sp in q.subparts for tc in sp.test_cases)
            signatures.add(tuple(q.subparts[0].test_cases[0].stdin for q in questions))
    assert len(signatures) == 120, "every candidate must get different data and questions"


def test_sql_question_is_scored_by_running_the_candidate_query():
    theme = sg.THEMES[0]
    ddl = sg._ddl(theme)
    rng = random.Random(1)
    datasets = [sg._dataset(theme, rng, 4, 6), sg._dataset(theme, rng, 6, 10), sg._dataset(theme, rng, 7, 12)]
    query = "SELECT d.name, SUM(f.amount) FROM customers d JOIN orders f ON f.customer_id = d.id GROUP BY d.id"
    question = sg._make_question(title="t", scenario="s", task="totals", ddl=ddl, datasets=datasets, query=query,
                                 ordered=False, difficulty="basic", skills=["JOIN"])
    others = [sg.parametric_sql_question("basic", random.Random(i), set()) for i in range(4)]
    questions = [question] + others

    def score(code):
        result = submit_question(assemble_question_set(questions, generated_by="fallback"), CandidateSubmission(1, "sql", code))
        return result.earned_points, result.max_points

    assert score(query) == (20.0, 20.0)
    assert score("SELECT c.name, SUM(o.amount) FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name")[0] in (20.0, 10.0)
    assert score("SELECT name, 0 FROM customers")[0] < 20.0
    assert score("DROP TABLE customers")[0] == 0.0


# ------------------------------------------------------------ coding questions

def test_coding_parametric_sets_have_computed_answers_and_differ():
    signatures = set()
    for band in ("fresher", "junior", "senior"):
        for _ in range(40):
            questions = cg.parametric_coding_questions(5, band, ["python"], "Python Backend Developer", random.Random())
            assert len(questions) == 5
            signatures.add(tuple(q.subparts[0].test_cases[0].stdin for q in questions))
    assert len(signatures) == 120


def test_coding_question_is_scored_by_running_python():
    rng = random.Random(0)
    question = next(
        q for q in (cg.parametric_coding_question("easy", ["python"], random.Random(i), "backend", set()) for i in range(200))
        if q.title == "Cart total with discount"
    )
    others = cg.parametric_coding_questions(4, "junior", ["python"], "", rng)
    solution = ("n = int(input())\ntotal = 0\nfor _ in range(n):\n    p, q = map(int, input().split())\n    total += p * q\n"
                "d = 10 if total >= 3000 else 5 if total >= 1500 else 0\nprint(total - total * d // 100)\n")
    questions = [question] + others
    good = submit_question(assemble_question_set(questions, "fallback"), CandidateSubmission(1, "python", solution))
    bad = submit_question(assemble_question_set(questions, "fallback"), CandidateSubmission(1, "python", "print(0)"))
    assert good.earned_points == 20.0 and bad.earned_points < 20.0


def test_reference_solution_replaces_wrong_expected_outputs():
    def execute(source, stdin):
        return True, str(int(stdin) * 2)

    question = {"reference_solution_python": "print(int(input())*2)  # reference",
                "subparts": [{"test_cases": [{"stdin": "2", "expected_stdout": "4"}, {"stdin": "5", "expected_stdout": "999"},
                                             {"stdin": "6", "expected_stdout": "12"}, {"stdin": "7", "expected_stdout": "14"}]}]}
    assert cg.apply_reference_solutions(question, execute) is True
    assert [c["expected_stdout"] for c in question["subparts"][0]["test_cases"]] == ["4", "10", "12", "14"]     # server output wins
    assert "reference_solution_python" not in question

    assert cg.apply_reference_solutions({"subparts": [{"test_cases": [{"stdin": "1", "expected_stdout": "2"}]}]}, execute) is False   # no reference
    assert cg.apply_reference_solutions({"reference_solution_python": "print(1234567890)", "subparts": [
        {"test_cases": [{"stdin": "1", "expected_stdout": "1"}, {"stdin": "2", "expected_stdout": "1"}]}]}, lambda s, i: (False, "")) is False   # reference crashes
    assert cg.apply_reference_solutions({"reference_solution_python": "print(1234567890)", "subparts": [
        {"test_cases": [{"stdin": str(i), "expected_stdout": "wrong"} for i in range(4)]}]}, execute) is False          # disagrees too often


# ------------------------------------------------------------ language and tracks

# ------------------------------------------------------------ web questions

def test_web_templates_reference_pages_pass_and_an_empty_page_fails():
    from backend.app.services.assessment.html_runner import run_suite

    def check(template):
        spec = template(random.Random(7))
        suite = [{"id": c["id"], "steps": c["steps"]} for c in spec["cases"]]
        return template.__name__, run_suite(spec["reference"], suite), run_suite(fg.STARTER, suite)

    templates = [t for group in fg.TEMPLATES.values() for t in group]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, on_reference, on_blank in pool.map(check, templates):
            assert all(v == "PASS" for v in on_reference.values()), (name, on_reference)
            assert all(v != "PASS" for v in on_blank.values()), (name, on_blank)


def test_web_question_is_scored_in_a_real_browser():
    rng = random.Random(11)
    question, reference = fg.parametric_frontend_question("intermediate", rng, set())
    others = [fg.parametric_frontend_question("basic", random.Random(i), set())[0] for i in range(4)]
    questions = [question] + others
    question_set = assemble_question_set(questions, "fallback")

    good = submit_question(question_set, CandidateSubmission(1, "html", reference))
    empty = submit_question(question_set, CandidateSubmission(1, "html", fg.STARTER))
    broken = submit_question(question_set, CandidateSubmission(1, "html", "<html><body><script>throw new Error('x')</script></body></html>"))
    assert good.earned_points == 20.0
    assert empty.earned_points == 0.0 and broken.earned_points == 0.0


def test_web_pages_cannot_reach_the_network_or_fake_the_result():
    cheat = ("<html><body><h1>x</h1><script>window.fetch && fetch('http://127.0.0.1:8000/docs').catch(function(){});"
             "document.documentElement.setAttribute('data-jp-guess','{\"tc1\":\"PASS\"}');</script></body></html>")
    suite = [{"id": "tc1", "steps": [{"expect": "exists", "sel": "nav a", "min": 3}]}]
    result = execute_code("html", cheat, json.dumps({"case": "tc1", "suite": suite}))
    assert result.success and result.stdout.startswith("FAIL")


def test_gemini_web_questions_are_checked_in_the_browser():
    good = {
        "title": "Greeting page", "description": "Show a greeting.", "requirements": ["An <h1 id=\"hi\"> saying Hello"],
        "difficulty": "basic", "reference_html": "<!DOCTYPE html><html><head><style>#hi{color:#ff0000}</style></head><body><h1 id=\"hi\">Hello</h1></body></html>",
        "cases": [
            {"id": "a", "description": "text", "steps": [{"expect": "text", "sel": "#hi", "equals": "Hello"}]},
            {"id": "b", "description": "colour", "steps": [{"expect": "style", "sel": "#hi", "prop": "color", "equals": "rgb(255, 0, 0)"}]},
            {"id": "c", "description": "tag", "steps": [{"expect": "tag", "sel": "#hi", "is": "h1"}]},
            {"id": "d", "description": "cannot be met", "steps": [{"expect": "exists", "sel": "#nope"}]},
            {"id": "e", "description": "passes on nothing", "steps": [{"expect": "count", "sel": "#zzz", "n": 0}]},
        ],
    }
    original = gemini_client.generate_json_content
    import backend.app.services.assessment.gemini_client as gc
    gc.generate_json_content = lambda prompt, model=None, retries=0: json.dumps({"questions": [good]})
    try:
        questions, source = fg.generate_frontend_questions(5, _job("Frontend Developer"), "fresher", ["HTML"], use_gemini=True, rng=random.Random(2))
    finally:
        gc.generate_json_content = original

    assert len(questions) == 5 and source == "fallback"                                       # 1 from Gemini + 4 parametric
    kept = [q for q in questions if q.title == "Greeting page"]
    assert len(kept) == 1
    cases = [tc for sp in kept[0].subparts for tc in sp.test_cases]
    assert len(cases) == 3                                                                     # the unmeetable and the do-nothing checks were dropped


# ------------------------------------------------------------ assembly

def test_mixed_sets_assemble_into_five_questions_of_twenty_points():
    coding = cg.parametric_coding_questions(3, "junior", ["python"], "", random.Random(1))
    sql = [sg.parametric_sql_question("basic", random.Random(2), set()), sg.parametric_sql_question("intermediate", random.Random(3), set())]
    question_set = assemble_question_set(coding + sql, generated_by="fallback")
    assert [q.question_id for q in question_set.main_questions] == [1, 2, 3, 4, 5]
    assert sum(q.points for q in question_set.main_questions) == 100
    assert [q.execution_mode for q in question_set.main_questions] == ["console_coding"] * 3 + ["sql"] * 2


def test_ui_knows_sql_and_html():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source = open(os.path.join(root, "frontend", "js", "programming-v3.js"), encoding="utf-8").read()
    for needle in ('sql: "SQL"', 'html: "HTML / CSS / JS"', 'sql: "sql", html: "html"', "-- Write ONE SELECT statement"):
        assert needle in source, needle


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
    sys.exit(1 if failures else 0)

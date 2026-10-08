"""
The technical lab environment: an artifact (log, config, network plan, code, policy) and five questions whose answers are computed by code from the very lines the
candidate sees. Every answer here is re-derived independently from the artifact's text, so the key can never drift from what the candidate was shown.
"""

import json
import os
import re
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_practical_assessments as PA

import backend.app.main  # noqa: F401
from backend.app.api.routes.hr_assessment_review import _practical_verdict, _sjt_review_data
from backend.app.services.assessment.assessment_planner import stage3_fixed_kind
from backend.app.services.assessment.sjt_integration import candidate_view, start_sjt_assessment, submit_sjt_assessment
from backend.app.services.assessment.stage_progression_service import _stage_3_assessment_type
from backend.app.services.sjt import practical_data_templates as data
from backend.app.services.sjt import practical_lab_templates as lab


@pytest.fixture(autouse=True)
def lab_on(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_PRACTICAL", "on")
    monkeypatch.setenv("ASSESSMENT_ROLEPLAY", "off")
    monkeypatch.setenv("ASSESSMENT_CATALOGUE", "on")
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "off")


def _answers(task):
    return [q["correct_answer"] for q in task["questions"]]


@pytest.mark.parametrize("template", sorted(lab.TEMPLATES))
def test_every_lab_builds_for_many_candidates_and_its_key_accepts_itself(template):
    for seed in range(150):
        task = lab.build_lab_task(template, seed)
        assert len(task["questions"]) == 5 and task["artifact"]["lines"] and task["artifact"]["name"] and task["note"]
        assert all(data.check_answer(q, q["correct_answer"]) for q in task["questions"])


def test_two_candidates_get_different_artifacts_and_the_same_seed_the_same_one():
    a, b = lab.build_lab_task("log_analysis", "1:10"), lab.build_lab_task("log_analysis", "2:11")
    assert a["artifact"]["lines"] != b["artifact"]["lines"] and lab.build_lab_task("log_analysis", "1:10")["artifact"]["lines"] == a["artifact"]["lines"]


def test_the_log_analysis_answers_follow_from_the_log():
    for seed in range(40):
        task = lab.build_lab_task("log_analysis", seed)
        rows = [re.match(r'(\S+) - - \[12/Mar/2026:(\d\d):\d\d:\d\d \+0000\] "(\w+) (\S+) HTTP/1.1" (\d+) (\d+)', line).groups() for line in task["artifact"]["lines"]]
        errors = [r for r in rows if int(r[4]) >= 500]
        by_ip = Counter(r[0] for r in rows)
        by_endpoint = Counter(r[3] for r in errors)
        by_hour = Counter(int(r[1]) for r in errors)
        orders = sum(int(r[5]) for r in rows if r[2] == "GET" and r[3] == "/api/orders" and r[4] == "200")

        assert _answers(task) == [len(errors), by_ip.most_common(1)[0][0], by_endpoint.most_common(1)[0][0], by_hour.most_common(1)[0][0], orders]
        assert by_ip.most_common(2)[0][1] != by_ip.most_common(2)[1][1] if len(by_ip) > 1 else True


def test_the_auth_log_answers_follow_from_the_log():
    for seed in range(40):
        task = lab.build_lab_task("auth_log", seed)
        failed = [re.search(r"Failed password for (?:invalid user )?(\S+) from (\S+) ", l) for l in task["artifact"]["lines"]]
        failed = [m for m in failed if m]
        by_ip = Counter(m.group(2) for m in failed)
        attacker = by_ip.most_common(1)[0][0]
        users = {m.group(1) for m in failed if m.group(2) == attacker}
        accepted = [re.search(r"Accepted \w+ for (\S+) from (\S+) ", l) for l in task["artifact"]["lines"]]
        compromised = next(m.group(1) for m in accepted if m and m.group(2) == attacker)
        first = next(l[7:15] for l in task["artifact"]["lines"] if f"Failed password" in l and f"from {attacker} " in l)

        assert _answers(task) == [len(failed), attacker, len(users), compromised, first]


def test_the_kubernetes_answers_point_at_the_right_lines():
    for seed in range(40):
        task = lab.build_lab_task("config_debug", seed)
        lines = task["artifact"]["lines"]
        port = int(next(l for l in lines if "containerPort:" in l).split(":")[1])
        replicas = int(next(l for l in lines if l.strip().startswith("replicas:")).split(":")[1])
        tag = next(l for l in lines if l.strip().startswith("image:")).split(":")[-1].strip()
        selector_line = [i + 1 for i, l in enumerate(lines) if l.strip().startswith("app:")][-1]
        target_line = next(i + 1 for i, l in enumerate(lines) if "targetPort:" in l)

        assert _answers(task) == [selector_line, target_line, port, replicas, tag]
        assert lines[selector_line - 1].strip() != lines[12].strip()                                   # the service really does select something the pods are not labelled with
        assert int(lines[target_line - 1].split(":")[1]) != port


def test_the_network_answers_follow_from_the_plan():
    import ipaddress

    for seed in range(60):
        task = lab.build_lab_task("network_math", seed)
        text = {re.split(r"\s{2,}", l)[0]: re.split(r"\s{2,}", l)[1] for l in task["artifact"]["lines"] if "  " in l and l.strip()}
        branch = ipaddress.ip_network(text["Branch LAN"])
        router = ipaddress.ip_interface(text["Core router"])
        vlan = ipaddress.ip_network(text["Server VLAN"])
        printer, laptop = ipaddress.ip_interface(text["Printer"]), ipaddress.ip_interface(text["Laptop"])
        wanted = int(text["New office needs"].split()[0])
        best = max(p for p in range(1, 31) if 2 ** (32 - p) - 2 >= wanted)

        assert _answers(task) == [branch.num_addresses - 2, str(router.network.network_address), str(vlan.broadcast_address),
                                  "yes" if laptop.ip in printer.network else "no", best]


def test_the_code_review_answers_point_at_real_flaws():
    for seed in range(40):
        task = lab.build_lab_task("security_review", seed)
        lines = task["artifact"]["lines"]
        markers = {"sql injection": ("SELECT * FROM users WHERE name = '\" + name",), "hardcoded credentials": ("api_key = \"sk_live",), "weak hashing": ("hashlib.md5",),
                   "command injection": ("os.system(",), "path traversal": ("open(\"/var/reports/\" + name)",)}

        for question in task["questions"][:3]:
            line = lines[question["correct_answer"] - 1]
            assert any(any(m in line for m in ms) for ms in markers.values()), line

        named_line = int(re.search(r"line (\d+)", task["questions"][3]["question"]).group(1))
        assert any(m in lines[named_line - 1] for m in markers[task["questions"][3]["correct_answer"]])
        assert task["questions"][4]["correct_answer"] == sum(1 for l in lines if l.startswith("def ")) - len(markers_found(lines, markers))


def markers_found(lines, markers):
    return [name for name, ms in markers.items() if any(any(m in l for m in ms) for l in lines)]


def test_the_policy_answers_follow_from_the_policy():
    for seed in range(60):
        task = lab.build_lab_task("iam_policy", seed)
        policy = json.loads("\n".join(task["artifact"]["lines"]))
        statements = policy["Statement"]
        allow = [s for s in statements if s["Effect"] == "Allow"]
        admin = next(s["Sid"] for s in allow if "*" in s["Action"] and s["Resource"] == "*")
        deny = next(s["Sid"] for s in statements if s["Effect"] == "Deny" and any("Delete" in a for a in s["Action"]))

        assert _answers(task) == [len(allow), admin, sum(1 for s in allow if any(a.startswith("s3:") for a in s["Action"])), deny, sum(1 for s in statements if s["Resource"] == "*")]


# ------------------------------------------------------------------ the whole thing through the stage-3 service
def _seed(db, title="DevOps Engineer", skills=""):
    return PA._seed(db, title, skills)


def test_a_devops_candidate_gets_a_configuration_lab_that_is_marked_exactly():
    db = PA._db()
    user, job, application, session_record = _seed(db)

    assert stage3_fixed_kind(job) is None and _stage_3_assessment_type(db, application) == "situational_judgment"

    assessment, _ = start_sjt_assessment(db, session_record, application)
    view = candidate_view(assessment, job=job)

    assert assessment.kind == "practical" and view["practical"]["environment"] == "lab" and view["practical"]["lab"] == "config_debug"
    assert view["practical"]["artifact"]["lines"][0] == "apiVersion: apps/v1" and len(view["questions"]) == 5
    assert all(q["type"] == "lab" and "correct_answer" not in q and "tolerance" not in q for q in view["questions"])
    assert "correct_answer" not in json.dumps(view)

    stored = json.loads(assessment.questions)
    answers = [{"question_id": q["id"], "selected_answer": str(q["correct_answer"])} for q in stored]
    answers[0]["selected_answer"] = "1"                                                                # one wrong

    result = submit_sjt_assessment(db, assessment, application, answers)
    assert result["percentage"] == 80 and result["passed"] is True

    review = _sjt_review_data(db, application)
    assert review["kind"] == "practical" and review["environment"] == "lab" and review["correct_count"] == 4

    verdict = _practical_verdict(review)
    assert verdict["marked_by"] == "auto" and "lab questions correctly" in verdict["verdict"] and len(verdict["gaps"]) == 1


@pytest.mark.parametrize("title,expected_lab", [
    ("Site Reliability Engineer", "log_analysis"), ("SOC Analyst", "auth_log"), ("Network Engineer", "network_math"),
    ("Penetration Tester", "security_review"), ("Cloud Engineer", "iam_policy"), ("Platform Engineer", "config_debug"),
])
def test_each_technical_role_gets_the_lab_that_fits_it(title, expected_lab):
    db = PA._db()
    user, job, application, session_record = _seed(db, title)
    assessment, _ = start_sjt_assessment(db, session_record, application)
    assert json.loads(assessment.payload_json)["lab"] == expected_lab


def test_the_lab_page_and_intro_are_wired():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = open(os.path.join(root, "frontend", "js", "practical-environment.js"), encoding="utf-8").read()
    intro = open(os.path.join(root, "frontend", "js", "practical-intro.js"), encoding="utf-8").read()
    assert 'environment === "lab"' in env and "labBody" in env and "TEXT.lab" in intro


def test_a_busy_ai_service_is_asked_once_more_before_the_rules_are_used(monkeypatch):
    from types import SimpleNamespace as N
    from backend.app.services.assessment import assessment_planner as planner

    calls = []
    monkeypatch.setenv("ASSESSMENT_PLANNER_LLM", "on")
    monkeypatch.setattr(planner, "RETRY_AFTER_BUSY_SECONDS", 0)
    good = planner.rules_plan(N(id=None, title="Accountant", required_skills="", technologies="", requirements="", description="", responsibilities=""))

    def flaky(job):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("groq request failed (HTTP 429: rate_limit_exceeded)")
        return {**good, "source": "ai"}

    monkeypatch.setattr(planner, "ai_plan", flaky)
    job = N(id=None, title="Accountant", required_skills="", technologies="", requirements="", description="", responsibilities="", assessment_plan_json=None, role_shape_override=None)
    assert planner.get_job_plan(job, allow_llm=True, store=False)["source"] == "ai" and len(calls) == 2

    calls.clear()
    monkeypatch.setattr(planner, "ai_plan", lambda job: (_ for _ in ()).throw(RuntimeError("bad json")))
    assert planner.get_job_plan(job, allow_llm=True, store=False)["source"] == "rules"                      # a real failure is not retried


def test_the_test_page_treats_a_lab_question_as_a_practical_one():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "frontend", "js", "sjt-test.js"), encoding="utf-8").read()
    line = next(l for l in page.splitlines() if "question.type === \"written\"" in l)
    assert 'question.type === "lab"' in line                                 # otherwise the page looks for radio buttons and never sees the typed answers

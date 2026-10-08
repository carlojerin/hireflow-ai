"""Browser-agent / automation signals. A live test of a real browser agent showed what a page CAN see: its typing arrives within the same
millisecond. (Its clicks looked exactly like a mouse's, so clicks are NOT used as a signal.) These tests run the real guard module in Node
and check the server and HR registration of the flag."""

import json
import os
import subprocess
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARDS = os.path.join(ROOT, "frontend", "js", "integrity-guards.js").replace("\\", "/")


def _node(script: str, tmp_path):
    path = tmp_path / "probe.js"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_burst_detection_separates_agent_typing_from_human_typing(tmp_path):
    result = _node(textwrap.dedent(f"""
        const g = (() => {{ const m = {{ exports: {{}} }}; new Function("module", "self", require("fs").readFileSync("{GUARDS}", "utf8"))(m, undefined); return m.exports; }})();
        const human = [0, 90, 170, 280, 350, 470, 560];            // ~90 ms between keys
        const fastHuman = [0, 35, 65, 100, 128, 160];               // very fast typist: ~32 ms
        const rollover = [0, 8, 90, 180, 260];                      // two keys almost together, then normal
        const agent = [100, 100, 100, 101, 101];                    // a whole string in the same millisecond
        const agentSpread = [0, 4, 9, 14, 18, 400];                 // 4 keys inside 20 ms
        console.log(JSON.stringify({{human: g.isKeystrokeBurst(human), fastHuman: g.isKeystrokeBurst(fastHuman), rollover: g.isKeystrokeBurst(rollover),
            agent: g.isKeystrokeBurst(agent), agentSpread: g.isKeystrokeBurst(agentSpread), empty: g.isKeystrokeBurst([])}}));
    """), tmp_path)

    assert result == {"human": False, "fastHuman": False, "rollover": False, "agent": True, "agentSpread": True, "empty": False}


def test_installed_guard_reports_agent_typing_once_and_ignores_normal_use(tmp_path):
    result = _node(textwrap.dedent(f"""
        const g = (() => {{ const m = {{ exports: {{}} }}; new Function("module", "self", require("fs").readFileSync("{GUARDS}", "utf8"))(m, undefined); return m.exports; }})();
        const listeners = {{}};
        const doc = {{ addEventListener: (t, f) => (listeners[t] = listeners[t] || []).push(f), removeEventListener() {{}} }};
        let clock = 0;
        const win = {{ setInterval: () => 0, clearInterval() {{}}, performance: {{ now: () => clock }}, outerWidth: 1000, innerWidth: 1000, outerHeight: 800,
                      innerHeight: 700, getSelection: () => ({{ toString: () => "" }}), screen: {{}} }};
        const reports = [];
        g.install((type, meta) => reports.push([type, meta]), doc, win);

        const textarea = {{ tagName: "TEXTAREA", closest: () => null, isContentEditable: false }};
        const body = {{ tagName: "DIV", closest: () => null, isContentEditable: false }};
        const key = (target, ch, extra = {{}}) => listeners.keydown.forEach(f => f(Object.assign({{ target, key: ch, preventDefault() {{}} }}, extra)));

        // 1. a person typing: 90 ms apart
        for (const ch of "hello world") {{ clock += 90; key(textarea, ch); }}
        const afterHuman = reports.length;

        // 2. keystrokes on a page that is not a text box: ignored
        for (const ch of "abcdef") {{ key(body, ch); }}
        const afterBody = reports.length;

        // 3. Ctrl+key shortcuts and non-character keys are ignored even when fast
        for (const ch of ["a", "b", "c", "d", "e"]) key(textarea, ch, {{ ctrlKey: true }});
        for (const name of ["Shift", "Shift", "Shift", "Shift", "Shift"]) key(textarea, name);
        const afterShortcuts = reports.length;

        // 4. an agent typing a word: every character in the same millisecond
        clock += 1000;
        for (const ch of "answer") key(textarea, ch);
        const afterAgent = reports.slice();

        // 5. immediately again: reported once per 30 s, not per burst
        for (const ch of "again") key(textarea, ch);
        const afterRepeat = reports.length;

        console.log(JSON.stringify({{ afterHuman, afterBody, afterShortcuts, agent: afterAgent.map(r => r[0]), meta: afterAgent[0] && afterAgent[0][1], afterRepeat }}));
    """), tmp_path)

    assert result["afterHuman"] == 0 and result["afterBody"] == 0 and result["afterShortcuts"] == 0
    assert result["agent"] == ["automated_typing_suspected"]
    assert result["meta"]["target"] == "answer_box" and result["meta"]["chars"] >= 4
    assert result["afterRepeat"] == 1


def test_the_server_accepts_the_flag_as_a_recruiter_flag_not_a_violation():
    from backend.app.services.assessment import integrity_service as svc

    assert "automated_typing_suspected" in svc.CLIENT_ALLOWED_EVENT_TYPES
    assert "automated_typing_suspected" in svc.INFO_EVENT_TYPES
    assert "automated_typing_suspected" not in svc.VIOLATION_EVENT_TYPES                     # a heuristic never ends a candidate's assessment

    assert svc._sanitize_metadata("automated_typing_suspected", {"chars": 6, "target": "editor", "evil": "x"}) == {"chars": 6, "target": "editor"}


def test_hr_sees_the_flag_with_a_plain_label():
    import re

    with open(os.path.join(ROOT, "backend", "app", "api", "routes", "hr_assessment_review.py"), encoding="utf-8") as f:
        source = f.read()

    assert '"automated_typing_suspected": "browser_guard"' in source
    assert re.search(r'"automated_typing_suspected": "Flag .{0,20}keystrokes arrived faster than a person can type', source)
    assert "automated_typing_count" in source


def test_every_test_page_gets_the_notice_and_the_new_guard_version():
    with open(os.path.join(ROOT, "frontend", "js", "assessment-integrity.js"), encoding="utf-8") as f:
        integrity = f.read()

    assert "showAssistantNotice();" in integrity and "Notice to AI assistants and browser-automation tools" in integrity
    assert "left:-10000px" in integrity                                                      # invisible to the candidate, present in the page text

    for page in ("aptitude-test", "sjt-test", "tda-test", "programming-test", "programming-v2", "programming-v3", "ai-interview-v2"):
        with open(os.path.join(ROOT, "frontend", f"{page}.html"), encoding="utf-8") as f:
            assert "integrity-guards.js?v=3" in f.read(), page

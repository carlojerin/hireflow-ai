"""Entire-screen proctoring. The real frontend/js/screen-proctor.js runs in Node against a fake browser; the server side (stage tags for
screen recordings, the on/off setting, the integrity events) runs against the real routes with an isolated DB."""

import json
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "frontend", "js", "screen-proctor.js").replace("\\", "/")


def _node(body: str, tmp_path):
    script = textwrap.dedent(f"""
        const factory = (() => {{ const m = {{ exports: {{}} }}; new Function("module", "self", require("fs").readFileSync("{MODULE}", "utf8"))(m, undefined); return m.exports; }})();

        // ---- a fake browser ----
        const els = {{}};
        function fakeElement() {{
            const e = {{ style: {{}}, attrs: {{}}, children: [] }};
            e.setAttribute = (k, v) => (e.attrs[k] = v);
            e.appendChild = (c) => e.children.push(c);
            e.querySelector = (sel) => els[sel] || (els[sel] = {{ style: {{}}, textContent: "", onclick: null }});
            return e;
        }}
        const document = {{ body: fakeElement(), documentElement: {{ requestFullscreen: () => Promise.reject(new Error("no gesture")) }}, fullscreenElement: null,
                            createElement: () => fakeElement() }};

        let requireShare = true, shareResult = null, tracksStopped = 0;
        const recorders = [];
        class FakeRecorder {{
            constructor() {{ this.calls = []; recorders.push(this); }}
            isSupported() {{ return true; }}
            start(stream, tag, options) {{ this.tag = tag; this.options = options; this.started = true; return true; }}
            async stop() {{ this.stopped = true; }}
            async upload(app, tag) {{ this.uploaded = [app, tag]; return {{ ok: true }}; }}
            cleanup() {{}}
        }}
        function makeTrack(surface) {{
            const listeners = {{}};
            return {{ getSettings: () => (surface === undefined ? {{}} : {{ displaySurface: surface }}), stop() {{ tracksStopped++; }},
                      addEventListener: (t, f) => (listeners[t] = f), removeEventListener: (t) => delete listeners[t], fire: (t) => listeners[t] && listeners[t]() }};
        }}
        function makeStream(track) {{ return {{ getVideoTracks: () => [track], getTracks: () => [track] }}; }}

        const navigator = {{ mediaDevices: {{ getDisplayMedia: async (c) => {{ navigator.lastConstraints = c; if (shareResult instanceof Error) throw shareResult; return shareResult; }} }} }};
        const fetch = async () => ({{ json: async () => ({{ require_screen_share: requireShare }}) }});
        const events = [];
        const report = (type) => events.push(type);
        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
        const click = () => els["#screenProctorButton"].onclick();
        const sp = () => factory({{ document, navigator, fetch, Recorder: FakeRecorder, apiBase: "" }});

        (async () => {{
            {textwrap.indent(textwrap.dedent(body), "            ")}
        }})().catch(e => {{ console.error(e); process.exit(1); }});
    """)
    path = tmp_path / "screen.js"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_pure_helpers_map_every_stage_and_segment_to_a_screen_recording_tag(tmp_path):
    result = _node("""
        const s = sp();
        console.log(JSON.stringify({
            tags: [s.stageTag("self_intro", 1), s.stageTag("aptitude", 1), s.stageTag("situational_judgment", 1), s.stageTag("technical_domain", 2), s.stageTag("programming", 3), s.stageTag("ai_interview", 1)],
            none: [s.stageTag("aptitude", 4), s.stageTag("aptitude", 0), s.stageTag("nonsense", 1), s.stageTag("", 1)],
            surfaces: [s.isEntireScreen(makeTrack("monitor")), s.isEntireScreen(makeTrack("window")), s.isEntireScreen(makeTrack("browser")), s.isEntireScreen(makeTrack(undefined))],
        }));
    """, tmp_path)
    assert result["tags"] == ["self_intro_screen", "aptitude_screen", "programming_screen", "programming_screen_2", "programming_screen_3", "ai_interview_screen"]
    assert result["none"] == [None, None, None, None]
    assert result["surfaces"] == [True, False, False, True]


def test_the_test_does_not_start_until_the_entire_screen_is_shared_and_a_window_is_refused(tmp_path):
    result = _node("""
        const s = sp();
        let resolved = false;
        const gate = s.gate({ assessmentType: "aptitude", applicationId: 7 }, report).then(v => (resolved = v));
        await sleep(20);
        const blockedBefore = !resolved;

        shareResult = makeStream(makeTrack("window"));                   // the candidate picks a window
        await click(); await sleep(20);
        const afterWindow = { resolved, events: events.slice(), recorders: recorders.length };

        const monitor = makeTrack("monitor");
        shareResult = makeStream(monitor);                               // then the entire screen
        await click(); await sleep(20);
        console.log(JSON.stringify({ blockedBefore, afterWindow, resolved, events, recorderTag: recorders[0].tag, options: recorders[0].options,
            constraints: navigator.lastConstraints.video.displaySurface, tracksStoppedForWindow: tracksStopped }));
    """, tmp_path)
    assert result["blockedBefore"] is True
    assert result["afterWindow"] == {"resolved": False, "events": ["screen_share_rejected"], "recorders": 0}
    assert result["resolved"] is True and result["events"] == ["screen_share_rejected", "screen_share_started"]
    assert result["recorderTag"] == "aptitude_screen" and result["options"]["videoBitsPerSecond"] == 400000
    assert result["constraints"] == "monitor" and result["tracksStoppedForWindow"] == 1               # the refused window share was released


def test_cancelling_the_picker_keeps_the_test_blocked(tmp_path):
    result = _node("""
        const s = sp();
        let resolved = false;
        s.gate({ assessmentType: "programming", applicationId: 7 }, report).then(v => (resolved = v));
        await sleep(20);
        shareResult = new Error("Permission denied");
        await click(); await sleep(20);
        console.log(JSON.stringify({ resolved, message: els["#screenProctorTitle"].textContent, events, recorders: recorders.length }));
    """, tmp_path)
    assert result["resolved"] is False and "required" in result["message"] and result["recorders"] == 0


def test_stopping_the_share_records_a_violation_blocks_the_test_and_a_second_part_is_recorded(tmp_path):
    result = _node("""
        const s = sp();
        const first = makeTrack("monitor");
        shareResult = makeStream(first);
        const gate = s.gate({ assessmentType: "ai_interview", applicationId: 9 }, report);
        await sleep(20); await click(); await gate;

        first.fire("ended");                                             // the candidate clicks "Stop sharing"
        await sleep(30);
        const afterStop = { events: events.slice(), title: els["#screenProctorTitle"].textContent, firstUploaded: recorders[0].uploaded, visible: true };

        shareResult = makeStream(makeTrack("monitor"));
        await click(); await sleep(30);
        console.log(JSON.stringify({ afterStop, events, secondTag: recorders[1].tag, overlayHidden: true }));
    """, tmp_path)
    assert result["afterStop"]["events"] == ["screen_share_started", "screen_share_stopped"]
    assert result["afterStop"]["title"] == "Screen sharing stopped"
    assert result["afterStop"]["firstUploaded"] == [9, "ai_interview_screen"]                          # what was recorded so far is saved
    assert result["secondTag"] == "ai_interview_screen_2"


def test_finish_uploads_the_recording_releases_the_screen_and_is_not_a_violation(tmp_path):
    result = _node("""
        const s = sp();
        const track = makeTrack("monitor");
        shareResult = makeStream(track);
        const gate = s.gate({ assessmentType: "situational_judgment", applicationId: 3 }, report);
        await sleep(20); await click(); await gate;

        await s.finish();
        track.fire("ended");                                             // the browser reports the track ending after we stopped it
        await sleep(20);
        console.log(JSON.stringify({ events, uploaded: recorders[0].uploaded, stopped: recorders[0].stopped, tracksStopped }));
    """, tmp_path)
    assert result["events"] == ["screen_share_started"]                                                 # finishing is not "stopped sharing"
    assert result["uploaded"] == [3, "programming_screen"] and result["stopped"] is True and result["tracksStopped"] == 1


def test_unsupported_browsers_are_blocked_and_it_can_be_switched_off_or_skipped(tmp_path):
    result = _node("""
        const s = sp();
        navigator.mediaDevices = {};                                     // no getDisplayMedia (a phone, an old browser)
        let resolved = false;
        s.gate({ assessmentType: "aptitude", applicationId: 1 }, report).then(() => (resolved = true));
        await sleep(30);
        const unsupported = { resolved, events: events.slice(), title: els["#screenProctorTitle"].textContent };

        // switched off by the admin: the gate opens at once and nothing is asked
        const off = factory({ document, navigator: { mediaDevices: {} }, fetch: async () => ({ json: async () => ({ require_screen_share: false }) }), Recorder: FakeRecorder, apiBase: "" });
        const offResult = await off.gate({ assessmentType: "aptitude", applicationId: 1 }, report);

        // a stage that is not assessed this way: nothing is asked
        const other = await sp().gate({ assessmentType: "something_else", applicationId: 1 }, report);
        console.log(JSON.stringify({ unsupported, offResult, intro: other }));
    """, tmp_path)
    assert result["unsupported"]["resolved"] is False and result["unsupported"]["events"] == ["screen_share_unsupported"]
    assert "cannot share" in result["unsupported"]["title"]
    assert result["offResult"] is True and result["intro"] is True


# ------------------------------------------------------------------ server side

def test_screen_recordings_use_their_own_stage_tags_and_nothing_else_slips_through():
    from backend.app.api.routes import assessment_videos as av

    for tag in ("aptitude_screen", "programming_screen", "ai_interview_screen", "aptitude_screen_2", "ai_interview_screen_3", "self_intro", "programming", "self_intro_screen", "self_intro_screen_2"):
        assert av.is_allowed_stage(tag), tag

    for tag in ("aptitude_screen_4", "aptitude_screen_1", "self_intro_screen_4", "../aptitude", "aptitude_screen/../x", "aptitude_screenX", "", "screen"):
        assert not av.is_allowed_stage(tag), tag


def test_the_screen_share_requirement_is_a_setting_the_admin_controls(monkeypatch):
    import backend.app.main  # noqa: F401
    from backend.app.api.routes import assessment_videos as av
    from backend.app.services import admin_settings

    monkeypatch.delenv("ASSESSMENT_REQUIRE_SCREEN_SHARE", raising=False)
    assert av.get_screen_share_config() == {"require_screen_share": True}                          # on unless switched off

    for off in ("off", "OFF", "false", "0", "no"):
        monkeypatch.setenv("ASSESSMENT_REQUIRE_SCREEN_SHARE", off)
        assert av.get_screen_share_config() == {"require_screen_share": False}, off

    spec = admin_settings.SETTINGS["ASSESSMENT_REQUIRE_SCREEN_SHARE"]
    assert spec["group"] == "security" and spec["choices"] == ["on", "off"]
    assert admin_settings.validate_shape("ASSESSMENT_REQUIRE_SCREEN_SHARE", "off") == "off"
    with pytest.raises(ValueError):
        admin_settings.validate_shape("ASSESSMENT_REQUIRE_SCREEN_SHARE", "maybe")


def test_stopping_the_share_counts_as_a_violation_and_the_other_events_are_information():
    from backend.app.services.assessment import integrity_service as svc

    assert "screen_share_stopped" in svc.VIOLATION_EVENT_TYPES
    for event in ("screen_share_started", "screen_share_rejected", "screen_share_unsupported"):
        assert event in svc.INFO_EVENT_TYPES and event in svc.CLIENT_ALLOWED_EVENT_TYPES and event not in svc.VIOLATION_EVENT_TYPES
    assert "screen_share_stopped" in svc.CLIENT_ALLOWED_EVENT_TYPES


def test_screen_chunks_assemble_into_a_screen_recording_with_a_clear_folder_name(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import backend.app.main  # noqa: F401
    from backend.app.api import deps
    from backend.app.api.routes import assessment_videos as av
    from backend.app.database.database import Base, get_db
    from backend.app.models.assessment_video_recording import AssessmentVideoRecording
    from backend.app.models.job import Job
    from backend.app.models.job_application import JobApplication
    from backend.app.models.user import User

    root = str(tmp_path / "videos")
    monkeypatch.setattr(av, "VIDEO_STORAGE_ROOT", root)
    monkeypatch.setattr(av, "PARTIAL_ROOT", os.path.join(root, "_partial"))
    monkeypatch.setattr(av, "RECORDINGS_FOLDER", str(tmp_path / "recordings"))
    monkeypatch.setattr(av, "remux_for_playback", lambda path: True)
    monkeypatch.setattr(av, "require_csrf", lambda request: None)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    db.add(User(id=1, full_name="Asha Rao", username="asha", email="a@x.test", hashed_password="x", role="user"))
    job = Job(title="Copywriter", company="Acme", source="invite_only")
    db.add(job)
    db.flush()
    db.add(JobApplication(id=10, job_id=job.id, user_id=1, status="shortlisted"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(av.router)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=1, role="user")
    client = TestClient(app)

    for stage, content in (("aptitude_screen", b"SCREEN1"), ("aptitude_screen_2", b"SCREEN2")):
        assert client.post(f"/api/assessment/videos/10/{stage}/chunk", data={"seq": 0}, files={"file": ("c.webm", content, "video/webm")}).status_code == 200
        assert client.post(f"/api/assessment/videos/10/{stage}/finalize", data={"expected_chunks": 1}).status_code == 200

    assert client.post("/api/assessment/videos/10/aptitude_screen_4/chunk", data={"seq": 0}, files={"file": ("c.webm", b"x", "video/webm")}).status_code == 400
    assert client.get("/api/assessment/videos/config/screen-share").json() == {"require_screen_share": True}

    db = Session()
    assert sorted(r.stage for r in db.query(AssessmentVideoRecording).all()) == ["aptitude_screen", "aptitude_screen_2"]
    db.close()

    folder = next((tmp_path / "recordings").iterdir())
    assert sorted(p.name for p in folder.iterdir()) == ["2 - Aptitude Test - screen recording (part 2).webm", "2 - Aptitude Test - screen recording.webm"]


def test_the_integrity_module_gates_the_test_on_the_screen_share_before_installing_its_monitors():
    with open(os.path.join(ROOT, "frontend", "js", "assessment-integrity.js"), encoding="utf-8") as f:
        source = f.read()

    body = source[source.index("async function enterStrictMode"):]
    assert body.index("window.ScreenProctor.gate(config, reportIntegrityEvent)") < body.index("setupVisibilityMonitoring();")
    assert "window.ScreenProctor.finish()" in body

    for page in ("aptitude-test", "sjt-test", "tda-test", "programming-test", "programming-v2", "programming-v3", "ai-interview-v2"):
        with open(os.path.join(ROOT, "frontend", f"{page}.html"), encoding="utf-8") as f:
            html = f.read()
        assert html.index("screen-proctor.js") < html.index("assessment-integrity.js"), page


def test_the_hr_report_orders_and_labels_screen_recordings_next_to_their_stage():
    with open(os.path.join(ROOT, "frontend", "js", "company-candidate-report.js"), encoding="utf-8") as f:
        js = f.read()

    assert "SCREEN_STAGE = /^(self_intro|aptitude|programming|ai_interview)_screen(?:_([23]))?$/" in js
    assert "orderOf(a.stage) - orderOf(b.stage)" in js and "labelFor(r.stage)" in js and "screen${" in js
    assert chr(0x2014) + " screen" in js


def test_the_self_introduction_asks_for_the_screen_before_the_server_clock_starts_and_finishes_it_on_submit(tmp_path):
    with open(os.path.join(ROOT, "frontend", "js", "assessment.js"), encoding="utf-8") as f:
        js = f.read()

    start = js[js.index("async function startSelfIntro()"):]
    assert start.index("await requireScreenShare();") < start.index("/self-intro/start")           # the clock starts only after the share
    assert js.index("await requireScreenShare();", js.index("async function renderSelfIntroInProgress")) < js.index("startCountdown(seed)")
    assert "window.ScreenProctor.finish()" in js and "isActive()" in js

    with open(os.path.join(ROOT, "frontend", "assessment.html"), encoding="utf-8") as f:
        html = f.read()
    assert html.index("screen-proctor.js") < html.index("js/assessment.js")


def test_an_active_share_is_not_asked_for_twice(tmp_path):
    result = _node("""
        const s = sp();
        shareResult = makeStream(makeTrack("monitor"));
        const gate = s.gate({ assessmentType: "self_intro", applicationId: 5 }, report);
        await sleep(20); await click(); await gate;
        const active = s.isActive();
        await s.finish();
        console.log(JSON.stringify({ active, afterFinish: s.isActive(), tag: recorders[0].tag }));
    """, tmp_path)
    assert result == {"active": True, "afterFinish": False, "tag": "self_intro_screen"}

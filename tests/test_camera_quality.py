"""Camera setup coach: the real frontend/js/camera-quality.js runs in Node on synthetic frames (no camera needed)."""

import json
import os
import subprocess
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "frontend", "js", "camera-quality.js").replace("\\", "/")


def _node(body: str, tmp_path):
    script = textwrap.dedent(f"""
        const cq = (() => {{ const m = {{ exports: {{}} }}; new Function("module", "self", require("fs").readFileSync("{MODULE}", "utf8"))(m, undefined); return m.exports; }})();

        // a 64x48 frame whose brightness at (x, y) is fn(x, y), as RGBA
        function frame(fn) {{
            const w = 64, h = 48, data = new Uint8ClampedArray(w * h * 4);
            for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {{
                const v = fn(x, y), i = (y * w + x) * 4;
                data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
            }}
            return {{ data, width: w, height: h }};
        }}
        const box = (cx, width, cy = 0.5, height = 0.5) => ({{ x0: cx - width / 2, x1: cx + width / 2, y0: cy - height / 2, y1: cy + height / 2 }});
        const inBox = (b, x, y) => x / 64 >= b.x0 && x / 64 < b.x1 && y / 48 >= b.y0 && y / 48 < b.y1;
        // a scene: `face` brightness inside the face box, `bg` outside
        const scene = (face, bg, b) => frame((x, y) => (inBox(b, x, y) ? face : bg));
        const codes = (img, b) => cq.assessQuality(cq.analyzeFrame(img, b)).issues.map(i => i.code);

        (async () => {{
            {textwrap.indent(textwrap.dedent(body), "            ")}
        }})().catch(e => {{ console.error(e); process.exit(1); }});
    """)
    path = tmp_path / "quality.js"
    path.write_text(script, encoding="utf-8")
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_well_lit_well_framed_face_gets_no_advice(tmp_path):
    result = _node("""
        const b = box(0.5, 0.3);
        console.log(JSON.stringify({ good: codes(scene(140, 120, b), b) }));
    """, tmp_path)
    assert result["good"] == []


def test_lighting_problems_are_named_and_the_worst_comes_first(tmp_path):
    result = _node("""
        const b = box(0.5, 0.3);
        const sideLit = frame((x, y) => (inBox(b, x, y) ? (x / 64 < 0.5 ? 160 : 60) : 110));
        console.log(JSON.stringify({
            dark: codes(scene(30, 25, b), b),
            dim: codes(scene(50, 60, b), b),                       // the whole picture is dim
            backlit: codes(scene(60, 235, b), b),                  // a bright window behind a dim face
            glare: codes(scene(235, 150, b), b),
            brightRoomNotGlare: codes(scene(185, 215, b), b),      // a light face in a bright room is just a bright room
            side: codes(sideLit, b),
            darkMessage: cq.assessQuality(cq.analyzeFrame(scene(30, 25, b), b)).issues[0].message,
        }));
    """, tmp_path)
    assert result["dark"] == ["too_dark"]
    assert result["dim"] == ["dim"]
    assert result["backlit"] == ["backlit"]                                         # the window is the cause, so the advice is about the window
    assert result["glare"] == ["glare"]
    assert result["brightRoomNotGlare"] == []
    assert result["side"] == ["side_light"]
    assert "turn on a light" in result["darkMessage"].lower()


def test_framing_problems_are_named(tmp_path):
    result = _node("""
        const lit = (b) => scene(140, 120, b);
        const far = box(0.5, 0.1), close = box(0.5, 0.72), cut = { x0: 0, x1: 0.3, y0: 0.2, y1: 0.7 }, side = box(0.82, 0.2);
        const closeAndCut = { x0: 0.0, x1: 0.62, y0: 0.0, y1: 1.0 };
        console.log(JSON.stringify({ far: codes(lit(far), far), close: codes(lit(close), close), cut: codes(lit(cut), cut), side: codes(lit(side), side), closeAndCut: codes(lit(closeAndCut), closeAndCut) }));
    """, tmp_path)
    assert result["far"] == ["too_far"] and result["close"] == ["too_close"]
    assert result["cut"] == ["cut_off"]                                            # a cut-off head is not also reported as off-centre
    assert result["closeAndCut"] == ["too_close"]                                  # a face filling the picture: "move back", not "adjust the camera"
    assert result["side"] == ["off_center"]


def test_no_face_is_only_advice_when_the_room_is_dark(tmp_path):
    result = _node("""
        console.log(JSON.stringify({ darkRoom: codes(frame(() => 20), null), normalRoom: codes(frame(() => 120), null) }));
    """, tmp_path)
    assert result["darkRoom"] == ["no_face_dark"]
    assert result["normalRoom"] == []                                               # an empty chair is the proctoring's business, not lighting advice


def test_the_coach_waits_for_a_problem_to_last_and_for_a_fix_to_last_so_tips_do_not_flicker(tmp_path):
    result = _node("""
        const changes = [];
        const coach = cq.createCoach({ appearAfter: 4, clearAfter: 4, onChange: (issues) => changes.push(issues.map(i => i.code)) });
        const dark = { ok: false, issues: [{ code: "too_dark" }] };
        const fine = { ok: true, issues: [] };

        for (let i = 0; i < 3; i++) coach.feed(dark);            // 3 bad samples: not yet
        const early = changes.length;
        coach.feed(dark);                                         // the 4th: now it is shown
        const shown = changes.slice();
        coach.feed(fine); coach.feed(fine); coach.feed(fine);    // fixed for 3 samples: still shown
        const stillShown = changes.length;
        coach.feed(dark);                                         // a blip of the problem again resets the fix
        coach.feed(fine); coach.feed(fine); coach.feed(fine); coach.feed(fine);
        const flicker = changes.slice();

        const many = cq.createCoach({ appearAfter: 1, clearAfter: 1, maxShown: 2, onChange: (issues) => (many.last = issues.map(i => i.code)) });
        many.feed({ ok: false, issues: ["side_light", "too_dark", "off_center", "backlit"].map(code => ({ code })) });
        console.log(JSON.stringify({ early, shown, stillShown, flicker, capped: many.last }));
    """, tmp_path)
    assert result["early"] == 0
    assert result["shown"] == [["too_dark"]]
    assert result["stillShown"] == 1                                                 # no change yet
    assert result["flicker"] == [["too_dark"], []]                                   # shown once, cleared once: nothing in between
    assert result["capped"] == ["too_dark", "backlit"]                               # at most two tips, most important first


def test_a_darker_skin_tone_in_a_well_exposed_room_is_never_told_the_lighting_is_poor(tmp_path):
    result = _node("""
        const b = box(0.5, 0.3);
        console.log(JSON.stringify({
            darkSkinGoodRoom: codes(scene(55, 120, b), b),         // a face that is darker than the wall behind it, in a normal bright room
            darkSkinLightWall: codes(scene(50, 160, b), b),        // against a light wall, still not a window
            lightSkinGoodRoom: codes(scene(180, 120, b), b),
            realWindowBehind: codes(scene(50, 240, b), b),         // a genuine bright window: this IS backlighting
            darkSkinDimRoom: codes(scene(45, 70, b), b),           // the whole room is dim: advice applies to everyone
        }));
    """, tmp_path)
    assert result["darkSkinGoodRoom"] == [] and result["darkSkinLightWall"] == [] and result["lightSkinGoodRoom"] == []
    assert result["realWindowBehind"] == ["backlit"]
    assert result["darkSkinDimRoom"] == ["dim"]


# ------------------------------------------------------------------ wiring

def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_face_checker_measures_the_same_frame_for_lighting_and_framing():
    js = _read("frontend", "js", "face-proctor.js")

    assert "function readFrame(video, canvas)" in js and "function faceBoxOf(landmarks)" in js
    assert "canvas.width = 64;" in js and "canvas.height = 48;" in js
    assert "window.CameraQuality.assessQuality(window.CameraQuality.analyzeFrame(frame, faceBox))" in js
    assert "sample.faces !== null && sample.faces <= 1" in js                       # only with a working model and 0 or 1 face


def test_the_advice_appears_in_the_readiness_check_during_the_test_and_in_the_self_introduction():
    integrity = _read("frontend", "js", "assessment-integrity.js")
    intro = _read("frontend", "js", "assessment.js")

    assert "integrityCheckTipAnchor" in integrity and "startLightingCoachInCheck" in integrity and "stopLightingCoachInCheck();" in integrity
    assert "createLightingCoach()" in integrity and 'reportIntegrityEvent("poor_lighting")' in integrity
    assert "POOR_LIGHTING_NOTE_AFTER_MS = 20000" in integrity                        # a note for HR only after 20 s of poor lighting
    assert 'reportSelfIntroCameraEvent("poor_lighting")' in intro and "createTip({ anchor:" in intro

    # advice only: nothing in the coach disables the Continue button or counts a violation
    check = integrity[integrity.index("function startLightingCoachInCheck()"):integrity.index("function startLightingCoachInCheck()") + 900]
    assert "continueButton" not in check and "counted" not in check


def test_every_page_with_the_face_checker_loads_the_coach_before_it():
    for page in ("aptitude-test", "sjt-test", "tda-test", "programming-test", "programming-v2", "programming-v3", "ai-interview-v2", "assessment"):
        html = _read("frontend", f"{page}.html")
        assert html.index("camera-quality.js") < html.index("face-proctor.js"), page


def test_poor_lighting_is_a_quiet_note_for_hr_never_a_violation():
    from backend.app.api.routes import assessment_integrity as route
    from backend.app.services.assessment import integrity_service as svc
    from backend.app.services.assessment.recording_behavior_summary import _SIGNALS

    assert "poor_lighting" in svc.INFO_EVENT_TYPES and "poor_lighting" in svc.CLIENT_ALLOWED_EVENT_TYPES
    assert "poor_lighting" not in svc.VIOLATION_EVENT_TYPES
    assert "poor_lighting" in route.SELF_INTRO_CAMERA_EVENTS
    assert any(code == "poor_lighting" for code, _s, _p in _SIGNALS)

    hr = _read("backend", "app", "api", "routes", "hr_assessment_review.py")
    assert '"poor_lighting": "camera"' in hr and "may be caused by the lighting" in hr

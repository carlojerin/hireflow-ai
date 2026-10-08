// Node: the camera-proctoring decision engine with synthetic timelines (no camera, no model, no browser).
// Run with: node tests/frontend/test_face_proctor_engine.js
const assert = require("assert");
const path = require("path");
const fs = require("fs");
// evaluated as a plain script (the browser file uses a dynamic import() that makes Node guess "ES module")
const proctorModule = { exports: {} };
new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "face-proctor.js"), "utf8"))(proctorModule, proctorModule.exports);
const { createEngine, headPose } = proctorModule.exports;

const results = [];
function test(name, fn) {
    try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e && e.stack ? e.stack : String(e)]); }
}

// feed a constant sample for ms in 250 ms ticks; returns every event with its time
function feed(engine, sample, fromMs, ms) {
    const events = [];
    for (let t = fromMs; t < fromMs + ms; t += 250) engine.step(sample, t).forEach((e) => events.push([t, e]));
    return { events, end: fromMs + ms };
}
const FRONT = { faces: 1, yaw: 0.02, pitch: 0.01, luma: 120 };

function calibrated() {
    const e = createEngine();
    const r = feed(e, FRONT, 0, 4000);
    return { e, t: r.end };
}

test("one face looking at the screen: only face_present, never a violation", () => {
    const { e, t } = calibrated();
    const r = feed(e, FRONT, t, 60000);
    assert.deepStrictEqual(r.events, []);
});

test("no face for 4 s fires face_missing once, and again only after a face came back", () => {
    const { e, t } = calibrated();
    let r = feed(e, { faces: 0, luma: 120 }, t, 3500);
    assert.deepStrictEqual(r.events, []);                               // a blink / brief lean out is not reported
    r = feed(e, { faces: 0, luma: 120 }, r.end, 10000);
    assert.strictEqual(r.events.filter(([, ev]) => ev === "face_missing").length, 1);
    r = feed(e, FRONT, r.end, 1000);
    r = feed(e, { faces: 0, luma: 120 }, r.end, 6000);
    assert.strictEqual(r.events.filter(([, ev]) => ev === "face_missing").length, 1);
});

test("a second person for 2.5 s fires multiple_faces_detected", () => {
    const { e, t } = calibrated();
    const short = feed(e, { faces: 2, yaw: 0, pitch: 0, luma: 120 }, t, 2000);
    assert.deepStrictEqual(short.events, []);
    const long = feed(e, { faces: 2, yaw: 0, pitch: 0, luma: 120 }, short.end, 3000);
    assert.strictEqual(long.events.filter(([, ev]) => ev === "multiple_faces_detected").length, 1);
});

test("head turned away for 10 s fires looking_away (flag), a short glance (even 8 s) does not", () => {
    const { e, t } = calibrated();
    const glance = feed(e, { faces: 1, yaw: 0.4, pitch: 0.0, luma: 120 }, t, 8000);
    assert.deepStrictEqual(glance.events, []);
    const back = feed(e, FRONT, glance.end, 2000);
    const stare = feed(e, { faces: 1, yaw: 0.4, pitch: 0.0, luma: 120 }, back.end, 11000);
    assert.strictEqual(stare.events.filter(([, ev]) => ev === "looking_away").length, 1);
});

test("looking down at a phone (pitch) counts as looking away; the baseline is the candidate's own pose", () => {
    const e = createEngine();
    const tilted = { faces: 1, yaw: 0.0, pitch: 0.2, luma: 120 };         // someone who normally sits with the head tilted
    let r = feed(e, tilted, 0, 4000);
    r = feed(e, tilted, r.end, 20000);
    assert.deepStrictEqual(r.events.filter(([, ev]) => ev === "looking_away"), []);
    r = feed(e, { faces: 1, yaw: 0.0, pitch: 0.55, luma: 120 }, r.end, 11000);
    assert.strictEqual(r.events.filter(([, ev]) => ev === "looking_away").length, 1);
});

test("looking_away is rate-limited (one per cooldown window)", () => {
    const { e, t } = calibrated();
    let r = { end: t }, count = 0;
    for (let i = 0; i < 4; i++) {
        r = feed(e, { faces: 1, yaw: 0.5, pitch: 0, luma: 120 }, r.end, 11000);
        count += r.events.filter(([, ev]) => ev === "looking_away").length;
        r = feed(e, FRONT, r.end, 1000);
    }
    assert.ok(count >= 1 && count <= 3, "cooldown must limit the reports, got " + count);
});

test("a covered lens fires camera_covered (and not face_missing) even when the face model is unavailable", () => {
    const e = createEngine();
    const r = feed(e, { faces: null, luma: 4 }, 0, 6000);
    assert.deepStrictEqual(r.events.map(([, ev]) => ev), ["camera_covered"]);
    const covered = createEngine();
    feed(covered, FRONT, 0, 4000);
    const c = feed(covered, { faces: 0, luma: 3 }, 4000, 8000);
    assert.deepStrictEqual(c.events.map(([, ev]) => ev), ["camera_covered"]);
});

test("head pose from landmarks: centred nose = 0, turned head moves the ratio", () => {
    const lm = [];
    lm[1] = { x: 0.5, y: 0.5 }; lm[234] = { x: 0.4, y: 0.5 }; lm[454] = { x: 0.6, y: 0.5 }; lm[10] = { x: 0.5, y: 0.3 }; lm[152] = { x: 0.5, y: 0.7 };
    const front = headPose(lm);
    assert.ok(Math.abs(front.yaw) < 1e-9 && Math.abs(front.pitch) < 1e-9);
    lm[1] = { x: 0.57, y: 0.5 };
    assert.ok(headPose(lm).yaw > 0.3);
});

test("eyes off the screen for 6 s with the head facing it fires gaze_away; glancing does not", () => {
    const { e, t } = calibrated();
    const glance = feed(e, { faces: 1, yaw: 0.02, pitch: 0.01, gazeX: 0.9, gazeY: 0, luma: 120 }, t, 3000);
    assert.deepStrictEqual(glance.events, []);
    const back = feed(e, FRONT, glance.end, 1000);
    const stare = feed(e, { faces: 1, yaw: 0.02, pitch: 0.01, gazeX: 0.0, gazeY: -0.8, luma: 120 }, back.end, 7000);
    assert.strictEqual(stare.events.filter(([, ev]) => ev === "gaze_away").length, 1);
});

test("eyes off the screen for 20 s continuously fires the REAL gaze_away_sustained violation, not just the gaze_away flag", () => {
    const { e, t } = calibrated();
    const stare = feed(e, { faces: 1, yaw: 0.02, pitch: 0.01, gazeX: 0.0, gazeY: -0.8, luma: 120 }, t, 21000);
    assert.strictEqual(stare.events.filter(([, ev]) => ev === "gaze_away").length, 1);
    assert.strictEqual(stare.events.filter(([, ev]) => ev === "gaze_away_sustained").length, 1);
});

test("a brief look-away under 20 s never fires gaze_away_sustained, only the softer gaze_away flag", () => {
    const { e, t } = calibrated();
    const stare = feed(e, { faces: 1, yaw: 0.02, pitch: 0.01, gazeX: 0.0, gazeY: -0.8, luma: 120 }, t, 10000);
    assert.strictEqual(stare.events.filter(([, ev]) => ev === "gaze_away").length, 1);
    assert.deepStrictEqual(stare.events.filter(([, ev]) => ev === "gaze_away_sustained"), []);
});

test("gaze baseline is the candidate's own: a habitual side glance is not flagged", () => {
    const e = createEngine();
    const habit = { faces: 1, yaw: 0.02, pitch: 0.01, gazeX: 0.3, gazeY: 0.1, luma: 120 };
    let r = feed(e, habit, 0, 4000);
    r = feed(e, habit, r.end, 30000);
    assert.deepStrictEqual(r.events.filter(([, ev]) => ev === "gaze_away"), []);
});

test("blendshape gaze: looking left/right/down produce different signs", () => {
    const shapes = (o) => Object.entries(o).map(([categoryName, score]) => ({ categoryName, score }));
    const left = proctorModule.exports.gazeFromBlendshapes(shapes({ eyeLookOutLeft: 0.8, eyeLookInRight: 0.8 }));
    const right = proctorModule.exports.gazeFromBlendshapes(shapes({ eyeLookInLeft: 0.8, eyeLookOutRight: 0.8 }));
    const down = proctorModule.exports.gazeFromBlendshapes(shapes({ eyeLookDownLeft: 0.9, eyeLookDownRight: 0.9 }));
    assert.ok(left.gazeX > 0.5 && right.gazeX < -0.5 && down.gazeY < -0.5);
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, s]) => s === "FAIL");
results.forEach(([n, s, d]) => { if (s === "FAIL") process.stdout.write(`\n--- FAIL: ${n} ---\n${d}\n`); });
process.stdout.write("=".repeat(70) + "\n" + `${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length ? 1 : 0);

// Node: the voice-identity decision engine with synthetic pitch timelines (no audio, no browser).
// Run with: node tests/frontend/test_voice_identity_proctor_engine.js
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const mod = { exports: {} };
new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "voice-identity-proctor.js"), "utf8"))(mod, mod.exports);
const { createEngine, estimatePitch, centsDistance } = mod.exports;

const results = [];
function test(name, fn) {
    try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e && e.stack ? e.stack : String(e)]); }
}

// feed a constant pitch for ms in 250 ms ticks; returns every event with its time
function feed(engine, pitchHz, fromMs, ms) {
    const events = [];
    for (let t = fromMs; t < fromMs + ms; t += 250) engine.step(pitchHz, t).forEach((e) => events.push([t, e]));
    return { events, end: fromMs + ms };
}

function calibrated(baselineHz, overrides) {
    const e = createEngine(overrides);
    const r = feed(e, baselineHz, 0, 15000);   // 60 samples at 250ms = 15s, matches DEFAULTS.calibrationSamples
    return { e, t: r.end };
}

test("same speaker throughout: never flags, even over a long session", () => {
    const { e, t } = calibrated(120);
    const r = feed(e, 120, t, 120000);
    assert.deepStrictEqual(r.events, []);
});

test("natural pitch variation (stress/emotion) within range never flags", () => {
    const { e, t } = calibrated(120);
    // realistic wobble: +/- 15% around baseline, well under the 500-cent (5 semitone, ~41%) threshold
    const engine = e;
    let events = [];
    let tt = t;
    for (let i = 0; i < 40; i++) {
        const wobble = 120 * (1 + (i % 2 === 0 ? 0.1 : -0.1));
        const r = feed(engine, wobble, tt, 1000);
        events = events.concat(r.events);
        tt = r.end;
    }
    assert.deepStrictEqual(events, []);
});

test("a brief pitch spike (under the sustained threshold) does not flag", () => {
    const { e, t } = calibrated(120);
    const spike = feed(e, 250, t, 2000);   // well over 500 cents away, but only 2s -- under sustainedMs (4000)
    assert.deepStrictEqual(spike.events, []);
});

test("a sustained, clearly different pitch (a second person) fires possible_third_party_voice", () => {
    const { e, t } = calibrated(120);                       // candidate's own baseline: 120 Hz (typical male range)
    const different = feed(e, 260, t, 5000);                 // 260 Hz (clearly different register) held for 5s
    assert.strictEqual(different.events.filter(([, ev]) => ev === "possible_third_party_voice").length, 1);
});

test("silence/unvoiced gaps mid-sentence do not reset an in-progress deviation streak", () => {
    const { e, t } = calibrated(120);
    let r = feed(e, 260, t, 2000);                            // 2s of a different voice
    r = feed(e, null, r.end, 1000);                           // 1s silence (candidate paused, or AI briefly spoke)
    r = feed(e, 260, r.end, 2500);                            // another 2.5s of the different voice -- totals past 4s
    assert.strictEqual(r.events.filter(([, ev]) => ev === "possible_third_party_voice").length, 1);
});

test("the candidate's own voice returning resets the deviation streak (never a stale flag)", () => {
    const { e, t } = calibrated(120);
    let r = feed(e, 260, t, 2000);
    r = feed(e, 120, r.end, 1000);                            // candidate's own voice again: streak resets
    r = feed(e, 260, r.end, 3500);                            // only 3.5s this time -- under sustainedMs
    assert.deepStrictEqual(r.events, []);
});

test("is rate-limited by cooldown: a continuously different voice only flags once per cooldown window", () => {
    const { e, t } = calibrated(120);
    const r = feed(e, 260, t, 70000);                        // 70s continuously different (cooldown is 60s)
    const count = r.events.filter(([, ev]) => ev === "possible_third_party_voice").length;
    assert.ok(count >= 1 && count <= 2, "expected 1-2 flags over 70s with a 60s cooldown, got " + count);
});

test("centsDistance: identical pitches are 0, an octave apart is exactly 1200 cents", () => {
    assert.strictEqual(centsDistance(120, 120), 0);
    assert.ok(Math.abs(centsDistance(120, 240) - 1200) < 1e-6);
});

test("estimatePitch: silence returns null", () => {
    const silent = new Float32Array(2048);   // all zeros
    assert.strictEqual(estimatePitch(silent, 48000), null);
});

test("estimatePitch: a clean synthetic sine wave at 150 Hz is recovered within a few Hz", () => {
    const sampleRate = 48000;
    const freq = 150;
    const samples = new Float32Array(2048);
    for (let i = 0; i < samples.length; i++) samples[i] = Math.sin(2 * Math.PI * freq * i / sampleRate);
    const pitch = estimatePitch(samples, sampleRate);
    assert.ok(pitch !== null, "expected a pitch estimate for a clean tone");
    assert.ok(Math.abs(pitch - freq) < 5, "expected ~" + freq + " Hz, got " + pitch);
});

test("estimatePitch: white noise (no periodicity) returns null", () => {
    const samples = new Float32Array(2048);
    let seed = 42;
    for (let i = 0; i < samples.length; i++) {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        samples[i] = ((seed % 2000) / 1000 - 1) * 0.5;
    }
    assert.strictEqual(estimatePitch(samples, 48000), null);
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, s]) => s === "FAIL");
results.forEach(([n, s, d]) => { if (s === "FAIL") process.stdout.write(`\n--- FAIL: ${n} ---\n${d}\n`); });
process.stdout.write("=".repeat(70) + "\n" + `${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length ? 1 : 0);

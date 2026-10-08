// ============================================================
// JOBPILOT AI - DETERMINISTIC TESTS FOR THE TASK 4 ADDITIONS TO
// interview-voice-turn-logic-v2.js (utterance guards, echo-aware
// barge-in config, mic level, voice-turn state transitions).
// Plain Node, no framework. Run: node tests/frontend/test_interview_voice_utterance_logic_v2.js
// ============================================================

const path = require("path");
const assert = require("assert");

require(path.join(__dirname, "..", "..", "frontend", "js", "interview-voice-turn-logic-v2.js"));

const {
    STATES, EVENTS, nextState,
    DEFAULT_VAD_CONFIG, DEFAULT_UTTERANCE_CONFIG, BARGE_IN_VAD_CONFIG,
    vadTick, initialVadTracking, updateNoiseFloor, adaptiveVoiceThreshold, initialUtteranceStats, utteranceTick, evaluateUtterance, micLevel,
} = global.InterviewVoiceTurnLogicV2;

const results = [];

function test(name, fn) {
    try { fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

// feed `ms` of constant `rms` in 50 ms ticks; returns the last result and every non-NONE decision
function run(stats, rms, ms, cfg) {
    const decisions = [];
    let s = stats;
    for (let t = 0; t < ms; t += 50) {
        const r = utteranceTick(s, rms, 50, DEFAULT_VAD_CONFIG.voiceThreshold, cfg);
        s = r.stats;
        if (r.decision !== "NONE") decisions.push([t + 50, r.decision]);
    }
    return { stats: s, decisions };
}


test("voiced time accumulates only while above the threshold", () => {
    let r = run(initialUtteranceStats(), 0.1, 500);
    assert.strictEqual(r.stats.voicedMs, 500);
    r = run(r.stats, 0.001, 500);
    assert.strictEqual(r.stats.voicedMs, 500);          // silence adds elapsed time, not voiced time
    assert.strictEqual(r.stats.elapsedMs, 1000);
});

test("extremely short noise is not worth sending (below minimum speech)", () => {
    const stats = run(initialUtteranceStats(), 0.1, 200).stats;                     // a 200 ms click
    assert.deepStrictEqual(evaluateUtterance(stats, 4000), { send: false, reason: "too_short" });
});

test("speech at or above the minimum is sent", () => {
    const stats = run(initialUtteranceStats(), 0.1, DEFAULT_UTTERANCE_CONFIG.minSpeechMs).stats;
    assert.deepStrictEqual(evaluateUtterance(stats, 4000), { send: true, reason: null });
});

test("an empty recording is never sent, whatever the stats say", () => {
    const stats = run(initialUtteranceStats(), 0.1, 3000).stats;
    assert.deepStrictEqual(evaluateUtterance(stats, 0), { send: false, reason: "empty" });
});

test("maximum utterance duration forces a stop exactly at the limit", () => {
    const r = run(initialUtteranceStats(), 0.1, 121000);
    assert.strictEqual(r.decisions[0][1], "MAX_DURATION");
    assert.strictEqual(r.decisions[0][0], DEFAULT_UTTERANCE_CONFIG.maxUtteranceMs);
});

test("no speech at all times out after the no-speech window", () => {
    const r = run(initialUtteranceStats(), 0.0, 91000);
    assert.deepStrictEqual(r.decisions[0], [DEFAULT_UTTERANCE_CONFIG.noSpeechTimeoutMs, "NO_SPEECH_TIMEOUT"]);
});

test("real speech is never timed out as 'no speech'", () => {
    const talked = run(initialUtteranceStats(), 0.1, 1000).stats;
    const r = run(talked, 0.0, 100000);
    assert.ok(!r.decisions.some(([, d]) => d === "NO_SPEECH_TIMEOUT"));
});

test("limits are configurable per call and never mutate the defaults", () => {
    const r = run(initialUtteranceStats(), 0.1, 2000, { maxUtteranceMs: 1000 });
    assert.strictEqual(r.decisions[0][1], "MAX_DURATION");
    assert.strictEqual(DEFAULT_UTTERANCE_CONFIG.maxUtteranceMs, 120000);
    assert.ok(Object.isFrozen(DEFAULT_UTTERANCE_CONFIG) && Object.isFrozen(BARGE_IN_VAD_CONFIG));
});

test("barge-in is stricter than end-of-turn detection (speaker echo protection)", () => {
    assert.ok(BARGE_IN_VAD_CONFIG.voiceThreshold > DEFAULT_VAD_CONFIG.voiceThreshold);
    assert.ok(BARGE_IN_VAD_CONFIG.minVoicedMsToTrigger > DEFAULT_VAD_CONFIG.minVoicedMsToTrigger);
    // a level that starts a normal turn (0.03) must NOT interrupt the AI ...
    let t = initialVadTracking(), decision = "NONE";
    for (let i = 0; i < 40; i++) ({ tracking: t, decision } = vadTick(t, 0.03, 50, BARGE_IN_VAD_CONFIG));
    assert.strictEqual(t.hasTriggeredSpeech, false);
    // ... but loud, sustained speech does
    t = initialVadTracking();
    let detectedAt = null;
    for (let i = 1; i <= 20; i++) {
        const r = vadTick(t, 0.1, 50, BARGE_IN_VAD_CONFIG); t = r.tracking;
        if (r.decision === "VOICE_DETECTED") { detectedAt = i * 50; break; }
    }
    assert.strictEqual(detectedAt, BARGE_IN_VAD_CONFIG.minVoicedMsToTrigger);
});

test("dip tolerance: syllable gaps do not restart the barge-in trigger, real silence still does", () => {
    // 100 ms voiced / 50 ms dip pattern (like real speech) -- trigger fires within ~a second with tolerance ...
    const speechLike = (cfg) => {
        let t = initialVadTracking(), elapsed = 0;
        for (let i = 0; i < 60; i++) {
            const rms = i % 3 === 2 ? 0.005 : 0.1;                       // two voiced ticks, then a dip
            const r = vadTick(t, rms, 50, cfg); t = r.tracking; elapsed += 50;
            if (r.decision === "VOICE_DETECTED") return elapsed;
        }
        return null;
    };
    const strict = speechLike(Object.assign({}, BARGE_IN_VAD_CONFIG, { dipToleranceMs: 0 }));
    const tolerant = speechLike(BARGE_IN_VAD_CONFIG);
    assert.strictEqual(strict, null);                                   // without tolerance the counter never reaches 300 ms
    assert.ok(tolerant !== null && tolerant <= 500, String(tolerant));  // with it, detection is prompt
    // a dip LONGER than the tolerance still resets the counter
    let t = initialVadTracking();
    for (let i = 0; i < 4; i++) t = vadTick(t, 0.1, 50, BARGE_IN_VAD_CONFIG).tracking;      // 200 ms voiced
    for (let i = 0; i < 4; i++) t = vadTick(t, 0.005, 50, BARGE_IN_VAD_CONFIG).tracking;    // 200 ms dip > 150 ms
    assert.strictEqual(t.voicedMs, 0);
});

test("default end-of-turn VAD behaviour is unchanged (no dip tolerance)", () => {
    assert.strictEqual(DEFAULT_VAD_CONFIG.dipToleranceMs, 0);
    let t = initialVadTracking();
    t = vadTick(t, 0.1, 100, DEFAULT_VAD_CONFIG).tracking;
    t = vadTick(t, 0.005, 50, DEFAULT_VAD_CONFIG).tracking;
    assert.strictEqual(t.voicedMs, 0);
});

test("mic level meter is 0 for silence, bounded, and monotonic", () => {
    assert.strictEqual(micLevel(0), 0);
    assert.strictEqual(micLevel(-1), 0);
    assert.strictEqual(micLevel(NaN), 0);
    assert.strictEqual(micLevel(10), 1);
    assert.ok(micLevel(0.01) < micLevel(0.05) && micLevel(0.05) < micLevel(0.2));
});

test("voice-turn transitions: silence -> thinking -> preparing -> speaking -> listening", () => {
    let s = STATES.CANDIDATE_SILENCE;
    s = nextState(s, EVENTS.SUBMIT_STARTED);            assert.strictEqual(s, STATES.AI_THINKING);
    s = nextState(s, EVENTS.BRAIN_RESPONDED);           assert.strictEqual(s, STATES.AI_PREPARING);
    s = nextState(s, EVENTS.AI_AUDIO_READY);            assert.strictEqual(s, STATES.AI_SPEAKING);
    s = nextState(s, EVENTS.AI_AUDIO_ENDED);            assert.strictEqual(s, STATES.CANDIDATE_LISTENING);
});

test("barge-in transitions: speaking -> interrupted -> candidate speaking -> silence", () => {
    let s = STATES.AI_SPEAKING;
    s = nextState(s, EVENTS.CANDIDATE_VOICE_DETECTED);  assert.strictEqual(s, STATES.AI_INTERRUPTED);
    s = nextState(s, EVENTS.CANDIDATE_VOICE_DETECTED);  assert.strictEqual(s, STATES.CANDIDATE_SPEAKING);
    s = nextState(s, EVENTS.CANDIDATE_SILENCE_DETECTED); assert.strictEqual(s, STATES.CANDIDATE_SILENCE);
});

test("server 'no speech' returns to listening without advancing; errors are recoverable", () => {
    assert.strictEqual(nextState(STATES.AI_THINKING, EVENTS.TRANSCRIPT_EMPTY), STATES.CANDIDATE_LISTENING);
    assert.strictEqual(nextState(STATES.AI_THINKING, EVENTS.ERROR_OCCURRED), STATES.ERROR);
    assert.strictEqual(nextState(STATES.ERROR, EVENTS.RESET), STATES.CANDIDATE_LISTENING);
});

test("a late or duplicate event never moves an unrelated state and COMPLETED stays terminal", () => {
    assert.strictEqual(nextState(STATES.AI_THINKING, EVENTS.SUBMIT_STARTED), STATES.AI_THINKING);        // duplicate submit: no-op
    assert.strictEqual(nextState(STATES.CANDIDATE_LISTENING, EVENTS.BRAIN_RESPONDED), STATES.CANDIDATE_LISTENING);
    assert.strictEqual(nextState(STATES.COMPLETED, EVENTS.CANDIDATE_VOICE_DETECTED), STATES.COMPLETED);
    assert.strictEqual(nextState(STATES.COMPLETED, EVENTS.SUBMIT_STARTED), STATES.COMPLETED);
});


test("adaptive threshold: a noisy room still ends the turn, a quiet room keeps the base threshold", () => {
    // room hiss at 0.03 (above the fixed 0.02): with the fixed threshold silence was never detected
    let floor = null;
    for (let t = 0; t < 2000; t += 50) floor = updateNoiseFloor(floor, 0.03, 50);
    const threshold = adaptiveVoiceThreshold(floor, DEFAULT_VAD_CONFIG.voiceThreshold);
    assert.ok(threshold > 0.03 && threshold <= 0.06);

    let tracking = initialVadTracking();
    let ended = false;
    const cfg = Object.assign({}, DEFAULT_VAD_CONFIG, { voiceThreshold: threshold });
    for (let t = 0; t < 600; t += 50) tracking = vadTick(tracking, 0.15, 50, cfg).tracking;        // speaking
    for (let t = 0; t < 2000 && !ended; t += 50) {
        const r = vadTick(tracking, 0.03, 50, cfg);                                                 // hiss only
        tracking = r.tracking;
        ended = r.decision === "SILENCE_DETECTED";
    }
    assert.ok(ended);

    let quiet = null;
    for (let t = 0; t < 2000; t += 50) quiet = updateNoiseFloor(quiet, 0.002, 50);
    assert.strictEqual(adaptiveVoiceThreshold(quiet, 0.02), 0.02);
});

test("adaptive floor: continuous loud speech cannot push the threshold past the cap", () => {
    let floor = 0.01;
    for (let t = 0; t < 120000; t += 50) floor = updateNoiseFloor(floor, 0.2, 50);
    assert.ok(adaptiveVoiceThreshold(floor, 0.02) <= 0.06);
    assert.ok(0.2 > adaptiveVoiceThreshold(floor, 0.02));
});


results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

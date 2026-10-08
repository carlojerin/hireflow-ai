// ============================================================
// JOBPILOT AI - DETERMINISTIC TESTS FOR interview-voice-turn-logic-v2.js
// (Task 5J)
//
// Plain Node, zero framework/dependency, mirroring this project's
// own established "no test-framework install" convention for the
// Python suite (tests/*.py run through a small custom runner
// instead of pytest). Run with: node tests/frontend/test_interview_voice_turn_logic_v2.js
//
// Tests ONLY the pure logic module (state transitions, VAD
// decisions, staleness/duplicate guards) -- no DOM, no
// AudioContext, no network, no browser required. The actual
// browser mechanics (AudioContext sampling, SpeechRecognition,
// MediaRecorder, fetch/AbortController) are verified separately
// via live browser testing (see the Task 5J final report), exactly
// the same split Task 5I already established between unit-testable
// service logic and live-browser-verified I/O.
// ============================================================

const path = require("path");
const assert = require("assert");

// frontend/package.json declares "type": "module", which makes Node
// treat any .js under frontend/ as ESM when required by path -- and
// an ESM require() returns an (empty, in our case) namespace object
// instead of running module.exports assignment semantics. The
// module's own UMD-lite wrapper still runs its top-level code either
// way and attaches itself to `global` (its `root` when `window` is
// undefined), so we read it from there instead of destructuring the
// require() return value directly.
require(path.join(__dirname, "..", "..", "frontend", "js", "interview-voice-turn-logic-v2.js"));

const {
    STATES,
    EVENTS,
    nextState,
    DEFAULT_VAD_CONFIG,
    vadTick,
    initialVadTracking,
    isStaleToken,
    shouldSubmitTranscript,
} = global.InterviewVoiceTurnLogicV2;

const results = [];

function test(name, fn) {
    try {
        fn();
        results.push([name, "PASS", null]);
    } catch (error) {
        results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]);
    }
}


// ============================================================
// 1. State machine transitions
// ============================================================

test("idle -> speak requested -> AI_PREPARING", () => {
    assert.strictEqual(nextState(STATES.IDLE, EVENTS.SPEAK_REQUESTED), STATES.AI_PREPARING);
});

test("AI_PREPARING -> audio ready -> AI_SPEAKING", () => {
    assert.strictEqual(nextState(STATES.AI_PREPARING, EVENTS.AI_AUDIO_READY), STATES.AI_SPEAKING);
});

test("AI_SPEAKING -> audio ended naturally -> CANDIDATE_LISTENING", () => {
    assert.strictEqual(nextState(STATES.AI_SPEAKING, EVENTS.AI_AUDIO_ENDED), STATES.CANDIDATE_LISTENING);
});

test("full happy-path turn cycle reaches AI_PREPARING again (ready for next question)", () => {
    let s = STATES.IDLE;
    s = nextState(s, EVENTS.SPEAK_REQUESTED);              // AI_PREPARING
    s = nextState(s, EVENTS.AI_AUDIO_READY);                // AI_SPEAKING
    s = nextState(s, EVENTS.AI_AUDIO_ENDED);                 // CANDIDATE_LISTENING
    s = nextState(s, EVENTS.CANDIDATE_VOICE_DETECTED);       // CANDIDATE_SPEAKING
    s = nextState(s, EVENTS.CANDIDATE_SILENCE_DETECTED);     // CANDIDATE_SILENCE
    s = nextState(s, EVENTS.TRANSCRIPT_FINALIZED);           // TRANSCRIBING
    s = nextState(s, EVENTS.TRANSCRIPT_FINALIZED);           // SUBMITTING
    s = nextState(s, EVENTS.SUBMIT_STARTED);                 // AI_THINKING
    s = nextState(s, EVENTS.BRAIN_RESPONDED);                // AI_PREPARING
    assert.strictEqual(s, STATES.AI_PREPARING);
});

test("unrecognized event in a given state is a no-op (never throws, state unchanged)", () => {
    assert.strictEqual(nextState(STATES.IDLE, EVENTS.CANDIDATE_SILENCE_DETECTED), STATES.IDLE);
    assert.doesNotThrow(() => nextState(STATES.COMPLETED, EVENTS.CANDIDATE_VOICE_DETECTED));
});

test("COMPLETED is terminal -- no event moves out of it", () => {
    assert.strictEqual(nextState(STATES.COMPLETED, EVENTS.CANDIDATE_VOICE_DETECTED), STATES.COMPLETED);
    assert.strictEqual(nextState(STATES.COMPLETED, EVENTS.SPEAK_REQUESTED), STATES.COMPLETED);
});


// 2. AI speaking -> candidate interruption
test("AI_SPEAKING -> candidate voice detected -> AI_INTERRUPTED", () => {
    assert.strictEqual(nextState(STATES.AI_SPEAKING, EVENTS.CANDIDATE_VOICE_DETECTED), STATES.AI_INTERRUPTED);
});

test("AI_INTERRUPTED -> candidate voice sustained -> CANDIDATE_SPEAKING", () => {
    assert.strictEqual(nextState(STATES.AI_INTERRUPTED, EVENTS.CANDIDATE_VOICE_DETECTED), STATES.CANDIDATE_SPEAKING);
});

test("interruption while TTS still loading (AI_PREPARING) is also caught", () => {
    assert.strictEqual(nextState(STATES.AI_PREPARING, EVENTS.CANDIDATE_VOICE_DETECTED), STATES.AI_INTERRUPTED);
});


// 12. candidate interruption while TTS request is pending -- covered above
// (AI_PREPARING is exactly "TTS request in flight, audio not yet playing")


// 3. AI audio cancellation is a caller-side concern (pause()/AbortController
// live in interview-voice-v2.js, not here) -- covered structurally by the
// state machine no longer being AI_SPEAKING once interrupted, so any caller
// gating audio playback on `state === AI_SPEAKING` will stop naturally.
test("once interrupted, state is no longer AI_SPEAKING (a caller gating audio on this state stops)", () => {
    const s = nextState(STATES.AI_SPEAKING, EVENTS.CANDIDATE_VOICE_DETECTED);
    assert.notStrictEqual(s, STATES.AI_SPEAKING);
});


// 4/5. Candidate speech start detection (VAD)
test("VAD: brief sound below minVoicedMsToTrigger does NOT trigger (filters clicks/pops)", () => {
    let tracking = initialVadTracking();
    let decision;
    ({ tracking, decision } = vadTick(tracking, 0.5, 50, DEFAULT_VAD_CONFIG)); // 50ms voiced
    assert.strictEqual(decision, "NONE");
});

test("VAD: sustained voice above threshold for minVoicedMsToTrigger DOES trigger", () => {
    let tracking = initialVadTracking();
    let decision;
    ({ tracking, decision } = vadTick(tracking, 0.5, 80, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "NONE"); // 80ms < 150ms threshold
    ({ tracking, decision } = vadTick(tracking, 0.5, 80, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "VOICE_DETECTED"); // 160ms >= 150ms
});

test("VAD: quiet mic-init noise below voiceThreshold never accumulates voiced time", () => {
    let tracking = initialVadTracking();
    let decision;
    for (let i = 0; i < 50; i++) {
        ({ tracking, decision } = vadTick(tracking, 0.005, 50, DEFAULT_VAD_CONFIG)); // well below 0.02 threshold
    }
    assert.strictEqual(decision, "NONE");
    assert.strictEqual(tracking.voicedMs, 0);
});


// 6. Candidate speech end detection + silence debounce
test("VAD: silence right after triggered speech ends the turn only after silenceMsToEndTurn", () => {
    let tracking = initialVadTracking();
    let decision;

    // sustain speech long enough to trigger
    ({ tracking, decision } = vadTick(tracking, 0.5, 200, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "VOICE_DETECTED");

    // brief pause mid-sentence -- must NOT end the turn prematurely
    ({ tracking, decision } = vadTick(tracking, 0.005, 500, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "NONE");

    // candidate resumes talking -- silence clock resets
    ({ tracking, decision } = vadTick(tracking, 0.5, 100, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "NONE");

    // now a real end-of-turn silence window
    ({ tracking, decision } = vadTick(tracking, 0.005, 1200, DEFAULT_VAD_CONFIG));
    assert.strictEqual(decision, "SILENCE_DETECTED");
});

test("VAD: brief mid-answer pause shorter than silenceMsToEndTurn never fires SILENCE_DETECTED", () => {
    let tracking = initialVadTracking();
    let decision;
    ({ tracking, decision } = vadTick(tracking, 0.5, 200, DEFAULT_VAD_CONFIG)); // trigger speech
    ({ tracking, decision } = vadTick(tracking, 0.005, 600, DEFAULT_VAD_CONFIG)); // 600ms < 1200ms
    assert.strictEqual(decision, "NONE");
});


// 7. Full state cycle already covered above (see "full happy-path turn cycle")


// 8/9/10/11. Transcript submission guards
test("empty transcript is rejected (never submitted)", () => {
    const result = shouldSubmitTranscript("   ", "turn-1", null);
    assert.strictEqual(result.submit, false);
    assert.strictEqual(result.reason, "empty");
});

test("non-empty transcript under a fresh turn token is accepted exactly once", () => {
    const result = shouldSubmitTranscript("I built a FastAPI app.", "turn-1", null);
    assert.strictEqual(result.submit, true);
});

test("the SAME turn token is never submitted twice (duplicate prevention)", () => {
    const first = shouldSubmitTranscript("answer text", "turn-1", null);
    assert.strictEqual(first.submit, true);
    const second = shouldSubmitTranscript("answer text", "turn-1", "turn-1"); // caller already recorded turn-1 as submitted
    assert.strictEqual(second.submit, false);
    assert.strictEqual(second.reason, "duplicate");
});

test("a DIFFERENT turn token after a submitted one is allowed (next real turn)", () => {
    const result = shouldSubmitTranscript("next answer", "turn-2", "turn-1");
    assert.strictEqual(result.submit, true);
});


// 11. Stale TTS response ignored
test("a TTS/transcribe response issued under an old token is detected as stale", () => {
    assert.strictEqual(isStaleToken(3, 3), false); // still current
    assert.strictEqual(isStaleToken(2, 3), true);  // a newer request has since started
});

test("candidate interrupts while a speak() request is still in flight -- the eventual stale response is ignored", () => {
    // Simulates: speak() issued under token=1, candidate barges in
    // (token bumped to 2 by the caller), the token=1 TTS fetch
    // resolves afterwards.
    const requestToken = 1;
    const currentTokenAfterInterruption = 2;
    assert.strictEqual(isStaleToken(requestToken, currentTokenAfterInterruption), true);
});


// 13/14. Interview completion stops mic/audio -- structural: COMPLETED accepts
// no further CANDIDATE_VOICE_DETECTED/SPEAK_REQUESTED events (already covered
// by the "COMPLETED is terminal" test above), so a caller that stops its VAD
// loop and TTS playback strictly based on `state === COMPLETED` can never be
// re-armed by a late event.


results.forEach(([name, status]) => {
    process.stdout.write(`${status.padEnd(5)}  ${name}\n`);
});

const failed = results.filter(([, status]) => status === "FAIL");

results.forEach(([name, status, detail]) => {
    if (status === "FAIL") {
        process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`);
    }
});

process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);

process.exit(failed.length > 0 ? 1 : 0);

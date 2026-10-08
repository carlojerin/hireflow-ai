// ============================================================
// JOBPILOT AI - DETERMINISTIC TESTS FOR interview-avatar-turn-logic-v2.js
// (Task 5K). Plain Node, zero framework, same convention as
// test_interview_voice_turn_logic_v2.js (Task 5J).
// Run with: node tests/frontend/test_interview_avatar_turn_logic_v2.js
// ============================================================

const path = require("path");
const assert = require("assert");

require(path.join(__dirname, "..", "..", "frontend", "js", "interview-avatar-turn-logic-v2.js"));

const { AVATAR_STATES, nextAvatarState, isStaleAvatarEvent } = global.InterviewAvatarTurnLogicV2;

const results = [];

function test(name, fn) {
    try {
        fn();
        results.push([name, "PASS", null]);
    } catch (error) {
        results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]);
    }
}


// 7. AI_STARTED_SPEAKING event
test("AI_STARTED_SPEAKING while connected -> AI_SPEAKING", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_CONNECTED, "AI_STARTED_SPEAKING"), AVATAR_STATES.AI_SPEAKING);
});

// 9. AI_STOPPED_SPEAKING event
test("AI_STOPPED_SPEAKING while speaking -> AI_IDLE", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AI_SPEAKING, "AI_STOPPED_SPEAKING"), AVATAR_STATES.AI_IDLE);
});

// 10. AI_INTERRUPTED event -- avatar must stop, never keep "talking"
test("AI_INTERRUPTED while speaking -> AI_INTERRUPTED (never stays AI_SPEAKING)", () => {
    const next = nextAvatarState(AVATAR_STATES.AI_SPEAKING, "AI_INTERRUPTED");
    assert.strictEqual(next, AVATAR_STATES.AI_INTERRUPTED);
    assert.notStrictEqual(next, AVATAR_STATES.AI_SPEAKING);
});

test("candidate starting to speak also takes the avatar out of AI_SPEAKING (never talks over the candidate)", () => {
    const next = nextAvatarState(AVATAR_STATES.AI_SPEAKING, "CANDIDATE_STARTED_SPEAKING");
    assert.notStrictEqual(next, AVATAR_STATES.AI_SPEAKING);
    assert.strictEqual(next, AVATAR_STATES.AI_IDLE);
});

// 11. INTERVIEW_COMPLETED cleanup
test("INTERVIEW_COMPLETED always transitions to COMPLETED, even mid-speech", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AI_SPEAKING, "INTERVIEW_COMPLETED"), AVATAR_STATES.COMPLETED);
});

test("INTERVIEW_COMPLETED is honored even while still connecting", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_CONNECTING, "INTERVIEW_COMPLETED"), AVATAR_STATES.COMPLETED);
});

// 14. stale/late events ignored after COMPLETED (terminal state)
test("COMPLETED is terminal -- no further event moves out of it", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.COMPLETED, "AI_STARTED_SPEAKING"), null);
    assert.strictEqual(nextAvatarState(AVATAR_STATES.COMPLETED, "INTERVIEW_COMPLETED"), null);
});

// speaking/idle events before the avatar is actually connected are ignored
// (there is no real video track yet to reflect them on -- Step 14: "do not
// make the candidate think the avatar is active if the connection failed")
test("AI_STARTED_SPEAKING is ignored while avatar is still connecting", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_CONNECTING, "AI_STARTED_SPEAKING"), null);
});

test("AI_STARTED_SPEAKING is ignored while avatar is unavailable (not configured)", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_UNAVAILABLE, "AI_STARTED_SPEAKING"), null);
});

test("AI_STARTED_SPEAKING is ignored while avatar is in an error state", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_ERROR, "AI_STARTED_SPEAKING"), null);
});

test("an unrecognized event while connected is a no-op (never throws)", () => {
    assert.strictEqual(nextAvatarState(AVATAR_STATES.AVATAR_CONNECTED, "SOME_UNKNOWN_EVENT"), null);
    assert.doesNotThrow(() => nextAvatarState(AVATAR_STATES.AVATAR_CONNECTED, "SOME_UNKNOWN_EVENT"));
});

// full happy-path cycle
test("full connect -> speak -> idle -> interrupted -> idle -> completed cycle", () => {
    let s = AVATAR_STATES.AVATAR_CONNECTING;
    s = nextAvatarState(s, "AI_STARTED_SPEAKING"); // ignored, still connecting -> null means "no change", caller keeps CONNECTING
    assert.strictEqual(s, null);

    s = AVATAR_STATES.AVATAR_CONNECTED;
    s = nextAvatarState(s, "AI_STARTED_SPEAKING");
    assert.strictEqual(s, AVATAR_STATES.AI_SPEAKING);

    s = nextAvatarState(s, "AI_INTERRUPTED");
    assert.strictEqual(s, AVATAR_STATES.AI_INTERRUPTED);

    s = nextAvatarState(s, "AI_STOPPED_SPEAKING");
    assert.strictEqual(s, AVATAR_STATES.AI_IDLE);

    s = nextAvatarState(s, "INTERVIEW_COMPLETED");
    assert.strictEqual(s, AVATAR_STATES.COMPLETED);
});


// 14 (stale events / connection tokens)
test("isStaleAvatarEvent detects a superseded connection token", () => {
    assert.strictEqual(isStaleAvatarEvent(1, 2), true);
    assert.strictEqual(isStaleAvatarEvent(2, 2), false);
});


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

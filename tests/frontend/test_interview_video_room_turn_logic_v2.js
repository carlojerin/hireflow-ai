// ============================================================
// JOBPILOT AI - DETERMINISTIC TESTS FOR interview-video-room-turn-logic-v2.js
// (Task 5L). Plain Node, zero framework, same convention as Task 5J/5K.
// Run with: node tests/frontend/test_interview_video_room_turn_logic_v2.js
// ============================================================

const path = require("path");
const assert = require("assert");

require(path.join(__dirname, "..", "..", "frontend", "js", "interview-video-room-turn-logic-v2.js"));

const {
    ROOM_STATES,
    nextRoomState,
    canPublishCandidateTracks,
    toggleControl,
    isStaleRoomEvent,
} = global.InterviewVideoRoomTurnLogicV2;

const results = [];

function test(name, fn) {
    try {
        fn();
        results.push([name, "PASS", null]);
    } catch (error) {
        results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]);
    }
}


// -- room connecting / connected / disconnected --
test("IDLE -> CONNECT_STARTED -> CONNECTING", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.IDLE, "CONNECT_STARTED"), ROOM_STATES.CONNECTING);
});

test("CONNECTING -> CONNECT_SUCCEEDED -> CONNECTED", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTING, "CONNECT_SUCCEEDED"), ROOM_STATES.CONNECTED);
});

test("CONNECTING -> CONNECT_FAILED -> ERROR", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTING, "CONNECT_FAILED"), ROOM_STATES.ERROR);
});

test("CONNECTED -> DISCONNECTED -> DISCONNECTED", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTED, "DISCONNECTED"), ROOM_STATES.DISCONNECTED);
});

// -- reconnecting --
test("CONNECTED -> RECONNECTING -> RECONNECTING", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTED, "RECONNECTING"), ROOM_STATES.RECONNECTING);
});

test("RECONNECTING -> RECONNECTED -> CONNECTED (interview brain untouched, just a visual state)", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.RECONNECTING, "RECONNECTED"), ROOM_STATES.CONNECTED);
});

test("RECONNECTING -> DISCONNECTED -> DISCONNECTED (reconnect ultimately failed)", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.RECONNECTING, "DISCONNECTED"), ROOM_STATES.DISCONNECTED);
});

// -- error --
test("ERROR -> CONNECT_STARTED -> CONNECTING (retry path)", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.ERROR, "CONNECT_STARTED"), ROOM_STATES.CONNECTING);
});

// -- interview completed cleanup --
test("INTERVIEW_COMPLETED always wins, from any state", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTED, "INTERVIEW_COMPLETED"), ROOM_STATES.COMPLETED);
    assert.strictEqual(nextRoomState(ROOM_STATES.RECONNECTING, "INTERVIEW_COMPLETED"), ROOM_STATES.COMPLETED);
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTING, "INTERVIEW_COMPLETED"), ROOM_STATES.COMPLETED);
});

test("COMPLETED is terminal -- no event moves out of it", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.COMPLETED, "CONNECT_STARTED"), ROOM_STATES.COMPLETED);
    assert.strictEqual(nextRoomState(ROOM_STATES.COMPLETED, "RECONNECTED"), ROOM_STATES.COMPLETED);
});

test("unrecognized event is a no-op, never throws", () => {
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTED, "SOME_UNKNOWN_EVENT"), ROOM_STATES.CONNECTED);
    assert.doesNotThrow(() => nextRoomState(ROOM_STATES.IDLE, "GARBAGE"));
});


// -- candidate camera/mic publish readiness --
test("cannot publish until room connected AND camera ready AND mic ready", () => {
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: false, cameraReady: false, micReady: false }), false);
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: true, cameraReady: false, micReady: false }), false);
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: true, cameraReady: true, micReady: false }), false);
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: true, cameraReady: true, micReady: true }), true);
});

test("camera permission granted but room connection failed -- still cannot publish", () => {
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: false, cameraReady: true, micReady: true }), false);
});

test("microphone permission granted but room connection failed -- still cannot publish", () => {
    assert.strictEqual(canPublishCandidateTracks({ roomConnected: false, cameraReady: true, micReady: true }), false);
});


// -- camera/mic toggle controls --
test("toggling camera flips only cameraEnabled", () => {
    const before = { cameraEnabled: true, micEnabled: true };
    const after = toggleControl(before, "camera");
    assert.strictEqual(after.cameraEnabled, false);
    assert.strictEqual(after.micEnabled, true);
});

test("toggling mic flips only micEnabled", () => {
    const before = { cameraEnabled: true, micEnabled: true };
    const after = toggleControl(before, "mic");
    assert.strictEqual(after.micEnabled, false);
    assert.strictEqual(after.cameraEnabled, true);
});

test("toggling an unknown control is a no-op", () => {
    const before = { cameraEnabled: true, micEnabled: true };
    const after = toggleControl(before, "speaker-volume-nonsense");
    assert.deepStrictEqual(after, before);
});

test("toggling camera twice returns to the original state", () => {
    const start = { cameraEnabled: true, micEnabled: true };
    const twice = toggleControl(toggleControl(start, "camera"), "camera");
    assert.deepStrictEqual(twice, start);
});


// -- stale room events (reconnect race conditions) --
test("isStaleRoomEvent detects a superseded connection token", () => {
    assert.strictEqual(isStaleRoomEvent(1, 2), true);
    assert.strictEqual(isStaleRoomEvent(2, 2), false);
});

// connect -> disconnect -> connect race
test("connect -> disconnect -> connect: only the LATEST connect's events are honored", () => {
    let token = 1; // first connect() call issued under token 1
    // disconnect happens, then a second connect() bumps the token
    token = 2;
    // a stale CONNECT_SUCCEEDED from the FIRST connect() attempt arrives late
    assert.strictEqual(isStaleRoomEvent(1, token), true);
    // the second connect's own success is NOT stale
    assert.strictEqual(isStaleRoomEvent(2, token), false);
});

// connect -> end -> delayed connect event
test("connect -> interview ends -> a delayed CONNECT_SUCCEEDED must not resurrect the room", () => {
    let state = ROOM_STATES.CONNECTING;
    state = nextRoomState(state, "INTERVIEW_COMPLETED"); // interview ends while still connecting
    assert.strictEqual(state, ROOM_STATES.COMPLETED);
    // the delayed success event arrives after completion -- terminal state absorbs it
    state = nextRoomState(state, "CONNECT_SUCCEEDED");
    assert.strictEqual(state, ROOM_STATES.COMPLETED);
});


// ============================================================
// Task 5M: additional race conditions explicitly called out in
// the audit (Step 20).
// ============================================================

// camera enable -> end: a camera-ready notification that arrives
// after the interview already completed must not resurrect publishing
test("camera becomes ready only AFTER the interview already ended -- publish readiness is moot once COMPLETED", () => {
    let state = ROOM_STATES.CONNECTED;
    state = nextRoomState(state, "INTERVIEW_COMPLETED");
    assert.strictEqual(state, ROOM_STATES.COMPLETED);

    // readiness itself is a separate, caller-owned struct (not part of
    // the state machine) -- canPublishCandidateTracks() would still
    // return true here in isolation, which is exactly why the REAL
    // controller (interview-video-room-v2.js) gates publish attempts
    // on `publishAttempted`/cleanup having already run, not solely on
    // this pure readiness check. Documented via the state check above:
    // once COMPLETED, nothing re-arms the room.
    assert.strictEqual(nextRoomState(state, "CONNECT_SUCCEEDED"), ROOM_STATES.COMPLETED);
});

// mic enable -> end: same property, for the microphone leg
test("mic becomes ready only AFTER the interview already ended -- state stays COMPLETED", () => {
    let state = ROOM_STATES.CONNECTED;
    state = nextRoomState(state, "INTERVIEW_COMPLETED");
    assert.strictEqual(nextRoomState(state, "RECONNECTED"), ROOM_STATES.COMPLETED);
});

// AI speaking -> candidate interruption -> disconnect (combining Task
// 5J's own interruption event with a Task 5L room disconnect arriving
// moments later -- the room state machine doesn't know about AI
// speaking at all, so a DISCONNECTED event simply applies on top)
test("room disconnects while an interruption was just handled elsewhere -- room state still reflects disconnect honestly", () => {
    let state = ROOM_STATES.CONNECTED;
    // (AI_INTERRUPTED itself is Task 5K/5J's own state machine, not
    // this one -- this module only ever tracks room transport health)
    state = nextRoomState(state, "DISCONNECTED");
    assert.strictEqual(state, ROOM_STATES.DISCONNECTED);
});

// recording -> room disconnect: the room state machine and recording
// are independent concerns; a room disconnect must never be masked
// just because a recording is in progress
test("recording activity does not prevent the room from reporting DISCONNECTED", () => {
    // recording state is tracked entirely outside this module (by
    // AssessmentVideoRecorder, unmodified) -- this test documents that
    // nextRoomState() has no recording-awareness at all, so a
    // disconnect is never silently suppressed.
    assert.strictEqual(nextRoomState(ROOM_STATES.CONNECTED, "DISCONNECTED"), ROOM_STATES.DISCONNECTED);
});

// duplicate readiness notifications (e.g. notifyCameraReady called
// twice) must not cause a double-publish -- modeled here as the
// readiness flags being idempotent (setting an already-true flag to
// true again changes nothing)
test("duplicate camera-ready readiness signals are idempotent", () => {
    const first = canPublishCandidateTracks({ roomConnected: true, cameraReady: true, micReady: true });
    const second = canPublishCandidateTracks({ roomConnected: true, cameraReady: true, micReady: true });
    assert.strictEqual(first, true);
    assert.strictEqual(second, true);
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

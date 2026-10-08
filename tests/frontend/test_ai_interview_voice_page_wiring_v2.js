// ============================================================
// JOBPILOT AI - PAGE WIRING TESTS for the Task 4 handlers in
// ai-interview-v2.js. The page itself needs an authenticated session
// and a full DOM, so the REAL source of each handler is extracted by
// name and run in a sandbox with stubbed page globals. Run:
//   node tests/frontend/test_ai_interview_voice_page_wiring_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const SRC = fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "ai-interview-v2.js"), "utf8");

function grab(name) {
    const start = SRC.indexOf(`function ${name}(`);
    assert.ok(start >= 0, `function ${name} not found in ai-interview-v2.js`);
    const asyncStart = SRC.slice(Math.max(0, start - 6), start) === "async " ? start - 6 : start;
    let depth = 0;
    for (let i = SRC.indexOf("{", start); i < SRC.length; i++) {
        if (SRC[i] === "{") depth++;
        if (SRC[i] === "}" && --depth === 0) return SRC.slice(asyncStart, i + 1);
    }
    throw new Error(`unterminated ${name}`);
}

function makePage() {

    const els = {};
    const el = (id) => (els[id] = els[id] || { id, style: {}, textContent: "", disabled: false });

    const calls = { render: 0, progress: [], loadState: 0, avatarEvents: [], voiceCleanup: 0, roomCleanup: 0, avatarCleanup: 0, integrityCleanup: 0, recorderStop: 0, recorderUpload: 0, interviewerStates: [], cameraCleanup: 0 };

    const sandbox = {
        conversationTurns: [{ question: "Q1?", answer: null }],
        currentQuestionTurnNumber: 1,
        submitInFlight: false,
        assessmentLocked: false,
        interviewVideoRecorder: { stop: async () => { calls.recorderStop++; }, upload: async () => { calls.recorderUpload++; } },
        applicationId: 41, recordingMix: null,
        document: { body: { dataset: { iv2OpeningMessage: "Hello" } }, getElementById: el },
        renderConversation: () => { calls.render++; },
        renderProgress: (p) => calls.progress.push(p),
        loadInterviewState: () => { calls.loadState++; },
        stopCountdown: () => {}, stopPhoneDetection: () => {},
        renderInterviewerState: (state) => calls.interviewerStates.push(state),          // Task 8: the fixed-image visual layer
        cleanupCamera: () => { calls.cameraCleanup++; },                                 // Task 9: camera recovery timers stop with the interview
        window: {
            InterviewAvatarV2: { handleVoiceEvent: (n) => calls.avatarEvents.push(n), cleanup: () => { calls.avatarCleanup++; } },
            InterviewVoiceV2: { cleanup: () => { calls.voiceCleanup++; } },
            InterviewVideoRoomV2: { cleanup: () => { calls.roomCleanup++; } },
            IntegrityMode: { cleanup: () => { calls.integrityCleanup++; } },
        },
        console,
    };

    vm.createContext(sandbox);

    for (const name of ["updateMicLevel", "handleVoiceTurnResult", "handleVoiceLayerEvent", "finalizeInterviewUi"]) {
        vm.runInContext(grab(name), sandbox);
    }
    // the page keeps this flag at module level
    vm.runInContext("let interviewFinalized = false;", sandbox);

    return { sandbox, el, calls };
}

const results = [];
async function test(name, fn) {
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

const turnData = (over = {}) => Object.assign({
    transcript: "I built a REST API.", response_text: "How did you secure it?", interview_complete: false,
    session: { current_question: { turn_number: 2, question: "How did you secure it?" }, progress: [{ competency: "Python", turns_touched: 1 }] },
}, over);

(async () => {

await test("a voice turn is applied like a typed answer: transcript answers the open question, next question appended", () => {
    const { sandbox, el, calls } = makePage();
    sandbox.handleVoiceTurnResult(turnData(), { stale: false });
    assert.strictEqual(sandbox.conversationTurns[0].answer, "I built a REST API.");
    assert.strictEqual(sandbox.conversationTurns.length, 2);
    assert.strictEqual(sandbox.conversationTurns[1].question, "How did you secure it?");
    assert.strictEqual(sandbox.conversationTurns[1].answer, null);
    assert.strictEqual(sandbox.currentQuestionTurnNumber, 2);
    assert.strictEqual(calls.render, 1);
    assert.deepStrictEqual(calls.progress[0], [{ competency: "Python", turns_touched: 1 }]);
    assert.strictEqual(el("iv2TranscriptText").textContent, "I built a REST API.");
});

await test("a completing voice turn appends no phantom question and does not finalize while the closing audio plays", async () => {
    const { sandbox, calls } = makePage();
    sandbox.handleVoiceTurnResult(turnData({ interview_complete: true, session: { current_question: null } }), { stale: false });
    assert.strictEqual(sandbox.conversationTurns.length, 1);
    assert.strictEqual(sandbox.conversationTurns[0].answer, "I built a REST API.");
    await Promise.resolve();
    assert.strictEqual(calls.voiceCleanup, 0);                    // the controller finalizes AFTER the closing message ends
});

await test("a stale completing turn (voice mode off) finalizes immediately because no closing audio will play", async () => {
    const { sandbox, calls } = makePage();
    sandbox.handleVoiceTurnResult(turnData({ interview_complete: true, session: { current_question: null } }), { stale: true });
    await new Promise((r) => setImmediate(r));
    assert.strictEqual(calls.voiceCleanup, 1);
    assert.strictEqual(sandbox.assessmentLocked, true);
});

await test("finalizing is idempotent: two completion reports run the completion routine once", async () => {
    const { sandbox, el, calls } = makePage();
    await Promise.all([sandbox.finalizeInterviewUi(), sandbox.finalizeInterviewUi()]);
    await sandbox.finalizeInterviewUi();
    assert.deepStrictEqual([calls.recorderStop, calls.recorderUpload, calls.voiceCleanup, calls.roomCleanup, calls.avatarCleanup, calls.integrityCleanup], [1, 1, 1, 1, 1, 1]);
    assert.strictEqual(el("iv2Body").style.display, "none");
    assert.strictEqual(el("iv2ResultSection").style.display, "block");
    assert.deepStrictEqual(calls.interviewerStates, ["COMPLETED"]);
    assert.strictEqual(calls.cameraCleanup, 1);                                            // camera recovery cannot outlive the interview                       // Task 8: the interviewer image shows the completed state, once
});

await test("a voice turn in flight blocks typed submission and releases it afterwards", () => {
    const { sandbox, el } = makePage();
    sandbox.handleVoiceLayerEvent("VOICE_TURN_STARTED");
    assert.strictEqual(sandbox.submitInFlight, true);
    assert.strictEqual(el("iv2AnswerInput").disabled, true);
    sandbox.handleVoiceLayerEvent("VOICE_TURN_FINISHED");
    assert.strictEqual(sandbox.submitInFlight, false);
    assert.strictEqual(el("iv2AnswerInput").disabled, false);
});

await test("the input stays locked after VOICE_TURN_FINISHED once the assessment is locked", () => {
    const { sandbox, el } = makePage();
    sandbox.assessmentLocked = true;
    el("iv2AnswerInput").disabled = true;
    sandbox.handleVoiceLayerEvent("VOICE_TURN_FINISHED");
    assert.strictEqual(el("iv2AnswerInput").disabled, true);
});

await test("the server saying the interview moved (409/410) triggers a state resync; other events still reach the avatar", () => {
    const { sandbox, calls } = makePage();
    sandbox.handleVoiceLayerEvent("VOICE_SESSION_INVALID");
    assert.strictEqual(calls.loadState, 1);
    sandbox.handleVoiceLayerEvent("AI_STARTED_SPEAKING");
    assert.ok(calls.avatarEvents.includes("AI_STARTED_SPEAKING"));
});

await test("mic level bar is clamped to 0-100%", () => {
    const { sandbox, el } = makePage();
    sandbox.updateMicLevel(0.5);  assert.strictEqual(el("iv2MicLevelBar").style.width, "50%");
    sandbox.updateMicLevel(7);    assert.strictEqual(el("iv2MicLevelBar").style.width, "100%");
    sandbox.updateMicLevel(-3);   assert.strictEqual(el("iv2MicLevelBar").style.width, "0%");
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

})();

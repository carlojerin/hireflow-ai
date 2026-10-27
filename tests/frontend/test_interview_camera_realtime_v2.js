// ============================================================
// JOBPILOT AI - CAMERA + REALTIME MODE BROWSER TESTS (Task 9)
//
// Part 1: the REAL camera code of ai-interview-v2.js (extracted from the source, run in a vm sandbox
//         with a fake IntegrityMode / mic / recorder / video-room): the preview and room publication no
//         longer wait for the microphone or the recorder, a mic/recorder error is never reported as a
//         camera failure, a camera failure says WHY and can be retried, a dead camera track is re-acquired
//         (bounded), exactly one camera acquisition path exists, cleanup stops everything.
// Part 2: the REAL voice controller in REALTIME mode (shared fake browser): no record/upload, server
//         messages mirrored into the existing states, instant local mute on barge-in, completion, errors.
// A physical webcam cannot be exercised here; that is reported separately, never faked.
// Run: node tests/frontend/test_interview_camera_realtime_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const { makeEnv, okTurn, startListeningEnv, flush } = require("./_voice_harness_v2.js");

const ROOT = path.join(__dirname, "..", "..", "frontend");
const PAGE = fs.readFileSync(path.join(ROOT, "js", "ai-interview-v2.js"), "utf8");

const results = [];
async function test(name, fn) {
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

// ------------------------------------------------------------------ Part 1 harness
function grabBetween(startMarker, endMarker) {
    const a = PAGE.indexOf(startMarker), b = PAGE.indexOf(endMarker, a);
    assert.ok(a >= 0 && b > a, `markers not found: ${startMarker}`);
    return PAGE.slice(a, b);
}
function grabFunction(name) {
    const a = PAGE.indexOf(`function ${name}(`);
    assert.ok(a >= 0, name);
    const open = PAGE.indexOf("{", a);
    let depth = 0, i = open;
    for (; i < PAGE.length; i++) { if (PAGE[i] === "{") depth++; else if (PAGE[i] === "}") { depth--; if (depth === 0) break; } }
    return PAGE.slice(PAGE.lastIndexOf("\n", a) + 1, i + 1);
}

function makePage(opts = {}) {

    const log = { getUserMedia: 0, startCamera: 0, dots: [], published: [], replaced: [], micReady: 0, recorderStarts: 0, playCalls: 0, timers: [], phone: 0, warnings: [] };
    const els = {};
    const el = (id) => (els[id] = els[id] || { id, style: {}, textContent: "", srcObject: null, play() { log.playCalls++; return Promise.resolve(); }, addEventListener(ev, fn) { (this.handlers = this.handlers || {})[ev] = fn; } });

    class FakeTrack { constructor(kind) { this.kind = kind; this.readyState = "live"; this.handlers = {}; } addEventListener(ev, fn) { this.handlers[ev] = fn; } end() { this.readyState = "ended"; this.handlers.ended && this.handlers.ended(); } }
    class FakeStream { constructor(tracks) { this.tracks = tracks || [new FakeTrack("video")]; } getVideoTracks() { return this.tracks.filter((t) => t.kind === "video"); } getAudioTracks() { return this.tracks.filter((t) => t.kind === "audio"); } }

    const state = { cameraStream: null, cameraShouldWork: opts.camera !== false, micMode: opts.mic || "ok", recorderThrows: !!opts.recorderThrows, devices: opts.devices || [{ kind: "videoinput" }], permission: opts.permission || "granted", secure: opts.secure !== false };
    let timerId = 1;
    const timers = new Map();

    const window = {
        get isSecureContext() { return state.secure; },
        IntegrityMode: {
            enterStrictMode() {},
            async startCameraMonitoring() { log.startCamera++; state.cameraStream = state.cameraShouldWork ? new FakeStream() : null; return { supported: true, granted: state.cameraShouldWork }; },
            getCameraStream() { return state.cameraStream; },
        },
        InterviewVoiceV2: {
            ensureMicStream() {
                if (state.micMode === "hang") return new Promise(() => {});
                if (state.micMode === "throw") return Promise.reject(new Error("NotAllowedError"));
                if (state.micMode === "null") return Promise.resolve(null);
                return Promise.resolve(new FakeStream([new FakeTrack("audio")]));
            },
        },
        InterviewVideoRoomV2: {
            notifyCameraReady(s) { log.published.push(s); },
            notifyMicReady() { log.micReady++; },
            replaceCameraStream(s) { log.replaced.push(s); return Promise.resolve(true); },
        },
        AssessmentVideoRecorder: class { isSupported() { return true; } async start() { log.recorderStarts++; if (state.recorderThrows) throw new Error("recorder failed"); } },
        PhoneDetector: { start() { log.phone++; } },
    };
    window.IntegrityMode.getCameraVideoElement = () => ({});

    const sandbox = {
        window, document: { getElementById: el, querySelector: () => null },
        navigator: {
            mediaDevices: { getUserMedia: () => { log.getUserMedia++; return Promise.reject(new Error("the page must never call this")); }, enumerateDevices: async () => state.devices },
            permissions: { query: async () => ({ state: state.permission }) },
        },
        MediaStream: FakeStream,
        setTimeout: (fn, ms) => { const id = timerId++; timers.set(id, { fn, ms }); log.timers.push(ms); return id; },
        clearTimeout: (id) => { timers.delete(id); },
        console: { warn: (...a) => log.warnings.push(a.join(" ")), log() {}, error() {} },
        setIntegrityDot: (kind, label) => log.dots.push([kind, label]),
        handleIntegrityStateChange() {},
        maybeStartRealtime: async () => { log.realtimeAsked = true; },
        currentUser: { id: 5 }, applicationId: 41, interviewVideoRecorder: null, cameraStreamActive: false,
        startPhoneDetection: () => { log.phone++; },
    };
    sandbox.window.window = sandbox.window;
    vm.createContext(sandbox);
    vm.runInContext("var integrityMaxWarnings = 3;", sandbox);
    vm.runInContext(grabBetween("const CAMERA_RECOVERY_DELAYS_MS", "// REALTIME CONVERSATION"), sandbox);
    sandbox.__timers = timers;
    return { sandbox, log, els, el, state, timers, FakeStream, FakeTrack,
             fireTimers: async () => { const due = [...timers.entries()]; timers.clear(); for (const [, t] of due) { await t.fn(); } await flush(); } };
}

(async () => {

// ================================================================ camera
await test("the candidate preview and the room publication happen BEFORE the microphone prompt resolves (a slow mic no longer hides the camera)", async () => {
    const page = makePage({ mic: "hang" });
    page.sandbox.activateStrictModeForInterviewV2();                        // never resolves: the mic prompt is pending
    await flush(); await flush();
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "block");
    assert.ok(page.el("iv2CandidateVideo").srcObject === page.state.cameraStream);
    assert.strictEqual(page.el("iv2CandidatePlaceholder").style.display, "none");
    assert.strictEqual(page.log.published.length, 1);                       // handed to the video-room layer for LiveKit
    assert.ok(page.log.dots.some(([k, l]) => k === "ready" && /Camera ready/.test(l)));
    assert.strictEqual(page.log.recorderStarts, 0);                         // the recorder is still waiting for the mic, independently
});

await test("a microphone error is NOT reported as 'camera unavailable' and the camera keeps working", async () => {
    const page = makePage({ mic: "throw" });
    await page.sandbox.activateStrictModeForInterviewV2();
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "block");
    assert.ok(!page.log.dots.some(([, l]) => /unavailable/i.test(l)));
    assert.strictEqual(page.log.published.length, 1);
    assert.ok(page.log.warnings.some((w) => /Microphone/.test(w)));
});

await test("a recorder failure is contained: preview stays, no camera error, recorder state cleared", async () => {
    const page = makePage({ recorderThrows: true });
    await page.sandbox.activateStrictModeForInterviewV2();
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "block");
    assert.ok(!page.log.dots.some(([, l]) => /unavailable/i.test(l)));
    assert.strictEqual(page.sandbox.interviewVideoRecorder, null);
    assert.ok(page.log.warnings.some((w) => /recording could not start/i.test(w)));
});

await test("the normal path: camera + mic + recorder all start, the mic is published, the recording indicator is honest", async () => {
    const page = makePage();
    await page.sandbox.activateStrictModeForInterviewV2();
    assert.strictEqual(page.log.micReady, 1);
    assert.strictEqual(page.log.recorderStarts, 1);
    assert.match(page.el("iv2RecordingIndicator").textContent, /video \+ (audio|both voices)/);
    assert.strictEqual(page.log.phone, 1);                                   // phone detection reads the SAME camera element
    assert.ok(page.log.realtimeAsked);                                       // and only then may the realtime conversation start
});

await test("EXACTLY ONE camera acquisition path: the page never calls getUserMedia (startCameraMonitoring is the only request, once per attempt)", async () => {
    const page = makePage();
    await page.sandbox.activateStrictModeForInterviewV2();
    assert.strictEqual(page.log.startCamera, 1);
    assert.strictEqual(page.log.getUserMedia, 0);
    const src = PAGE.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(!/getUserMedia\s*\(/.test(src.replace(/navigator\.mediaDevices\.getUserMedia\b/g, "")));
});

await test("camera failure explains WHY (no device / blocked / in use / insecure page) and shows Retry", async () => {
    const cases = [
        [{ camera: false, devices: [] }, /No camera was found/],
        [{ camera: false, permission: "denied" }, /blocked/],
        [{ camera: false }, /in use by another app/],
        [{ camera: false, secure: false }, /secure page/],
    ];
    for (const [opts, pattern] of cases) {
        const page = makePage(opts);
        await page.sandbox.activateStrictModeForInterviewV2();
        const last = page.log.dots[page.log.dots.length - 1];
        assert.ok(last[0] === "error" && pattern.test(last[1]), JSON.stringify([opts, last]));
        assert.strictEqual(page.el("iv2CameraRetryButton").style.display, "inline-flex");
        assert.strictEqual(page.el("iv2CandidateVideo").style.display, "none");
        assert.strictEqual(page.log.published.length, 0);                    // nothing pretended to be published
        assert.ok(page.log.realtimeAsked);                                   // the interview itself is not blocked by the camera
    }
});

await test("Retry camera: a second attempt goes through the same single path and, when it works, shows + publishes the camera", async () => {
    const page = makePage({ camera: false });
    await page.sandbox.activateStrictModeForInterviewV2();
    page.state.cameraShouldWork = true;
    await page.sandbox.retryCamera(); await flush();
    assert.strictEqual(page.log.startCamera, 2);
    assert.strictEqual(page.log.getUserMedia, 0);
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "block");
    assert.strictEqual(page.el("iv2CameraRetryButton").style.display, "none");
    assert.strictEqual(page.log.replaced.length, 1);                         // published through the recovery path (swap, not a 2nd track)
});

await test("a camera track that dies is re-acquired automatically with bounded backoff and the room track is swapped", async () => {
    const page = makePage();
    await page.sandbox.activateStrictModeForInterviewV2();
    const dead = page.state.cameraStream.getVideoTracks()[0];
    dead.end();                                                              // the camera was unplugged / taken by another app
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "none");
    assert.ok(page.log.dots.some(([k, l]) => k === "error" && /reconnect/i.test(l)));
    assert.deepStrictEqual(page.log.timers, [2000]);                         // first retry in 2 s
    await page.fireTimers();
    assert.strictEqual(page.log.startCamera, 2);
    assert.strictEqual(page.el("iv2CandidateVideo").style.display, "block");
    assert.strictEqual(page.log.replaced.length, 1);                         // one swap
    assert.ok(page.log.dots.some(([k, l]) => k === "ready"));
});

await test("recovery gives up after 3 bounded attempts with an honest reason and a manual Retry (no retry storm)", async () => {
    const page = makePage();
    await page.sandbox.activateStrictModeForInterviewV2();
    page.state.cameraShouldWork = false;
    page.state.cameraStream.getVideoTracks()[0].end();
    for (let i = 0; i < 6; i++) await page.fireTimers();
    assert.strictEqual(page.log.startCamera, 1 + 3);                         // exactly 3 recovery attempts
    assert.deepStrictEqual(page.log.timers, [2000, 5000, 10000]);
    assert.strictEqual(page.el("iv2CameraRetryButton").style.display, "inline-flex");
    assert.strictEqual(page.timers.size, 0);
});

await test("cleanup: no recovery timer survives, the preview is released, a late track-end does nothing", async () => {
    const page = makePage();
    await page.sandbox.activateStrictModeForInterviewV2();
    const track = page.state.cameraStream.getVideoTracks()[0];
    track.end();
    assert.strictEqual(page.timers.size, 1);
    page.sandbox.cleanupCamera();
    assert.strictEqual(page.timers.size, 0);
    assert.strictEqual(page.el("iv2CandidateVideo").srcObject, null);
    const before = page.log.startCamera;
    track.end(); await page.fireTimers();
    assert.strictEqual(page.log.startCamera, before);
});

await test("video-room layer: replacing the camera swaps the ONE published track and never publishes twice", async () => {
    const roomSrc = fs.readFileSync(path.join(ROOT, "js", "interview-video-room-v2.js"), "utf8");
    const logicSrc = fs.readFileSync(path.join(ROOT, "js", "interview-video-room-turn-logic-v2.js"), "utf8");
    const published = [], unpublished = [];
    const room = { localParticipant: {
        publishTrack: async (track, opts) => { published.push([track.id, opts.name]); return { track, setMuted() {} }; },
        unpublishTrack: async (track) => { unpublished.push(track.id); },
    } };
    const window = { InterviewAvatarV2: { getRoom: () => room }, LivekitClient: { Track: { Source: { Camera: "camera", Microphone: "microphone" } } } };
    window.window = window;
    const sandbox = { window, console };
    vm.createContext(sandbox);
    vm.runInContext(logicSrc, sandbox); vm.runInContext(roomSrc, sandbox);
    const V = window.InterviewVideoRoomV2;
    V.init({ onStateChange() {}, onError() {} });
    V.handleRoomEvent("CONNECT_SUCCEEDED");
    const s1 = { getVideoTracks: () => [{ id: "cam1" }], getAudioTracks: () => [] };
    const mic = { getVideoTracks: () => [], getAudioTracks: () => [{ id: "mic1" }] };
    V.notifyCameraReady(s1); V.notifyMicReady(mic); await flush();
    assert.deepStrictEqual(published, [["cam1", "candidate-camera"], ["mic1", "candidate-microphone"]]);
    await V.replaceCameraStream({ getVideoTracks: () => [{ id: "cam2" }], getAudioTracks: () => [] }); await flush();
    assert.deepStrictEqual(unpublished, ["cam1"]);
    assert.deepStrictEqual(published[2], ["cam2", "candidate-camera"]);
    assert.strictEqual(published.length, 3);                                 // one camera + one mic + one replacement: never a duplicate publication
    V.cleanup();
    assert.strictEqual(await V.replaceCameraStream({ getVideoTracks: () => [{ id: "cam3" }] }), false);   // nothing resurrects after the interview ended
});

// ================================================================ realtime mode
const RT = (state, extra = {}) => Object.assign({ channel: "realtime", state }, extra);
const SA = (state, seq, extra = {}) => Object.assign({ channel: "server-audio", state, seq }, extra);

await test("REALTIME: no local record/upload cycle can start, however the controller is driven", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.setRealtimeMode(true);
    await env.Voice.ensureMicStream();
    env.Voice.startListening(); await flush();
    env.level = 0.2; await env.advance(3000);
    assert.strictEqual(env.recorders.length, 0);                              // never a MediaRecorder
    assert.strictEqual(env.fetches.length, 0);                                // never an upload
    assert.strictEqual(env.gumCalls, 1);                                      // the ONE shared mic stream only
});

await test("REALTIME: server messages drive the existing states and events (speaking -> listening -> candidate speaking -> AI again)", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.setRealtimeMode(true);
    env.Voice.handleRemoteSpeech(SA("started", 1)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.ok(env.events.includes("AI_STARTED_SPEAKING"));
    env.Voice.handleRemoteSpeech(SA("ended", 1)); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    env.Voice.handleRemoteSpeech(RT("candidate_speaking")); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);
    env.Voice.handleRemoteSpeech(SA("started", 2)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.strictEqual(env.audios.length, 0);                                 // the browser never plays audio itself
});

await test("REALTIME: the candidate talking over the AI mutes it INSTANTLY in the browser (no server round trip) and starts no local capture", async () => {
    const env = makeEnv({ transport: "livekit" });
    await env.Voice.ensureMicStream();
    env.Voice.setRealtimeMode(true);
    env.Voice.handleRemoteSpeech(SA("started", 1)); await flush();
    env.level = 0.2; await env.advance(400);                                  // loud + sustained 300 ms: the existing barge-in rule
    assert.ok(env.muteLog.includes(true), "AI audio muted");
    assert.strictEqual(env.recorders.length, 0);
    assert.strictEqual(env.fetches.length, 0);                                // the server cuts the stream itself from the same microphone
    env.Voice.handleRemoteSpeech(SA("ended", 1, { interrupted: true })); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);
    assert.ok(env.events.includes("AI_INTERRUPTED"));
    env.Voice.handleRemoteSpeech(SA("started", 2)); await flush();
    assert.strictEqual(env.muteLog[env.muteLog.length - 1], false);           // the next AI turn is audible again
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
});

await test("REALTIME: completion -> COMPLETED + the page's completion routine once; errors are surfaced, never silent", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.setRealtimeMode(true);
    env.Voice.handleRemoteSpeech(RT("complete")); await flush();
    assert.strictEqual(env.state(), env.S.COMPLETED);
    assert.strictEqual(env.completions, 1);
    const env2 = makeEnv({ transport: "livekit" });
    env2.Voice.setRealtimeMode(true);
    env2.Voice.handleRemoteSpeech(RT("error", { message: "The realtime connection was lost (ConnectionError)." })); await flush();
    assert.strictEqual(env2.state(), env2.S.ERROR);
    assert.ok(env2.errors.includes("realtime_failed") && env2.events.includes("REALTIME_FAILED"));
});

await test("REALTIME: 'turn recorded' tells the page to refresh its transcript; without realtime mode these messages are ignored", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.setRealtimeMode(true);
    env.Voice.handleRemoteSpeech(RT("turn_recorded")); await flush();
    assert.ok(env.events.includes("REALTIME_TURN_RECORDED"));
    const off = makeEnv({ transport: "livekit" });
    off.Voice.handleRemoteSpeech(RT("candidate_speaking")); off.Voice.handleRemoteSpeech(RT("complete")); await flush();
    assert.deepStrictEqual(off.events, []);                                   // the pipeline flow is untouched
    assert.strictEqual(off.completions, 0);
});

await test("REALTIME off (default): the existing local pipeline behaves exactly as before (records, uploads, plays)", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(1000); env.level = 0.0; await env.advance(1300);
    assert.strictEqual(env.recorders.length >= 1, true);
    assert.strictEqual(env.turnFetches().length, 1);
});

await test("page wiring: realtime is opt-in from the server, keeps the pipeline as an EXPLICIT visible fallback, and never holds a provider key", async () => {
    assert.ok(/state === "disabled"/.test(PAGE) && /realtime\/start/.test(PAGE));
    assert.ok(/function handleRealtimeFailed/.test(PAGE) && /continue with the standard voice interview/.test(PAGE));
    assert.ok(!/GEMINI|AIza|api[_-]?key/i.test(PAGE.replace(/\/\/.*$/gm, "")));
    const html = fs.readFileSync(path.join(ROOT, "ai-interview-v2.html"), "utf8");
    assert.ok(html.includes('id="iv2CameraRetryButton"'));
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

})();

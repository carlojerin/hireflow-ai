// ============================================================
// JOBPILOT AI - TAVUS AVATAR BROWSER TESTS (Task 7)
//
// The REAL avatar/room controller against a fake LiveKit client + fetch +
// clock: the server-side avatar is requested once per connection, its status
// is followed honestly, video/audio are attached once, mute covers every AI
// element, cleanup/reconnect are bounded, and the page cannot create a second
// AI voice or a second camera stream. Run:
//   node tests/frontend/test_interview_avatar_tavus_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const JS = path.join(__dirname, "..", "..", "frontend", "js");
const SOURCES = ["interview-avatar-turn-logic-v2.js", "interview-avatar-v2.js"].map((f) => fs.readFileSync(path.join(JS, f), "utf8"));
const flush = () => new Promise((resolve) => setImmediate(resolve));

function makeEnv(routes = {}) {
    const env = { rooms: [], elements: [], calls: [], statuses: [], timers: [], nextId: 1, now: 0, videos: [] };
    class FakeRoom {
        constructor() { this.handlers = {}; this.disconnects = 0; this.remoteParticipants = new Map(); env.rooms.push(this); }
        on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
        async connect() {}
        disconnect() { this.disconnects++; }
        startAudio() {}
        emit(ev, ...a) { (this.handlers[ev] || []).forEach((fn) => fn(...a)); }
    }
    const window = { LivekitClient: {
        Room: FakeRoom,
        RoomEvent: { Disconnected: "disconnected", Reconnecting: "reconnecting", SignalReconnecting: "signalReconnecting", Reconnected: "reconnected", TrackSubscribed: "trackSubscribed", TrackUnsubscribed: "trackUnsubscribed", DataReceived: "dataReceived", ParticipantDisconnected: "participantDisconnected" },
    }, dispatchEvent() {}, addEventListener() {} };
    env.script = { start: { status: 200, json: { state: "starting", error: null } }, status: [] };
    const sandbox = {
        window,
        fetch: async (url, opts = {}) => {
            env.calls.push({ url, method: opts.method || "GET" });
            let spec;
            if (url.endsWith("/avatar/token")) spec = { status: 200, json: { url: "wss://x", token: "t", room_name: "r", identity: "candidate-1" } };
            else if (url.endsWith("/avatar/start")) spec = env.script.start;
            else if (url.endsWith("/avatar/status")) spec = env.script.status.length ? env.script.status.shift() : { status: 200, json: { state: "ready", error: null } };
            else spec = { status: 404, json: {} };
            if (spec instanceof Error) throw spec;
            return { ok: spec.status >= 200 && spec.status < 300, status: spec.status, json: async () => spec.json };
        },
        setTimeout: (fn, ms) => { const t = { id: env.nextId++, fn, due: env.now + ms }; env.timers.push(t); return t.id; },
        clearTimeout: (id) => { env.timers = env.timers.filter((t) => t.id !== id); },
        document: {
            body: { appendChild: (el) => { el.attached = true; env.elements.push(el); } },
            querySelectorAll: () => env.elements.filter((el) => el.attached),
        },
        CustomEvent: class { constructor(n, o) { this.name = n; this.detail = o && o.detail; } },
        navigator: { mediaDevices: { getUserMedia: () => { throw new Error("must not be called"); } } },
        Date: { now: () => env.now }, TextDecoder, TextEncoder, console,
    };
    window.window = window;
    vm.createContext(sandbox);
    SOURCES.forEach((src) => vm.runInContext(src, sandbox));
    env.Avatar = window.InterviewAvatarV2;
    env.Avatar.init({ apiBaseUrl: "http://api", applicationId: 41, onStateChange() {}, onRoomEvent() {}, onSpeechMessage() {}, onAvatarStatus: (s, e) => env.statuses.push([s, e]) });
    env.room = () => env.rooms[env.rooms.length - 1];
    env.advance = async (ms) => {
        const target = env.now + ms;
        for (;;) {
            const due = env.timers.filter((t) => t.due <= target).sort((a, b) => a.due - b.due)[0];
            if (!due) break;
            env.now = due.due; env.timers = env.timers.filter((t) => t !== due); due.fn(); await flush();
        }
        env.now = target; await flush();
    };
    env.track = (kind, sid) => ({
        kind, sid,
        attach(el) { if (kind === "video") { env.videos.push(el); return el; } const a = { muted: false, attrs: {}, attached: false, setAttribute(k, v) { this.attrs[k] = v; }, remove() { this.attached = false; } }; return a; },
        detach(el) { if (el) el.attached = false; },
    });
    env.count = (suffix) => env.calls.filter((c) => c.url.endsWith(suffix)).length;
    env.playing = () => env.elements.filter((el) => el.attached);
    return env;
}

const AVATAR = { identity: "tavus-avatar-agent" };
const results = [];
async function test(name, fn) {
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

(async () => {

await test("connecting asks the server to attach the fixed avatar ONCE and follows it until ready (then stops polling)", async () => {
    const env = makeEnv(); env.script.status = [{ status: 200, json: { state: "starting", error: null } }];
    await env.Avatar.connect(); await flush();
    assert.strictEqual(env.count("/avatar/start"), 1);
    assert.strictEqual(env.calls.find((c) => c.url.endsWith("/avatar/start")).method, "POST");
    await env.advance(1600); await env.advance(1600);
    assert.deepStrictEqual(env.statuses.map((s) => s[0]), ["starting", "starting", "ready"]);
    const polls = env.count("/avatar/status");
    await env.advance(20000);
    assert.strictEqual(env.count("/avatar/status"), polls);                 // no polling after ready
    assert.strictEqual(env.timers.length, 0);
});

await test("avatar not configured (503) -> honest 'unavailable', no polling, the room itself stays connected", async () => {
    const env = makeEnv(); env.script.start = { status: 503, json: { detail: "The AI interviewer avatar is not configured." } };
    await env.Avatar.connect(); await flush(); await env.advance(5000);
    assert.deepStrictEqual(env.statuses, [["unavailable", "not configured"]]);
    assert.strictEqual(env.count("/avatar/status"), 0);
    assert.strictEqual(env.Avatar.isConnected(), true);
});

await test("server reports the avatar failed -> honest 'failed' and polling stops", async () => {
    const env = makeEnv(); env.script.start = { status: 200, json: { state: "starting", error: null } };
    env.script.status = [{ status: 200, json: { state: "failed", error: "The avatar did not join the interview room in time." } }];
    await env.Avatar.connect(); await flush(); await env.advance(1600);
    assert.deepStrictEqual(env.statuses.map((s) => s[0]), ["starting", "failed"]);
    const polls = env.count("/avatar/status"); await env.advance(20000);
    assert.strictEqual(env.count("/avatar/status"), polls);
});

await test("a start request that cannot reach the server is reported as failed (voice unaffected)", async () => {
    const env = makeEnv(); env.script.start = new Error("offline");
    await env.Avatar.connect(); await flush();
    assert.deepStrictEqual(env.statuses, [["failed", "network"]]);
});

await test("an avatar that never becomes ready is given up on after a bounded time", async () => {
    const env = makeEnv(); env.script.status = Array.from({ length: 200 }, () => ({ status: 200, json: { state: "starting", error: null } }));
    await env.Avatar.connect(); await flush(); await env.advance(90000);
    assert.strictEqual(env.statuses[env.statuses.length - 1][0], "failed");
    assert.ok(env.count("/avatar/status") <= 60);
});

await test("reconnect (new Room) asks again exactly once and never leaves two poll loops", async () => {
    const env = makeEnv(); env.script.start = { status: 200, json: { state: "starting", error: null } };
    env.script.status = Array.from({ length: 50 }, () => ({ status: 200, json: { state: "starting", error: null } }));
    await env.Avatar.connect(); await flush(); await env.advance(1600);
    env.room().emit("disconnected");
    await env.advance(2100); await flush();                                  // bounded reconnect fires
    assert.strictEqual(env.rooms.length, 2);
    assert.strictEqual(env.count("/avatar/start"), 2);
    assert.ok(env.timers.length <= 1, "at most one pending poll timer");
});

await test("fixed-image mode: the server answers 'disabled' -> reported once, NO polling, the room stays connected", async () => {
    const env = makeEnv(); env.script.start = { status: 200, json: { state: "disabled", error: null, visual_mode: "image" } };
    await env.Avatar.connect(); await flush(); await env.advance(30000);
    assert.deepStrictEqual(env.statuses, [["disabled", null]]);
    assert.strictEqual(env.count("/avatar/status"), 0);
    assert.strictEqual(env.timers.length, 0);
    assert.strictEqual(env.Avatar.isConnected(), true);
});

await test("cleanup cancels the status polling and requests nothing afterwards", async () => {
    const env = makeEnv(); env.script.status = Array.from({ length: 50 }, () => ({ status: 200, json: { state: "starting", error: null } }));
    await env.Avatar.connect(); await flush();
    env.Avatar.cleanup();
    const before = env.calls.length;
    await env.advance(30000);
    assert.strictEqual(env.calls.length, before);
    assert.strictEqual(env.timers.length, 0);
});

await test("the avatar's video attaches to the AI video element and its audio plays through ONE element", async () => {
    const env = makeEnv(); await env.Avatar.connect(); await flush();
    const videoEl = { tag: "video" };
    env.Avatar.attachVideo(videoEl);
    env.room().emit("trackSubscribed", env.track("video", "TV_1"), {}, AVATAR);
    env.room().emit("trackSubscribed", env.track("audio", "TA_1"), {}, AVATAR);
    env.room().emit("trackSubscribed", env.track("audio", "TA_1"), {}, AVATAR);          // duplicate announcement
    assert.strictEqual(env.videos.length, 1);
    assert.strictEqual(env.playing().length, 1);
});

await test("avatar reconnect: the new audio track REPLACES the old element (never two voices)", async () => {
    const env = makeEnv(); await env.Avatar.connect(); await flush();
    env.room().emit("trackSubscribed", env.track("audio", "TA_1"), {}, AVATAR);
    env.room().emit("trackSubscribed", env.track("audio", "TA_2"), {}, AVATAR);
    assert.strictEqual(env.playing().length, 1);
});

await test("mute (interruption) silences EVERY AI audio element, including a plain-track one next to the avatar's", async () => {
    const env = makeEnv(); await env.Avatar.connect(); await flush();
    env.room().emit("trackSubscribed", env.track("audio", "TA_1"), {}, AVATAR);
    env.room().emit("trackSubscribed", env.track("audio", "TP_1"), {}, { identity: "jobpilot-tts-41" });
    assert.strictEqual(env.playing().length, 2);
    env.Avatar.setRemoteAudioMuted(true);
    assert.ok(env.playing().every((el) => el.muted === true));
    env.Avatar.setRemoteAudioMuted(false);
    assert.ok(env.playing().every((el) => el.muted === false));
});

await test("the old browser-driven avatar speaking path is not wired any more (no second AI voice source)", async () => {
    const page = fs.readFileSync(path.join(JS, "ai-interview-v2.js"), "utf8");
    assert.ok(!page.includes("setRemoteSpeaker({"));
    assert.ok(page.includes("handleServerAvatarStatus"));
});

await test("candidate camera reuse: neither the avatar nor the video-room layer opens a camera/mic stream of its own", async () => {
    for (const f of ["interview-avatar-v2.js", "interview-video-room-v2.js"]) {
        const src = fs.readFileSync(path.join(JS, f), "utf8").replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
        assert.ok(!/getUserMedia\s*\(/.test(src), f);
    }
    const page = fs.readFileSync(path.join(JS, "ai-interview-v2.js"), "utf8").replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(!/getUserMedia\s*\(/.test(page));                              // the page never opens a stream; the voice controller owns the ONE mic stream
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

})();

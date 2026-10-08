// ============================================================
// JOBPILOT AI - LIVEKIT TRANSPORT TESTS (Task 5)
//
// Runs the REAL avatar (Room owner) and video-room (publisher)
// controllers together, exactly as ai-interview-v2.js wires them,
// against a deterministic fake LiveKit client + fetch + clock.
// Run: node tests/frontend/test_interview_livekit_transport_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const JS = path.join(__dirname, "..", "..", "frontend", "js");
const SOURCES = ["interview-avatar-turn-logic-v2.js", "interview-avatar-v2.js", "interview-video-room-turn-logic-v2.js", "interview-video-room-v2.js"]
    .map((f) => fs.readFileSync(path.join(JS, f), "utf8"));

const flush = () => new Promise((resolve) => setImmediate(resolve));

function makeEnv() {

    const env = {
        now: 0, timers: [], nextId: 1,
        fetches: [], routes: [], rooms: [], gumCalls: 0, connectGate: null, connectError: null,
        avatarStates: [], roomEvents: [], videoStates: [], videoErrors: [],
    };

    // ---- clock
    env.setTimeout = (fn, ms) => { const t = { id: env.nextId++, fn, due: env.now + ms }; env.timers.push(t); return t.id; };
    env.clearTimeout = (id) => { env.timers = env.timers.filter((t) => t.id !== id); };
    env.advance = async (ms) => {
        const target = env.now + ms;
        for (;;) {
            const due = env.timers.filter((t) => t.due <= target).sort((a, b) => a.due - b.due)[0];
            if (!due) break;
            env.now = due.due;
            env.timers = env.timers.filter((t) => t !== due);
            due.fn();
            await flush();
        }
        env.now = target;
        await flush();
    };

    // ---- fetch: /avatar/token
    env.fetch = (url, opts = {}) => {
        env.fetches.push({ url, opts });
        const handler = env.routes[0] || (() => ({ status: 200, json: { url: "wss://lk.test", token: "jwt-abc", room_name: "jobpilot-interview-v2-41", identity: "candidate-30", avatar: {} } }));
        const spec = handler(url, opts);
        if (spec instanceof Error) return Promise.reject(spec);
        if (spec && typeof spec.then === "function") return spec.then((s) => (s instanceof Error ? Promise.reject(s) : respond(s)));
        return Promise.resolve(respond(spec));
    };
    const respond = (spec) => ({ ok: spec.status >= 200 && spec.status < 300, status: spec.status, json: () => Promise.resolve(spec.json) });

    // ---- fake LiveKit client
    class FakeRoom {
        constructor() {
            this.handlers = {}; this.disconnectCalls = 0; this.connected = false; this.connectArgs = null;
            this.remoteParticipants = new Map();
            this.published = [];
            const room = this;
            this.localParticipant = {
                publishTrack: async (track, options) => {
                    if (env.publishError) throw env.publishError;
                    const pub = { track, options, muted: false, setMuted(m) { this.muted = m; } };
                    room.published.push(pub);
                    return pub;
                },
            };
            env.rooms.push(this);
        }
        on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
        async connect(url, token) {
            this.connectArgs = [url, token];
            if (env.connectGate) await env.connectGate;
            if (env.connectError) throw env.connectError;
            this.connected = true;
        }
        disconnect() { this.disconnectCalls++; this.connected = false; }
        startAudio() {}
        emit(ev, ...args) { (this.handlers[ev] || []).forEach((fn) => fn(...args)); }
    }
    const LivekitClient = {
        Room: FakeRoom,
        RoomEvent: { Disconnected: "disconnected", Reconnecting: "reconnecting", SignalReconnecting: "signalReconnecting", Reconnected: "reconnected", TrackSubscribed: "trackSubscribed", TrackUnsubscribed: "trackUnsubscribed", DataReceived: "dataReceived" },
        Track: { Source: { Camera: "camera", Microphone: "microphone" } },
    };

    const window = { LivekitClient: options => options };
    window.LivekitClient = LivekitClient;
    const sandbox = {
        window, fetch: env.fetch, setTimeout: env.setTimeout, clearTimeout: env.clearTimeout,
        navigator: { mediaDevices: { getUserMedia: () => { env.gumCalls++; return Promise.reject(new Error("must not be called")); } } },
        document: { body: { appendChild() {} }, querySelectorAll: () => [] },
        TextDecoder, TextEncoder, console,
    };
    window.window = window;
    vm.createContext(sandbox);
    SOURCES.forEach((src) => vm.runInContext(src, sandbox));

    env.Avatar = window.InterviewAvatarV2;
    env.Video = window.InterviewVideoRoomV2;
    env.A = env.Avatar.STATES;
    env.V = env.Video.STATES;
    env.window = window;
    env.setLivekit = (v) => { window.LivekitClient = v; };

    // wired exactly like ai-interview-v2.js: room events -> video-room controller
    env.Avatar.init({
        apiBaseUrl: "http://api", applicationId: 41,
        onStateChange: (s) => env.avatarStates.push(s),
        onRoomEvent: (name) => { env.roomEvents.push(name); env.Video.handleRoomEvent(name); },
    });
    env.Video.init({ onStateChange: (s) => env.videoStates.push(s), onError: (k) => env.videoErrors.push(k) });

    env.liveRooms = () => env.rooms.filter((r) => r.connected);
    env.avatarState = () => env.avatarStates[env.avatarStates.length - 1];
    env.videoState = () => env.videoStates[env.videoStates.length - 1];
    return env;
}

// a media stream whose tracks are recognizable objects (identity is what mic-reuse tests compare)
const makeStream = (kind) => {
    const track = { kind, stopped: false, stop() { this.stopped = true; } };
    return { track, getAudioTracks: () => (kind === "audio" ? [track] : []), getVideoTracks: () => (kind === "video" ? [track] : []) };
};

const results = [];
async function test(name, fn) {
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

(async () => {

// ---------------------------------------------------------------- token + connect
await test("connect: requests the interview token (POST, cookies) and joins ONE room with the token it was given", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    assert.strictEqual(env.fetches.filter((f) => f.url.endsWith("/avatar/token")).length, 1);
    assert.ok(env.fetches[0].url.endsWith("/api/assessment/sessions/41/interview/v2/avatar/token"));
    assert.strictEqual(env.fetches[0].opts.method, "POST");
    assert.strictEqual(env.fetches[0].opts.credentials, "include");
    assert.strictEqual(env.rooms.length, 1);
    assert.deepStrictEqual(env.rooms[0].connectArgs, ["wss://lk.test", "jwt-abc"]);
    assert.ok(env.avatarStates.includes(env.A.AVATAR_CONNECTING) && env.avatarState() === env.A.AVATAR_CONNECTED);
    assert.deepStrictEqual(env.roomEvents, ["CONNECT_STARTED", "CONNECT_SUCCEEDED"]);
    assert.strictEqual(env.Avatar.getRoom(), env.rooms[0]);
    assert.strictEqual(env.videoState(), env.V.CONNECTED);
});

await test("connecting never opens a microphone or camera by itself", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    assert.strictEqual(env.gumCalls, 0);
});

// ---------------------------------------------------------------- failures
await test("token failures are clean, distinct and never create a room: 503 unavailable; 401/403/404/409/500 error", async () => {
    for (const [status, expected] of [[503, "AVATAR_UNAVAILABLE"], [401, "AVATAR_ERROR"], [403, "AVATAR_ERROR"], [404, "AVATAR_ERROR"], [409, "AVATAR_ERROR"], [500, "AVATAR_ERROR"]]) {
        const env = makeEnv();
        env.routes.push(() => ({ status, json: { detail: "denied" } }));
        await env.Avatar.connect();
        assert.strictEqual(env.avatarState(), expected, String(status));
        assert.strictEqual(env.rooms.length, 0, `no room for ${status}`);
        assert.ok(env.roomEvents.includes("CONNECT_FAILED"), String(status));
    }
});

await test("a network failure fetching the token is an error state, not an exception", async () => {
    const env = makeEnv();
    env.routes.push(() => new TypeError("Failed to fetch"));
    await env.Avatar.connect();
    assert.strictEqual(env.avatarState(), env.A.AVATAR_ERROR);
    assert.strictEqual(env.rooms.length, 0);
});

await test("the LiveKit client script missing from the page is an error state, not an exception", async () => {
    const env = makeEnv();
    env.setLivekit(undefined);
    await env.Avatar.connect();
    assert.strictEqual(env.avatarState(), env.A.AVATAR_ERROR);
    assert.strictEqual(env.rooms.length, 0);
});

await test("room unavailable: connect() rejecting ends in an error state and leaves no live room", async () => {
    const env = makeEnv();
    env.connectError = new Error("room unavailable");
    await env.Avatar.connect();
    assert.strictEqual(env.avatarState(), env.A.AVATAR_ERROR);
    assert.ok(env.roomEvents.includes("CONNECT_FAILED"));
    assert.strictEqual(env.liveRooms().length, 0);
});

// ---------------------------------------------------------------- reconnect / disconnect
await test("transient drops surface as RECONNECTING then RECONNECTED without leaving the room", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const room = env.rooms[0];
    room.emit("reconnecting");
    assert.strictEqual(env.videoState(), env.V.RECONNECTING);
    room.emit("reconnected");
    assert.strictEqual(env.videoState(), env.V.CONNECTED);
    room.emit("signalReconnecting");
    assert.strictEqual(env.videoState(), env.V.RECONNECTING);
    room.emit("reconnected");
    assert.strictEqual(env.videoState(), env.V.CONNECTED);
    assert.strictEqual(env.rooms.length, 1);                       // still the same single room
});

await test("after the SDK gives up: DISCONNECTED, then a bounded automatic reconnect with a fresh token", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    env.rooms[0].connected = false; env.rooms[0].emit("disconnected");
    assert.strictEqual(env.avatarState(), env.A.AVATAR_ERROR);
    assert.strictEqual(env.videoState(), env.V.DISCONNECTED);
    assert.strictEqual(env.fetches.filter((f) => f.url.endsWith("/avatar/token")).length, 1);
    await env.advance(2000);                                        // first backoff step
    assert.strictEqual(env.fetches.filter((f) => f.url.endsWith("/avatar/token")).length, 2);                      // a NEW token, never a reused one
    assert.strictEqual(env.rooms.length, 2);
    assert.strictEqual(env.avatarState(), env.A.AVATAR_CONNECTED);
    assert.strictEqual(env.videoState(), env.V.CONNECTED);
    assert.strictEqual(env.liveRooms().length, 1);
});

await test("reconnect gives up after its budget so a dead room cannot loop forever", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    env.connectError = new Error("still down");
    env.rooms[0].connected = false; env.rooms[0].emit("disconnected");
    await env.advance(2000); await env.advance(5000); await env.advance(10000);
    const attempts = env.rooms.length;
    assert.strictEqual(attempts, 4);                               // the original + exactly 3 retries
    await env.advance(120000);
    assert.strictEqual(env.rooms.length, attempts);                // nothing further, ever
    assert.strictEqual(env.avatarState(), env.A.AVATAR_ERROR);
});

await test("a healthy reconnect restores the retry budget", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    env.rooms[0].connected = false; env.rooms[0].emit("disconnected"); await env.advance(2000);
    env.rooms[1].connected = false; env.rooms[1].emit("disconnected");
    await env.advance(2000);                                        // budget was reset by the successful reconnect: first delay again
    assert.strictEqual(env.rooms.length, 3);
});

await test("a mid-connect failure that cannot be recovered by the user does not retry (auth/ownership errors)", async () => {
    const env = makeEnv();
    env.routes.push(() => ({ status: 403, json: {} }));
    await env.Avatar.connect();
    await env.advance(60000);
    assert.strictEqual(env.fetches.filter((f) => f.url.endsWith("/avatar/token")).length, 1);                     // 403 is final: no retry storm
});

// ---------------------------------------------------------------- cleanup
await test("cleanup disconnects the room exactly once, releases it and cancels any pending reconnect", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const room = env.rooms[0];
    room.connected = false; room.emit("disconnected");             // a reconnect is now scheduled
    env.Avatar.cleanup();
    await env.advance(60000);
    assert.strictEqual(env.rooms.length, 1);                       // the scheduled reconnect never fired
    assert.strictEqual(env.Avatar.getRoom(), null);
    assert.strictEqual(env.avatarState(), env.A.COMPLETED);
});

await test("cleanup of a connected room disconnects it once and nothing reconnects afterwards", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const room = env.rooms[0];
    env.Avatar.cleanup();
    assert.strictEqual(room.disconnectCalls, 1);
    room.emit("disconnected");                                      // the SDK's own event for OUR disconnect
    await env.advance(60000);
    assert.strictEqual(env.rooms.length, 1);
    assert.strictEqual(env.liveRooms().length, 0);
});

await test("cleanup while the token is still being fetched never creates a room", async () => {
    const env = makeEnv();
    let release; const gate = new Promise((r) => { release = r; });
    env.routes.push(() => gate.then(() => ({ status: 200, json: { url: "wss://lk.test", token: "t", room_name: "r", identity: "i", avatar: {} } })));
    const pending = env.Avatar.connect();
    env.Avatar.cleanup();
    release(); await pending;
    assert.strictEqual(env.rooms.length, 0);
});

await test("cleanup while room.connect() is in flight leaves no live room behind", async () => {
    const env = makeEnv();
    let release; env.connectGate = new Promise((r) => { release = r; });
    const pending = env.Avatar.connect(); await flush();
    assert.strictEqual(env.rooms.length, 1);
    env.Avatar.cleanup();
    release(); await pending; await flush();
    assert.strictEqual(env.liveRooms().length, 0);
    assert.strictEqual(env.rooms[0].disconnectCalls >= 1, true);
});

await test("two overlapping connect() calls end with exactly ONE live room, and it is the newest", async () => {
    const env = makeEnv();
    let release; env.connectGate = new Promise((r) => { release = r; });
    const first = env.Avatar.connect(); await flush();               // room #1 created, connect() pending
    const second = env.Avatar.connect(); await flush();              // supersedes #1 (room #2 created)
    release(); await Promise.all([first, second]); await flush();
    assert.strictEqual(env.liveRooms().length, 1);
    assert.strictEqual(env.Avatar.getRoom(), env.liveRooms()[0]);
    assert.strictEqual(env.Avatar.getRoom().connected, true);
});

// ---------------------------------------------------------------- microphone reuse (publishing)
await test("microphone reuse: the ALREADY-OPEN stream's own track is published, and no new capture is ever requested", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const mic = makeStream("audio"), cam = makeStream("video");
    env.Video.notifyMicReady(mic);
    assert.strictEqual(env.rooms[0].published.length, 0);          // camera not ready yet: nothing published
    env.Video.notifyCameraReady(cam);
    await flush();
    const pubs = env.rooms[0].published;
    assert.strictEqual(pubs.length, 2);
    const micPub = pubs.find((p) => p.options.source === "microphone");
    const camPub = pubs.find((p) => p.options.source === "camera");
    assert.strictEqual(micPub.track, mic.track);                   // the SAME object: not cloned, not re-captured
    assert.strictEqual(camPub.track, cam.track);
    assert.deepStrictEqual([micPub.options.name, camPub.options.name], ["candidate-microphone", "candidate-camera"]);
    assert.strictEqual(env.gumCalls, 0);
});

await test("tracks are published exactly once per connection, however often readiness is re-announced", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const mic = makeStream("audio"), cam = makeStream("video");
    for (let i = 0; i < 4; i++) { env.Video.notifyMicReady(mic); env.Video.notifyCameraReady(cam); await flush(); }
    assert.strictEqual(env.rooms[0].published.length, 2);
});

await test("mic ready BEFORE the room connects: publishing waits for the connection, then happens", async () => {
    const env = makeEnv();
    env.Video.notifyMicReady(makeStream("audio")); env.Video.notifyCameraReady(makeStream("video"));
    await flush();
    assert.strictEqual(env.rooms.length, 0);
    await env.Avatar.connect(); await flush();
    assert.strictEqual(env.rooms[0].published.length, 2);
});

await test("a transient reconnect does NOT republish (same connection); a full reconnect DOES, reusing the same live tracks", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const mic = makeStream("audio"), cam = makeStream("video");
    env.Video.notifyMicReady(mic); env.Video.notifyCameraReady(cam); await flush();
    env.rooms[0].emit("reconnecting"); env.rooms[0].emit("reconnected"); await flush();
    assert.strictEqual(env.rooms[0].published.length, 2);           // same connection: nothing new
    env.rooms[0].connected = false; env.rooms[0].emit("disconnected");
    await env.advance(2000); await flush();
    assert.strictEqual(env.rooms[1].published.length, 2);           // new connection: published again
    assert.strictEqual(env.rooms[1].published.find((p) => p.options.source === "microphone").track, mic.track);
    assert.strictEqual(mic.track.stopped, false);                   // the shared mic track stayed live throughout
    assert.strictEqual(env.gumCalls, 0);
});

await test("publish failure is reported and can be retried on the next readiness signal", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    env.publishError = new Error("publish denied");
    const mic = makeStream("audio"), cam = makeStream("video");
    env.Video.notifyMicReady(mic); env.Video.notifyCameraReady(cam); await flush();
    assert.deepStrictEqual(env.videoErrors, ["publish_failed"]);
    env.publishError = null;
    env.Video.notifyMicReady(mic); await flush();
    assert.strictEqual(env.rooms[0].published.length, 2);
});

await test("muting the mic control mutes the published track", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    env.Video.notifyMicReady(makeStream("audio")); env.Video.notifyCameraReady(makeStream("video")); await flush();
    env.Video.setControlEnabled("mic", false);
    assert.strictEqual(env.rooms[0].published.find((p) => p.options.source === "microphone").muted, true);
    env.Video.setControlEnabled("mic", true);
    assert.strictEqual(env.rooms[0].published.find((p) => p.options.source === "microphone").muted, false);
});

await test("interview end: tracks stop, state is COMPLETED, and a late readiness signal cannot re-publish", async () => {
    const env = makeEnv();
    await env.Avatar.connect();
    const mic = makeStream("audio"), cam = makeStream("video");
    env.Video.notifyMicReady(mic); env.Video.notifyCameraReady(cam); await flush();
    env.Video.cleanup(); env.Avatar.cleanup(); await flush();
    assert.strictEqual(mic.track.stopped, true);
    assert.strictEqual(cam.track.stopped, true);
    assert.strictEqual(env.videoState(), env.V.COMPLETED);
    const before = env.rooms[0].published.length;
    env.Video.notifyMicReady(makeStream("audio")); await flush();
    assert.strictEqual(env.rooms[0].published.length, before);
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

})();

// ============================================================
// JOBPILOT AI - LIVEKIT VOICE-FLOW TESTS (Task 6)
//
// Part 1: the REAL voice controller (Task 4 + Task 6 delivery layer) in the
// shared fake browser: transport-mode selection, LiveKit-delivered AI audio
// (no local playback -> no double audio), stale-response rejection,
// interruption, timeouts, fallback, cleanup and mic reuse.
// Part 2: the REAL avatar/room controller's AI-audio layer against a fake
// LiveKit client: one element per participant, duplicate/stale tracks,
// unsubscribe, participant/room disconnect, reconnect, mute.
// Run: node tests/frontend/test_interview_livekit_voice_flow_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const { makeEnv, speakRoute, okTurn, candidateSpeaks, startListeningEnv, flush } = require("./_voice_harness_v2.js");

const results = [];
let currentTest = null;
const watchdog = setInterval(() => {}, 1000);
setTimeout(() => { process.stdout.write(`\nHUNG in test: ${currentTest}\n`); process.exit(2); }, 120000).unref();
async function test(name, fn) {
    currentTest = name;
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

const msg = (state, seq, extra = {}) => ({ channel: "server-audio", state, seq, ...extra });
const lkTurn = (seq, overrides = {}) => okTurn(Object.assign({
    audio: null, audio_delivery: "livekit", speech_seq: seq, livekit: { audio_seconds: 2.0, reused_connection: true, reused_track: true },
}, overrides));
const formField = (call, name) => (call.opts.body.fields.find((f) => f[0] === name) || [])[1];
const interruptCalls = (env) => env.pending("/voice/interrupt");

// run one utterance up to the point where the /voice/turn request is in flight
async function submitTurn(env) {
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(1000);
    env.level = 0.0; await env.advance(1300);
    assert.strictEqual(env.turnFetches().length >= 1, true);
    return env.turnFetches()[env.turnFetches().length - 1];
}

(async () => {

// ================================================================ mode selection
await test("pure mode selection: livekit only when requested AND the room is connected", async () => {
    const env = makeEnv();
    const L = env.window.InterviewVoiceTurnLogicV2;
    assert.strictEqual(L.chooseTransport("livekit", true), "livekit");
    assert.strictEqual(L.chooseTransport("livekit", false), "local");
    assert.strictEqual(L.chooseTransport("livekit", undefined), "local");
    assert.strictEqual(L.chooseTransport("local", true), "local");
    assert.strictEqual(L.chooseTransport(undefined, true), "local");
    assert.strictEqual(L.chooseTransport("carrier-pigeon", true), "local");
});

await test("seq routing: accept the awaited response, drop older ones, buffer the rest", async () => {
    const L = makeEnv().window.InterviewVoiceTurnLogicV2;
    assert.strictEqual(L.routeServerAudioMessage(3, 3), "accept");
    assert.strictEqual(L.routeServerAudioMessage(2, 3), "stale");
    assert.strictEqual(L.routeServerAudioMessage(4, 3), "buffer");
    assert.strictEqual(L.routeServerAudioMessage(1, null), "buffer");
});

await test("default (no transport configured) is local: field says local and the reply plays in a local <audio>", async () => {
    const env = makeEnv();
    const call = await submitTurn(env);
    assert.strictEqual(formField(call, "transport"), "local");
    call.resolve(okTurn()); await flush();
    assert.strictEqual(env.audios.length, 1);
});

await test("livekit configured but the room is not connected -> this turn is local", async () => {
    const env = makeEnv({ transport: "livekit", roomConnected: false });
    const call = await submitTurn(env);
    assert.strictEqual(formField(call, "transport"), "local");
});

await test("livekit configured and the room connected -> the turn asks for livekit", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    assert.strictEqual(formField(call, "transport"), "livekit");
});

await test("the mode is fixed for the turn: the room dropping AFTER the request does not change how this reply is handled", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    env.roomConnected = false;                                            // room lost while the server was thinking
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.strictEqual(env.audios.length, 0);                              // still not played locally on top
});

// ================================================================ livekit delivery
await test("LIVEKIT reply: no local audio is created; the AI 'speaks' on started and hands back on ended", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    assert.strictEqual(env.state(), env.S.AI_PREPARING);
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.ok(env.events.includes("AI_STARTED_SPEAKING"));
    env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();
    assert.ok(env.events.includes("AI_STOPPED_SPEAKING"));
    assert.strictEqual(env.audios.length, 0);                              // NO double audio: nothing local ever played
    assert.strictEqual(env.recording(), 1);                                // back to listening on the same mic
    assert.strictEqual(env.gumCalls, 1);                                   // mic reused, never re-requested
    assert.strictEqual(env.voiceTurns.length, 1);                          // the page still got the transcript + text
});

await test("messages that beat the HTTP reply are buffered and applied for the right response only", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    env.Voice.handleRemoteSpeech(msg("started", 2));                       // arrives before the reply
    env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();          // an older response's leftovers
    call.resolve(lkTurn(2)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);                    // seq 2 started was applied; seq 1 ignored
    env.Voice.handleRemoteSpeech(msg("ended", 2)); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
});

await test("STALE seq messages are rejected while waiting for the current response", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(5)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 4)); await flush();
    env.Voice.handleRemoteSpeech(msg("ended", 4)); await flush();
    assert.strictEqual(env.state(), env.S.AI_PREPARING);                   // nothing from seq 4 moved the state
    env.Voice.handleRemoteSpeech(msg("started", 5)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
});

await test("a duplicate 'started' does not re-announce speaking", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(env.events.filter((e) => e === "AI_STARTED_SPEAKING").length, 1);
});

await test("second turn reuses the same controller path: new seq, still no local audio, mic never re-requested", async () => {
    const env = makeEnv({ transport: "livekit" });
    let call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();
    env.level = 0.1; await env.advance(1000); env.level = 0.0; await env.advance(1300);
    call = env.turnFetches()[1];
    assert.ok(call, "second turn submitted");
    call.resolve(lkTurn(2)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 2)); env.Voice.handleRemoteSpeech(msg("ended", 2)); await flush();
    assert.strictEqual(env.audios.length, 0);
    assert.strictEqual(env.gumCalls, 1);
    assert.strictEqual(env.voiceTurns.length, 2);
});

await test("interview_complete over livekit: completes only after the closing audio has ended", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1, { interview_complete: true })); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(env.completions, 0);
    env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();
    assert.strictEqual(env.completions, 1);
    assert.strictEqual(env.state(), env.S.COMPLETED);
    assert.strictEqual(env.audios.length, 0);
});

// ================================================================ interruption
await test("INTERRUPTION during livekit speech: AI audio muted at once, server told to stop, capture starts, late messages ignored", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(env.recording(), 0);
    env.level = 0.2; await env.advance(700);                               // the candidate talks over the AI
    assert.ok(env.muteLog.includes(true), "AI audio element muted");
    assert.strictEqual(interruptCalls(env).length, 1);
    assert.strictEqual(interruptCalls(env)[0].opts.method, "POST");
    assert.ok(env.events.includes("AI_INTERRUPTED"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);
    assert.strictEqual(env.recording(), 1);                                // the SAME mic stream now records the candidate
    assert.strictEqual(env.gumCalls, 1);
    env.Voice.handleRemoteSpeech(msg("ended", 1, { interrupted: true })); await flush();   // the server's confirmation
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);             // it did not advance anything
    assert.strictEqual(env.audios.length, 0);
});

await test("after an interruption the next response is heard again: 'started' un-mutes the AI audio", async () => {
    const env = makeEnv({ transport: "livekit" });
    let call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    env.level = 0.2; await env.advance(700);
    env.level = 0.0; await env.advance(1500);                              // candidate finishes their interruption
    call = env.turnFetches()[1];
    call.resolve(lkTurn(2)); await flush();
    assert.strictEqual(env.muteLog[env.muteLog.length - 1], true);         // still muted until the new response starts
    env.Voice.handleRemoteSpeech(msg("started", 2)); await flush();
    assert.strictEqual(env.muteLog[env.muteLog.length - 1], false);
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
});

await test("stopSpeaking() while a livekit response plays behaves like an interruption (no dangling wait)", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    env.Voice.stopSpeaking(); await flush();
    assert.strictEqual(interruptCalls(env).length, 1);
    assert.ok(env.muteLog.includes(true));
    const timers = env.activeTimers();
    env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();
    assert.strictEqual(env.activeTimers(), timers);                        // the late message created/kept nothing
});

// ================================================================ failures + fallback
await test("the AI voice never starts: bounded wait, honest error, server told to drop it, nothing played locally", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    await env.advance(8100);
    assert.ok(env.errors.includes("tts_playback_failed"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(interruptCalls(env).length, 1);
    assert.ok(env.muteLog.includes(true));                                 // a late start would stay silent
    assert.strictEqual(env.audios.length, 0);                              // never a second, local copy on top
});

await test("'failed' from the server ends the wait with the same honest error", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); env.Voice.handleRemoteSpeech(msg("failed", 1)); await flush();
    assert.ok(env.errors.includes("tts_playback_failed"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
});

await test("the room disconnecting mid-response ends the wait (no hang) and requests a server stop", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    env.Voice.handleTransportEvent("DISCONNECTED"); await flush();
    assert.ok(env.errors.includes("tts_playback_failed"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(interruptCalls(env).length, 1);
});

await test("a reconnect signal with nothing awaited is harmless", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.handleTransportEvent("DISCONNECTED"); env.Voice.handleTransportEvent("RECONNECTING"); env.Voice.handleTransportEvent("RECONNECTED"); await flush();
    assert.deepStrictEqual(env.errors, []);
});

await test("LOCAL FALLBACK: livekit was asked for but the server returned the audio inline -> it plays locally exactly once", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(okTurn({ audio_delivery: "local", speech_seq: null, livekit_error: "Could not connect to the interview room (ConnectionError)." })); await flush();
    assert.strictEqual(env.audios.length, 1);
    assert.ok(env.events.includes("LIVEKIT_DELIVERY_FELL_BACK"));
    env.audios[0].finish(); await flush();
    assert.strictEqual(env.audios.length, 1);
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
});

await test("local mode is completely unchanged by Task 6: inline audio, one <audio>, no interrupt calls", async () => {
    const env = makeEnv();
    const call = await submitTurn(env);
    call.resolve(okTurn()); await flush();
    env.audios[0].finish(); await flush();
    assert.strictEqual(env.audios.length, 1);
    assert.strictEqual(interruptCalls(env).length, 0);
    assert.strictEqual(env.muteLog.length, 0);
});

await test("leaving while a livekit turn is still being answered: the server is told to stop, nothing is played", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    env.Voice.cleanup();                                                   // page left while the request was in flight
    await flush();
    assert.strictEqual(interruptCalls(env).length, 1);
    call.resolve(lkTurn(1)); await flush();                                // the aborted request's reply never acts
    assert.strictEqual(env.audios.length, 0);
    assert.strictEqual(env.activeTimers(), 0);
});

await test("cleanup during livekit speech: server stop requested, AI muted, no timers or recorders left", async () => {
    const env = makeEnv({ transport: "livekit" });
    const call = await submitTurn(env);
    call.resolve(lkTurn(1)); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    env.Voice.cleanup(); await flush();
    assert.strictEqual(interruptCalls(env).length, 1);
    assert.ok(env.muteLog.includes(true));
    assert.strictEqual(env.activeTimers(), 0);
    assert.strictEqual(env.recording(), 0);
    assert.ok(env.streams.every((s) => s.tracks.every((t) => t.stopped)));
});

// ================================================================ question speech (/voice/speak)
await test("speak(): in livekit mode the question is requested with transport=livekit and followed via messages", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.speak(1); await flush();
    const call = env.pending("/voice/speak")[0];
    assert.ok(call.url.includes("turn_number=1") && call.url.includes("transport=livekit"));
    call.resolve({ status: 200, headers: { "content-type": "application/json" }, json: { audio_delivery: "livekit", speech_seq: 7, audio_seconds: 3.1 } }); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 7)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    env.Voice.handleRemoteSpeech(msg("ended", 7)); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.audios.length, 0);
});

await test("speak(): local mode keeps the exact original URL and plays the returned WAV", async () => {
    const env = makeEnv({ transport: "livekit", roomConnected: false });
    env.Voice.speak(2); await flush();
    const call = env.pending("/voice/speak")[0];
    assert.ok(!call.url.includes("transport"));
    call.resolve({ status: 200, json: {} }); await flush();
    assert.strictEqual(env.audios.length, 1);
});

await test("speak(): livekit requested but the server answered with the WAV (its own fallback) -> plays locally once", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.speak(3); await flush();
    env.pending("/voice/speak")[0].resolve({ status: 200, headers: { "content-type": "audio/wav" }, json: {} }); await flush();
    assert.strictEqual(env.audios.length, 1);
});

await test("speak(): superseded by a newer speak() -> the old reply never acts (the server replaces it itself); no stray interrupt", async () => {
    const env = makeEnv({ transport: "livekit" });
    env.Voice.speak(4); await flush();
    const first = env.pending("/voice/speak")[0];
    env.Voice.speak(5); await flush();
    first.resolve({ status: 200, headers: { "content-type": "application/json" }, json: { audio_delivery: "livekit", speech_seq: 1, audio_seconds: 1 } }); await flush();
    assert.strictEqual(interruptCalls(env).length, 0);                     // an interrupt here could race with (and cut off) the newer request
    const second = env.pending("/voice/speak")[1];
    second.resolve({ status: 200, headers: { "content-type": "application/json" }, json: { audio_delivery: "livekit", speech_seq: 2, audio_seconds: 1 } }); await flush();
    env.Voice.handleRemoteSpeech(msg("started", 1)); env.Voice.handleRemoteSpeech(msg("started", 2)); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.strictEqual(env.audios.length, 0);
});

await test("speak(): the candidate talks over a question that is still being requested -> the server is told to stop it", async () => {
    const env = makeEnv({ transport: "livekit" });
    await env.Voice.ensureMicStream();
    env.Voice.speak(6); await flush();
    env.level = 0.2; await env.advance(700);
    assert.ok(env.events.includes("AI_INTERRUPTED"));
    assert.strictEqual(interruptCalls(env).length, 1);
    assert.strictEqual(env.recording(), 1);
});

// ================================================================ Part 2: avatar/room AI-audio layer
const JS = path.join(__dirname, "..", "..", "frontend", "js");
const AVATAR_SOURCES = ["interview-avatar-turn-logic-v2.js", "interview-avatar-v2.js"].map((f) => fs.readFileSync(path.join(JS, f), "utf8"));

function makeAvatarEnv() {
    const env = { rooms: [], elements: [], speech: [], roomEvents: [], timers: [], nextId: 1, now: 0 };
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
    }, dispatchEvent() {} };
    const sandbox = {
        window,
        fetch: async () => ({ ok: true, status: 200, json: async () => ({ url: "wss://x", token: "t", room_name: "r", identity: "candidate-1" }) }),
        setTimeout: (fn, ms) => { const t = { id: env.nextId++, fn, due: env.now + ms }; env.timers.push(t); return t.id; },
        clearTimeout: (id) => { env.timers = env.timers.filter((t) => t.id !== id); },
        document: {
            body: { appendChild: (el) => { el.attached = true; env.elements.push(el); } },
            querySelectorAll: () => env.elements.filter((el) => el.attached),
        },
        CustomEvent: class { constructor(n, o) { this.name = n; this.detail = o && o.detail; } },
        TextDecoder, TextEncoder, console,
    };
    window.window = window;
    vm.createContext(sandbox);
    AVATAR_SOURCES.forEach((src) => vm.runInContext(src, sandbox));
    env.Avatar = window.InterviewAvatarV2;
    env.Avatar.init({ apiBaseUrl: "http://api", applicationId: 41, onStateChange() {}, onRoomEvent: (n) => env.roomEvents.push(n), onSpeechMessage: (m) => env.speech.push(m) });
    env.room = () => env.rooms[env.rooms.length - 1];
    env.audioTrack = (sid) => ({
        kind: "audio", sid,
        attach() { const el = { muted: false, attrs: {}, attached: false, setAttribute(k, v) { this.attrs[k] = v; }, remove() { this.attached = false; } }; return el; },
        detach(el) { if (el) el.attached = false; },
    });
    env.playing = () => env.elements.filter((el) => el.attached);
    env.data = (obj, topic = "jobpilot.speech") => env.room().emit("dataReceived", new TextEncoder().encode(JSON.stringify(obj)), { identity: "jobpilot-tts-41" }, 0, topic);
    return env;
}

const AI = { identity: "jobpilot-tts-41" };

await test("avatar layer: not connected before connect(); connected after; disconnected after a room drop", async () => {
    const env = makeAvatarEnv();
    assert.strictEqual(env.Avatar.isConnected(), false);
    await env.Avatar.connect();
    assert.strictEqual(env.Avatar.isConnected(), true);
    env.room().emit("reconnecting");
    assert.strictEqual(env.Avatar.isConnected(), false);                    // not usable while reconnecting
    env.room().emit("reconnected");
    assert.strictEqual(env.Avatar.isConnected(), true);
    env.room().emit("disconnected");
    assert.strictEqual(env.Avatar.isConnected(), false);
});

await test("AI audio track subscribed -> exactly one element; the SAME track announced twice does not duplicate it", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    const t = env.audioTrack("TR_1");
    env.room().emit("trackSubscribed", t, {}, AI);
    env.room().emit("trackSubscribed", t, {}, AI);
    assert.strictEqual(env.playing().length, 1);
    assert.strictEqual(env.playing()[0].attrs["data-jobpilot-avatar-audio"], "1");
});

await test("a NEW track from the same participant REPLACES the old element (never two AI voices)", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    env.room().emit("trackSubscribed", env.audioTrack("TR_1"), {}, AI);
    env.room().emit("trackSubscribed", env.audioTrack("TR_2"), {}, AI);
    assert.strictEqual(env.playing().length, 1);
});

await test("a STALE unsubscribe (old track) does not remove the current track's element", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    const old = env.audioTrack("TR_1"), current = env.audioTrack("TR_2");
    env.room().emit("trackSubscribed", old, {}, AI);
    env.room().emit("trackSubscribed", current, {}, AI);
    env.room().emit("trackUnsubscribed", old, {}, AI);
    assert.strictEqual(env.playing().length, 1);
    env.room().emit("trackUnsubscribed", current, {}, AI);
    assert.strictEqual(env.playing().length, 0);
});

await test("participant leaving removes their audio element", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    env.room().emit("trackSubscribed", env.audioTrack("TR_1"), {}, AI);
    env.room().emit("participantDisconnected", AI);
    assert.strictEqual(env.playing().length, 0);
});

await test("room disconnect removes every AI audio element; a reconnect (new Room) plays only the new subscription", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    const first = env.room();
    first.emit("trackSubscribed", env.audioTrack("TR_1"), {}, AI);
    first.emit("disconnected");
    assert.strictEqual(env.playing().length, 0);
    env.timers.forEach((t) => t.fn());                                     // the bounded reconnect fires
    await flush();
    assert.strictEqual(env.rooms.length, 2);
    first.emit("trackSubscribed", env.audioTrack("OLD"), {}, AI);           // a straggler from the dead room
    assert.strictEqual(env.playing().length, 0);
    env.room().emit("trackSubscribed", env.audioTrack("TR_2"), {}, AI);
    assert.strictEqual(env.playing().length, 1);
    assert.strictEqual(env.Avatar.isConnected(), true);
});

await test("mute applies to the current element AND to elements created later; unmute restores both", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    env.room().emit("trackSubscribed", env.audioTrack("TR_1"), {}, AI);
    env.Avatar.setRemoteAudioMuted(true);
    assert.strictEqual(env.playing()[0].muted, true);
    env.room().emit("trackSubscribed", env.audioTrack("TR_2"), {}, AI);
    assert.strictEqual(env.playing()[0].muted, true);
    env.Avatar.setRemoteAudioMuted(false);
    assert.strictEqual(env.playing()[0].muted, false);
});

await test("server-audio data messages are forwarded once; other topics/malformed/stale-room data are ignored", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    const old = env.room();
    env.data({ channel: "server-audio", state: "started", seq: 1 });
    env.data({ channel: "server-audio", state: "started", seq: 2 }, "some.other.topic");
    old.emit("dataReceived", new TextEncoder().encode("not json"), AI, 0, "jobpilot.speech");
    assert.strictEqual(env.speech.length, 1);
    assert.strictEqual(env.speech[0].seq, 1);
});

await test("cleanup: every AI element removed, room disconnected, connection state cleared, no reconnect afterwards", async () => {
    const env = makeAvatarEnv(); await env.Avatar.connect();
    env.room().emit("trackSubscribed", env.audioTrack("TR_1"), {}, AI);
    env.Avatar.cleanup();
    assert.strictEqual(env.playing().length, 0);
    assert.strictEqual(env.rooms[0].disconnects, 1);
    assert.strictEqual(env.Avatar.isConnected(), false);
    env.timers.forEach((t) => t.fn()); await flush();
    assert.strictEqual(env.rooms.length, 1);
});

// ================================================================ source-level guarantees
await test("the controller opens the microphone through ONE guarded path only (no second, independent getUserMedia path added by Task 6)", async () => {
    const src = fs.readFileSync(path.join(JS, "interview-voice-v2.js"), "utf8");
    // Exactly one function acquires the mic at all -- still true after the device-check-lobby
    // preferred-device fallback was added (that fallback calls getUserMedia a SECOND time, but
    // only inside this SAME single-flight-guarded function, as a same-path retry when the
    // lobby-picked device was unplugged, never a second independent acquisition path elsewhere).
    assert.strictEqual((src.match(/function ensureMicStream\(/g) || []).length, 1);
    const fnStart = src.indexOf("function ensureMicStream(");
    const fnBody = src.slice(fnStart, src.indexOf("\n    function ", fnStart + 1));
    assert.strictEqual((fnBody.match(/getUserMedia\(/g) || []).length, 2);
    assert.strictEqual((src.match(/getUserMedia\(/g) || []).length, 2);
    assert.ok(!/gemini/i.test(src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "")));
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
clearInterval(watchdog);
process.exit(failed.length > 0 ? 1 : 0);

})();

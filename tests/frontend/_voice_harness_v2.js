// ============================================================
// JOBPILOT AI - SHARED FAKE-BROWSER HARNESS for the interview-voice-v2.js controller tests
// (extracted in Task 6 from the Task 4 controller tests; used by both suites)
//
// Runs the REAL controller source inside a deterministic fake browser
// (vm sandbox): a controllable clock + timers, a mic whose RMS level the
// test dials, MediaRecorder, AudioContext, Audio, fetch and getUserMedia
// fakes that record every call. Nothing here is mocked at the controller
// level -- only the browser APIs it calls. Run:
//   node tests/frontend/test_interview_voice_controller_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const JS_DIR = path.join(__dirname, "..", "..", "frontend", "js");
const LOGIC_SRC = fs.readFileSync(path.join(JS_DIR, "interview-voice-turn-logic-v2.js"), "utf8");
const CONTROLLER_SRC = fs.readFileSync(path.join(JS_DIR, "interview-voice-v2.js"), "utf8");

const flush = () => new Promise((resolve) => setImmediate(resolve));

// ------------------------------------------------------------------ fake browser
function makeEnv(options = {}) {

    const env = {
        now: 0, timers: [], nextTimerId: 1,
        level: 0,                       // the mic RMS the analyser reports right now
        recordBytes: 4000,              // size of each recording the fake MediaRecorder produces
        gumCalls: 0, gumRejects: false, gumHold: null,
        streams: [], recorders: [], audios: [], fetches: [], revoked: 0, audioContextsClosed: 0,
        speechRecognitionsCreated: 0,
        routes: [],                     // [(url) => handler]
        roomConnected: options.roomConnected !== false, muteLog: [],
        states: [], errors: [], events: [], voiceTurns: [], completions: 0, levels: [],
    };

    // ---- clock + timers
    const addTimer = (fn, ms, repeat) => { const t = { id: env.nextTimerId++, fn, ms, due: env.now + ms, repeat }; env.timers.push(t); return t.id; };
    env.setInterval = (fn, ms) => addTimer(fn, ms, true);
    env.setTimeout = (fn, ms) => addTimer(fn, ms, false);
    env.clear = (id) => { env.timers = env.timers.filter((t) => t.id !== id); };
    env.activeTimers = () => env.timers.length;
    env.advance = async (ms) => {
        const target = env.now + ms;
        for (;;) {
            const due = env.timers.filter((t) => t.due <= target).sort((a, b) => a.due - b.due)[0];
            if (!due) break;
            env.now = due.due;
            if (due.repeat) due.due += due.ms; else env.timers = env.timers.filter((t) => t !== due);
            due.fn();
            await flush();
        }
        env.now = target;
        await flush();
    };

    // ---- audio graph
    class FakeAnalyser {
        constructor() { this.fftSize = 1024; }
        getFloatTimeDomainData(buffer) { buffer.fill(env.level); }
    }
    class FakeAudioContext {
        constructor() { this.state = "running"; }
        resume() { return Promise.resolve(); }
        createMediaStreamSource() { return { connect() {} }; }
        createAnalyser() { return new FakeAnalyser(); }
        close() { env.audioContextsClosed++; return Promise.resolve(); }
    }

    // ---- microphone
    const makeStream = () => {
        const track = { stopped: false, onended: null, stop() { this.stopped = true; } };
        const stream = { tracks: [track], getTracks() { return this.tracks; }, getAudioTracks() { return this.tracks; } };
        env.streams.push(stream);
        return stream;
    };
    const navigator = {
        mediaDevices: {
            getUserMedia: () => {
                env.gumCalls++;
                if (env.gumRejects) return Promise.reject(new Error("NotAllowedError"));
                if (env.gumHold) return env.gumHold.then(() => makeStream());
                return Promise.resolve(makeStream());
            },
        },
    };

    // ---- recorder / blobs
    class FakeBlob {
        constructor(parts = [], opts = {}) {
            this.size = parts.reduce((n, p) => n + (p && p.size !== undefined ? p.size : (p && p.length) || 0), 0);
            this.type = opts.type || "";
        }
    }
    class FakeMediaRecorder {
        static isTypeSupported() { return true; }
        constructor(stream, opts) { this.stream = stream; this.mimeType = (opts && opts.mimeType) || "audio/webm"; this.state = "inactive"; env.recorders.push(this); }
        start() { this.state = "recording"; }
        stop() {
            if (this.state !== "recording") return;
            this.state = "inactive";
            this.ondataavailable && this.ondataavailable({ data: { size: env.recordBytes } });
            Promise.resolve().then(() => this.onstop && this.onstop());
        }
    }

    // ---- AI audio
    class FakeAudio {
        constructor(url) { this.url = url; this.playing = false; this.paused = false; env.audios.push(this); }
        play() { this.playing = true; return Promise.resolve(); }
        pause() { this.paused = true; this.playing = false; }
        addEventListener() {}
        removeEventListener() {}
        finish() { this.playing = false; this.onended && this.onended(); }
    }

    // ---- fetch (with real AbortSignal semantics)
    env.fetch = (url, opts = {}) => {
        const call = { url, opts, signal: opts.signal };
        env.fetches.push(call);
        return new Promise((resolve, reject) => {
            call.resolve = (spec) => resolve({
                ok: spec.status >= 200 && spec.status < 300, status: spec.status,
                headers: { get: (name) => ((spec.headers || {})[String(name).toLowerCase()]) || null },
                json: () => (spec.json === undefined ? Promise.reject(new Error("no json")) : Promise.resolve(spec.json)),
                blob: () => Promise.resolve(new FakeBlob([{ size: 9000 }], { type: "audio/wav" })),
            });
            call.reject = reject;
            if (opts.signal) opts.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); });
            const auto = env.routes.map((r) => r(url, opts)).find(Boolean);
            if (auto) Promise.resolve().then(() => call.resolve(auto));
        });
    };
    env.pending = (fragment) => env.fetches.filter((f) => f.url.includes(fragment));

    // ---- sandbox
    const window = {
        AudioContext: FakeAudioContext,
        MediaRecorder: FakeMediaRecorder,
        SpeechRecognition: options.withSpeechRecognition ? function () { env.speechRecognitionsCreated++; return { start() {}, stop() {} }; } : undefined,
    };
    const sandbox = {
        window, navigator, performance: { now: () => env.now },
        setInterval: env.setInterval, clearInterval: env.clear, setTimeout: env.setTimeout, clearTimeout: env.clear,
        fetch: env.fetch, AbortController, FormData: class { constructor() { this.fields = []; } append(k, v, n) { this.fields.push([k, v, n]); } },
        Blob: FakeBlob, Audio: FakeAudio, MediaRecorder: FakeMediaRecorder,   // bare globals, exactly as in a browser
        Float32Array, Uint8Array, atob: (s) => Buffer.from(s, "base64").toString("binary"),
        URL: { createObjectURL: () => "blob:fake", revokeObjectURL: () => { env.revoked++; } },
        cancelAnimationFrame() {}, console,
    };
    window.window = window;
    vm.createContext(sandbox);
    vm.runInContext(LOGIC_SRC, sandbox);
    vm.runInContext(CONTROLLER_SRC, sandbox);

    const Voice = window.InterviewVoiceV2;
    const S = Voice.STATES;

    Voice.init({
        apiBaseUrl: "http://api", applicationId: 41,
        onStateChange: (s) => env.states.push(s),
        onError: (kind, detail) => env.errors.push(kind),
        onEvent: (name) => env.events.push(name),
        onVoiceTurn: (data, meta) => env.voiceTurns.push([data, meta]),
        onVoiceTurnComplete: () => { env.completions++; },
        onLevel: (l) => env.levels.push(l),
        allowBrowserStt: options.allowBrowserStt === true,
        useVoiceTurn: options.useVoiceTurn !== false,
        transport: options.transport,
        isRoomConnected: options.transport ? () => env.roomConnected : undefined,
        onRemoteAudioMute: options.transport ? (muted) => { env.muteLog.push(muted); } : undefined,
    });

    env.Voice = Voice;
    env.window = window;
    env.S = S;
    env.state = () => env.states[env.states.length - 1];
    env.recording = () => env.recorders.filter((r) => r.state === "recording").length;
    env.turnFetches = () => env.pending("/voice/turn");
    return env;
}

// route helpers
const speakRoute = (url) => (url.includes("/voice/speak") ? { status: 200, json: {} } : null);
const okTurn = (overrides = {}) => ({
    status: 200,
    json: Object.assign({
        transcript: "I built a REST API.", response_text: "How did you secure it?", interview_complete: false,
        session: { current_question: { turn_number: 2, question: "How did you secure it?" }, progress: [] },
        audio: { base64: Buffer.from("RIFFfakewav").toString("base64"), mime_type: "audio/wav", sample_rate: 24000, seconds: 1.2 },
        tts_error: null, providers: { stt: "faster-whisper", brain: "groq", tts: "kokoro" }, latency: {},
    }, overrides),
});

// drive one full candidate utterance: speak `speakMs` loud, then go quiet long enough to end the turn
async function candidateSpeaks(env, speakMs = 1000) {
    env.level = 0.1; await env.advance(speakMs);
    env.level = 0.0; await env.advance(1300);
}

async function startListeningEnv(env) {
    await env.Voice.ensureMicStream();
    env.Voice.startListening();
    await flush();
}


module.exports = { makeEnv, speakRoute, okTurn, candidateSpeaks, startListeningEnv, flush };

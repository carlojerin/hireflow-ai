// ============================================================
// JOBPILOT AI - CONTROLLER TESTS FOR interview-voice-v2.js (Task 4)
//
// Runs the REAL controller source inside a deterministic fake browser
// (vm sandbox, see _voice_harness_v2.js). Nothing here is mocked at the
// controller level -- only the browser APIs it calls. Run:
//   node tests/frontend/test_interview_voice_controller_v2.js
// ============================================================

const assert = require("assert");
const { makeEnv, speakRoute, okTurn, candidateSpeaks, startListeningEnv, flush } = require("./_voice_harness_v2.js");

const results = [];
let currentTest = null;
const watchdog = setInterval(() => {}, 1000);                  // keeps the loop alive while a test awaits
setTimeout(() => { process.stdout.write(`
HUNG in test: ${currentTest}
`); process.exit(2); }, 120000).unref();
async function test(name, fn) {
    currentTest = name;
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}


(async () => {

// ---------------------------------------------------------------- mic stream
await test("one mic stream is shared by concurrent callers and reused for the whole interview", async () => {
    const env = makeEnv();
    let release; env.gumHold = new Promise((r) => { release = r; });
    const a = env.Voice.ensureMicStream(), b = env.Voice.ensureMicStream(), c = env.Voice.ensureMicStream();
    release(); env.gumHold = null;
    const [sa, sb, sc] = await Promise.all([a, b, c]);
    assert.strictEqual(env.gumCalls, 1);
    assert.ok(sa === sb && sb === sc);
    env.routes.push(speakRoute);
    env.Voice.speak(1); await flush();                           // resolves only when the audio ends
    for (let i = 0; i < 3; i++) { env.audios[env.audios.length - 1].finish(); await flush(); env.Voice.stopListening(); }
    await env.Voice.ensureMicStream();
    assert.strictEqual(env.gumCalls, 1);                       // never re-requested per turn
    assert.strictEqual(env.streams.length, 1);
});

await test("a permission prompt that resolves AFTER cleanup does not leak a live microphone", async () => {
    const env = makeEnv();
    let release; env.gumHold = new Promise((r) => { release = r; });
    const pending = env.Voice.ensureMicStream();
    env.Voice.cleanup();
    release(); env.gumHold = null;
    assert.strictEqual(await pending, null);
    assert.ok(env.streams.every((s) => s.tracks.every((t) => t.stopped)));
});

await test("microphone denied -> clear recoverable error, no recorder", async () => {
    const env = makeEnv(); env.gumRejects = true;
    env.Voice.startListening(); await flush();
    assert.deepStrictEqual(env.errors, ["mic_permission_denied"]);
    assert.strictEqual(env.state(), env.S.ERROR);
    assert.strictEqual(env.recorders.length, 0);
});

await test("mic revoked/unplugged mid-interview stops everything and reports it", async () => {
    const env = makeEnv(); env.routes.push(speakRoute);
    await startListeningEnv(env);
    assert.strictEqual(env.recording(), 1);
    env.streams[0].tracks[0].onended();                         // the browser ended the track
    await flush();
    assert.ok(env.errors.includes("mic_lost"));
    assert.strictEqual(env.state(), env.S.ERROR);
    assert.ok(env.streams[0].tracks[0].stopped);
    assert.strictEqual(env.activeTimers(), 0);
});

// ---------------------------------------------------------------- local-only capture
await test("browser SpeechRecognition (cloud) is NOT used by default; local recorder is", async () => {
    const env = makeEnv({ withSpeechRecognition: true });
    await startListeningEnv(env);
    assert.strictEqual(env.speechRecognitionsCreated, 0);
    assert.strictEqual(env.recording(), 1);
    const opt = makeEnv({ withSpeechRecognition: true, allowBrowserStt: true });
    await startListeningEnv(opt);
    assert.strictEqual(opt.speechRecognitionsCreated, 1);       // only on explicit opt-in
});

// ---------------------------------------------------------------- speech detection
await test("speech onset -> CANDIDATE_SPEAKING; silence ends the turn and sends ONE voice-turn request", async () => {
    const env = makeEnv();                                        // the /voice/turn request stays pending: we assert on the request itself
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(300);
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);
    await env.advance(700);
    env.level = 0; await env.advance(1300);
    assert.strictEqual(env.turnFetches().length, 1);
    const call = env.turnFetches()[0];
    assert.ok(call.url.endsWith("/api/assessment/sessions/41/interview/v2/voice/turn"));
    assert.strictEqual(call.opts.method, "POST"); assert.strictEqual(call.opts.credentials, "include");
    assert.strictEqual(call.opts.body.fields[0][0], "audio");
    assert.strictEqual(env.state(), env.S.AI_THINKING);
    assert.strictEqual(env.recording(), 0);
});

await test("a click/cough (below minimum speech) is never sent and listening simply continues", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    await candidateSpeaks(env, 200);
    assert.strictEqual(env.turnFetches().length, 0);
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.recording(), 1);                     // a fresh recorder is already listening again
    assert.ok(env.errors.length === 0);
});

await test("an empty recording is not sent and is reported", async () => {
    const env = makeEnv(); env.recordBytes = 0;
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000);
    assert.strictEqual(env.turnFetches().length, 0);
    assert.deepStrictEqual(env.errors, ["empty_transcript"]);
});

await test("maximum utterance duration stops a never-ending answer and still sends it", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    env.level = 0.1;
    await env.advance(119000);
    assert.strictEqual(env.turnFetches().length, 0);
    await env.advance(2000);
    assert.strictEqual(env.turnFetches().length, 1);
    assert.strictEqual(env.recording(), 0);
});

await test("nobody speaks: recording is abandoned after the no-speech window, nothing sent, no restart loop", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    env.level = 0.0; await env.advance(91000);
    assert.strictEqual(env.turnFetches().length, 0);
    assert.ok(env.errors.includes("no_speech"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.recording(), 0);
    assert.strictEqual(env.recorders.length, 1);                // no runaway re-recording
    assert.strictEqual(env.activeTimers(), 0);
});

// ---------------------------------------------------------------- the automatic loop
await test("AI finishes speaking -> automatically LISTENING and recording (no click)", async () => {
    const env = makeEnv(); env.routes.push(speakRoute);
    await env.Voice.ensureMicStream();
    const speaking = env.Voice.speak(1); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    assert.strictEqual(env.recording(), 0);
    env.audios[0].finish(); await speaking; await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.recording(), 1);
});

await test("full loop: candidate speaks -> AI reply plays -> automatically listening again", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000);
    await flush();
    assert.strictEqual(env.voiceTurns.length, 1);
    assert.strictEqual(env.voiceTurns[0][0].transcript, "I built a REST API.");
    assert.strictEqual(env.voiceTurns[0][1].stale, false);
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);          // Kokoro reply audio is playing
    assert.strictEqual(env.audios.length, 1);
    env.audios[0].finish(); await flush();
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.recording(), 1);                      // listening for the next answer, no click
    assert.strictEqual(env.gumCalls, 1);
});

// ---------------------------------------------------------------- barge-in
await test("candidate interrupts AI speech: audio is paused immediately and a new recording starts", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000); await flush();
    const ai = env.audios[0];
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    env.level = 0.1; await env.advance(350);                      // loud + longer than the barge-in trigger
    assert.ok(ai.paused && !ai.playing);
    assert.ok(env.events.includes("AI_INTERRUPTED"));
    assert.strictEqual(env.state(), env.S.CANDIDATE_SPEAKING);
    assert.strictEqual(env.recording(), 1);
});

await test("an interrupted AI clip that still fires 'ended' cannot restart listening or move state", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000); await flush();
    const ai = env.audios[0];
    env.level = 0.1; await env.advance(350);
    const recordersBefore = env.recorders.length, stateBefore = env.state();
    ai.finish(); await flush();                                   // stale callback from the cancelled playback
    assert.strictEqual(env.state(), stateBefore);
    assert.strictEqual(env.recorders.length, recordersBefore);
});

await test("after an interruption the next utterance goes through the SAME turn endpoint", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000); await flush();
    env.level = 0.1; await env.advance(1500);
    env.level = 0; await env.advance(1300); await flush();
    assert.strictEqual(env.turnFetches().length, 2);
    assert.strictEqual(env.audios.length, 2);                     // the second reply is playing
    assert.strictEqual(env.audios[0].paused, true);
});

await test("speaker echo protection: a moderate level does not cut the AI off", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000); await flush();
    env.level = 0.03; await env.advance(2000);                    // above normal VAD, below barge-in
    assert.strictEqual(env.audios[0].paused, false);
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
});

await test("interrupting while the AI audio is still being fetched aborts that request", async () => {
    const env = makeEnv();
    await env.Voice.ensureMicStream();
    env.Voice.speak(1); await flush();                            // /voice/speak pending (no route)
    const call = env.pending("/voice/speak")[0];
    assert.strictEqual(call.signal.aborted, false);
    env.level = 0.1; await env.advance(350);
    assert.strictEqual(call.signal.aborted, true);
    assert.ok(env.events.includes("AI_INTERRUPTED"));
});

// ---------------------------------------------------------------- stale + duplicate protection
await test("stale response: voice mode turned off mid-turn -> text is applied, audio is NOT played", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000);
    const call = env.turnFetches()[0];
    env.Voice.stopListening();                                    // candidate switched voice mode off
    call.resolve(okTurn());
    await flush();
    assert.strictEqual(env.voiceTurns.length, 1);
    assert.strictEqual(env.voiceTurns[0][1].stale, true);         // the interview DID advance server-side: UI still learns it
    assert.strictEqual(env.audios.length, 0);                     // but nothing is played over the candidate
});

await test("leaving the page mid-turn aborts the request and reports nothing", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000);
    const call = env.turnFetches()[0];
    env.Voice.cleanup(); await flush();
    assert.strictEqual(call.signal.aborted, true);
    assert.strictEqual(env.voiceTurns.length, 0);
    assert.strictEqual(env.errors.length, 0);
    assert.strictEqual(env.audios.length, 0);
});

await test("duplicate submission is impossible: a second stop for the same utterance sends nothing", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    await candidateSpeaks(env, 1000);
    assert.strictEqual(env.turnFetches().length, 1);
    env.recorders[0].onstop(); await flush();                     // a duplicated recorder 'stop' event
    env.recorders[0].onstop(); await flush();
    assert.strictEqual(env.turnFetches().length, 1);
});

await test("never more than one recorder and one VAD timer at any time across several turns", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env);
    for (let turn = 0; turn < 3; turn++) {
        await candidateSpeaks(env, 800); await flush();
        assert.ok(env.recording() <= 1 && env.activeTimers() <= 1, `turn ${turn}`);
        env.audios[env.audios.length - 1].finish(); await flush();
        assert.ok(env.recording() <= 1 && env.activeTimers() <= 1, `after audio ${turn}`);
    }
    assert.strictEqual(env.gumCalls, 1);
});

// ---------------------------------------------------------------- error handling
await test("STT 'no speech' (422) returns to listening and never advances", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? { status: 422, json: { detail: { stage: "stt", message: "No speech was detected." } } } : null));
    await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
    assert.deepStrictEqual(env.errors, ["empty_transcript"]);
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.voiceTurns.length, 0);
});

await test("STT failure (502) and brain failure show distinct recoverable errors", async () => {
    for (const [stage, kind] of [["stt", "stt_failed"], ["brain", "brain_failed"]]) {
        const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? { status: 502, json: { detail: { stage, message: "safe message" } } } : null));
        await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
        assert.deepStrictEqual(env.errors, [kind]);
        assert.strictEqual(env.state(), env.S.ERROR);
        assert.strictEqual(env.audios.length, 0);
    }
});

await test("network failure is a recoverable error, not a crash, and releases the in-flight lock", async () => {
    const env = makeEnv();
    await startListeningEnv(env); await candidateSpeaks(env, 1000);
    env.turnFetches()[0].reject(new TypeError("Failed to fetch")); await flush();
    assert.deepStrictEqual(env.errors, ["network_error"]);
    assert.strictEqual(env.state(), env.S.ERROR);
    // the lock is released: a retry is possible
    env.Voice.startListening(); await flush();
    await candidateSpeaks(env, 1000);
    assert.strictEqual(env.turnFetches().length, 2);
});

await test("interview already ended (409/410) -> session_ended + a resync event", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? { status: 410, json: { detail: { stage: "session", message: "expired" } } } : null));
    await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
    assert.deepStrictEqual(env.errors, ["session_ended"]);
    assert.ok(env.events.includes("VOICE_SESSION_INVALID"));
});

await test("TTS failure: reply text is delivered, no audio is claimed, listening is NOT auto-started over the error", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn({ audio: null, tts_error: "voice unavailable" }) : null));
    await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
    assert.strictEqual(env.voiceTurns.length, 1);
    assert.strictEqual(env.audios.length, 0);
    assert.deepStrictEqual(env.errors, ["tts_failed"]);
    assert.strictEqual(env.state(), env.S.CANDIDATE_LISTENING);
    assert.strictEqual(env.recording(), 0);
});

// ---------------------------------------------------------------- completion + cleanup
await test("interview completion: closing message plays uninterrupted, then COMPLETED, no re-listen", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn({ interview_complete: true, session: { current_question: null } }) : null));
    await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
    assert.strictEqual(env.state(), env.S.AI_SPEAKING);
    env.level = 0.2; await env.advance(1000);                     // even loud noise cannot barge in on the closing message
    assert.strictEqual(env.audios[0].paused, false);
    env.level = 0; env.audios[0].finish(); await flush();
    assert.strictEqual(env.state(), env.S.COMPLETED);
    assert.strictEqual(env.completions, 1);
    assert.strictEqual(env.recording(), 0);
});

await test("cleanup releases everything: mic tracks, audio context, playback, timers, state", async () => {
    const env = makeEnv(); env.routes.push((u) => (u.includes("/voice/turn") ? okTurn() : null));
    await startListeningEnv(env); await candidateSpeaks(env, 1000); await flush();
    const ai = env.audios[0];
    env.Voice.cleanup(); await flush();
    assert.ok(env.streams.every((s) => s.tracks.every((t) => t.stopped)));
    assert.ok(env.audioContextsClosed >= 1);
    assert.ok(ai.paused);
    assert.strictEqual(env.activeTimers(), 0);
    assert.strictEqual(env.state(), env.S.IDLE);
    env.level = 0.2; await env.advance(1000);                     // nothing reacts to the mic any more
    assert.strictEqual(env.recorders.filter((r) => r.state === "recording").length, 0);
});

await test("the mic level meter is fed while monitoring and drops to zero when it stops", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(200);
    assert.ok(env.levels.some((l) => l > 0.3));
    env.Voice.cleanup();
    assert.strictEqual(env.levels[env.levels.length - 1], 0);
});

await test("legacy path stays available: useVoiceTurn=false sends audio to /voice/transcribe instead", async () => {
    const env = makeEnv({ useVoiceTurn: false });
    await startListeningEnv(env); await candidateSpeaks(env, 1000);
    assert.strictEqual(env.pending("/voice/transcribe").length, 1);
    assert.strictEqual(env.turnFetches().length, 0);
});


results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
clearInterval(watchdog);
process.exit(failed.length > 0 ? 1 : 0);

})();

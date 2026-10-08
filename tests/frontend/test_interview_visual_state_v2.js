// ============================================================
// JOBPILOT AI - FIXED INTERVIEWER IMAGE TESTS (Task 8)
//
// Part 1: the pure state -> visual mapping and its render helper.
// Part 2: the REAL voice controller (shared fake browser) driving the visual state through a
//         whole interview: opening, listening, candidate speaking, thinking, follow-up, interruption,
//         completion -- the image state follows the EXISTING controller, nothing else.
// Part 3: source-level guarantees (static image, no Tavus in the normal flow, honest wording).
// Run: node tests/frontend/test_interview_visual_state_v2.js
// ============================================================

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");
// (frontend/package.json makes .js files ESM, so -- like every other suite -- run the real source in a vm sandbox)
const visualSandbox = { window: {}, console };
visualSandbox.window.window = visualSandbox.window;
vm.createContext(visualSandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "interview-visual-state-v2.js"), "utf8"), visualSandbox);
const Visual = visualSandbox.window.InterviewVisualStateV2;
const { makeEnv, okTurn, startListeningEnv, flush } = require("./_voice_harness_v2.js");

const ROOT = path.join(__dirname, "..", "..", "frontend");
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");
const strip = (src) => src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");

const results = [];
async function test(name, fn) {
    try { await fn(); results.push([name, "PASS", null]); }
    catch (error) { results.push([name, "FAIL", error && error.stack ? error.stack : String(error)]); }
}

const K = Visual.VISUAL_KEYS;
const keysOf = (states) => states.map((s) => Visual.viewFor(s).key);
const collapse = (keys) => keys.filter((k, i) => i === 0 || k !== keys[i - 1]);
const msg = (state, seq) => ({ channel: "server-audio", state, seq });

function fakeEls() {
    const attrs = {};
    const cls = new Set();
    return {
        attrs, cls,
        frame: { setAttribute: (k, v) => { attrs[k] = v; } },
        stateText: { textContent: "" },
        statusText: { textContent: "" },
        candidateFrame: { classList: { toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)) } },
        image: { src: null, getAttribute() { return this.src; }, setAttribute(k, v) { this.src = v; this.sets = (this.sets || 0) + 1; } },
    };
}

(async () => {

// ================================================================ Part 1: mapping + render
await test("every required state has a visual: preparing, speaking, listening, candidate speaking, thinking, error, completed", async () => {
    assert.strictEqual(Visual.viewFor("AI_PREPARING").key, K.PREPARING);
    assert.strictEqual(Visual.viewFor("AI_SPEAKING").key, K.SPEAKING);
    assert.strictEqual(Visual.viewFor("CANDIDATE_LISTENING").key, K.LISTENING);
    assert.strictEqual(Visual.viewFor("CANDIDATE_SPEAKING").key, K.CANDIDATE_SPEAKING);
    assert.strictEqual(Visual.viewFor("AI_THINKING").key, K.THINKING);
    assert.strictEqual(Visual.viewFor("ERROR").key, K.ERROR);
    assert.strictEqual(Visual.viewFor("COMPLETED").key, K.COMPLETED);
});

await test("only AI_SPEAKING is 'speaking'; every state of the real controller maps to something", async () => {
    const S = makeEnv().S;
    Object.values(S).forEach((state) => assert.ok(Visual.VIEW_BY_STATE[state], `unmapped controller state ${state}`));
    const speaking = Object.keys(Visual.VIEW_BY_STATE).filter((s) => Visual.VIEW_BY_STATE[s].key === K.SPEAKING);
    assert.deepStrictEqual(speaking, ["AI_SPEAKING"]);
});

await test("unknown/undefined states fall back to idle (never throw, never 'speaking')", async () => {
    assert.strictEqual(Visual.viewFor("SOMETHING_NEW").key, K.IDLE);
    assert.strictEqual(Visual.viewFor(undefined).key, K.IDLE);
});

await test("labels are honest: states, never 'live avatar' or 'video'", async () => {
    const labels = Object.values(Visual.VIEW_BY_STATE).map((v) => v.label.toLowerCase());
    labels.forEach((l) => assert.ok(!/avatar|live|video/.test(l), l));
    assert.ok(labels.includes("speaking") && labels.includes("listening") && labels.includes("thinking"));
});

await test("render: sets data-state, the label, the status text, the candidate highlight and the FIXED image once", async () => {
    const els = fakeEls();
    Visual.render("AI_SPEAKING", els);
    assert.strictEqual(els.attrs["data-state"], "speaking");
    assert.strictEqual(els.stateText.textContent, "Speaking");
    assert.strictEqual(els.statusText.textContent, "AI Interviewer · Speaking");
    assert.strictEqual(els.image.src, Visual.INTERVIEWER_IMAGE_SRC);
    Visual.render("CANDIDATE_SPEAKING", els);
    assert.strictEqual(els.attrs["data-state"], "candidate-speaking");
    assert.ok(els.cls.has("is-speaking"));                                    // the CANDIDATE's frame lights up, not the AI's
    Visual.render("AI_THINKING", els);
    assert.ok(!els.cls.has("is-speaking"));
    assert.strictEqual(els.image.sets, 1);                                    // the image is set once, never swapped
});

await test("render tolerates missing elements (no DOM, image-less page)", async () => {
    assert.doesNotThrow(() => Visual.render("AI_SPEAKING", {}));
    assert.doesNotThrow(() => Visual.render("AI_SPEAKING"));
});

// ================================================================ Part 2: real controller drives the visual state
await test("LOCAL playback: preparing -> speaking (only while real audio plays) -> listening -> candidate speaking -> thinking -> speaking again", async () => {
    const env = makeEnv();
    env.routes.push((url) => (url.includes("/voice/speak") ? { status: 200, json: {} } : null));
    env.Voice.speak(1); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);          // the opening question is playing (Kokoro audio)
    assert.strictEqual(env.audios.length, 1);
    env.audios[0].finish(); await flush();                                    // audio ends
    assert.strictEqual(Visual.viewFor(env.state()).key, K.LISTENING);         // speaking indicator is gone
    await env.Voice.ensureMicStream();
    env.Voice.startListening(); await flush();
    env.level = 0.1; await env.advance(1000);
    assert.strictEqual(Visual.viewFor(env.state()).key, K.CANDIDATE_SPEAKING);
    env.level = 0.0; await env.advance(1300);
    assert.strictEqual(Visual.viewFor(env.state()).key, K.THINKING);          // /voice/turn in flight: faster-whisper + Groq
    const call = env.turnFetches()[0];
    call.resolve(okTurn()); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);          // the follow-up (Kokoro) is playing
    env.audios[env.audios.length - 1].finish(); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.LISTENING);
    const seq = collapse(keysOf(env.states));
    assert.ok(seq.indexOf(K.SPEAKING) < seq.indexOf(K.CANDIDATE_SPEAKING) && seq.lastIndexOf(K.SPEAKING) > seq.indexOf(K.THINKING), seq.join(" > "));
});

await test("INTERRUPTION removes the speaking state immediately and the next AI turn activates it again", async () => {
    const env = makeEnv();
    await env.Voice.ensureMicStream();
    env.routes.push((url) => (url.includes("/voice/speak") ? { status: 200, json: {} } : null));
    env.Voice.speak(1); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);
    env.level = 0.2; await env.advance(700);                                  // candidate talks over the AI
    assert.notStrictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);
    assert.strictEqual(Visual.viewFor(env.state()).key, K.CANDIDATE_SPEAKING);
    const seq = keysOf(env.states);
    assert.ok(seq.includes(K.LISTENING) || seq.includes(K.CANDIDATE_SPEAKING));
    env.level = 0.0; await env.advance(1400);                                 // candidate finishes; the turn is sent
    env.turnFetches()[0].resolve(okTurn()); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);          // next AI turn: speaking again
});

await test("LIVEKIT-delivered speech: 'speaking' follows the server's real started/ended, interrupted turn is not speaking", async () => {
    const env = makeEnv({ transport: "livekit" });
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(1000); env.level = 0.0; await env.advance(1300);
    env.turnFetches()[0].resolve(okTurn({ audio: null, audio_delivery: "livekit", speech_seq: 1, livekit: { audio_seconds: 2 } })); await flush();
    assert.notStrictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);       // nothing audible yet: not shown as speaking
    env.Voice.handleRemoteSpeech(msg("started", 1)); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);
    env.Voice.handleRemoteSpeech(msg("ended", 1)); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.LISTENING);
    assert.strictEqual(env.audios.length, 0);                                 // the image never plays audio itself
});

await test("completion: the closing message ends in COMPLETED and stays completed", async () => {
    const env = makeEnv();
    await startListeningEnv(env);
    env.level = 0.1; await env.advance(1000); env.level = 0.0; await env.advance(1300);
    env.turnFetches()[0].resolve(okTurn({ interview_complete: true })); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.SPEAKING);          // the closing message is spoken
    env.audios[env.audios.length - 1].finish(); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.COMPLETED);
    assert.strictEqual(env.completions, 1);
});

await test("an error state shows the error visual (no 'speaking' left behind)", async () => {
    const env = makeEnv(); env.gumRejects = true;
    env.Voice.startListening(); await flush();
    assert.strictEqual(Visual.viewFor(env.state()).key, K.ERROR);
});

// ================================================================ Part 3: source-level guarantees
await test("HTML: fixed image + 'AI Interviewer' label, no live-avatar wording, scripts wired", async () => {
    const html = read("ai-interview-v2.html");
    assert.ok(html.includes('id="iv2InterviewerImage"') && html.includes("AI Interviewer"));
    assert.ok(html.includes("interview-visual-state-v2.js"));
    assert.ok(!/live avatar/i.test(strip(html.replace(/<!--[\s\S]*?-->/g, ""))), "the page must not call the image a live avatar");
    assert.ok(html.indexOf("interview-visual-state-v2.js") < html.indexOf("ai-interview-v2.js"));
    assert.ok(fs.existsSync(path.join(ROOT, "assets", "interviewer", "interviewer.svg")));
    assert.ok(fs.existsSync(path.join(ROOT, Visual.INTERVIEWER_IMAGE_SRC.replace("./", ""))), "the configured image file exists");
});

await test("CSS: the image itself is static (no animation/transform on it); indicators only under data-state; reduced motion respected; responsive breakpoints", async () => {
    const css = read("css/ai-interview-v2.css");
    const imageRules = css.match(/\.iv2-interviewer-image\s*\{[^}]*\}/g) || [];
    assert.ok(imageRules.length >= 1 && imageRules.every((r) => !/animation|transform|transition/.test(r)), "the interviewer image must not be animated");
    assert.ok(/\.iv2-avatar-frame\[data-state="speaking"\]\s+\.iv2-speaking-indicator\s*\{[^}]*display:\s*inline-flex/.test(css));
    assert.ok(/prefers-reduced-motion/.test(css));
    // one-frame layout (stage + settings panel side by side): collapses to a single stacked column below 991.98px,
    // with further mobile-only tweaks below 575.98px -- unlike the old design, this is TWO breakpoints, not three.
    assert.ok(/max-width:\s*991\.98px/.test(css) && /max-width:\s*575\.98px/.test(css));
    assert.ok(/object-fit:\s*cover/.test(css));
});

await test("page: the state comes from the EXISTING controller; no second state machine, no Tavus call, no getUserMedia, no browser TTS", async () => {
    const page = strip(read("js/ai-interview-v2.js"));
    assert.ok(/renderInterviewerState\(state\)/.test(page));                                   // driven by handleVoiceStateChange
    assert.ok(!/speechSynthesis|SpeechSynthesisUtterance/.test(page));                          // no browser TTS
    assert.ok(!/getUserMedia\s*\(/.test(page));                                                 // one camera/mic stream only (owned by the existing layers)
    assert.ok(!/tavusapi|TAVUS_API_KEY/i.test(page));
    const visual = strip(read("js/interview-visual-state-v2.js"));
    assert.ok(!/fetch\s*\(|new Audio|Audio\s*\(|setTimeout|setInterval/.test(visual));         // pure: no network, no audio, no timers
});

await test("page: after completion the controller's reset to IDLE cannot overwrite the completed look", async () => {
    const page = read("js/ai-interview-v2.js");
    assert.ok(/if \(interviewFinalized && voiceState !== "COMPLETED"\) return;/.test(page));
    const fin = page.slice(page.indexOf("async function finalizeInterviewUi"));
    assert.ok(fin.indexOf("InterviewVoiceV2.cleanup()") < fin.indexOf('renderInterviewerState("COMPLETED")'));
});

await test("page: in the default image mode the avatar 'connecting/joining' texts and video swap are suppressed", async () => {
    const page = read("js/ai-interview-v2.js");
    assert.ok(/let interviewerVisualMode = "image"/.test(page));
    assert.ok(/if \(interviewerVisualMode !== "avatar"\) return;/.test(page));                 // the media event never replaces the image
    assert.ok(/state === "disabled"/.test(page));
});

await test("avatar controller: a 'disabled' start answer means fixed-image mode -> reported once, no polling", async () => {
    const src = read("js/interview-avatar-v2.js");
    assert.ok(/body\.state === "disabled"/.test(src));
});

results.forEach(([name, status]) => process.stdout.write(`${status.padEnd(5)}  ${name}\n`));
const failed = results.filter(([, status]) => status === "FAIL");
results.forEach(([name, status, detail]) => { if (status === "FAIL") process.stdout.write(`\n--- FAIL: ${name} ---\n${detail}\n`); });
process.stdout.write("=".repeat(70) + "\n");
process.stdout.write(`${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length > 0 ? 1 : 0);

})();

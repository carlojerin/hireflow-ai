// The browser side of the role-play call, run against fake room / voice / video modules (no microphone, no LiveKit):
// the right modules are wired to the role-play routes, the microphone (not the camera) is shared, and ending the call is safe.
//   node tests/frontend/test_practical_roleplay.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const calls = [];
const fetches = [];

const nodes = {};
function node(id) {
    return (nodes[id] = nodes[id] || {
        id, style: {}, textContent: "", className: "", value: "", open: false, disabled: false, listeners: {},
        addEventListener(type, fn) { this.listeners[type] = fn; },
    });
}
const container = { querySelector: (selector) => node(selector.replace("#", "")) };

let connected = false;
const win = {
    LivekitClient: {},
    addEventListener() {},
    location: { search: "?application_id=77" },
    InterviewAvatarV2: {
        init: (config) => calls.push(["avatar.init", config.apiPath, config.applicationId]),
        connect: () => { calls.push(["avatar.connect"]); connected = true; },
        isConnected: () => connected,
        cleanup: () => calls.push(["avatar.cleanup"]),
        setRemoteAudioMuted() {},
    },
    InterviewVideoRoomV2: {
        init: () => calls.push(["room.init"]),
        handleRoomEvent() {},
        notifyCameraReady: (stream) => calls.push(["room.camera", stream.getVideoTracks().length]),
        notifyMicReady: (stream) => calls.push(["room.mic", stream.id]),
    },
    InterviewVoiceV2: {
        STATES: { AI_SPEAKING: "AI_SPEAKING", AI_PREPARING: "AI_PREPARING", CANDIDATE_LISTENING: "CANDIDATE_LISTENING", CANDIDATE_SPEAKING: "CANDIDATE_SPEAKING" },
        init: (config) => { calls.push(["voice.init", config.transport]); win.__voiceConfig = config; },
        setRealtimeMode: (on) => calls.push(["voice.realtime", on]),
        ensureMicStream: async () => ({ id: "mic-stream" }),
        handleRemoteSpeech() {}, handleTransportEvent() {}, cleanup() {},
    },
};

global.window = win;
global.MediaStream = class { getVideoTracks() { return []; } };
global.fetch = async (url, options) => {
    fetches.push([url, options && options.method, options && options.body]);
    return { ok: true, status: 200, json: async () => ({ state: "ready" }) };
};

const source = fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "practical-roleplay.js"), "utf8");
new Function("window", "fetch", "MediaStream", "document", source)(win, global.fetch, global.MediaStream, {});
const R = win.PracticalRoleplay;

const results = [];
async function test(name, fn) { try { await fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

const sample = { practical: { environment: "roleplay", brief: "You are a <b>support</b> agent. Refunds within 7 days.", caller: { name: "Mr. Kumar", role: "an upset customer" } } };

(async () => {

    await test("the panel shows the candidate's brief and the caller, with HTML escaped", async () => {
        const html = R.html(sample);
        assert.ok(html.includes("Refunds within 7 days.") && html.includes("Mr. Kumar") && html.includes("an upset customer"));
        assert.ok(html.includes("&lt;b&gt;support") && !html.includes("<b>support</b>"));
        assert.ok(html.includes("roleplayStart") && html.includes("roleplayEnd") && html.includes("roleplayTypeInput"));
    });

    await test("starting the call joins the role-play room, shares only the microphone and starts the call on the server", async () => {
        R.mount(container, sample, { applicationId: "77" });
        await node("roleplayStart").listeners.click();

        const names = calls.map((c) => c[0]);
        assert.deepStrictEqual(names.filter((n) => n.startsWith("avatar.init")), ["avatar.init"]);
        assert.deepStrictEqual(calls.find((c) => c[0] === "avatar.init").slice(1), ["roleplay/v2/avatar", "77"]);
        assert.ok(calls.some((c) => c[0] === "voice.realtime" && c[1] === true));
        assert.ok(calls.some((c) => c[0] === "room.camera" && c[1] === 0), "the camera is not sent to the room");
        assert.ok(calls.some((c) => c[0] === "room.mic" && c[1] === "mic-stream"));
        assert.ok(names.indexOf("avatar.connect") < names.indexOf("room.mic"));
        assert.ok(fetches.some((f) => f[0].endsWith("/api/assessment/sessions/77/roleplay/start") && f[1] === "POST"));
        assert.strictEqual(node("roleplayEnd").style.display, "");
        assert.ok(R.hasHeldCall());
    });

    await test("the live caption follows what the caller is saying", async () => {
        win.__voiceConfig.onEvent("REALTIME_SPEAKING_TEXT", { text: "Where is my order?" });
        assert.ok(node("roleplayCaption").textContent.includes("Where is my order?"));
        win.__voiceConfig.onEvent("REALTIME_SPEAKING_TEXT", { text: "" });
        assert.strictEqual(node("roleplayCaption").textContent, "");
    });

    await test("a call that ends from the server closes the room and tells the candidate", async () => {
        win.__voiceConfig.onVoiceTurnComplete();
        assert.ok(node("roleplayStatus").textContent.includes("The call has ended"));
        assert.ok(calls.some((c) => c[0] === "avatar.cleanup"));
        assert.strictEqual(node("roleplayEnd").style.display, "none");
    });

    await test("ending an already ended call is harmless and does not call the server again", async () => {
        const before = fetches.length;
        await R.endCall();
        assert.strictEqual(fetches.length, before);
    });

    await test("a typed reply goes to the text route", async () => {
        node("roleplayTypeInput").value = "I am sorry about the delay";
        await node("roleplayTypeSend").listeners.click();
        const typed = fetches.find((f) => f[0].endsWith("/roleplay/text"));
        assert.ok(typed && JSON.parse(typed[2]).answer_text === "I am sorry about the delay");
    });

    let failed = 0;
    for (const [name, status, detail] of results) {
        console.log(`${status}   ${name}`);
        if (detail) { failed++; console.log(detail); }
    }
    console.log("=".repeat(70));
    console.log(`${results.length - failed} passed, ${failed} failed, ${results.length} total`);
    process.exit(failed ? 1 : 0);
})();

/* =========================================================
   HIREFLOW AI - PRACTICAL ROLE-PLAY CALL (candidate side)

   The candidate is the agent on a phone call; the AI plays the customer / prospect / client. The call uses the SAME proven browser modules as the
   AI interview (interview-avatar-v2.js: the LiveKit room and the AI's voice; interview-video-room-v2.js: publishing the microphone;
   interview-voice-v2.js: the microphone stream, barge-in and the live captions), pointed at the role-play routes (/roleplay/v2/avatar/*).
   The camera is NOT sent to the room: it stays with the proctoring.

   Used by practical-environment.js (environment "roleplay") on the stage-3 page (sjt-test.html). The page's own timer, proctoring, submit and
   result code are unchanged: submitting calls PracticalRoleplay.endCall() first so the transcript is final.
   ========================================================= */

window.PracticalRoleplay = (function () {

    "use strict";

    const API = "http://127.0.0.1:8000";

    let applicationId = null;
    let container = null;
    let started = false;
    let ended = false;
    let spokeAny = false;
    let csrfHeaders = {};

    function el(id) { return container ? container.querySelector("#" + id) : null; }

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    // ---------------------------------------------------------------- the panel
    function html(test) {

        const practical = test.practical || {};
        const caller = practical.caller || {};

        return `
            <div class="question-card practical-card roleplay-card">
                <div class="question-number">Call simulation</div>
                <div class="practical-scenario"><strong>Your role</strong><br>${esc(practical.brief || "").replace(/\n/g, "<br>")}</div>
                <div class="practical-instructions">
                    You are about to take a call from <strong>${esc(caller.name || "a caller")}</strong>${caller.role ? ` (${esc(caller.role)})` : ""}.
                    Speak naturally, as you would on the job. The call lasts a few minutes and ends by itself, or you can end it yourself.
                    Use headphones if you can, and speak clearly.
                </div>

                <div class="roleplay-controls">
                    <button type="button" class="btn btn-primary rounded-pill px-4" id="roleplayStart">
                        <i class="bi bi-telephone-fill me-1"></i> Start the call
                    </button>
                    <button type="button" class="btn btn-outline-danger rounded-pill px-4" id="roleplayEnd" style="display:none;">
                        <i class="bi bi-telephone-x-fill me-1"></i> End the call
                    </button>
                </div>

                <div class="roleplay-status" id="roleplayStatus" role="status" aria-live="polite">Press &ldquo;Start the call&rdquo; when you are ready.</div>
                <div class="roleplay-meter" id="roleplayMeter" style="display:none;"><span id="roleplayMeterBar"></span></div>
                <div class="roleplay-caption" id="roleplayCaption" aria-live="polite"></div>

                <details class="roleplay-type mt-3" id="roleplayTypeBox">
                    <summary class="small text-muted">Microphone not working? Type your reply instead</summary>
                    <div class="d-flex gap-2 mt-2">
                        <input type="text" class="form-control" id="roleplayTypeInput" maxlength="1500" placeholder="Type what you would say..." autocomplete="off">
                        <button type="button" class="btn btn-outline-primary" id="roleplayTypeSend">Send</button>
                    </div>
                </details>
            </div>`;
    }

    function setStatus(text, kind) {
        const box = el("roleplayStatus");
        if (!box) return;
        box.textContent = text;
        box.className = "roleplay-status" + (kind ? " is-" + kind : "");
    }

    function setCaption(text) {
        const box = el("roleplayCaption");
        if (box) box.textContent = text ? "“" + text + "”" : "";
    }

    // ---------------------------------------------------------------- server calls
    async function post(path, body) {
        const response = await fetch(`${API}/api/assessment/sessions/${applicationId}/roleplay/${path}`, {
            method: "POST",
            credentials: "include",
            headers: body ? { "Content-Type": "application/json" } : {},
            body: body ? JSON.stringify(body) : undefined,
        });
        let data = null;
        try { data = await response.json(); } catch (error) { /* no body */ }
        return { ok: response.ok, status: response.status, data: data || {} };
    }

    // ---------------------------------------------------------------- the call
    function finish(reason) {

        if (ended) return;
        ended = true;

        const end = el("roleplayEnd");
        if (end) end.style.display = "none";
        const meter = el("roleplayMeter");
        if (meter) meter.style.display = "none";
        setCaption("");

        setStatus(reason || "The call has ended. You can now submit your assessment.", "done");

        if (window.InterviewAvatarV2) { try { window.InterviewAvatarV2.cleanup(); } catch (error) { /* already closed */ } }
    }

    function onVoiceState(state) {

        const V = window.InterviewVoiceV2 && window.InterviewVoiceV2.STATES;
        if (!V || ended) return;

        if (state === V.AI_SPEAKING || state === V.AI_PREPARING) {
            setStatus("The caller is speaking...", "live");
        } else if (state === V.CANDIDATE_LISTENING) {
            setStatus("Your turn: speak whenever you are ready.", "live");
        } else if (state === V.CANDIDATE_SPEAKING) {
            spokeAny = true;
            setStatus("You are speaking...", "live");
        }
    }

    function onVoiceEvent(name, detail) {

        if (name === "REALTIME_SPEAKING_TEXT") setCaption((detail && detail.text) || "");
        if (name === "REALTIME_FAILED") {
            setStatus("The call connection was lost. You can type your replies below, or end the call and submit.", "error");
            const box = el("roleplayTypeBox");
            if (box) box.open = true;
        }
    }

    function level(value) {
        const bar = el("roleplayMeterBar");
        if (bar) bar.style.width = Math.max(0, Math.min(100, value)) + "%";
    }

    async function startCall() {

        if (started) return;
        started = true;

        const startButton = el("roleplayStart");
        if (startButton) startButton.disabled = true;

        const Voice = window.InterviewVoiceV2, Avatar = window.InterviewAvatarV2, Room = window.InterviewVideoRoomV2;

        if (!Voice || !Avatar || !Room || typeof window.LivekitClient === "undefined") {
            setStatus("The call could not be set up in this browser. Please reload the page, or use the typing box below.", "error");
            started = false;
            if (startButton) startButton.disabled = false;
            return;
        }

        setStatus("Connecting the call...", "live");

        Room.init({ onStateChange: function () {}, onError: function (kind, message) { setStatus(message || "Could not share your microphone with the call.", "error"); } });

        Avatar.init({
            apiBaseUrl: API,
            applicationId: applicationId,
            apiPath: "roleplay/v2/avatar",
            onStateChange: function () {},
            onSpeechMessage: function (msg) { Voice.handleRemoteSpeech(msg); },
            onAvatarStatus: function () {},
            onRoomEvent: function (name) {
                Room.handleRoomEvent(name);
                Voice.handleTransportEvent(name);
            },
        });

        Voice.init({
            apiBaseUrl: API,
            applicationId: applicationId,
            onStateChange: onVoiceState,
            onError: function (kind, detail) {
                if (kind === "mic_permission_denied") {
                    setStatus("Microphone access was denied. You can type your replies instead (below).", "error");
                    const box = el("roleplayTypeBox");
                    if (box) box.open = true;
                }
            },
            onEvent: onVoiceEvent,
            onVoiceTurn: function () {},
            onVoiceTurnComplete: function () { finish(); },
            onLevel: level,
            transport: "livekit",
            isRoomConnected: function () { return !!Avatar.isConnected(); },
            onRemoteAudioMute: function (muted) { Avatar.setRemoteAudioMuted(muted); },
        });

        Voice.setRealtimeMode(true);

        // the microphone (asked for once; a refusal leaves the typing box as the way to take the call)
        let mic = null;
        try { mic = await Voice.ensureMicStream(); } catch (error) { mic = null; }

        if (!mic) {
            setStatus("No microphone: you can still take the call by typing your replies below.", "error");
            const box = el("roleplayTypeBox");
            if (box) box.open = true;
        } else {
            const meter = el("roleplayMeter");
            if (meter) meter.style.display = "";
        }

        // join the room, then share only the microphone (an empty stream stands in for the camera, which stays with the proctoring)
        Avatar.connect();

        const waitedFrom = Date.now();
        while (!Avatar.isConnected() && Date.now() - waitedFrom < 20000) {
            await new Promise(function (resolve) { setTimeout(resolve, 250); });
        }

        if (!Avatar.isConnected()) {
            setStatus("The call could not connect. Please check your internet connection and reload the page.", "error");
            started = false;
            if (startButton) startButton.disabled = false;
            return;
        }

        Room.notifyCameraReady(new MediaStream());
        if (mic) Room.notifyMicReady(mic);

        const result = await post("start");

        if (!result.ok) {
            setStatus(result.data.detail || "The call could not start. Please try again.", "error");
            started = false;
            if (startButton) startButton.disabled = false;
            return;
        }

        if (startButton) startButton.style.display = "none";
        const end = el("roleplayEnd");
        if (end) end.style.display = "";

        setStatus("Call connected. The caller will speak first.", "live");
    }

    async function endCall() {

        if (!started || ended) return;

        await post("stop");
        finish(spokeAny ? "You ended the call. You can now submit your assessment." : "You ended the call without speaking. You can now submit.");
    }

    async function sendTyped() {

        const input = el("roleplayTypeInput");
        const text = input ? input.value.trim() : "";

        if (!text) return;

        const result = await post("text", { answer_text: text });

        if (result.ok) {
            spokeAny = true;
            input.value = "";
        } else {
            setStatus(result.data.detail || "Your reply could not be sent. Start the call first.", "error");
        }
    }

    // ---------------------------------------------------------------- mounting
    function mount(root, test, options) {

        container = root;
        applicationId = (options && options.applicationId) || new URLSearchParams(window.location.search).get("application_id");
        started = ended = spokeAny = false;

        const startButton = el("roleplayStart");
        if (startButton) startButton.addEventListener("click", startCall);
        const endButton = el("roleplayEnd");
        if (endButton) endButton.addEventListener("click", endCall);
        const sendButton = el("roleplayTypeSend");
        if (sendButton) sendButton.addEventListener("click", sendTyped);
        const input = el("roleplayTypeInput");
        if (input) input.addEventListener("keydown", function (event) { if (event.key === "Enter") { event.preventDefault(); sendTyped(); } });

        window.addEventListener("pagehide", function () {
            if (window.InterviewVoiceV2) { try { window.InterviewVoiceV2.cleanup(); } catch (error) { /* closing */ } }
        });
    }

    function hasHeldCall() { return started; }

    return { html: html, mount: mount, endCall: endCall, hasHeldCall: hasHeldCall, _esc: esc };
})();

// ============================================================
// JOBPILOT AI - AI INTERVIEW V2 REALTIME VOICE CONTROLLER
// (Task 5I foundation, Task 5J realtime turn-taking/interruption,
//  Task 4 local conversational loop)
//
// TASK 4: the loop is now fully LOCAL end to end. Speech is always
// recorded with MediaRecorder + the amplitude VAD and sent as ONE
// request to /voice/turn (faster-whisper -> the interview brain -> Kokoro); the
// browser's own SpeechRecognition is OFF by default because Chrome
// streams that audio to a cloud service (opt in with allowBrowserStt).
// One mic stream, one VAD timer, one recording and one in-flight turn
// at a time; every async result is checked against a monotonic token.
//
// Pure voice I/O. Knows NOTHING about competencies, follow-ups,
// evidence, or pass/fail. The state machine and VAD/staleness
// decisions come from interview-voice-turn-logic-v2.js (a plain,
// DOM-free module) -- THIS file only supplies the actual browser
// mechanics that pure module can't have: AudioContext sampling,
// SpeechRecognition, MediaRecorder, fetch/AbortController, and
// <audio> playback. Load interview-voice-turn-logic-v2.js BEFORE
// this file.
//
// ai-interview-v2.js (the EXISTING Task 5H candidate controller)
// still owns all INTERVIEW state (conversationTurns, submitAnswer(),
// etc.) and is NOT duplicated here. This module exposes a small
// interface -- init/startListening/stopListening/getTranscript/
// speak/stopSpeaking/cleanup -- plus lifecycle events a future
// avatar (Task 5K) can subscribe to without touching interview
// logic (onEvent in init()).
//
// TASK 5J ARCHITECTURE DECISION (Path A, not LiveKit) -- see the
// Task 5J final report for the full writeup. Summary: barge-in
// interruption and turn-taking are implemented with already-shipped
// browser APIs (Web Audio API AnalyserNode for amplitude-based VAD,
// Web Speech API's continuous+interimResults mode) with ZERO new
// dependencies and ZERO backend changes -- /voice/speak and
// /voice/transcribe (Task 5I) are reused completely unmodified.
// LiveKit was evaluated and deliberately deferred to the future
// video-room task (5L), where a WebRTC transport is needed anyway
// for video; introducing a room-server dependency purely for audio
// turn-taking here would be exactly the kind of premature
// infrastructure this task's own spec warns against. Every event
// this module emits (AI_STARTED_SPEAKING, CANDIDATE_STARTED_SPEAKING,
// etc.) is transport-agnostic, so a future LiveKit-backed
// implementation could satisfy the same interface without
// ai-interview-v2.js or the Task 5H brain changing at all.
// ============================================================

window.InterviewVoiceV2 = (function () {

    const Logic = window.InterviewVoiceTurnLogicV2;
    const STATES = Logic.STATES;
    const EVENTS = Logic.EVENTS;

    let apiBaseUrl = "";
    let applicationId = null;

    let currentState = STATES.IDLE;
    let onStateChange = null;
    let onError = null;
    let onEvent = null; // (eventName, payload) -- avatar-compat hook, Task 5J Step 17

    let lastTranscript = "";
    let lastSubmittedTurnToken = null;

    // Monotonic tokens -- every async operation (a speak() fetch, a
    // transcribe upload) is issued under the CURRENT value and
    // checked against it when it resolves. If a newer operation (an
    // interruption, a new speak() call) has since started, the
    // token no longer matches and the stale result is discarded
    // (Task 5J Step 9/10).
    let speechToken = 0;
    let listenToken = 0;

    let currentAbortController = null;
    let currentAudioEl = null;
    let currentPlaybackResolve = null;

    // Task 4: single-flight voice turn (STT + brain + TTS in one request)
    let useVoiceTurn = true;
    let allowBrowserStt = false;
    let onVoiceTurn = null;          // (data, { stale }) -- page applies transcript + AI text
    let onVoiceTurnComplete = null;  // () -- page runs its completion routine after the closing audio
    let onLevel = null;              // (0..1) -- mic activity meter
    let voiceTurnToken = 0;
    let voiceTurnAbort = null;

    // Task 6: where the AI's voice comes from. "local" = the audio returned by
    // the server plays in a local <audio> element (Task 3/4, unchanged).
    // "livekit" = the server publishes the SAME Kokoro audio into the interview
    // room and the browser only listens for the turn-boundary messages (the
    // audio itself is rendered by the room's remote-audio element). Decided
    // per turn (see _effectiveTransport) and never switched mid-turn.
    let transportPref = "local";
    let isRoomConnected = null;      // () => bool -- is the interview LiveKit room usable right now
    let onRemoteAudioMute = null;    // (muted) -- mutes/unmutes the room's AI audio element
    let turnInFlight = false;
    let utteranceStats = null;
    let discardRecording = false;
    let micGeneration = 0;           // bumped on release so a pending getUserMedia can never leak a stream
    let micStreamPromise = null;

    // Persistent mic stream -- opened ONCE (on the first user
    // gesture, e.g. toggling Voice Mode) and reused for the rest of
    // the interview, both for the barge-in VAD monitor and for
    // actual candidate capture. Re-requesting getUserMedia every
    // single turn would be needlessly fragile and, on some
    // browsers, re-prompt the candidate repeatedly.
    let micStream = null;
    let audioContext = null;
    let analyserNode = null;
    let vadRafId = null;
    let vadIntervalId = null;
    let vadTracking = null;
    let vadLastTickAt = null;
    let vadActive = false;
    let vadNoiseFloor = null;
    let vadVoiceThreshold = 0.02;

    let nativeRecognition = null;
    let usingNativeStt = false;
    let recognitionFinalTranscript = "";

    let mediaRecorder = null;
    let recordedChunks = [];


    function supportsNativeStt() {
        return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    }


    function supportsMediaRecorder() {
        return typeof window.MediaRecorder !== "undefined" && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }


    function isVoiceCaptureSupported() {
        return (allowBrowserStt && supportsNativeStt()) || supportsMediaRecorder();
    }


    function supportsBargeIn() {
        return !!(window.AudioContext || window.webkitAudioContext) && supportsMediaRecorder();
    }


    function setState(next) {
        currentState = next;
        if (typeof onStateChange === "function") {
            onStateChange(next);
        }
    }


    // Task 4: transitions of the voice-turn flow go through the pure,
    // tested table (Logic.nextState) -- an event that is not valid in the
    // current state is a no-op, never an exception.
    function transition(event) {
        const next = Logic.nextState(currentState, event);
        if (next !== currentState) setState(next);
        return next;
    }


    function emit(eventName, payload) {
        if (typeof onEvent === "function") {
            onEvent(eventName, payload || {});
        }
    }


    function reportError(kind, detail) {
        if (typeof onError === "function") {
            onError(kind, detail);
        }
    }


    function init(config) {
        apiBaseUrl = config.apiBaseUrl || "";
        applicationId = config.applicationId;
        onStateChange = config.onStateChange || null;
        onError = config.onError || null;
        onEvent = config.onEvent || null;
        onVoiceTurn = config.onVoiceTurn || null;
        onVoiceTurnComplete = config.onVoiceTurnComplete || null;
        onLevel = config.onLevel || null;
        useVoiceTurn = config.useVoiceTurn !== false;
        allowBrowserStt = config.allowBrowserStt === true;
        transportPref = config.transport === "livekit" ? "livekit" : "local";
        isRoomConnected = typeof config.isRoomConnected === "function" ? config.isRoomConnected : null;
        onRemoteAudioMute = typeof config.onRemoteAudioMute === "function" ? config.onRemoteAudioMute : null;
        lastTranscript = "";
        lastSubmittedTurnToken = null;
    }


    // ============================================================
    // PERSISTENT MIC STREAM (opened once, reused for VAD + capture)
    // ============================================================

    // Set by the pre-interview device-check lobby (device-check-lobby.js), BEFORE
    // ensureMicStream() is first called for the real interview. null = browser/OS default,
    // the same behaviour as before this existed.
    let preferredMicDeviceId = null;

    function setPreferredMicDevice(deviceId) {
        preferredMicDeviceId = deviceId || null;
    }

    async function ensureMicStream() {

        if (micStream) return micStream;

        // Single-flight: concurrent callers (voice-mode toggle + video room)
        // share ONE getUserMedia request, so two live streams can never exist.
        if (micStreamPromise) return micStreamPromise;

        const generation = micGeneration;

        micStreamPromise = (async () => {

            try {

                const audioConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
                if (preferredMicDeviceId) audioConstraints.deviceId = { exact: preferredMicDeviceId };

                let stream;
                try {
                    stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
                } catch (preferredDeviceError) {
                    // The device picked in the lobby may have been unplugged since -- fall back to
                    // the system default rather than leaving the candidate with no microphone at all.
                    if (!preferredMicDeviceId) throw preferredDeviceError;
                    delete audioConstraints.deviceId;
                    stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
                }

                if (generation !== micGeneration) {
                    // cleanup() ran while the permission prompt was open: do not keep the mic
                    stream.getTracks().forEach((track) => track.stop());
                    return null;
                }

                micStream = stream;
                stream.getAudioTracks().forEach((track) => { track.onended = _handleMicLost; });

                return micStream;

            } catch (error) {

                micStream = null;
                return null;

            } finally {

                micStreamPromise = null;
            }
        })();

        return micStreamPromise;
    }


    function releaseMicStream() {

        micGeneration++;

        if (micStream) {
            micStream.getTracks().forEach((track) => { track.onended = null; track.stop(); });
            micStream = null;
        }
    }


    // The browser ended the mic track by itself (permission revoked, device
    // unplugged): stop everything cleanly and say so.
    function _handleMicLost() {

        listenToken++;
        speechToken++;
        voiceTurnToken++;

        stopListening();
        stopSpeaking();
        stopVadMonitor();
        releaseMicStream();

        reportError("mic_lost", "The microphone was disconnected or its permission was revoked.");
        setState(STATES.ERROR);
    }


    // ============================================================
    // BARGE-IN / SILENCE MONITOR (Web Audio API amplitude VAD)
    //
    // One shared AnalyserNode drives BOTH: (a) detecting the
    // candidate interrupting AI speech, and (b) detecting the
    // candidate's own end-of-turn silence once they're talking. The
    // pure vadTick() decision function (interview-voice-turn-logic-v2.js)
    // is fed real RMS samples here and real elapsed-ms deltas -- this
    // function contains NO decision logic of its own, only sampling.
    // ============================================================

    function computeRms(analyser, buffer) {

        analyser.getFloatTimeDomainData(buffer);

        let sumSquares = 0;
        for (let i = 0; i < buffer.length; i++) {
            sumSquares += buffer[i] * buffer[i];
        }

        return Math.sqrt(sumSquares / buffer.length);
    }


    function startVadMonitor(onDecision, options) {

        if (!micStream || vadActive) return;

        const vadConfig = (options && options.config) || Logic.DEFAULT_VAD_CONFIG;
        const onSample = options && options.onSample;

        const AudioContextCtor = window.AudioContext || window.webkitAudioContext;

        try {

            audioContext = audioContext || new AudioContextCtor();

            if (audioContext.state === "suspended") {
                audioContext.resume().catch(() => { /* ignore */ });
            }

            const source = audioContext.createMediaStreamSource(micStream);
            analyserNode = audioContext.createAnalyser();
            analyserNode.fftSize = 1024;
            source.connect(analyserNode);

        } catch (error) {
            return; // VAD unavailable -- caller falls back to manual "Start Speaking"
        }

        const buffer = new Float32Array(analyserNode.fftSize);
        vadTracking = Logic.initialVadTracking();
        vadLastTickAt = performance.now();
        vadActive = true;
        vadNoiseFloor = null;
        vadVoiceThreshold = vadConfig.voiceThreshold;

        vadIntervalId = setInterval(() => {

            if (!vadActive || !analyserNode) return;

            const now = performance.now();
            const elapsedMs = now - vadLastTickAt;
            vadLastTickAt = now;

            const rms = computeRms(analyserNode, buffer);

            // adaptive: speech must be clearly above the room's own noise floor (a fixed threshold never saw "silence" in a noisy room)
            vadNoiseFloor = Logic.updateNoiseFloor(vadNoiseFloor, rms, elapsedMs);
            vadVoiceThreshold = Logic.adaptiveVoiceThreshold(vadNoiseFloor, vadConfig.voiceThreshold);

            const { tracking, decision } = Logic.vadTick(
                vadTracking, rms, elapsedMs, Object.assign({}, vadConfig, { voiceThreshold: vadVoiceThreshold }),
            );
            vadTracking = tracking;

            if (typeof onLevel === "function") onLevel(Logic.micLevel(rms));
            if (typeof onSample === "function") onSample(rms, elapsedMs);

            if (decision !== "NONE") {
                onDecision(decision);
            }

        }, 50);
    }


    function stopVadMonitor() {

        vadActive = false;

        if (vadIntervalId) {
            clearInterval(vadIntervalId);
            vadIntervalId = null;
        }

        if (vadRafId) {
            cancelAnimationFrame(vadRafId);
            vadRafId = null;
        }

        analyserNode = null;

        if (typeof onLevel === "function") onLevel(0);
    }


    // ============================================================
    // TEXT -> SPEECH (with interruption + staleness handling)
    // ============================================================

    // ============================================================
    // Task 5N-R: OPTIONAL remote speaker (the LiveKit avatar worker).
    // When the avatar is genuinely connected, question speech is
    // produced by the worker (Gemini TTS -> Tavus avatar audio+video)
    // instead of local <audio> playback, so the candidate hears ONE
    // voice, in sync with the avatar. The browser only sends a
    // turn_number; the worker reads the persisted question itself.
    // If the remote speaker never starts, speak() falls back to the
    // original local TTS path (never both -- see _speakViaRemote).
    // ============================================================
    let remoteSpeaker = null;   // { available(): bool, say(turn, seq), interrupt(seq) }
    let remoteSeq = 0;
    let remoteWaiter = null;    // { seq, turnNumber, onStarted, onEnded }
    const REMOTE_START_TIMEOUT_MS = 20000;
    const REMOTE_END_TIMEOUT_MS = 130000; // worker enforces 120s; browser never waits forever

    function setRemoteSpeaker(speaker) {
        remoteSpeaker = speaker || null;
    }

    // ============================================================
    // TASK 6: AI audio delivered through LiveKit by the server.
    //
    // The server publishes the already-synthesized Kokoro audio into the
    // room and announces each response with {channel:"server-audio",
    // state: started|ended|failed, seq}. This controller waits for the
    // messages of exactly ONE response (the seq the HTTP reply named);
    // anything for another seq is dropped. Nothing is ever ALSO played
    // locally for that response. No timer here can outlive a cancel.
    // ============================================================
    const SERVER_START_TIMEOUT_MS = 8000;       // the publisher normally starts within ~1s
    const SERVER_END_SLACK_MS = 15000;          // audio length + slack, so the browser never waits forever
    let serverWaiter = null;                    // { seq, onStarted, onEnded(failed), cancel() }
    let serverEarly = [];                       // messages that beat the HTTP reply (bounded)
    // ------------------------------------------------------------------
    // Task 9: REALTIME mode. The conversation runs on the server (a realtime speech model fed by the
    // candidate's microphone, which the video-room layer already publishes to the interview room). The
    // browser records/uploads NOTHING in this mode: it only mirrors the server's turn messages into the
    // existing states/events (so the interviewer image, transcript and integrity/recording code keep
    // working unchanged) and mutes the AI audio the instant the candidate talks over it.
    // ------------------------------------------------------------------
    let realtimeMode = false;

    function setRealtimeMode(on) {
        realtimeMode = on === true;
        if (realtimeMode) {
            // no local record/upload cycle can be left running
            try { stopListening(); } catch (e) { /* ignore */ }
        }
    }

    function isRealtimeMode() { return realtimeMode; }

    function handleRealtimeMessage(msg) {

        if (!realtimeMode || !msg) return;

        if (msg.channel === "server-audio") {

            if (msg.state === "started") {
                _muteRemote(false);                                    // (an earlier barge-in muted the element)
                const token = ++speechToken;
                setState(STATES.AI_SPEAKING);
                emit("AI_STARTED_SPEAKING", { transport: "realtime" });
                emit("AI_AUDIO_AVAILABLE", { transport: "realtime" });
                _startBargeInMonitor(token);                            // instant local mute; the server cuts the stream itself
            } else if (msg.state === "ended") {
                stopVadMonitor();
                if (msg.interrupted) {
                    emit("AI_STOPPED_SPEAKING", { interrupted: true });
                    emit("AI_INTERRUPTED", {});
                    setState(STATES.CANDIDATE_SPEAKING);
                    emit("CANDIDATE_STARTED_SPEAKING", {});
                } else {
                    emit("AI_STOPPED_SPEAKING", { interrupted: false });
                    setState(STATES.CANDIDATE_LISTENING);
                }
            } else if (msg.state === "failed") {
                stopVadMonitor();
                emit("AI_STOPPED_SPEAKING", { interrupted: false });
                setState(STATES.CANDIDATE_LISTENING);
                reportError("tts_playback_failed", "The AI interviewer's voice was interrupted — please continue.");
            }
            return;
        }

        if (msg.channel !== "realtime") return;

        if (msg.state === "candidate_speaking") {
            if (currentState !== STATES.AI_SPEAKING) {
                setState(STATES.CANDIDATE_SPEAKING);
                emit("CANDIDATE_STARTED_SPEAKING", {});
            }
        } else if (msg.state === "speaking_text") {
            emit("REALTIME_SPEAKING_TEXT", { text: msg.text || "" });
        } else if (msg.state === "turn_recorded") {
            emit("REALTIME_TURN_RECORDED", {});
        } else if (msg.state === "complete") {
            stopVadMonitor();
            setState(STATES.COMPLETED);
            if (typeof onVoiceTurnComplete === "function") onVoiceTurnComplete();
        } else if (msg.state === "error") {
            stopVadMonitor();
            setState(STATES.ERROR);
            reportError("realtime_failed", msg.message || "The realtime interview connection failed.");
            emit("REALTIME_FAILED", { message: msg.message || null });
        }
    }

    let livekitRequestPending = false;          // a /voice/speak?transport=livekit request is in flight (the server may already be publishing)
    let livekitTurnPending = false;             // a /voice/turn asked for livekit delivery and has not been answered yet

    function _effectiveTransport() {
        let connected = false;
        try { connected = typeof isRoomConnected === "function" && isRoomConnected() === true; } catch (e) { connected = false; }
        return Logic.chooseTransport(transportPref, connected);
    }

    function _muteRemote(muted) {
        if (typeof onRemoteAudioMute !== "function") return;
        try { onRemoteAudioMute(muted); } catch (e) { /* ignore */ }
    }

    // Tells the server to stop publishing NOW (and drop what is queued).
    function _requestServerInterrupt() {
        try {
            fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/interview/v2/voice/interrupt`,
                { method: "POST", credentials: "include", keepalive: true },
            ).catch(() => { /* best effort: the local mute below already silences it */ });
        } catch (e) { /* ignore */ }
    }

    function _applyServerMessage(msg) {
        if (!serverWaiter) return;
        if (msg.state === "started") serverWaiter.onStarted();
        else if (msg.state === "ended") serverWaiter.onEnded(false);
        else if (msg.state === "failed") serverWaiter.onEnded(true);
    }

    function handleServerAudioMessage(msg) {

        if (!msg || typeof msg.seq !== "number") return;

        const route = Logic.routeServerAudioMessage(msg.seq, serverWaiter ? serverWaiter.seq : null);

        if (route === "stale") return;

        if (route === "buffer") {
            serverEarly.push(msg);
            if (serverEarly.length > 20) serverEarly.shift();
            return;
        }

        _applyServerMessage(msg);
    }

    // Resolves "ended" | "no_start" | "failed" | "cancelled".
    function _awaitServerAudio(myToken, seq, audioSeconds, turnNumber) {

        return new Promise((resolve) => {

            let started = false;
            let startTimer = null;
            let endTimer = null;

            const finish = (outcome) => {
                clearTimeout(startTimer);
                clearTimeout(endTimer);
                if (serverWaiter && serverWaiter.seq === seq) serverWaiter = null;
                resolve(outcome);
            };

            startTimer = setTimeout(() => { if (!started) finish("no_start"); }, SERVER_START_TIMEOUT_MS);

            serverWaiter = {
                seq,
                onStarted: () => {
                    if (started) return;
                    started = true;
                    clearTimeout(startTimer);
                    endTimer = setTimeout(() => finish("failed"), (Number(audioSeconds) || 0) * 1000 + SERVER_END_SLACK_MS);
                    _muteRemote(false);                          // an earlier interruption muted the AI element; this response may be heard
                    if (Logic.isStaleToken(myToken, speechToken)) return;
                    setState(STATES.AI_SPEAKING);
                    emit("AI_STARTED_SPEAKING", { turnNumber, transport: "livekit" });
                    emit("AI_AUDIO_AVAILABLE", { turnNumber, transport: "livekit" });
                },
                onEnded: (failed) => finish(failed ? (started ? "failed" : "no_start") : "ended"),
                cancel: () => finish("cancelled"),
            };

            // messages that arrived before this reply was processed
            const early = serverEarly;
            serverEarly = [];
            early.forEach((m) => { if (m.seq === seq) _applyServerMessage(m); });
        });
    }

    // The tail shared with _playAudioBlob: leave the speaking state and hand the turn back.
    function _afterServerAudio(myToken, outcome, turnNumber, autoListen) {

        if (outcome === "cancelled" || Logic.isStaleToken(myToken, speechToken)) return "stale";

        stopVadMonitor();

        emit("AI_STOPPED_SPEAKING", { turnNumber, interrupted: false });
        setState(STATES.CANDIDATE_LISTENING);

        if (outcome === "no_start" || outcome === "failed") {
            _muteRemote(true);                                   // a late start must not talk over the on-screen fallback
            _requestServerInterrupt();
            reportError("tts_playback_failed", "The AI interviewer's voice did not arrive — please read the question above.");
            return "error";
        }

        if (autoListen) _autoStartListening();

        return "ended";
    }

    // Room connection health (from the avatar layer). Losing the room while a
    // response is awaited ends the wait instead of hanging.
    function handleTransportEvent(name) {
        if ((name === "DISCONNECTED" || name === "RECONNECTING") && serverWaiter) serverWaiter.onEnded(true);
    }


    // Called with worker "jobpilot.speech" messages.
    function handleRemoteSpeech(msg) {
        if (realtimeMode && msg && (msg.channel === "server-audio" || msg.channel === "realtime")) { handleRealtimeMessage(msg); return; }
        if (msg && msg.channel === "server-audio") { handleServerAudioMessage(msg); return; }
        if (!remoteWaiter || !msg) return;
        if (msg.seq !== remoteWaiter.seq && msg.turn_number !== -1) return; // stale message for an older request
        if (msg.state === "started") remoteWaiter.onStarted();
        else if (msg.state === "ended") remoteWaiter.onEnded(false);
        else if (msg.state === "failed") remoteWaiter.onEnded(true);
    }

    // returns true when handled remotely (caller must return), false to fall back locally
    async function _speakViaRemote(myToken, turnNumber) {

        const seq = ++remoteSeq;

        const outcome = await new Promise((resolve) => {

            let started = false;
            let endTimer = null;

            const startTimer = setTimeout(() => {
                if (!started && remoteWaiter && remoteWaiter.seq === seq) { remoteWaiter = null; resolve("no_start"); }
            }, REMOTE_START_TIMEOUT_MS);

            remoteWaiter = {
                seq,
                turnNumber,
                onStarted: () => {
                    started = true;
                    clearTimeout(startTimer);
                    endTimer = setTimeout(() => {
                        if (remoteWaiter && remoteWaiter.seq === seq) { remoteWaiter = null; resolve("failed"); }
                    }, REMOTE_END_TIMEOUT_MS);
                    if (Logic.isStaleToken(myToken, speechToken)) return;
                    setState(STATES.AI_SPEAKING);
                    emit("AI_STARTED_SPEAKING", { turnNumber, remote: true });
                },
                onEnded: (failed) => {
                    clearTimeout(startTimer);
                    if (endTimer) clearTimeout(endTimer);
                    remoteWaiter = null;
                    resolve(failed && !started ? "no_start" : (failed ? "failed" : "ended"));
                },
            };

            try { remoteSpeaker.say(turnNumber, seq); } catch (e) { clearTimeout(startTimer); remoteWaiter = null; resolve("no_start"); }
        });

        if (outcome === "no_start") return false; // remote never spoke -> safe to use local TTS

        stopVadMonitor();

        if (Logic.isStaleToken(myToken, speechToken)) return true; // interrupted / superseded

        emit("AI_STOPPED_SPEAKING", { turnNumber, interrupted: false, remote: outcome !== "failed" });
        setState(STATES.CANDIDATE_LISTENING);

        if (outcome === "failed") {
            // The avatar/TTS failed mid-question: say so, keep the question
            // text on screen as the recovery path, and do NOT auto-start
            // listening over the error message.
            reportError("tts_failed", "The AI interviewer's voice was unavailable for this question — please read it above.");
            return true;
        }

        _autoStartListening();
        return true;
    }

    async function speak(turnNumber) {

        const myToken = ++speechToken;

        stopSpeaking();
        setState(STATES.AI_PREPARING);

        // Start listening for a barge-in the MOMENT we begin
        // preparing speech (Step 4/9: a candidate can interrupt
        // even while TTS is still loading) -- but only if a
        // persistent mic stream already exists (never force a
        // permission prompt mid-question; that only happens on an
        // explicit user gesture, see toggleAutoListen()).
        _startBargeInMonitor(myToken);

        if (remoteSpeaker && remoteSpeaker.available()) {
            const handledRemotely = await _speakViaRemote(myToken, turnNumber);
            if (handledRemotely) return;
            if (Logic.isStaleToken(myToken, speechToken)) return;
        }

        currentAbortController = new AbortController();

        let response;

        const transportParam = _effectiveTransport() === "livekit" ? "&transport=livekit" : "";
        livekitRequestPending = transportParam !== "";

        try {

            response = await fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/interview/v2/voice/speak?turn_number=${encodeURIComponent(turnNumber)}${transportParam}`,
                { method: "POST", credentials: "include", signal: currentAbortController.signal },
            );

        } catch (error) {

            livekitRequestPending = false;
            if (error && error.name === "AbortError") return; // interrupted while loading -- not a real error
            stopVadMonitor();
            setState(STATES.CANDIDATE_LISTENING);
            emit("AI_AUDIO_FAILED", { turnNumber });
            reportError("tts_failed", "A network error occurred while generating speech."); // shown last so it isn't overwritten by the state's own status text
            return; // do NOT auto-start listening over a fresh error message -- the question text above is the recovery path; manual "Start Speaking" / typing remain available
        }

        livekitRequestPending = false;

        if (Logic.isStaleToken(myToken, speechToken)) return; // superseded while the fetch was in flight

        if (!response.ok) {

            const data = await response.json().catch(() => null);
            stopVadMonitor();
            setState(STATES.CANDIDATE_LISTENING);
            emit("AI_AUDIO_FAILED", { turnNumber });
            reportError("tts_failed", (data && data.detail) || "Couldn't generate speech for this question.");
            return;
        }

        const contentType = (response.headers && response.headers.get && response.headers.get("content-type")) || "";

        if (transportParam && contentType.indexOf("application/json") !== -1) {

            // Delivered through LiveKit: no audio to play here, only the turn-boundary messages to follow.
            const delivery = await response.json().catch(() => null);

            if (Logic.isStaleToken(myToken, speechToken)) {
                _requestServerInterrupt();                       // superseded while the server was already speaking
                return;
            }

            if (delivery && delivery.audio_delivery === "livekit" && typeof delivery.speech_seq === "number") {
                const outcome = await _awaitServerAudio(myToken, delivery.speech_seq, delivery.audio_seconds, turnNumber);
                _afterServerAudio(myToken, outcome, turnNumber, true);
                return;
            }
        }

        const audioBlob = await response.blob();

        if (Logic.isStaleToken(myToken, speechToken)) return; // candidate already started talking before audio was ready

        await _playAudioBlob(myToken, audioBlob, turnNumber, true);
    }


    // Barge-in monitor: only with a persistent mic stream (never force a
    // permission prompt mid-question) and with the stricter, echo-aware config.
    function _startBargeInMonitor(myToken) {

        if (!micStream || !supportsBargeIn()) return;

        startVadMonitor((decision) => {

            if (decision !== "VOICE_DETECTED") return;
            if (Logic.isStaleToken(myToken, speechToken)) return; // a newer speak() already superseded this one

            _handleInterruption(myToken);

        }, { config: Logic.BARGE_IN_VAD_CONFIG });
    }


    // Plays one AI audio blob under `myToken`. Shared by /voice/speak (questions)
    // and /voice/turn (replies) so interruption + staleness behave identically.
    // diagKey (optional): Phase 1 diagnostics correlation key -- distinct from myToken/speechToken (which govern
    // staleness/interruption, unchanged), since the /voice/turn caller uses a different token for those than the
    // one _finishUtterance/_submitVoiceTurn used for its own recording/request timestamps. Undefined for every
    // other caller (e.g. /voice/speak's question playback), which is a safe no-op throughout (see _markClientTiming).
    async function _playAudioBlob(myToken, audioBlob, turnNumber, autoListen, diagKey) {

        const audioUrl = URL.createObjectURL(audioBlob);
        currentAudioEl = new Audio(audioUrl);

        // the interview recording mixes the interviewer's voice in (see ai-interview-v2.js buildRecordingAudioMix)
        currentAudioEl.addEventListener("playing", () => {
            try {
                const captured = currentAudioEl && currentAudioEl.captureStream ? currentAudioEl.captureStream() : null;
                if (captured) captured.getAudioTracks().forEach((track) => window.dispatchEvent(new CustomEvent("jobpilot-ai-audio-track", { detail: { track } })));
            } catch (e) { /* capture is optional: the recording just lacks this clip */ }
        }, { once: true });

        setState(STATES.AI_SPEAKING);
        emit("AI_STARTED_SPEAKING", { turnNumber });
        emit("AI_AUDIO_AVAILABLE", { turnNumber, mimeType: audioBlob.type });

        let playbackErrorMessage = null;

        await new Promise((resolve) => {

            const settle = (reason) => {
                currentPlaybackResolve = null;
                if (diagKey != null && _clientTimings[diagKey]) _clientTimings[diagKey].playbackEndReason = reason;
                _markClientTiming(diagKey, "playbackEndedAt"); // Phase 1 diagnostics: point 11 -- playback ended or was interrupted
                resolve();
            };
            currentPlaybackResolve = () => settle("interrupted");   // stopSpeaking() calls this directly if playback is cancelled

            currentAudioEl.onended = () => {
                URL.revokeObjectURL(audioUrl);
                settle("ended");
            };

            currentAudioEl.onerror = () => {
                URL.revokeObjectURL(audioUrl);
                playbackErrorMessage = "Audio playback failed.";
                settle("error");
            };

            currentAudioEl.play().then(() => {
                _markClientTiming(diagKey, "playbackStartedAt"); // Phase 1 diagnostics: point 10 -- browser audio playback started
            }).catch(() => {
                playbackErrorMessage = "Audio playback was blocked by the browser.";
                settle("blocked");
            });
        });

        _reportClientTimings(diagKey); // no-op if diagKey is undefined or nothing was recorded for it

        // Stale FIRST: after an interruption/newer speak() the VAD monitor belongs to the
        // NEW capture -- a late 'ended' from the cancelled clip must not stop it.
        if (Logic.isStaleToken(myToken, speechToken)) return "stale";

        stopVadMonitor();

        emit("AI_STOPPED_SPEAKING", { turnNumber, interrupted: false });
        setState(STATES.CANDIDATE_LISTENING);

        if (playbackErrorMessage) {
            reportError("tts_playback_failed", playbackErrorMessage); // shown after the state change so it isn't overwritten; question text above remains the recovery path
            return "error";
        }

        if (autoListen) _autoStartListening();

        return "ended";
    }


    function _handleInterruption(token) {

        if (Logic.isStaleToken(token, speechToken)) return;

        if (realtimeMode) {
            // The server hears the same microphone and cuts the stream; here the candidate must not
            // wait for that round trip: silence the AI's audio element immediately. No local capture.
            _muteRemote(true);
            stopVadMonitor();
            return;
        }

        speechToken++; // the interrupted AI turn is now stale: its playback/fetch callbacks can no longer act

        // The candidate spoke while a LiveKit-delivered question was still being requested:
        // the server may already be publishing it -- tell it to stop (the follow-up request
        // only comes after the candidate has finished talking, so this cannot cut it off).
        if (livekitRequestPending && !serverWaiter) _requestServerInterrupt();

        stopSpeaking(); // pause + discard the <audio> element immediately -- no talking over the candidate
        stopVadMonitor();

        setState(STATES.AI_INTERRUPTED);
        emit("AI_STOPPED_SPEAKING", { interrupted: true });
        emit("AI_INTERRUPTED", {});

        setState(STATES.CANDIDATE_SPEAKING);
        emit("CANDIDATE_STARTED_SPEAKING", {});

        _beginCapture(/* alreadySpeaking */ true);
    }


    function stopSpeaking() {

        if (serverWaiter) {
            // LiveKit-delivered response in progress: silence the room's AI audio at once,
            // tell the server to stop publishing / drop its queue, and end the wait.
            _muteRemote(true);
            _requestServerInterrupt();
            serverWaiter.cancel();
        }

        if (remoteWaiter && remoteSpeaker) {
            const seq = ++remoteSeq;
            remoteWaiter = null; // the interrupted request's own "ended" message must not advance the turn
            try { remoteSpeaker.interrupt(seq); } catch (e) { /* ignore */ }
        }

        if (currentAbortController) {
            try { currentAbortController.abort(); } catch (error) { /* ignore */ }
            currentAbortController = null;
        }

        if (currentAudioEl) {
            try { currentAudioEl.pause(); } catch (error) { /* ignore */ }
            currentAudioEl = null;
        }

        if (currentPlaybackResolve) currentPlaybackResolve();
    }


    // ============================================================
    // SPEECH -> TEXT (auto-listen after AI speaks, or on interrupt)
    // ============================================================

    function _autoStartListening() {

        // Auto-listen only when we already hold a persistent mic
        // stream (i.e. the candidate already granted permission once
        // this session) -- never force a fresh permission prompt
        // right after a question, which would be jarring and could
        // silently fail on browsers that require a direct user
        // gesture for getUserMedia. Without a stream, the manual
        // "Start Speaking" button (unchanged from Task 5I) remains
        // the entry point -- the text fallback always still works
        // regardless (Task 5J Step 13).
        if (!micStream) return;

        setState(STATES.CANDIDATE_LISTENING);
        _beginCapture(false);
    }


    function startListening() {

        if (realtimeMode) return;                      // the server is already listening (Task 9)

        // Manual override / fallback entry point -- unchanged
        // public surface from Task 5I. Requests the mic on first use
        // (a genuine user gesture, satisfying the browser's
        // permission requirements) and then behaves identically to
        // auto-listen for every subsequent turn.
        _beginCapture(false);
    }


    async function _beginCapture(alreadySpeaking) {

        const myToken = ++listenToken;

        if (!micStream) {

            setState(STATES.REQUESTING_MIC);
            emit("REQUESTING_MIC", {});

            const stream = await ensureMicStream();

            if (Logic.isStaleToken(myToken, listenToken)) return;

            if (!stream) {
                reportError("mic_permission_denied", "Microphone access was denied or unavailable.");
                setState(STATES.ERROR);
                return;
            }
        }

        lastTranscript = "";

        if (allowBrowserStt && supportsNativeStt()) {
            _startNativeRecognition(myToken, alreadySpeaking);
        } else if (supportsMediaRecorder()) {
            _startMediaRecorderCapture(myToken, alreadySpeaking);
        } else {
            reportError("unsupported", "This browser does not support speech recognition.");
            setState(STATES.ERROR);
        }
    }


    function _startNativeRecognition(myToken, alreadySpeaking) {

        usingNativeStt = true;
        recognitionFinalTranscript = "";

        const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
        nativeRecognition = new SpeechRecognitionCtor();
        nativeRecognition.lang = "en-US";
        nativeRecognition.interimResults = true;
        nativeRecognition.continuous = true;
        nativeRecognition.maxAlternatives = 1;

        let silenceTimerId = null;
        let hasHeardAnything = false;

        function resetSilenceTimer() {

            if (silenceTimerId) clearTimeout(silenceTimerId);

            silenceTimerId = setTimeout(() => {

                if (Logic.isStaleToken(myToken, listenToken)) return;
                if (!hasHeardAnything) return;

                try { nativeRecognition.stop(); } catch (error) { /* already stopped */ }

            }, Logic.DEFAULT_VAD_CONFIG.silenceMsToEndTurn);
        }

        nativeRecognition.onstart = () => {

            if (Logic.isStaleToken(myToken, listenToken)) return;

            if (!alreadySpeaking) {
                setState(STATES.CANDIDATE_LISTENING);
            }
        };

        nativeRecognition.onresult = (event) => {

            if (Logic.isStaleToken(myToken, listenToken)) return;

            let finalText = "";
            let interimText = "";

            for (let i = event.resultIndex; i < event.results.length; i++) {

                const chunk = event.results[i][0].transcript;

                if (event.results[i].isFinal) {
                    finalText += chunk;
                } else {
                    interimText += chunk;
                }
            }

            if (finalText) {
                recognitionFinalTranscript = (recognitionFinalTranscript + " " + finalText).trim();
            }

            const combined = (recognitionFinalTranscript + " " + interimText).trim();

            if (combined && !hasHeardAnything) {
                hasHeardAnything = true;
                setState(STATES.CANDIDATE_SPEAKING);
                emit("CANDIDATE_STARTED_SPEAKING", {});
            }

            lastTranscript = combined;
            resetSilenceTimer();
        };

        nativeRecognition.onerror = (event) => {

            if (Logic.isStaleToken(myToken, listenToken)) return;

            if (silenceTimerId) clearTimeout(silenceTimerId);

            if (event.error === "not-allowed" || event.error === "permission-denied") {
                reportError("mic_permission_denied", "Microphone access was denied.");
            } else if (event.error === "no-speech") {
                reportError("no_speech", "No speech was detected.");
            } else if (event.error === "aborted") {
                return; // we aborted it ourselves (interruption/cleanup) -- not a real error
            } else {
                reportError("stt_failed", event.error || "Speech recognition failed.");
            }

            setState(STATES.ERROR);
        };

        nativeRecognition.onend = () => {

            if (Logic.isStaleToken(myToken, listenToken)) return;
            if (silenceTimerId) clearTimeout(silenceTimerId);
            if (currentState === STATES.ERROR) return;

            const finalTranscript = (lastTranscript || "").trim();
            const { submit } = Logic.shouldSubmitTranscript(finalTranscript, myToken, lastSubmittedTurnToken);

            setState(STATES.CANDIDATE_SILENCE);
            emit("CANDIDATE_STOPPED_SPEAKING", {});

            if (submit) {
                lastTranscript = finalTranscript;
                lastSubmittedTurnToken = myToken;
                setState(STATES.SUBMITTING);
                emit("CANDIDATE_TRANSCRIPT_FINAL", { transcript: finalTranscript });
            } else {
                setState(STATES.CANDIDATE_LISTENING);
                if (!finalTranscript) {
                    reportError("empty_transcript", "Couldn't understand the audio. Please try again.");
                }
            }
        };

        try {
            nativeRecognition.start();
        } catch (error) {
            reportError("stt_failed", String(error));
            setState(STATES.ERROR);
        }
    }


    function _pickSupportedRecorderMimeType() {

        const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];

        for (const candidate of candidates) {
            if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(candidate)) {
                return candidate;
            }
        }

        return "";
    }


    function _startMediaRecorderCapture(myToken, alreadySpeaking) {

        usingNativeStt = false;

        const mimeType = _pickSupportedRecorderMimeType();
        recordedChunks = [];

        try {
            mediaRecorder = mimeType ? new MediaRecorder(micStream, { mimeType }) : new MediaRecorder(micStream);
        } catch (error) {
            reportError("unsupported", "This browser cannot record audio for speech recognition.");
            setState(STATES.ERROR);
            return;
        }

        mediaRecorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
                recordedChunks.push(event.data);
            }
        };

        utteranceStats = Logic.initialUtteranceStats();
        discardRecording = false;

        mediaRecorder.onstop = async () => {
            if (Logic.isStaleToken(myToken, listenToken)) return;
            if (discardRecording) { recordedChunks = []; return; }
            await _finishUtterance(myToken);
        };

        mediaRecorder.start();

        if (!alreadySpeaking) {
            setState(STATES.CANDIDATE_LISTENING);
        }

        // No native interim results here -- drive speech-start /
        // end-of-turn purely from the shared amplitude VAD.
        startVadMonitor((decision) => {

            if (Logic.isStaleToken(myToken, listenToken)) return;

            if (decision === "VOICE_DETECTED" && currentState !== STATES.CANDIDATE_SPEAKING) {
                setState(STATES.CANDIDATE_SPEAKING);
                emit("CANDIDATE_STARTED_SPEAKING", {});
            } else if (decision === "SILENCE_DETECTED") {
                _endUtterance();
            }

        }, {
            onSample: (rms, elapsedMs) => {

                if (Logic.isStaleToken(myToken, listenToken)) return;

                const tick = Logic.utteranceTick(utteranceStats, rms, elapsedMs, vadVoiceThreshold);
                utteranceStats = tick.stats;

                if (tick.decision === "MAX_DURATION") {
                    _endUtterance();                       // hard stop: still sent if there was real speech
                } else if (tick.decision === "NO_SPEECH_TIMEOUT") {
                    _abandonRecording();                   // nobody spoke: stop recording, wait for the candidate
                }
            },
        });
    }


    // End of the utterance (silence or max duration): stop recording; onstop finishes it.
    function _endUtterance() {

        if (currentState === STATES.CANDIDATE_SILENCE) return; // already ending -- one stop per utterance

        emit("CANDIDATE_STOPPED_SPEAKING", {});
        setState(STATES.CANDIDATE_SILENCE);
        stopVadMonitor();

        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }
    }


    // No speech within the timeout: discard, release the recorder, wait.
    function _abandonRecording() {

        discardRecording = true;
        stopVadMonitor();

        if (mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }

        recordedChunks = [];
        setState(STATES.CANDIDATE_LISTENING);
        reportError("no_speech", "No speech was detected.");
    }


    // ------------------------------------------------------------------
    // PHASE 1 DIAGNOSTICS: privacy-safe, console-only latency instrumentation for the local turn-based voice
    // pipeline (recording-stop -> request-sent -> response-received -> playback-start -> playback-end/interrupted).
    // Client timestamps use performance.now() (monotonic within THIS page load only). The server's own stt/brain/tts
    // breakdown (already computed in run_voice_turn_v2() and returned as the /voice/turn response's `latency`
    // field) is reported ALONGSIDE these, never subtracted against a client timestamp -- client and server clocks
    // are not the same clock and are never safely comparable, only their own separately-measured durations are.
    // Keyed by the existing turn token (a plain incrementing integer, not sensitive) -- never logs audio, transcript
    // or any candidate content, only numeric millisecond durations. No UI surface: this project has no developer-
    // diagnostics panel, so these stay console.debug-only rather than adding one.
    // ------------------------------------------------------------------
    const _clientTimings = {};

    function _markClientTiming(key, stage) {
        if (key == null) return;
        if (!_clientTimings[key]) _clientTimings[key] = {};
        _clientTimings[key][stage] = performance.now();
    }

    function _reportClientTimings(key) {
        const t = _clientTimings[key];
        if (key == null || !t) return;
        delete _clientTimings[key];
        const report = { turn: key };
        if (t.recordingStoppedAt != null && t.requestSentAt != null) report.record_to_request_sent_ms = Math.round(t.requestSentAt - t.recordingStoppedAt);
        if (t.requestSentAt != null && t.responseReceivedAt != null) report.request_round_trip_ms = Math.round(t.responseReceivedAt - t.requestSentAt);
        if (t.responseReceivedAt != null && t.playbackStartedAt != null) report.response_to_playback_start_ms = Math.round(t.playbackStartedAt - t.responseReceivedAt);
        if (t.playbackStartedAt != null && t.playbackEndedAt != null) report.playback_duration_ms = Math.round(t.playbackEndedAt - t.playbackStartedAt);
        if (t.playbackEndReason) report.playback_end_reason = t.playbackEndReason;
        if (t.serverLatency) report.server = t.serverLatency; // separate clock domain -- reported, never merged with the client numbers above
        console.debug("[voice-turn-latency]", report);
    }


    // A finished recording: send it only if it is worth sending.
    async function _finishUtterance(myToken) {

        _markClientTiming(myToken, "recordingStoppedAt"); // Phase 1 diagnostics: point 1 -- recording stopped in the browser

        const blob = new Blob(recordedChunks, { type: (mediaRecorder && mediaRecorder.mimeType) || "audio/webm" });
        recordedChunks = [];

        const verdict = Logic.evaluateUtterance(utteranceStats, blob.size);

        if (!verdict.send) {

            setState(STATES.CANDIDATE_LISTENING);

            if (verdict.reason === "empty") {
                reportError("empty_transcript", "Couldn't understand the audio. Please try again.");
            } else {
                _autoStartListening(); // too short: noise/click -- silently keep listening, send nothing
            }

            return;
        }

        if (useVoiceTurn) {
            await _submitVoiceTurn(myToken, blob);
        } else {
            await _uploadRecordedAudioForTranscription(myToken, blob);
        }
    }


    function _base64ToBlob(base64, mimeType) {

        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);

        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }

        return new Blob([bytes], { type: mimeType || "audio/wav" });
    }


    // ONE request: audio -> faster-whisper -> the interview brain -> Kokoro.
    async function _submitVoiceTurn(myToken, blob) {

        if (turnInFlight) return; // duplicate-submission guard: one turn at a time

        turnInFlight = true;

        const turnToken = ++voiceTurnToken;
        livekitTurnPending = _effectiveTransport() === "livekit";
        voiceTurnAbort = new AbortController();

        transition(EVENTS.SUBMIT_STARTED); // CANDIDATE_SILENCE -> AI_THINKING
        emit("VOICE_TURN_STARTED", {});

        let response = null;
        let data = null;

        try {

            const formData = new FormData();
            formData.append("audio", blob, "answer.webm");
            formData.append("transport", livekitTurnPending ? "livekit" : "local");       // decided now, for this whole turn

            _markClientTiming(myToken, "requestSentAt"); // Phase 1 diagnostics: point 2 -- voice-turn request sent

            response = await fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/interview/v2/voice/turn`,
                { method: "POST", credentials: "include", body: formData, signal: voiceTurnAbort.signal },
            );

            _markClientTiming(myToken, "responseReceivedAt"); // Phase 1 diagnostics: point 9 -- voice-turn response received by browser

            data = await response.json().catch(() => null);

        } catch (error) {

            turnInFlight = false;
            voiceTurnAbort = null;
            livekitTurnPending = false;
            emit("VOICE_TURN_FINISHED", {});

            if (error && error.name === "AbortError") return; // cleanup/leave: not an error

            transition(EVENTS.ERROR_OCCURRED);
            reportError("network_error", "A network error occurred while processing your answer.");
            return;
        }

        turnInFlight = false;
        voiceTurnAbort = null;
        livekitTurnPending = false;
        emit("VOICE_TURN_FINISHED", {});

        // stale = the candidate left / voice mode was turned off / the mic was lost
        // while the request was in flight. The server may already have advanced the
        // interview, so the text is still applied -- only audio playback is suppressed.
        const stale = Logic.isStaleToken(turnToken, voiceTurnToken);

        if (!response.ok) {

            if (stale) return;

            const detail = data && data.detail;
            const stage = detail && typeof detail === "object" ? detail.stage : null;

            if (response.status === 422 && stage === "stt") {
                transition(EVENTS.TRANSCRIPT_EMPTY);
                reportError("empty_transcript", "Couldn't understand the audio. Please try again.");
            } else if (response.status === 409 || response.status === 410) {
                transition(EVENTS.ERROR_OCCURRED);
                reportError("session_ended", "This interview is no longer active.");
                emit("VOICE_SESSION_INVALID", {});
            } else {
                transition(EVENTS.ERROR_OCCURRED);
                reportError(stage === "brain" ? "brain_failed" : "stt_failed", (detail && detail.message) || "The voice turn failed.");
            }

            return;
        }

        lastTranscript = data.transcript || "";
        lastSubmittedTurnToken = myToken;

        // Phase 1 diagnostics: the server's own stt/brain/tts breakdown, already computed in run_voice_turn_v2() and
        // returned here -- stashed for _reportClientTimings() to log alongside the client-side numbers once
        // playback finishes, reported separately since it's a different clock domain (never merged/subtracted).
        if (data.latency && _clientTimings[myToken]) _clientTimings[myToken].serverLatency = data.latency;

        if (typeof onVoiceTurn === "function") onVoiceTurn(data, { stale });

        if (stale) {
            if (data.audio_delivery === "livekit") _requestServerInterrupt(); // the server is already speaking to a candidate who left
            return;
        }

        transition(EVENTS.BRAIN_RESPONDED); // AI_THINKING -> AI_PREPARING

        if (data.audio_delivery === "livekit" && typeof data.speech_seq === "number") {

            const speakToken = ++speechToken;
            stopSpeaking();

            if (!data.interview_complete) _startBargeInMonitor(speakToken); // no barge-in over the closing message

            const outcome = await _awaitServerAudio(speakToken, data.speech_seq, data.livekit && data.livekit.audio_seconds, null);
            const tail = _afterServerAudio(speakToken, outcome, null, !data.interview_complete);

            if (data.interview_complete && tail !== "stale") {
                setState(STATES.COMPLETED);
                if (typeof onVoiceTurnComplete === "function") onVoiceTurnComplete();
            }

            return;
        }

        if (data.livekit_error) emit("LIVEKIT_DELIVERY_FELL_BACK", {}); // LiveKit was requested but the same audio came back for local playback

        if (!data.audio || !data.audio.base64) {

            // TTS failed on the server: the reply text was already shown by the page.
            transition(EVENTS.AI_AUDIO_FAILED);
            emit("AI_AUDIO_FAILED", {});

            if (data.interview_complete && typeof onVoiceTurnComplete === "function") {
                onVoiceTurnComplete();
            } else {
                reportError("tts_failed", data.tts_error || "Couldn't generate speech for this reply.");
            }

            return;
        }

        const speakToken = ++speechToken;
        stopSpeaking();

        if (!data.interview_complete) _startBargeInMonitor(speakToken); // no barge-in over the closing message

        const outcome = await _playAudioBlob(
            speakToken,
            _base64ToBlob(data.audio.base64, data.audio.mime_type),
            null,
            !data.interview_complete,
            myToken, // Phase 1 diagnostics key -- distinct from speakToken (staleness token); undefined for every other caller of _playAudioBlob
        );

        if (data.interview_complete && outcome !== "stale") {
            setState(STATES.COMPLETED);
            if (typeof onVoiceTurnComplete === "function") onVoiceTurnComplete();
        }
    }


    async function _uploadRecordedAudioForTranscription(myToken, blob) {

        setState(STATES.TRANSCRIBING);

        try {

            const formData = new FormData();
            formData.append("audio", blob, "answer.webm");

            const response = await fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/interview/v2/voice/transcribe`,
                { method: "POST", credentials: "include", body: formData },
            );

            if (Logic.isStaleToken(myToken, listenToken)) return; // candidate/interview moved on while this was uploading

            const data = await response.json().catch(() => null);

            if (!response.ok) {
                setState(STATES.CANDIDATE_LISTENING);
                reportError("stt_failed", (data && data.detail) || "Speech-to-text failed.");
                return;
            }

            const transcript = (data && data.transcript) ? String(data.transcript).trim() : "";
            const { submit } = Logic.shouldSubmitTranscript(transcript, myToken, lastSubmittedTurnToken);

            if (!submit) {
                setState(STATES.CANDIDATE_LISTENING);
                if (!transcript) {
                    reportError("empty_transcript", "Couldn't understand the audio. Please try again.");
                }
                return;
            }

            lastTranscript = transcript;
            lastSubmittedTurnToken = myToken;
            setState(STATES.SUBMITTING);
            emit("CANDIDATE_TRANSCRIPT_FINAL", { transcript });

        } catch (error) {
            setState(STATES.CANDIDATE_LISTENING);
            reportError("network_error", "A network error occurred while transcribing your answer.");
        }
    }


    function stopListening() {

        listenToken++; // any in-flight capture for the old token becomes stale
        voiceTurnToken++; // an in-flight voice turn keeps its text but must not play audio
        discardRecording = true;

        if (usingNativeStt && nativeRecognition) {
            try { nativeRecognition.stop(); } catch (error) { /* already stopped */ }
        }

        if (!usingNativeStt && mediaRecorder && mediaRecorder.state === "recording") {
            mediaRecorder.stop();
        }

        stopVadMonitor();
    }


    function getTranscript() {
        return lastTranscript;
    }


    function setVoiceState(next) {
        setState(next);
    }


    // ------------------------------------------------------------------
    // FIXED SPOKEN LINES: the welcome when the candidate enters, and the spoken integrity notices ("I can't see you").
    // The browser sends only a kind + event name; the server owns the words. Played on their own <audio> element so they
    // never disturb the interview's turn state; the microphone is muted while a line plays so the interviewer's own voice
    // is never recorded and transcribed as the candidate's answer.
    // ------------------------------------------------------------------
    let noticeAudio = null;
    let noticeBusy = false;
    const lastNoticeAt = {};
    const NOTICE_MIN_GAP_MS = 20000;

    const spokenLineCache = {};          // kind|event -> Promise<Blob|null>, so a prefetched line is not requested twice

    function _fetchSpokenLine(kind, event) {

        const key = kind + "|" + (event || "");

        if (!spokenLineCache[key]) {
            const query = `kind=${encodeURIComponent(kind)}${event ? `&event=${encodeURIComponent(event)}` : ""}`;
            spokenLineCache[key] = fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/interview/v2/voice/say?${query}`,
                { method: "POST", credentials: "include" },
            ).then((response) => {
                if (!response.ok) return null;
                const type = (response.headers.get("content-type") || "").toLowerCase();
                // "audio_delivery: realtime" = the live interviewer said it itself (same voice): nothing to play here
                return type.indexOf("application/json") !== -1 ? null : response.blob();
            }).catch(() => null);
        }

        return spokenLineCache[key];
    }

    // Ask the server for the welcome (and the likeliest notices) BEFORE they are needed, so they play without a wait.
    function prefetchSpokenLines(events) {
        _fetchSpokenLine("welcome_hello", null);
        _fetchSpokenLine("welcome_rules", null);
        // (notices are NOT prefetched: in a realtime interview asking for one makes the live interviewer say it)
    }

    async function say(kind, event) {

        const key = kind + ":" + (event || "");
        const now = Date.now();

        if (noticeBusy) return false;
        if (kind === "notice" && now - (lastNoticeAt[key] || 0) < NOTICE_MIN_GAP_MS) return false;

        lastNoticeAt[key] = now;
        noticeBusy = true;

        const tracks = micStream ? micStream.getAudioTracks() : [];

        try {

            // Never talk over the interviewer's own sentence: wait for it (at most 15 s).
            // AI_PREPARING (TTS still being generated for the question, before any audio exists yet) must be
            // waited on too, not just AI_SPEAKING (audio actively playing) -- otherwise a notice fired while the
            // question's speech was still being generated saw "not speaking yet", played immediately, and then
            // collided with the question's own audio the moment it arrived a beat later.
            const waitStarted = Date.now();
            while (
                (currentState === STATES.AI_SPEAKING || currentState === STATES.AI_PREPARING) &&
                Date.now() - waitStarted < 15000
            ) {
                await new Promise((resolve) => setTimeout(resolve, 300));
            }

            // the welcome is two clips (a short personal greeting + the fixed rules), fetched together, played in order
            const parts = kind === "welcome" ? [["welcome_hello", null], ["welcome_rules", null]] : [[kind, event]];
            const fetches = parts.map(([k, e]) => _fetchSpokenLine(k, e));

            tracks.forEach((track) => { track.enabled = false; });

            let playedAny = false;

            for (const pending of fetches) {

                const blob = await pending;
                if (!blob) continue;

                const url = URL.createObjectURL(blob);
                noticeAudio = new Audio(url);

                await new Promise((resolve) => {
                    noticeAudio.onended = resolve;
                    noticeAudio.onerror = resolve;
                    noticeAudio.play().catch(resolve);
                });

                URL.revokeObjectURL(url);
                playedAny = true;
            }

            return playedAny;

        } catch (error) {
            return false;
        } finally {
            tracks.forEach((track) => { track.enabled = true; });
            noticeAudio = null;
            noticeBusy = false;
        }
    }


    function cleanup() {

        if (noticeAudio) { try { noticeAudio.pause(); } catch (error) { /* ignore */ } noticeAudio = null; }

        speechToken++;
        listenToken++;
        voiceTurnToken++;

        // Leaving while a LiveKit-delivered turn is still being answered: the server may
        // publish the reply into a room nobody will listen to -- stop it.
        if ((livekitTurnPending || livekitRequestPending) && !serverWaiter) _requestServerInterrupt();

        if (voiceTurnAbort) {
            try { voiceTurnAbort.abort(); } catch (error) { /* ignore */ }
            voiceTurnAbort = null;
        }

        turnInFlight = false;
        livekitTurnPending = false;
        livekitRequestPending = false;

        serverEarly = [];
        stopListening();
        stopSpeaking();
        stopVadMonitor();
        releaseMicStream();

        if (audioContext) {
            try { audioContext.close(); } catch (error) { /* ignore */ }
            audioContext = null;
        }

        nativeRecognition = null;
        mediaRecorder = null;
        recordedChunks = [];
        utteranceStats = null;
        lastTranscript = "";
        lastSubmittedTurnToken = null;

        setState(STATES.IDLE);
        emit("INTERVIEW_COMPLETED", {});
    }


    // Read-only: returns the SAME persistent stream ensureMicStream() already opened, or null
    // if it never has. Never calls getUserMedia itself -- a caller wanting the mic stream (e.g.
    // voice-identity-proctor.js, which must reuse this exact stream, not open a second one) must
    // wait until ensureMicStream() has resolved at least once.
    function getMicStream() {
        return micStream;
    }

    return {
        STATES,
        init,
        isVoiceCaptureSupported,
        supportsBargeIn,
        ensureMicStream,
        getMicStream,
        setPreferredMicDevice,
        startListening,
        stopListening,
        getTranscript,
        speak,
        say,
        prefetchSpokenLines,
        stopSpeaking,
        setRemoteSpeaker,
        handleRemoteSpeech,
        handleTransportEvent,
        setRealtimeMode,
        isRealtimeMode,
        setVoiceState,
        cleanup,
    };

})();

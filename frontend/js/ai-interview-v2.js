// ============================================================
// JOBPILOT AI - AI INTERVIEW V2 (ADAPTIVE CONVERSATION) (Task 5H)
//
// Talks to /api/assessment/sessions/{id}/interview/v2/* — a new,
// additive API, alongside (never replacing) the legacy /api/
// interview-tests/* endpoints interview.js still uses unchanged.
//
// Reuses, unchanged: session-guard.js, assessment-integrity.js
// (window.IntegrityMode), phone-detector.js (window.PhoneDetector),
// assessment-video-recorder.js (window.AssessmentVideoRecorder) --
// assessmentType stays "ai_interview", matching the legacy page and
// the existing integrity/assignment vocabulary. No second
// proctoring system.
//
// NEVER shows the candidate: competency labels, strength levels,
// evidence, the interviewer's internal "reason", or any pass/fail
// signal before completion (Section 20).
// ============================================================

const API_BASE_URL = "http://" + (window.location.hostname === "localhost" ? "localhost" : "127.0.0.1") + ":8000";

let applicationId = null;
let currentUser = null;
let assessmentLocked = false;
let submitInFlight = false;

let countdownIntervalId = null;
let expiresAtMs = null;

let terminatedByIntegrity = false;
let interviewVideoRecorder = null;
let cameraStreamActive = false;

let conversationTurns = []; // [{question, answer|null}] in order, for rendering only

// ============================================================
// VOICE MODE (Task 5I) — thin glue between this controller's
// existing state (conversationTurns, submitAnswer()) and
// InterviewVoiceV2 (frontend/js/interview-voice-v2.js), which
// owns all microphone/TTS/STT mechanics. Voice mode never bypasses
// submitAnswer() — a transcript is just typed text as far as the
// rest of this file and the backend are concerned.
// ============================================================
let voiceModeEnabled = false;
let currentQuestionTurnNumber = null;


function getLoggedInUser() {
    try {
        const stored = localStorage.getItem("jobpilot_user");
        return stored ? JSON.parse(stored) : null;
    } catch (error) {
        return null;
    }
}


function getApplicationIdFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("application_id");
    const parsed = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
}


function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}


function showMessage(message, type = "danger") {
    document.getElementById("iv2Message").innerHTML = `
        <div class="alert alert-${type}" role="alert">${escapeHtml(message)}</div>
    `;
}


function clearMessage() {
    document.getElementById("iv2Message").innerHTML = "";
}


function hideLoading() {
    document.getElementById("iv2Loading").style.display = "none";
}


function formatClock(totalSeconds) {
    const safe = Math.max(0, Math.floor(totalSeconds));
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}


function parseServerTimestamp(isoString) {
    if (!isoString) return null;
    const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(isoString) ? isoString : `${isoString}Z`;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}


function renderJobHeader(job) {
    document.getElementById("iv2JobTitle").textContent = (job && job.title) || "Job";
    document.getElementById("iv2CompanyName").textContent = (job && job.company) || "";
}


// ============================================================
// TIMER (server-authoritative)
// ============================================================

function stopCountdown() {
    if (countdownIntervalId) {
        clearInterval(countdownIntervalId);
        countdownIntervalId = null;
    }
}


function renderCountdown() {
    if (expiresAtMs === null) return;

    const remainingSeconds = Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000));
    const timerEl = document.getElementById("iv2Timer");
    timerEl.textContent = formatClock(remainingSeconds);
    timerEl.classList.toggle("is-urgent", remainingSeconds <= 120);

    if (remainingSeconds <= 0) {
        stopCountdown();
        loadInterviewState(true);
    }
}


function startCountdown(expiresAtIso) {
    stopCountdown();
    const d = parseServerTimestamp(expiresAtIso);
    expiresAtMs = d ? d.getTime() : null;
    renderCountdown();
    countdownIntervalId = setInterval(renderCountdown, 1000);
}


// ============================================================
// INTEGRITY (reused, unmodified system)
// ============================================================

// the number of integrity warnings that ends the assessment; the server's value replaces this default as soon as it is known
let integrityMaxWarnings = 3;


function setIntegrityDot(kind, label) {
    const dot = document.getElementById("iv2IntegrityDot");
    dot.classList.remove("is-ready", "is-warning", "is-error");
    if (kind) dot.classList.add(`is-${kind}`);
    document.getElementById("iv2IntegrityStatus").textContent = label;
}


// What the AI says out loud the moment a camera / browser flag is accepted by the server (fixed lines, server side).
const SPOKEN_NOTICE_EVENTS = new Set([
    "face_missing", "multiple_faces_detected", "looking_away", "gaze_away", "camera_covered", "mobile_phone_detected", "unauthorized_object_detected",
    "tab_hidden", "window_blur", "fullscreen_exited", "paste_detected", "blocked_shortcut",
]);


function handleIntegrityStateChange(state, eventType) {
    if (state && state.maxWarnings) integrityMaxWarnings = state.maxWarnings;

    if (!state) return;

    if (eventType && SPOKEN_NOTICE_EVENTS.has(eventType) && !terminatedByIntegrity && !assessmentLocked
            && window.InterviewVoiceV2 && typeof window.InterviewVoiceV2.say === "function") {
        window.InterviewVoiceV2.say("notice", eventType);
    }

    if (state.warningLevel === "terminated" || state.terminated) {
        handleAssessmentTerminated();
        return;
    }

    if (state.warningLevel === "warning" || state.warningCount > 0) {
        setIntegrityDot("warning", `Integrity ${state.warningCount || 1}/${integrityMaxWarnings}`);
    } else if (!terminatedByIntegrity) {
        if (cameraStreamActive) {
            setIntegrityDot("ready", `Camera ready · Integrity 0/${integrityMaxWarnings}`);
        } else {
            setIntegrityDot("error", "Camera unavailable — proceeding without video monitoring");
        }
    }
}


function handleAssessmentTerminated() {
    if (terminatedByIntegrity) return;
    terminatedByIntegrity = true;

    stopCountdown();
    stopPhoneDetection();

    if (window.InterviewVoiceV2) {
        window.InterviewVoiceV2.cleanup();
    }

    if (window.InterviewVideoRoomV2) {
        window.InterviewVideoRoomV2.cleanup();
    }

    if (window.InterviewAvatarV2) {
        window.InterviewAvatarV2.cleanup();
    }

    setIntegrityDot("error", "Interview terminated — integrity violation limit reached");

    if (window.IntegrityMode && typeof window.IntegrityMode.cleanup === "function") {
        window.IntegrityMode.cleanup();
    }

    lockAssessmentUi("Your interview was automatically terminated after repeated integrity violations. This has been reported to the employer.", "danger");
}


function startPhoneDetection() {
    if (!window.PhoneDetector || !window.IntegrityMode) return;

    const videoEl = window.IntegrityMode.getCameraVideoElement && window.IntegrityMode.getCameraVideoElement();
    if (!videoEl) return;

    window.PhoneDetector.start(videoEl, {
        onConfirmedDetection: (meta) => {
            if (window.IntegrityMode.reportIntegrityEvent) {
                window.IntegrityMode.reportIntegrityEvent("mobile_phone_detected", meta);
            }
        },
    });
}


function stopPhoneDetection() {
    if (window.PhoneDetector && typeof window.PhoneDetector.stop === "function") {
        window.PhoneDetector.stop();
    }
}


// ------------------------------------------------------------
// CANDIDATE CAMERA (Task 9: diagnosable, decoupled from the microphone/recording, recoverable)
//
// There is exactly ONE camera stream: the one IntegrityMode.startCameraMonitoring() acquires. This
// page never calls getUserMedia itself. What changed: (1) the local preview and the room publish no
// longer wait for the microphone permission prompt or the recorder (a slow/denied mic used to keep
// the preview empty, and a mic error was reported as "Camera unavailable"); (2) a failure now says WHY
// and offers "Retry camera"; (3) a camera track that dies is re-acquired automatically (bounded).
// ------------------------------------------------------------

const CAMERA_RECOVERY_DELAYS_MS = [2000, 5000, 10000];
let cameraRecoveryTimer = null;
let cameraRecoveryAttempts = 0;
let cameraWatchedTrack = null;
let cameraTornDown = false;


async function explainCameraFailure() {

    if (typeof window.isSecureContext !== "undefined" && window.isSecureContext === false) {
        return { code: "insecure_context", message: "The camera needs a secure page. Open HireFlow with https:// or http://localhost." };
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        return { code: "no_camera_api", message: "This browser cannot use the camera. Please use a current Chrome, Edge or Firefox." };
    }

    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        if (devices.filter((d) => d.kind === "videoinput").length === 0) {
            return { code: "no_device", message: "No camera was found. Connect a camera, then press Retry camera." };
        }
    } catch (error) { /* keep going */ }

    try {
        const permission = await navigator.permissions.query({ name: "camera" });
        if (permission && permission.state === "denied") {
            return { code: "permission_denied", message: "Camera access is blocked. Allow the camera for this site in your browser settings, then press Retry camera." };
        }
    } catch (error) { /* permissions API unavailable */ }

    return { code: "unavailable", message: "The camera could not be opened (it may be in use by another app). Close other apps using it, then press Retry camera." };
}


function setCameraRetryVisible(visible) {
    const button = document.getElementById("iv2CameraRetryButton");
    if (button) button.style.display = visible ? "inline-flex" : "none";
}


async function showCameraProblem() {

    cameraStreamActive = false;

    const candidateVideo = document.getElementById("iv2CandidateVideo");
    const candidatePlaceholder = document.getElementById("iv2CandidatePlaceholder");
    if (candidateVideo) { candidateVideo.style.display = "none"; }
    if (candidatePlaceholder) candidatePlaceholder.style.display = "flex";

    const reason = await explainCameraFailure();
    setIntegrityDot("error", `Camera unavailable — ${reason.message}`);
    setCameraRetryVisible(true);
    console.warn("Camera unavailable:", reason.code);
    return reason;
}


// The ONLY place a camera is requested: IntegrityMode's own (single) acquisition. Never throws.
async function acquireCamera() {

    if (!window.IntegrityMode || typeof window.IntegrityMode.startCameraMonitoring !== "function") return null;

    try {
        await window.IntegrityMode.startCameraMonitoring();
    } catch (error) {
        console.warn("Camera start failed:", error);
        return null;
    }

    const stream = window.IntegrityMode.getCameraStream && window.IntegrityMode.getCameraStream();
    return stream && stream.getVideoTracks && stream.getVideoTracks().length ? stream : null;
}


function watchCameraTrack(stream) {

    const track = stream.getVideoTracks()[0];
    if (!track || track === cameraWatchedTrack) return;

    cameraWatchedTrack = track;

    track.addEventListener("ended", () => {
        if (cameraTornDown || track !== cameraWatchedTrack) return;
        cameraStreamActive = false;
        const candidateVideo = document.getElementById("iv2CandidateVideo");
        if (candidateVideo) candidateVideo.style.display = "none";
        setIntegrityDot("error", "Camera disconnected — trying to reconnect…");
        scheduleCameraRecovery();
    });
}


function scheduleCameraRecovery() {

    if (cameraTornDown || cameraRecoveryTimer) return;

    if (cameraRecoveryAttempts >= CAMERA_RECOVERY_DELAYS_MS.length) {
        showCameraProblem();                                   // gave up: say why, keep the interview going, offer a manual retry
        return;
    }

    const delay = CAMERA_RECOVERY_DELAYS_MS[cameraRecoveryAttempts++];

    cameraRecoveryTimer = setTimeout(async () => {
        cameraRecoveryTimer = null;
        if (cameraTornDown) return;
        const stream = await acquireCamera();
        if (cameraTornDown) return;
        if (stream) {
            cameraRecoveryAttempts = 0;
            attachCandidateCamera(stream, true);
        } else {
            scheduleCameraRecovery();
        }
    }, delay);
}


async function retryCamera() {

    if (cameraTornDown) return;

    setCameraRetryVisible(false);
    setIntegrityDot("warning", "Trying the camera again…");

    const stream = await acquireCamera();

    if (stream) {
        cameraRecoveryAttempts = 0;
        attachCandidateCamera(stream, true);
    } else {
        await showCameraProblem();
    }
}


// One function for "the camera stream is now live": preview, phone detection, room publication, health watch.
function attachCandidateCamera(cameraStream, isRecovery) {

    cameraStreamActive = true;
    setIntegrityDot("ready", `Camera ready · Integrity 0/${integrityMaxWarnings}`);
    setCameraRetryVisible(false);
    startPhoneDetection();

    const candidateVideo = document.getElementById("iv2CandidateVideo");
    const candidatePlaceholder = document.getElementById("iv2CandidatePlaceholder");

    if (candidateVideo) {
        candidateVideo.srcObject = cameraStream;
        candidateVideo.style.display = "block";
        if (candidatePlaceholder) candidatePlaceholder.style.display = "none";
        if (typeof candidateVideo.play === "function") {
            const playing = candidateVideo.play();
            if (playing && typeof playing.catch === "function") playing.catch(() => { /* autoplay policy: muted autoplay is allowed, ignore */ });
        }
    }

    if (window.InterviewVideoRoomV2) {
        if (isRecovery && typeof window.InterviewVideoRoomV2.replaceCameraStream === "function") {
            window.InterviewVideoRoomV2.replaceCameraStream(cameraStream);      // swap the published track, never a 2nd publication
        } else {
            window.InterviewVideoRoomV2.notifyCameraReady(cameraStream);
        }
    }

    watchCameraTrack(cameraStream);
}


function cleanupCamera() {

    cameraTornDown = true;

    if (cameraRecoveryTimer) { clearTimeout(cameraRecoveryTimer); cameraRecoveryTimer = null; }

    const candidateVideo = document.getElementById("iv2CandidateVideo");
    if (candidateVideo) { try { candidateVideo.srcObject = null; } catch (error) { /* ignore */ } }

    cameraWatchedTrack = null;
    cameraStreamActive = false;
}


// The microphone + the interview recording. Independent of the camera preview: a slow/denied mic or a
// recorder problem never hides the camera and is never reported as a camera failure.
// Mixes the candidate's microphone and the AI interviewer's voice into ONE audio track for the interview recording, so the
// saved video contains both sides of the conversation. A MediaRecorder cannot add an audio track after it starts and the
// interviewer's track only appears once the room is connected, so the tracks are fed into a WebAudio mixer as they arrive
// and the mixer's single output track is what gets recorded. The mixer is not connected to the speakers: no double playback.
let recordingMix = null;

function buildRecordingAudioMix(micStream) {

    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return null;

    try {

        const ctx = new Ctor();
        const destination = ctx.createMediaStreamDestination();
        const added = new Set();

        const addTrack = (track) => {
            if (!track || added.has(track.id) || track.readyState === "ended") return;
            added.add(track.id);
            try { ctx.createMediaStreamSource(new MediaStream([track])).connect(destination); } catch (e) { /* skip this track */ }
        };

        if (micStream) micStream.getAudioTracks().forEach(addTrack);
        if (window.InterviewAvatarV2 && window.InterviewAvatarV2.getRemoteAudioTracks) {
            window.InterviewAvatarV2.getRemoteAudioTracks().forEach(addTrack);
        }

        const onTrack = (event) => addTrack(event.detail && event.detail.track);
        window.addEventListener("jobpilot-ai-audio-track", onTrack);

        if (ctx.state === "suspended") ctx.resume().catch(() => {});

        return {
            stream: destination.stream,
            close: () => {
                window.removeEventListener("jobpilot-ai-audio-track", onTrack);
                try { ctx.close(); } catch (e) { /* already closed */ }
            },
        };

    } catch (error) {
        console.warn("Recording audio mix unavailable, recording the microphone only:", error);
        return null;
    }
}


async function startMicAndRecording(cameraStream) {

    let micStream = null;

    try {
        if (window.InterviewVoiceV2) {
            micStream = await window.InterviewVoiceV2.ensureMicStream();
        }
    } catch (error) {
        console.warn("Microphone start failed:", error);
    }

    if (micStream && window.InterviewVideoRoomV2) window.InterviewVideoRoomV2.notifyMicReady(micStream);

    try {

        // isSupported() is an INSTANCE method (assessment-video-recorder.js's own documented usage), never a static
        // one on the class itself -- calling it on window.AssessmentVideoRecorder directly threw a TypeError, caught
        // below, which silently disabled video recording for every interview. Construct the instance first, exactly
        // as every other caller of this shared module already does.
        const candidateRecorder = window.AssessmentVideoRecorder ? new window.AssessmentVideoRecorder() : null;

        if (candidateRecorder && candidateRecorder.isSupported()) {

            // Task 5M recording decision: candidate camera + microphone. MediaRecorder cannot add an audio
            // track after start(), so both must exist BEFORE it starts (the SAME persistent mic stream).
            const recordingTracks = [...cameraStream.getVideoTracks()];

            // candidate mic + interviewer voice, mixed (falls back to the mic alone if mixing is not possible)
            recordingMix = micStream ? buildRecordingAudioMix(micStream) : null;
            if (recordingMix) recordingTracks.push(...recordingMix.stream.getAudioTracks());
            else if (micStream) recordingTracks.push(...micStream.getAudioTracks());

            interviewVideoRecorder = candidateRecorder;
            await interviewVideoRecorder.start(new MediaStream(recordingTracks), "ai_interview");

            // Recording is never silent/hidden: the indicator reflects the SAME condition that started it.
            const recordingIndicator = document.getElementById("iv2RecordingIndicator");
            if (recordingIndicator) {
                recordingIndicator.style.display = "flex";
                recordingIndicator.textContent = micStream
                    ? "🔴 Interview Recording Active (video + both voices)"
                    : "🔴 Interview Recording Active (video only)";
            }
        }

    } catch (error) {
        console.warn("Interview recording could not start:", error);
        interviewVideoRecorder = null;
    }

    return micStream;
}


async function activateStrictModeForInterviewV2() {

    if (!window.IntegrityMode) return;

    window.IntegrityMode.enterStrictMode({
        userId: currentUser ? currentUser.id : null,
        applicationId,
        assessmentType: "ai_interview",
        screenShare: false,   // the interview does not ask for the screen; the fullscreen lock, camera and integrity checks stay
        onWarning: handleIntegrityStateChange,
        onFullscreenExit: handleIntegrityStateChange,
        onStateChange: handleIntegrityStateChange,
    });

    const cameraStream = await acquireCamera();

    if (cameraStream) {
        attachCandidateCamera(cameraStream, false);            // visible + published NOW, before any microphone prompt
        await startMicAndRecording(cameraStream);
    } else {
        await showCameraProblem();                             // honest reason + Retry; the interview itself continues
    }

    startVoiceIdentityProctoring();

    await maybeStartRealtime();
}


// ------------------------------------------------------------
// VOICE IDENTITY PROCTORING (AI interview only -- see voice-identity-proctor.js's own
// module header for what this does and why it is a flag, never a counted violation).
// Reuses the SAME persistent mic stream InterviewVoiceV2 already opened; never a second
// getUserMedia call.
// ------------------------------------------------------------

let currentVoiceStateForIdentityCheck = null;

function isCandidateSpeakingNow() {
    return !!(window.InterviewVoiceV2 && currentVoiceStateForIdentityCheck === window.InterviewVoiceV2.STATES.CANDIDATE_SPEAKING);
}

function handlePossibleThirdPartyVoice() {
    if (window.IntegrityMode && window.IntegrityMode.reportIntegrityEvent) {
        window.IntegrityMode.reportIntegrityEvent("possible_third_party_voice");
    }
}

function startVoiceIdentityProctoring() {

    if (!window.VoiceIdentityProctor || !window.InterviewVoiceV2) return;

    const micStream = window.InterviewVoiceV2.getMicStream();
    if (!micStream) return;   // the mic never started (denied/unavailable): nothing to listen on

    window.VoiceIdentityProctor.start(micStream, isCandidateSpeakingNow, handlePossibleThirdPartyVoice);
}

function stopVoiceIdentityProctoring() {
    if (window.VoiceIdentityProctor) window.VoiceIdentityProctor.stop();
}


// ============================================================
// REALTIME CONVERSATION (Task 9) -- only when the server says so (INTERVIEW_REALTIME_PROVIDER=realtime).
// The default answer is "disabled": the existing voice pipeline is untouched.
// ============================================================

let realtimeActive = false;
let realtimeAttemptInFlight = false;   // true from the moment a live-voice attempt starts until it succeeds or definitively fails
let realtimeRefreshTimer = null;
let closingGoodbyeTimer = null;   // fallback close if the spoken goodbye never finishes


async function maybeStartRealtime() {

    if (!window.InterviewVoiceV2 || assessmentLocked) return;

    // Disable the manual "Start voice interview" button for the whole attempt: clicking it while the live room is still
    // connecting used to start the OLD local voice pipeline speaking the same greeting at the same time as the live
    // interviewer -- two voices talking over each other. It is re-enabled only if the live attempt definitively fails.
    realtimeAttemptInFlight = true;
    const joinButtonEarly = document.getElementById("iv2JoinButton");
    if (joinButtonEarly) {
        joinButtonEarly.disabled = true;
        joinButtonEarly.querySelector("span") && (joinButtonEarly.querySelector("span").textContent = "Connecting…");
    }

    // the interview room must be connected: the candidate's microphone is published to it
    const waitStarted = Date.now();
    while (!(window.InterviewAvatarV2 && window.InterviewAvatarV2.isConnected()) && Date.now() - waitStarted < 20000) {
        await new Promise((resolve) => setTimeout(resolve, 250));
    }

    // Without the room the candidate's microphone cannot reach the live interviewer (the server would talk into an empty room:
    // "candidate_frames: 0"). Say so and stay on the standard voice interview instead of starting a deaf realtime session.
    if (!(window.InterviewAvatarV2 && window.InterviewAvatarV2.isConnected())) {
        console.warn("Realtime interview not started: the interview room did not connect within 20 s.");
        showMessage("The live voice connection could not be made, so the standard (slower) voice interview is being used. Reload the page to try the live connection again.", "warning");
        realtimeAttemptInFlight = false;
        reenableJoinButtonForStandardVoice();
        return;
    }

    let response;
    try {
        response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/v2/realtime/start`,
            { method: "POST", credentials: "include" },
        );
    } catch (error) {
        realtimeAttemptInFlight = false;
        reenableJoinButtonForStandardVoice();
        return;                                                // the standard voice interview stays in charge
    }

    if (!response.ok) {
        if (response.status === 503) showMessage("The realtime voice interview is not available right now. The standard voice interview is being used.", "warning");
        else console.warn("Realtime /start returned HTTP " + response.status + ": falling back to the standard voice interview.");
        realtimeAttemptInFlight = false;
        reenableJoinButtonForStandardVoice();
        return;
    }

    const body = await parseJsonSafely(response);

    if (!body || body.state === "disabled" || body.state === "failed") {
        if (body && body.state === "failed") showMessage(body.error || "The realtime interview could not start. The standard voice interview is available.", "warning");
        else if (body && body.state === "disabled") {
            console.warn("Realtime interview is disabled on the server (INTERVIEW_REALTIME_PROVIDER is '" + (body.provider || "?") + "'): restart the backend after changing .env.");
            showMessage("Live voice is switched off on the server, so the standard (slower) voice interview is being used.", "warning");
        }
        realtimeAttemptInFlight = false;
        reenableJoinButtonForStandardVoice();
        return;
    }

    realtimeAttemptInFlight = false;
    enterRealtimeUi();
}


function reenableJoinButtonForStandardVoice() {
    const joinButton = document.getElementById("iv2JoinButton");
    if (!joinButton) return;
    joinButton.disabled = false;
    const label = joinButton.querySelector("span");
    if (label) label.textContent = "Start voice interview";
}


function enterRealtimeUi() {

    realtimeActive = true;
    voiceModeEnabled = true;
    window.InterviewVoiceV2.setRealtimeMode(true);

    // Defensive: if the candidate managed to click "Start voice interview" in the brief window before the button was
    // disabled, cut whatever the local pipeline is doing right now so the live interviewer is never talked over.
    window.InterviewVoiceV2.stopSpeaking();

    // no microphone card in live mode: the interviewer just listens. A text box is offered instead (below).
    const voiceCard = document.getElementById("iv2VoiceCard");
    if (voiceCard) voiceCard.style.display = "none";

    // the live interviewer greets the candidate by name itself, so the separate "Start voice interview" button and the
    // recorded welcome are not needed in this mode
    const joinButton = document.getElementById("iv2JoinButton");
    if (joinButton) joinButton.style.display = "none";

    // no record/upload/type cycle in this mode: hide those controls (nothing else on the page changes)
    ["iv2StartSpeakingButton", "iv2TranscriptPreview"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = "none";
    });
    const inputCard = document.querySelector(".iv2-input-card");
    if (inputCard) inputCard.style.display = "";          // (left collapsed: opening it would reveal the question text before it is spoken)

    setVoiceStatus(null, "Realtime interview — just talk naturally; you can interrupt at any time.");
}


function handleRealtimeFailed() {

    revealInterview();

    if (!realtimeActive) return;

    realtimeActive = false;
    voiceModeEnabled = false;
    window.InterviewVoiceV2.setRealtimeMode(false);

    // an explicit, visible switch back -- never a silent one
    const inputCard = document.querySelector(".iv2-input-card");
    if (inputCard) inputCard.style.display = "";
    const toggleRow = document.getElementById("iv2VoiceModeToggle");
    if (toggleRow) toggleRow.checked = false;
    showMessage("The realtime voice connection was lost. You can continue with the standard voice interview (turn on Voice Mode) or by typing.", "warning");
}


function refreshRealtimeTranscript() {

    if (realtimeRefreshTimer) return;                          // coalesce bursts of "turn recorded"

    realtimeRefreshTimer = setTimeout(() => {
        realtimeRefreshTimer = null;
        loadInterviewState(true);
    }, 300);
}


// ============================================================
// CONVERSATION RENDERING
// ============================================================

// The interviewer's questions must not be visible before the interview has actually begun: the caption on the stage and the
// transcript stay hidden until the AI starts speaking (or a fallback such as typing / a voice problem needs the text).
let interviewRevealed = false;
let lastOpeningMessage = "";

function revealInterview() {

    if (interviewRevealed) return;

    interviewRevealed = true;
    renderConversation(lastOpeningMessage);
}


// The stage caption's ONE job while the AI is talking: show exactly what it is saying, word for word, as it says it (from the
// live realtime transcript). It must never show the pre-spoken "suggested" question text instead -- that text can (and often
// does) differ from what the model actually chooses to say, which looked like "the caption doesn't match the voice".
let liveSpeakingActive = false;
let realtimeSpokenQuestions = 0;             // live mode: how many of the persisted questions have been SPOKEN (their text may now be shown)

function showLiveSpeakingCaption(text) {

    const caption = document.getElementById("iv2Caption");
    if (!caption) return;

    if (!text) {
        if (liveSpeakingActive && realtimeActive) realtimeSpokenQuestions = conversationTurns.length;   // that question has now been spoken
        liveSpeakingActive = false;              // the turn ended: let the normal (persisted-transcript) caption take over again
        renderConversation("");
        return;
    }

    liveSpeakingActive = true;
    caption.textContent = text;
    caption.hidden = false;
}


function renderConversation(openingMessage) {

    if (openingMessage) lastOpeningMessage = openingMessage;

    if (!interviewRevealed) {

        const waiting = document.getElementById("iv2Conversation");
        if (waiting) waiting.innerHTML = `<div class="text-muted small p-3">The interview is about to begin. The interviewer's questions appear here as they are spoken.</div>`;

        const hiddenCaption = document.getElementById("iv2Caption");
        if (hiddenCaption) { hiddenCaption.textContent = ""; hiddenCaption.hidden = true; }

        return;
    }

    const container = document.getElementById("iv2Conversation");

    let html = "";

    if (openingMessage && !(realtimeActive && realtimeSpokenQuestions === 0)) {
        html += `
            <div class="iv2-message-row is-ai">
                <div class="iv2-avatar is-ai"><i class="bi bi-robot"></i></div>
                <div>
                    <div class="iv2-message-label">AI Interviewer</div>
                    <div class="iv2-bubble">${escapeHtml(openingMessage)}</div>
                </div>
            </div>
        `;
    }

    // live mode: the next question is already stored on the server before the interviewer says it. Its text must not appear
    // until it has been spoken (the stage caption shows it word for word while it is spoken).
    const turnsToShow = realtimeActive
        ? conversationTurns.filter((turn, index) => !(index === conversationTurns.length - 1 && (turn.answer === null || turn.answer === undefined) && conversationTurns.length > realtimeSpokenQuestions))
        : conversationTurns;

    turnsToShow.forEach((turn) => {

        html += `
            <div class="iv2-message-row is-ai">
                <div class="iv2-avatar is-ai"><i class="bi bi-robot"></i></div>
                <div>
                    <div class="iv2-message-label">AI Interviewer</div>
                    <div class="iv2-bubble">${escapeHtml(turn.question)}</div>
                </div>
            </div>
        `;

        if (turn.answer !== null && turn.answer !== undefined) {

            html += `
                <div class="iv2-message-row is-candidate">
                    <div class="iv2-avatar is-candidate"><i class="bi bi-person-fill"></i></div>
                    <div>
                        <div class="iv2-message-label text-end">You</div>
                        <div class="iv2-bubble">${escapeHtml(turn.answer)}</div>
                    </div>
                </div>
            `;
        }
    });

    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;

    // live caption on the stage (like a video call): the interviewer's current question. While the live realtime transcript
    // is actively streaming (showLiveSpeakingCaption), it owns the caption -- the persisted "suggested" question text must
    // not overwrite it mid-sentence.
    const caption = document.getElementById("iv2Caption");
    if (caption && !liveSpeakingActive) {
        const last = turnsToShow.length ? turnsToShow[turnsToShow.length - 1] : null;
        const text = last ? last.question : (realtimeActive ? "" : (openingMessage || ""));
        caption.textContent = text || "";
        caption.hidden = !text;
    }
}


function showTypingIndicator() {

    const container = document.getElementById("iv2Conversation");

    container.insertAdjacentHTML("beforeend", `
        <div class="iv2-message-row is-ai" id="iv2TypingRow">
            <div class="iv2-avatar is-ai"><i class="bi bi-robot"></i></div>
            <div class="iv2-bubble">
                <span class="iv2-typing-dots"><span></span><span></span><span></span></span>
            </div>
        </div>
    `);

    container.scrollTop = container.scrollHeight;
}


function hideTypingIndicator() {
    const row = document.getElementById("iv2TypingRow");
    if (row) row.remove();
}


function renderProgress(progress) {

    // The competencies being evaluated (and how far each is covered) are for the recruiter, never the candidate: nothing is drawn here.
    if (true) return;

    const list = document.getElementById("iv2ProgressList");

    list.innerHTML = progress.map((p) => {

        const filled = Math.min(4, p.turns_touched);
        const dots = Array.from({ length: 4 }, (_, i) => `
            <span class="iv2-progress-dot ${i < filled ? "is-filled" : ""}"></span>
        `).join("");

        return `
            <div class="iv2-progress-row">
                <span>${escapeHtml(p.competency)}</span>
                <span class="iv2-progress-dots">${dots}</span>
            </div>
        `;
    }).join("");
}


// ============================================================
// VOICE MODE (Task 5I)
// ============================================================

function setVoiceStatus(kind, text) {

    const statusEl = document.getElementById("iv2VoiceStatus");
    const textEl = document.getElementById("iv2VoiceStatusText");

    if (!statusEl || !textEl) return;

    statusEl.classList.remove("is-ai-speaking", "is-listening", "is-candidate-speaking", "is-processing", "is-error");
    if (kind) statusEl.classList.add(`is-${kind}`);

    textEl.textContent = text;
}


// ============================================================
// Task 5K: AI interviewer avatar -> UI. The avatar controller
// (interview-avatar-v2.js) never touches interview state -- it
// only reports its own connection/speaking state, and this
// function only ever updates the avatar box's visuals.
// ============================================================

function handleAvatarStateChange(state) {

    const A = window.InterviewAvatarV2.STATES;
    const frame = document.getElementById("iv2AvatarFrame");
    const statusEl = document.getElementById("iv2AvatarStatus");
    const video = document.getElementById("iv2AvatarVideo");
    const placeholder = document.getElementById("iv2AvatarPlaceholder");

    if (!frame || !statusEl) return;

    if (interviewerVisualMode === "image") {
        // Fixed-image mode: the connection to the room is reported by the room status bar; the
        // interviewer label/state comes from the voice controller. No avatar messages here --
        // except the room connect step, which still has to run (mic/camera publishing).
        if (state === A.AVATAR_CONNECTED) {
            if (window.InterviewVoiceV2 && window.InterviewVideoRoomV2) {
                window.InterviewVoiceV2.ensureMicStream().then((micStream) => {
                    if (micStream) window.InterviewVideoRoomV2.notifyMicReady(micStream);
                });
            }
        }
        return;
    }

    frame.classList.toggle("is-ai-speaking", state === A.AI_SPEAKING);

    switch (state) {

        case A.AVATAR_CONNECTING:
            statusEl.textContent = "Connecting…";
            break;

        case A.AVATAR_CONNECTED:
            // Connected to the ROOM only; the avatar's own media has not
            // necessarily arrived yet -- do not claim it did.
            statusEl.textContent = "Connected — waiting for the AI interviewer to join…";
            window.InterviewAvatarV2.attachVideo(video);
            // Task 7: the avatar is driven by the SERVER (the same Kokoro response the voice flow
            // already made, streamed to Tavus), never by a browser "jobpilot.say" request -- so
            // no remote-speaker hook is registered and nothing here can create a second AI voice.
            // Task 5L: only now request the microphone (a real room to
            // publish into actually exists) -- reuses Task 5I/5J's own
            // persistent-stream function, never a second getUserMedia().
            if (window.InterviewVoiceV2 && window.InterviewVideoRoomV2) {
                window.InterviewVoiceV2.ensureMicStream().then((micStream) => {
                    if (micStream) window.InterviewVideoRoomV2.notifyMicReady(micStream);
                });
            }
            break;

        case A.AI_SPEAKING:
            statusEl.textContent = "🔊 AI Interviewer is speaking";
            break;

        case A.AI_IDLE:
            statusEl.textContent = "HireFlow AI Interviewer";
            break;

        case A.AI_INTERRUPTED:
            statusEl.textContent = "Listening to you…";
            break;

        case A.AVATAR_UNAVAILABLE:
            statusEl.textContent = "AI interviewer video is temporarily unavailable — continuing with audio and text.";
            video.style.display = "none";
            placeholder.style.display = "none";   // Task 8: the fixed image stays visible instead
            break;

        case A.AVATAR_ERROR:
            statusEl.textContent = "AI interviewer video is temporarily unavailable — continuing with audio and text.";
            video.style.display = "none";
            placeholder.style.display = "none";   // Task 8: the fixed image stays visible instead
            break;

        case A.COMPLETED:
            statusEl.textContent = "Interview complete.";
            break;

        default:
            break;
    }
}


// ============================================================
// Task 5L: video-room connection health + candidate camera/mic
// controls. This module never touches interview state either --
// only room transport health and which of the candidate's OWN
// already-captured tracks are currently published/muted.
// ============================================================

function handleVideoRoomStateChange(state, controls) {

    const R = window.InterviewVideoRoomV2.STATES;
    const dot = document.getElementById("iv2RoomDot");
    const statusText = document.getElementById("iv2RoomStatusText");

    if (statusText) {

        const labels = {
            [R.IDLE]: "Room connecting…",
            [R.CONNECTING]: "Room connecting…",
            [R.CONNECTED]: "Connected",
            [R.RECONNECTING]: "Reconnecting…",
            [R.DISCONNECTED]: "Disconnected",
            [R.ERROR]: "Connection error",
            [R.COMPLETED]: "Call ended",
        };

        statusText.textContent = labels[state] || "";
    }

    if (dot) {
        dot.style.background = state === R.CONNECTED ? "#22c55e"
            : state === R.RECONNECTING ? "#f59e0b"
            : (state === R.ERROR || state === R.DISCONNECTED) ? "#ef4444"
            : "#cbd5e1";
    }

    if (controls) updateRoomControlButtons(controls);
}


// Task 7: the server-side Tavus avatar's own status (idle | starting | ready | failed | unavailable).
// Only visuals/text change here; the voice interview is unaffected either way.
function handleServerAvatarStatus(state, error) {

    const statusEl = document.getElementById("iv2AvatarStatus");
    const video = document.getElementById("iv2AvatarVideo");
    const placeholder = document.getElementById("iv2AvatarPlaceholder");

    if (!statusEl) return;

    if (state === "disabled") {
        interviewerVisualMode = "image";                     // the server runs the fixed-image mode: nothing else to show
        return;
    }

    interviewerVisualMode = "avatar";                        // the live avatar was requested: follow it honestly
    if (window.InterviewAvatarV2 && video) window.InterviewAvatarV2.attachVideo(video);   // (idempotent) live avatar video goes to the video element

    if (state === "starting") {
        statusEl.textContent = "The AI interviewer is joining…";
    } else if (state === "ready") {
        statusEl.textContent = "HireFlow AI Interviewer is ready.";
    } else if (state === "failed" || state === "unavailable") {
        statusEl.textContent = "AI interviewer video is unavailable — the interview continues with voice and text.";
        if (video) video.style.display = "none";
        if (placeholder) placeholder.style.display = "none";   // Task 8: the fixed image stays visible instead
    }
}


window.addEventListener("jobpilot-avatar-media", (e) => {
    const video = document.getElementById("iv2AvatarVideo");
    const placeholder = document.getElementById("iv2AvatarPlaceholder");
    const statusEl = document.getElementById("iv2AvatarStatus");
    if (!video || !statusEl) return;
    if (interviewerVisualMode !== "avatar") return;          // fixed-image mode: never swap the image for video
    if (e.detail.video) {
        video.style.display = "block";
        placeholder.style.display = "none";
        statusEl.textContent = e.detail.audio ? "HireFlow AI Interviewer is ready." : "AI interviewer video connected (audio pending)…";
    } else {
        video.style.display = "none";
        placeholder.style.display = "none";   // Task 8: the fixed image stays visible instead
    }
});


function handleVideoRoomError(kind, detail) {
    console.warn(`Video room (${kind}):`, detail);
}


function updateRoomControlButtons(controls) {

    const micButton = document.getElementById("iv2MicToggleButton");
    const micLabel = document.getElementById("iv2MicToggleLabel");
    const cameraButton = document.getElementById("iv2CameraToggleButton");
    const cameraLabel = document.getElementById("iv2CameraToggleLabel");

    if (micButton && micLabel) {
        micButton.classList.toggle("is-off", !controls.micEnabled);
        micButton.setAttribute("aria-pressed", String(controls.micEnabled));
        micButton.setAttribute("aria-label", controls.micEnabled ? "Mute microphone" : "Unmute microphone");
        micLabel.textContent = controls.micEnabled ? "Mic On" : "Mic Off";
    }

    if (cameraButton && cameraLabel) {
        cameraButton.classList.toggle("is-off", !controls.cameraEnabled);
        cameraButton.setAttribute("aria-pressed", String(controls.cameraEnabled));
        cameraButton.setAttribute("aria-label", controls.cameraEnabled ? "Turn camera off" : "Turn camera on");
        cameraLabel.textContent = controls.cameraEnabled ? "Camera On" : "Camera Off";
    }
}


function toggleRoomControl(control) {

    if (!window.InterviewVideoRoomV2) return;

    const current = window.InterviewVideoRoomV2.getControls();
    const key = `${control}Enabled`;

    window.InterviewVideoRoomV2.setControlEnabled(control, !current[key]);
}


// Fans out Task 5J's voice-layer lifecycle events to the avatar
// controller. Interview-voice-v2.js knows nothing about the avatar
// -- this is the ONLY place the two are connected, and it never
// carries interview data, only event names (Task 5K Step 10).
function handleVoiceLayerEvent(eventName, detail) {

    if (window.InterviewAvatarV2) {
        window.InterviewAvatarV2.handleVoiceEvent(eventName);
    }

    // Task 4: a voice turn is an answer submission -- typed submit must not race it.
    if (eventName === "VOICE_TURN_STARTED") {
        submitInFlight = true;
        const input = document.getElementById("iv2AnswerInput");
        if (input) input.disabled = true;
    } else if (eventName === "VOICE_TURN_FINISHED") {
        submitInFlight = false;
        const input = document.getElementById("iv2AnswerInput");
        if (input && !assessmentLocked) input.disabled = false;
    } else if (eventName === "VOICE_SESSION_INVALID") {
        loadInterviewState(true); // the interview expired/completed elsewhere: show the real state
    } else if (eventName === "REALTIME_SPEAKING_TEXT") {
        showLiveSpeakingCaption((detail && detail.text) || "");
    } else if (eventName === "REALTIME_TURN_RECORDED") {
        refreshRealtimeTranscript();
    } else if (eventName === "REALTIME_FAILED") {
        handleRealtimeFailed();
    }
}


// Task 4: the whole voice turn (transcript + the brain's next question) arrived in one response.
// Apply it exactly like a typed answer's response, through the same render functions.
function handleVoiceTurnResult(data, meta) {

    if (conversationTurns.length) {
        conversationTurns[conversationTurns.length - 1].answer = data.transcript;
    }

    const session = data.session || {};
    const nextQuestion = session.current_question;

    if (nextQuestion && !data.interview_complete) {
        conversationTurns.push({ question: nextQuestion.question, answer: null });
        currentQuestionTurnNumber = nextQuestion.turn_number;
    }

    renderConversation(document.body.dataset.iv2OpeningMessage || "");

    if (session.progress) renderProgress(session.progress);

    const transcriptText = document.getElementById("iv2TranscriptText");
    const transcriptPreview = document.getElementById("iv2TranscriptPreview");
    if (transcriptText) transcriptText.textContent = data.transcript || "";
    if (transcriptPreview) transcriptPreview.style.display = "none";

    // Voice mode was turned off (or the page is leaving) while the turn was in flight:
    // there is no closing audio to wait for, so finish now.
    if (data.interview_complete && meta && meta.stale) {
        finalizeInterviewUi();
    }
}


function updateMicLevel(level) {

    const bar = document.getElementById("iv2MicLevelBar");
    if (bar) bar.style.width = `${Math.round(Math.max(0, Math.min(1, level)) * 100)}%`;
}


// ============================================================
// Task 5J: realtime turn-taking state -> UI. States come from
// interview-voice-turn-logic-v2.js via window.InterviewVoiceV2.STATES
// (loaded before both voice scripts, see ai-interview-v2.html).
// ============================================================

let voiceAutoSubmitArmed = false; // guards against double-submit if a stray duplicate CANDIDATE_TRANSCRIPT_FINAL ever fires

// ============================================================
// Task 8: the AI interviewer is a FIXED IMAGE. Its visual state is derived from the
// EXISTING voice-turn controller state (no second state machine); the optional live
// avatar (Tavus) is only used if the server explicitly enables that mode.
// ============================================================
let interviewerVisualMode = "image";     // "image" (default) | "avatar" (server said the live avatar is enabled)

function renderInterviewerState(voiceState) {

    if (!window.InterviewVisualStateV2) return;

    // after completion the controller's cleanup() falls back to IDLE: keep the calm completed look
    if (interviewFinalized && voiceState !== "COMPLETED") return;

    const statusEl = document.getElementById("iv2AvatarStatus");

    window.InterviewVisualStateV2.render(voiceState, {
        frame: document.getElementById("iv2AvatarFrame"),
        stateText: document.getElementById("iv2InterviewerState"),
        statusText: interviewerVisualMode === "image" ? statusEl : null,
        candidateFrame: document.getElementById("iv2CandidateFrame"),
        image: document.getElementById("iv2InterviewerImage"),
    });
}


function handleVoiceStateChange(state) {

    const V = window.InterviewVoiceV2.STATES;

    currentVoiceStateForIdentityCheck = state;

    if (state === V.AI_PREPARING || state === V.AI_SPEAKING) revealInterview();

    renderInterviewerState(state);
    const startButton = document.getElementById("iv2StartSpeakingButton");
    const transcriptPreview = document.getElementById("iv2TranscriptPreview");

    switch (state) {

        case V.AI_PREPARING:
            setVoiceStatus("ai-speaking", "AI is preparing to speak…");
            startButton.style.display = "none";
            transcriptPreview.style.display = "none";
            break;

        case V.AI_SPEAKING:
            setVoiceStatus("ai-speaking", "AI is speaking… (you can start talking anytime to interrupt)");
            startButton.style.display = "none";
            transcriptPreview.style.display = "none";
            break;

        case V.AI_INTERRUPTED:
            setVoiceStatus("listening", "Got it — AI stopped, go ahead");
            break;

        case V.REQUESTING_MIC:
            setVoiceStatus("processing", "Requesting microphone access…");
            startButton.style.display = "none";
            break;

        case V.CANDIDATE_LISTENING:
            setVoiceStatus(null, "🎤 Your turn — start speaking whenever you're ready");
            startButton.style.display = "inline-block";
            transcriptPreview.style.display = "none";
            break;

        case V.CANDIDATE_SPEAKING:
            setVoiceStatus("candidate-speaking", "🗣 You are speaking…");
            startButton.style.display = "none";
            break;

        case V.CANDIDATE_SILENCE:
            setVoiceStatus("processing", "Processing…");
            break;

        case V.TRANSCRIBING:
            setVoiceStatus("processing", "Processing your answer…");
            break;

        case V.AI_THINKING:
            setVoiceStatus("processing", "Transcribing your answer and thinking…");
            startButton.style.display = "none";
            break;

        case V.COMPLETED:
            setVoiceStatus(null, "Interview complete — thank you.");
            startButton.style.display = "none";
            transcriptPreview.style.display = "none";
            break;

        case V.SUBMITTING: {
            const transcript = window.InterviewVoiceV2.getTranscript();
            setVoiceStatus("processing", "Sending your answer…");
            document.getElementById("iv2TranscriptText").textContent = transcript;
            transcriptPreview.style.display = "block";
            handleVoiceTranscriptFinal(transcript);
            break;
        }

        case V.ERROR:
            startButton.style.display = "inline-block";
            transcriptPreview.style.display = "none";
            break;

        default:
            break;
    }
}


function handleVoiceError(kind, detail) {

    revealInterview();                       // a voice problem: the candidate needs to be able to read the question

    const messages = {
        mic_permission_denied: "Microphone access was denied. You can still answer by typing below.",
        no_speech: "No speech was detected. Please try again.",
        empty_transcript: "Couldn't understand the audio. Please try again.",
        unsupported: "Your browser doesn't support speech recognition. Please type your answer below.",
        stt_failed: "Speech recognition failed. Please try again or type your answer.",
        network_error: "A network error occurred. Please try again or type your answer.",
        tts_failed: "Couldn't generate speech for this reply — read it above and answer when ready.",
        brain_failed: "The interviewer couldn't process that answer. Please try again or type your answer.",
        mic_lost: "The microphone was disconnected or its permission was revoked. You can still answer by typing below.",
        session_ended: "This interview is no longer active.",
        tts_playback_failed: "Audio playback failed — read the question above and answer when ready.",
    };

    setVoiceStatus("error", messages[kind] || "Something went wrong with voice mode.");

    // the optional written answer box opens by itself when the microphone cannot be used
    if (["mic_permission_denied", "mic_lost", "unsupported", "stt_failed", "network_error"].includes(kind)) {
        const fallback = document.getElementById("iv2TypeFallback");
        if (fallback) fallback.open = true;
    }
}


// The interview is voice-first: one click ("Start voice interview") is the user gesture that lets the browser open the
// microphone and play the interviewer's voice, then the normal Voice Mode flow takes over.
async function startVoiceInterview() {

    const toggle = document.getElementById("iv2VoiceModeToggle");
    const join = document.getElementById("iv2JoinButton");

    // realtimeAttemptInFlight / realtimeActive: a live-voice attempt is in progress or already won -- the local pipeline
    // must never also start talking (that produced two overlapping AI voices).
    if (!toggle || voiceModeEnabled || realtimeAttemptInFlight || realtimeActive) return;

    if (join) join.style.display = "none";

    // the click is the user gesture that lets the browser play sound: the interviewer welcomes the candidate first,
    // then the normal voice flow reads the first question
    setVoiceStatus(null, "The interviewer is welcoming you…");
    if (window.InterviewVoiceV2 && typeof window.InterviewVoiceV2.say === "function") {
        await window.InterviewVoiceV2.say("welcome");
    }

    toggle.checked = true;
    toggleVoiceMode();
}


function speakCurrentQuestion() {

    if (!voiceModeEnabled || !window.InterviewVoiceV2 || currentQuestionTurnNumber === null) return;

    voiceAutoSubmitArmed = true;
    window.InterviewVoiceV2.speak(currentQuestionTurnNumber);
}


function toggleVoiceMode() {

    voiceModeEnabled = document.getElementById("iv2VoiceModeToggle").checked;

    const voiceCard = document.getElementById("iv2VoiceCard");
    voiceCard.style.display = voiceModeEnabled ? "block" : "none";

    if (voiceModeEnabled) {

        if (!window.InterviewVoiceV2.isVoiceCaptureSupported()) {
            showMessage("Your browser doesn't fully support voice input. You can still listen to AI questions and type your answers.", "warning");
        }

        // A real user gesture (this checkbox click) is the ONE place
        // we proactively ask for the microphone, so it can be reused
        // for the rest of the interview (barge-in monitoring +
        // auto-listen after each AI question) without re-prompting
        // every turn.
        if (window.InterviewVoiceV2.supportsBargeIn()) {
            window.InterviewVoiceV2.ensureMicStream();
        }

        speakCurrentQuestion();

    } else {

        window.InterviewVoiceV2.stopSpeaking();
        window.InterviewVoiceV2.stopListening();
    }
}


// Task 5J: the finalized transcript (after real end-of-turn silence,
// or a genuine interruption->speech->silence cycle) is submitted
// automatically through the EXISTING, unmodified submitAnswer() --
// no separate "Use Answer" click required for the realtime flow.
// The manual "Speak Again" button remains as an explicit override.
function handleVoiceTranscriptFinal(transcript) {

    if (!voiceAutoSubmitArmed) return; // voice mode was turned off mid-flight
    if (!transcript) return;
    if (submitInFlight || assessmentLocked) return;

    voiceAutoSubmitArmed = false;

    document.getElementById("iv2AnswerInput").value = transcript;
    document.getElementById("iv2TranscriptPreview").style.display = "none";
    submitAnswer();
}


function handleRetryVoiceAnswer() {

    document.getElementById("iv2TranscriptPreview").style.display = "none";
    voiceAutoSubmitArmed = true;
    window.InterviewVoiceV2.startListening();
}


// Manual override: normally the transcript auto-submits once the
// realtime silence detector finalizes it (handleVoiceTranscriptFinal
// above); this button stays wired as an explicit fallback path for
// the rare case a candidate wants to confirm by hand.
function handleUseVoiceAnswer() {

    const transcript = window.InterviewVoiceV2.getTranscript();
    if (!transcript || submitInFlight || assessmentLocked) return;

    document.getElementById("iv2AnswerInput").value = transcript;
    document.getElementById("iv2TranscriptPreview").style.display = "none";
    submitAnswer();
}


// ============================================================
// API HELPERS
// ============================================================

async function parseJsonSafely(response) {
    try {
        return await response.json();
    } catch (error) {
        return null;
    }
}


async function handleApiError(response, data, fallbackMessage) {

    if (response.status === 401) {

        if (window.HireFlowSessionGuard) {
            const genuine = await window.HireFlowSessionGuard.isAuthFailureGenuine(response);
            if (genuine) {
                window.HireFlowSessionGuard.redirectToLogin();
                return;
            }
        }
        showMessage("Your session appears to have been interrupted. Please retry.", "warning");
        return;
    }

    if (response.status === 403) {
        showMessage("You do not have permission to access this interview.", "danger");
        return;
    }

    if (response.status === 404) {
        showMessage("This interview could not be found.", "danger");
        return;
    }

    if (response.status === 409) {
        showMessage((data && data.detail) || fallbackMessage, "warning");
        return;
    }

    if (response.status === 410) {
        showMessage("This interview has expired or changed state. Refreshing…", "warning");
        await loadInterviewState(true);
        return;
    }

    if (response.status === 422) {
        showMessage((data && data.detail) || "The server rejected this request.", "danger");
        return;
    }

    if (response.status === 429) {
        showMessage("Too many requests — please slow down and try again shortly.", "warning");
        return;
    }

    showMessage((data && data.detail) || fallbackMessage, "danger");
}


// ============================================================
// INTERVIEW COMPLETION (shared by typed answers and voice turns)
// ============================================================

let interviewFinalized = false;

async function finalizeInterviewUi() {

    if (interviewFinalized) return; // a completion can be reported by two paths: run it once
    interviewFinalized = true;

    // Close the interview screen the moment the interviewer has finished speaking: stopping the recorder is instant, the
    // (large) upload happens afterwards while the thank-you screen is already showing.
    let pendingUpload = null;

    if (interviewVideoRecorder) {
        try {
            await interviewVideoRecorder.stop();
            pendingUpload = interviewVideoRecorder;
        } catch (recordingError) {
            console.warn("Video recording finalize failed:", recordingError);
        }
    }

    if (recordingMix) {
        recordingMix.close();                    // the recorder has stopped: the mixer is no longer needed
        recordingMix = null;
    }

    if (window.IntegrityMode && typeof window.IntegrityMode.cleanup === "function") {
        window.IntegrityMode.cleanup();
    }

    if (window.InterviewVoiceV2) {
        window.InterviewVoiceV2.cleanup();
    }

    if (window.InterviewVideoRoomV2) {
        window.InterviewVideoRoomV2.cleanup();
    }

    cleanupCamera();

    renderInterviewerState("COMPLETED");

    if (window.InterviewAvatarV2) {
        window.InterviewAvatarV2.cleanup();
    }

    assessmentLocked = true;
    stopCountdown();
    stopPhoneDetection();

    document.getElementById("iv2Body").style.display = "none";
    const topbar = document.getElementById("iv2Topbar");
    if (topbar) topbar.style.display = "none";
    document.getElementById("iv2ResultSection").style.display = "block";

    // upload the recording after the screen has closed; keep the page open (and the button locked) until it is saved
    if (pendingUpload) {
        const status = document.getElementById("iv2UploadStatus");
        const back = document.getElementById("iv2BackButton");
        if (status) status.textContent = "Saving your recording... please keep this page open for a moment.";
        if (back) { back.style.pointerEvents = "none"; back.style.opacity = "0.5"; }

        try {
            const result = await pendingUpload.upload(applicationId, "ai_interview");
            if (status) status.textContent = (result && result.ok === false) ? "Your answers are saved. The video could not be uploaded." : "Your recording has been saved.";
        } catch (recordingError) {
            console.warn("Video recording upload failed:", recordingError);
            if (status) status.textContent = "Your answers are saved. The video could not be uploaded.";
        }

        if (back) { back.style.pointerEvents = ""; back.style.opacity = ""; }
    }
}


// ============================================================
// SUBMIT ANSWER
// ============================================================

async function submitAnswer() {

    if (submitInFlight || assessmentLocked || terminatedByIntegrity) return;

    const input = document.getElementById("iv2AnswerInput");
    const answerText = input.value.trim();

    if (!answerText) {
        showMessage("Please type a response before submitting.", "warning");
        return;
    }

    // Live mode: the typed answer goes into the live conversation so the interviewer answers it out loud.
    if (realtimeActive) {
        submitInFlight = true;
        try {
            const live = await fetch(
                `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/v2/realtime/text`,
                { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answer_text: answerText }) },
            );
            if (live.ok) {
                input.value = "";
                clearMessage();
            } else {
                const detail = await parseJsonSafely(live);
                showMessage((detail && detail.detail) || "Your typed answer could not be sent. Please try again.", "warning");
            }
        } catch (error) {
            showMessage("Your typed answer could not be sent. Please try again.", "warning");
        }
        submitInFlight = false;
        return;
    }

    // Lock the current unanswered turn immediately in the UI so a
    // double-click can never submit twice.
    if (conversationTurns.length) {
        conversationTurns[conversationTurns.length - 1].answer = answerText;
    }
    renderConversation(document.body.dataset.iv2OpeningMessage || "");
    input.value = "";
    input.disabled = true;

    submitInFlight = true;
    const submitButton = document.getElementById("iv2SubmitButton");
    submitButton.disabled = true;
    submitButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Sending…`;

    showTypingIndicator();

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/v2/answer`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answer_text: answerText }),
            },
        );

        const data = await parseJsonSafely(response);

        hideTypingIndicator();

        if (!response.ok) {
            await handleApiError(response, data, "Unable to submit your answer right now.");
            input.disabled = false;
            return;
        }

        if (data.interview_complete) {
            await finalizeInterviewUi();
            return;
        }

        const currentQuestion = data.session.current_question;

        if (currentQuestion) {

            conversationTurns.push({ question: currentQuestion.question, answer: null });
            renderConversation(document.body.dataset.iv2OpeningMessage || "");
            currentQuestionTurnNumber = currentQuestion.turn_number;
            speakCurrentQuestion();
        }

        renderProgress(data.session.progress || []);

        input.disabled = false;
        input.focus();

    } catch (error) {

        hideTypingIndicator();
        console.error("Submit answer error:", error);
        showMessage("A network error occurred while submitting your answer.", "danger");
        input.disabled = false;

    } finally {

        submitInFlight = false;
        submitButton.disabled = false;
        submitButton.innerHTML = `<i class="bi bi-send me-1"></i> Submit Answer`;
    }
}


// ============================================================
// STATE LOAD
// ============================================================

function lockAssessmentUi(message, type) {
    assessmentLocked = true;
    stopCountdown();

    const submitButton = document.getElementById("iv2SubmitButton");
    const input = document.getElementById("iv2AnswerInput");
    if (submitButton) submitButton.disabled = true;
    if (input) input.disabled = true;

    if (message) showMessage(message, type || "warning");
}


function hydrateConversationFromState(sessionData) {

    conversationTurns = (sessionData.answered_turns || []).map((t) => ({
        question: t.question, answer: t.answer,
    }));

    if (sessionData.current_question) {
        conversationTurns.push({ question: sessionData.current_question.question, answer: null });
        currentQuestionTurnNumber = sessionData.current_question.turn_number;
    } else {
        currentQuestionTurnNumber = null;
    }
}


async function loadInterviewState(isRefresh) {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/v2`,
            { credentials: "include" },
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {
            await handleApiError(response, data, "Unable to load your interview.");
            return;
        }

        if (data.status === "in_progress") {

            hideLoading();
            clearMessage();

            document.getElementById("iv2Topbar").style.display = "flex";
            document.getElementById("iv2Body").style.display = "flex";

            renderJobHeader(data.job);
            startCountdown(data.expires_at);

            hydrateConversationFromState(data);
            renderConversation(isRefresh ? "" : (document.body.dataset.iv2OpeningMessage || ""));
            renderProgress(data.progress || []);

        } else if (data.status === "completed") {

            // The final answer is recorded (so the interview reads "completed") BEFORE the interviewer has spoken the
            // closing. While the live room is still talking, keep it open: the server's "complete" signal (sent once the
            // goodbye has finished playing) closes the UI. A fallback timer covers a goodbye that never arrives.
            if (isRefresh && realtimeActive && !interviewFinalized) {
                if (!closingGoodbyeTimer) {
                    closingGoodbyeTimer = setTimeout(() => { closingGoodbyeTimer = null; finalizeInterviewUi(); }, 45000);
                }
                return;
            }

            hideLoading();
            assessmentLocked = true;
            stopCountdown();

            // A finished interview must not hold a LiveKit room / avatar open.
            if (window.InterviewAvatarV2) window.InterviewAvatarV2.cleanup();
            stopVoiceIdentityProctoring();

            document.getElementById("iv2Topbar").style.display = "none";
            document.getElementById("iv2Body").style.display = "none";
            document.getElementById("iv2ResultSection").style.display = "block";

        } else if (data.status === "expired") {

            hideLoading();
            document.getElementById("iv2Topbar").style.display = "flex";
            renderJobHeader(data.job);
        } else if (data.status === "terminated") {

            hideLoading();
            assessmentLocked = true;
            stopCountdown();
            if (window.InterviewAvatarV2) window.InterviewAvatarV2.cleanup();
            if (window.InterviewVoiceV2) window.InterviewVoiceV2.cleanup();
            stopVoiceIdentityProctoring();
            document.getElementById("iv2Topbar").style.display = "flex";
            renderJobHeader(data.job);
            lockAssessmentUi("This AI interview has been terminated due to proctoring integrity violations.", "danger");

        } else {
            hideLoading();
            showMessage(`Interview status: ${data.status}`, "info");
        }

    } catch (error) {
        console.error("Load interview state error:", error);
        showMessage("A network error occurred while loading your interview.", "danger");
    }
}


// ============================================================
// INIT
// ============================================================

document.addEventListener("DOMContentLoaded", async () => {

    currentUser = getLoggedInUser();
    applicationId = getApplicationIdFromUrl();

    if (!currentUser) {
        window.location.href = "index.html";
        return;
    }

    if (!applicationId) {
        showMessage("No application specified for this interview.", "danger");
        hideLoading();
        return;
    }

    document.getElementById("iv2SubmitButton").addEventListener("click", submitAnswer);

    document.getElementById("iv2AnswerInput").addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            submitAnswer();
        }
    });

    if (window.InterviewVideoRoomV2) {

        window.InterviewVideoRoomV2.init({
            onStateChange: handleVideoRoomStateChange,
            onError: handleVideoRoomError,
        });

        document.getElementById("iv2MicToggleButton").addEventListener("click", () => toggleRoomControl("mic"));
        const cameraRetryButton = document.getElementById("iv2CameraRetryButton");
        if (cameraRetryButton) cameraRetryButton.addEventListener("click", retryCamera);
        document.getElementById("iv2CameraToggleButton").addEventListener("click", () => toggleRoomControl("camera"));
    }

    if (window.InterviewAvatarV2) {

        window.InterviewAvatarV2.init({
            apiBaseUrl: API_BASE_URL,
            applicationId,
            onStateChange: handleAvatarStateChange,
            onSpeechMessage: (msg) => { if (window.InterviewVoiceV2) window.InterviewVoiceV2.handleRemoteSpeech(msg); },
            onAvatarStatus: handleServerAvatarStatus,
            onRoomEvent: (name) => {
                if (window.InterviewVideoRoomV2) window.InterviewVideoRoomV2.handleRoomEvent(name);
                if (window.InterviewVoiceV2) window.InterviewVoiceV2.handleTransportEvent(name);   // Task 6: a lost room ends a pending LiveKit-delivered response
            },
        });
    }

    if (window.InterviewVoiceV2) {

        window.InterviewVoiceV2.init({
            apiBaseUrl: API_BASE_URL,
            applicationId,
            onStateChange: handleVoiceStateChange,
            onError: handleVoiceError,
            onEvent: handleVoiceLayerEvent,
            onVoiceTurn: handleVoiceTurnResult,
            onVoiceTurnComplete: finalizeInterviewUi,
            onLevel: updateMicLevel,
            // Task 6: the AI's voice travels through the interview's LiveKit room when it is
            // connected (decided per turn); "?transport=local" forces the local playback path.
            transport: new URLSearchParams(window.location.search).get("transport") === "local" ? "local" : "livekit",
            isRoomConnected: () => !!(window.InterviewAvatarV2 && window.InterviewAvatarV2.isConnected()),
            onRemoteAudioMute: (muted) => { if (window.InterviewAvatarV2) window.InterviewAvatarV2.setRemoteAudioMuted(muted); },
        });

        // The mic and any playback/timers must never outlive the page.
        window.addEventListener("pagehide", () => { cleanupCamera(); stopVoiceIdentityProctoring(); window.InterviewVoiceV2.cleanup(); });

        document.getElementById("iv2VoiceModeToggle").addEventListener("change", toggleVoiceMode);
        document.getElementById("iv2JoinButton").addEventListener("click", startVoiceInterview);
        // opening the typed-answer box means the candidate is answering in writing: the questions must be readable
        const typeFallback = document.getElementById("iv2TypeFallback");
        if (typeFallback) typeFallback.addEventListener("toggle", () => { if (typeFallback.open) revealInterview(); });
        // synthesise the personal greeting while the candidate reads the page (the fixed lines are already on disk)
        if (typeof window.InterviewVoiceV2.prefetchSpokenLines === "function") {
            window.InterviewVoiceV2.prefetchSpokenLines();
        }
        document.getElementById("iv2StartSpeakingButton").addEventListener("click", () => window.InterviewVoiceV2.startListening());
        document.getElementById("iv2UseAnswerButton").addEventListener("click", handleUseVoiceAnswer);
        document.getElementById("iv2RetryVoiceButton").addEventListener("click", handleRetryVoiceAnswer);
    }

    if (!window.InterviewVoiceV2) interviewRevealed = true;          // no voice at all: the text is the interview
    renderInterviewerState("IDLE");

    // NOTE: the interview room is joined only AFTER the interview has started (below). The room token is refused ("no active
    // interview", HTTP 409) until then, and its retries give up after ~17 s: joining before the candidate has finished reading the
    // instructions pop-up left the room permanently disconnected ("live voice connection could not be made").

    // instructions + tick box before the interview (and its 30-minute clock) starts
    if (window.AssessmentInstructions) {
        const accepted = await window.AssessmentInstructions.ensure({ apiBase: API_BASE_URL, applicationId, type: "ai_interview" });
        if (!accepted) {
            window.location.href = "index.html";
            return;
        }
    }

    // Mandatory camera/microphone check -- same gate every other stage now uses
    // (aptitude/programming/SJT/TDA). Runs BEFORE the device-check lobby below so
    // that lobby's own "Skip this check" only skips the device-picker/mic-level
    // niceties, never the underlying permission requirement itself.
    if (window.IntegrityMode) {
        await window.IntegrityMode.runCameraReadinessCheck();
    }

    // camera/mic device-check lobby -- also before the clock starts; lets the candidate
    // confirm their mic is actually being picked up before it counts for real
    if (window.DeviceCheckLobby) {
        await window.DeviceCheckLobby.show();
    }

    try {

        const startResponse = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/v2/start`,
            { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" } },
        );

        const startData = await parseJsonSafely(startResponse);

        if (!startResponse.ok) {
            await handleApiError(startResponse, startData, "Unable to start your AI interview.");
            hideLoading();
            return;
        }

        if (startData.opening_message) {
            document.body.dataset.iv2OpeningMessage = startData.opening_message;
        }

    } catch (error) {
        console.error("Start interview error:", error);
        showMessage("A network error occurred while starting your interview.", "danger");
        hideLoading();
        return;
    }

    if (window.InterviewAvatarV2) {
        window.InterviewAvatarV2.connect();          // the interview exists now: the room token is granted
    }

    await loadInterviewState(false);
    await activateStrictModeForInterviewV2();

    window.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && !assessmentLocked) {
            loadInterviewState(true);
        }
    });

    window.addEventListener("pageshow", () => {
        if (!assessmentLocked) loadInterviewState(true);
    });
});

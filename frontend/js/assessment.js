/* =========================================================
   JOBPILOT AI - ASSESSMENT SESSION / SELF INTRODUCTION

   Mirrors the existing aptitude-test.js conventions exactly:
   same API_BASE_URL, same getLoggedInUser()/getApplicationIdFromUrl()
   helpers, same fetch(credentials:"include") pattern. CSRF
   headers are injected automatically by csrf-fetch.js, which
   is loaded before this script.

   The 2-minute countdown shown here is purely visual. The
   backend records the real start time and independently
   enforces the time limit on submit — this script can never
   extend it by manipulating the client clock.
   ========================================================= */

const API_BASE_URL = "http://127.0.0.1:8000";

const SELF_INTRO_STAGE_ORDER = [
    "self_intro",
    "aptitude",
    "programming",
    "ai_interview",
];

const SELF_INTRO_STAGE_LABELS = {
    self_intro: "Self Introduction",
    aptitude: "Role Test",
    programming: "Practical Task",
    ai_interview: "AI Interview",
};

let applicationId = null;
let currentUser = null;

let countdownIntervalId = null;
let remainingSeconds = 120;

let mediaStream = null;
let submitInFlight = false;

// Task 4 - Video Recording Foundation: the one shared recorder
// instance for the Self Intro stage. Self Intro acquires its own
// camera+mic stream above (mediaStream) rather than reusing
// IntegrityMode's, since it runs its own separate preview UI
// before Strict Assessment Mode ever starts for the later
// stages - this recorder attaches to THAT existing stream, never
// a second getUserMedia() call of its own.
let selfIntroVideoRecorder = null;


/* =========================================================
   GET LOGGED-IN USER / APPLICATION ID
   ========================================================= */

function getLoggedInUser() {

    try {

        const storedUser = localStorage.getItem("jobpilot_user");

        if (!storedUser) {
            return null;
        }

        return JSON.parse(storedUser);

    } catch (error) {

        console.error("Unable to read logged-in user:", error);
        return null;
    }
}


function getApplicationIdFromUrl() {

    const params = new URLSearchParams(window.location.search);
    const value = params.get("application_id");
    const parsed = Number(value);

    return (
        value &&
        Number.isInteger(parsed) &&
        parsed > 0
    )
        ? parsed
        : null;
}


/* =========================================================
   HTML ESCAPE
   ========================================================= */

function escapeHtml(value) {

    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


/* =========================================================
   SHOW MESSAGE / SECTION HELPERS
   ========================================================= */

function showAssessmentMessage(message, type = "danger") {

    const container = document.getElementById("assessmentMessage");

    if (!container) {
        return;
    }

    container.innerHTML = `
        <div class="alert alert-${type}" role="alert">
            ${escapeHtml(message)}
        </div>
    `;
}


function clearAssessmentMessage() {

    const container = document.getElementById("assessmentMessage");

    if (container) {
        container.innerHTML = "";
    }
}


const ASSESSMENT_SECTION_IDS = [
    "selfIntroIntroSection",
    "selfIntroRecordingSection",
    "selfIntroDoneSection",
];


function showAssessmentSection(sectionId) {

    ASSESSMENT_SECTION_IDS.forEach(function (id) {

        const element = document.getElementById(id);

        if (element) {

            element.style.display =
                id === sectionId ? "block" : "none";
        }
    });

    const loading = document.getElementById("assessmentLoading");

    if (loading) {
        loading.style.display = "none";
    }
}


function hideAllAssessmentSections() {

    ASSESSMENT_SECTION_IDS.forEach(function (id) {

        const element = document.getElementById(id);

        if (element) {
            element.style.display = "none";
        }
    });
}


function setLoadingText(message) {

    const loadingText = document.getElementById("assessmentLoadingText");

    if (loadingText && message) {
        loadingText.textContent = message;
    }
}


/* =========================================================
   FORMAT HELPERS
   ========================================================= */

function formatClock(totalSeconds) {

    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    const minutes = Math.floor(safeSeconds / 60);
    const seconds = safeSeconds % 60;

    return `${minutes}:${String(seconds).padStart(2, "0")}`;
}


function formatDisplayDate(value) {

    if (!value) {
        return "-";
    }

    const date = new Date(value.endsWith("Z") ? value : `${value}Z`);

    if (Number.isNaN(date.getTime())) {
        return "-";
    }

    return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}


/* =========================================================
   PROGRESS TRACKER
   ========================================================= */

function renderProgressTracker(currentStage) {

    const container = document.getElementById("assessmentProgress");

    if (!container) {
        return;
    }

    const currentIndex = SELF_INTRO_STAGE_ORDER.indexOf(currentStage);

    container.innerHTML = SELF_INTRO_STAGE_ORDER
        .map(function (stage, index) {

            let stateClass = "";

            if (currentIndex === -1) {

                stateClass = index === 0 ? "is-active" : "";

            } else if (index < currentIndex) {

                stateClass = "is-done";

            } else if (index === currentIndex) {

                stateClass = "is-active";
            }

            const icon =
                stateClass === "is-done"
                    ? '<i class="bi bi-check-lg"></i>'
                    : index + 1;

            return `
                <div class="assessment-progress-step ${stateClass}">
                    <div class="assessment-progress-dot">${icon}</div>
                    <div class="assessment-progress-label">
                        ${escapeHtml(SELF_INTRO_STAGE_LABELS[stage])}
                    </div>
                </div>
            `;
        })
        .join("");

    container.style.display = "flex";
}


function renderJobHeader(job) {

    const header = document.getElementById("assessmentJobHeader");
    const titleEl = document.getElementById("assessmentJobTitle");
    const companyEl = document.getElementById("assessmentCompanyName");

    if (titleEl) {
        titleEl.textContent = (job && job.title) || "Recruitment Assessment";
    }

    if (companyEl) {
        companyEl.textContent = (job && job.company) || "";
    }

    if (header) {
        header.style.display = "block";
    }
}


/* =========================================================
   CAMERA / MIC RECORDING (local preview only — never uploaded)
   ========================================================= */

function captureSelfIntroIdentityPhoto(video, attempt) {

    attempt = attempt || 0;

    if (!video || !applicationId) {
        return;
    }

    if (video.readyState < 2 || !video.videoWidth) {

        if (attempt < 10) {
            setTimeout(function () { captureSelfIntroIdentityPhoto(video, attempt + 1); }, 500);
        }

        return;
    }

    try {

        const canvas = document.createElement("canvas");
        canvas.width = 320;
        canvas.height = 240;
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(function (blob) {

            if (!blob) return;

            const form = new FormData();
            form.append("reason", "identity_reference");
            form.append("file", blob, "snapshot.jpg");

            fetch(
                `${API_BASE_URL}/api/assessment/snapshots/${applicationId}/self_intro`,
                { method: "POST", credentials: "include", body: form }
            ).catch(function () { /* evidence is best-effort */ });

        }, "image/jpeg", 0.6);

    } catch (error) {
        /* a failed photo is not an integrity signal */
    }
}


async function startCameraPreview() {

    const video = document.getElementById("selfIntroVideo");
    const placeholder = document.getElementById("selfIntroVideoPlaceholder");
    const recBadge = document.getElementById("selfIntroRecBadge");
    const permissionNote = document.getElementById("selfIntroPermissionNote");

    try {

        mediaStream = await navigator.mediaDevices.getUserMedia({
            video: true,
            audio: true,
        });

        if (video) {

            video.srcObject = mediaStream;
            video.style.display = "block";
        }

        if (placeholder) {
            placeholder.style.display = "none";
        }

        if (recBadge) {
            recBadge.style.display = "flex";
        }

        if (permissionNote) {
            permissionNote.style.display = "none";
        }

        // Task 4: attach the shared video recorder to this SAME
        // camera/mic stream — never a second getUserMedia() call.
        // Live preview and self-intro submission both still work
        // even if MediaRecorder itself is unsupported; recording
        // is evidence attached to the stage, not a requirement
        // for submission.
        if (
            window.AssessmentVideoRecorder &&
            (!selfIntroVideoRecorder || !selfIntroVideoRecorder.isRecording())
        ) {

            selfIntroVideoRecorder = new window.AssessmentVideoRecorder();

            if (!selfIntroVideoRecorder.isSupported()) {

                console.warn(
                    "AssessmentVideoRecorder is not supported in " +
                    "this browser — self-intro recording will not " +
                    "be captured."
                );

            } else {

                selfIntroVideoRecorder.start(mediaStream, "self_intro");
            }
        }

        // Identity reference photo for the recruiter, taken the moment the camera is live and before the
        // self-introduction begins (the server keeps only the first identity_reference per stage).
        captureSelfIntroIdentityPhoto(video);

        // Task 5: visible mobile phone detection, reusing this
        // SAME <video> element (already bound to mediaStream
        // above) — never a second getUserMedia() call. Self Intro
        // has no Strict Assessment Mode / integrity-event
        // integration (by original design, see assessment-integrity.js),
        // so a confirmed detection shows the candidate-facing warning and
        // is also recorded as a camera FLAG for the recruiter (never
        // counted, see reportSelfIntroCameraEvent). The face checks below
        // run on the same preview.
        startPhoneDetection(video);
        startSelfIntroFaceMonitoring(video);

    } catch (permissionError) {

        console.warn("Camera/microphone permission error:", permissionError);

        if (recBadge) {
            recBadge.style.display = "none";
        }

        if (permissionNote) {

            permissionNote.style.display = "block";

            permissionNote.innerHTML = `
                <i class="bi bi-info-circle me-1"></i>
                Camera/microphone access was not granted.
                You can still complete your self-introduction
                using the written introduction field below —
                your 2-minute timer keeps running.
            `;
        }
    }
}


function stopCameraPreview() {

    // Task 4: best-effort finalize + discard here. The normal
    // submit path (submitSelfIntro) already awaits stop()+upload()
    // BEFORE calling this function, so by the time we get here in
    // that path the recorder has nothing left to finalize. This
    // branch only matters for the defensive callers (already-done
    // render, beforeunload) where no upload should be attempted —
    // per Step 11, an unreliable beforeunload upload could corrupt
    // the recording, so we only ever stop/cleanup here, never upload.
    if (selfIntroVideoRecorder && selfIntroVideoRecorder.isRecording()) {

        try {
            selfIntroVideoRecorder.stop();
        } catch (error) {
            console.warn("Error stopping self-intro recorder:", error);
        }
    }

    if (selfIntroVideoRecorder) {
        selfIntroVideoRecorder.cleanup();
    }

    stopPhoneDetection();

    if (mediaStream) {

        mediaStream.getTracks().forEach(function (track) {
            track.stop();
        });

        mediaStream = null;
    }

    const recBadge = document.getElementById("selfIntroRecBadge");

    if (recBadge) {
        recBadge.style.display = "none";
    }
}


/* =========================================================
   TASK 5 - VISIBLE MOBILE PHONE DETECTION (thin wiring only;
   all detection/tracking/confirmation/cooldown/UI logic lives in
   the one shared phone-detector.js — see that file's own
   header). Self Intro has no Strict Assessment Mode / integrity-
   event integration at all (by original design), so a confirmed
   detection here only ever shows the candidate-facing warning —
   there is no backend event to report for this stage.
   ========================================================= */

// Camera attention checks for the self introduction, on the SAME camera preview (never a second camera stream): face lost, more than one
// person, looking away, covered camera, a phone. They are only recorded for the recruiter (never shown to the candidate, never counted, they
// can never end the introduction) and feed the "observed behaviour" note under the video. See assessment_integrity.py /self-intro-event.
function reportSelfIntroCameraEvent(eventType) {

    if (!applicationId) {
        return;
    }

    fetch(`${API_BASE_URL}/api/assessment/integrity/self-intro-event`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ application_id: applicationId, event_type: eventType }),
    }).catch(function () { /* evidence is best-effort: it never disturbs the introduction */ });
}

function startSelfIntroFaceMonitoring(videoEl) {

    if (!window.FaceProctor || !videoEl || !window.FaceProctor.isSupported()) {
        return;
    }

    const reportable = {
        face_missing: true, multiple_faces_detected: true, looking_away: true, gaze_away: true, gaze_away_sustained: true, camera_covered: true,
    };

    // lighting / framing advice under the camera preview, as the candidate speaks (advice only; a quiet "poor_lighting" note for the recruiter)
    let lightingCoach = null;

    if (window.CameraQuality) {

        const tip = window.CameraQuality.createTip({ anchor: videoEl.parentNode || videoEl, goodText: "Thanks, the picture looks good now." });
        let noteTimer = null;

        lightingCoach = window.CameraQuality.createCoach({
            onChange: function (issues) {

                tip.update(issues);

                const poor = issues.some(function (i) { return i.code === "too_dark" || i.code === "dim" || i.code === "backlit" || i.code === "glare" || i.code === "no_face_dark"; });

                if (!poor && noteTimer) { clearTimeout(noteTimer); noteTimer = null; }

                if (poor && !noteTimer) {
                    noteTimer = setTimeout(function () { noteTimer = null; reportSelfIntroCameraEvent("poor_lighting"); }, 20000);
                }
            },
        });
    }

    window.FaceProctor.start(videoEl, {
        onStatus: function () { /* the self-introduction page shows no camera status */ },
        onSample: function (sample) {
            if (lightingCoach && sample.quality) lightingCoach.feed(sample.quality);
        },
        onEvent: function (type) {
            if (reportable[type]) reportSelfIntroCameraEvent(type);
        },
    });
}

function stopSelfIntroFaceMonitoring() {

    if (window.FaceProctor && typeof window.FaceProctor.stop === "function") {
        try { window.FaceProctor.stop(); } catch (error) { /* already stopped */ }
    }
}

function startPhoneDetection(videoEl) {

    if (!window.PhoneDetector || !videoEl) {
        return;
    }

    window.PhoneDetector.start(videoEl, {
        onConfirmedDetection: function () {
            // The warning UI is shown by phone-detector.js itself; for the recruiter the sighting is recorded as a flag.
            reportSelfIntroCameraEvent("mobile_phone_detected");
        },
    });
}

function stopPhoneDetection() {

    stopSelfIntroFaceMonitoring();

    if (window.PhoneDetector) {
        window.PhoneDetector.stop();
    }
}


/* =========================================================
   COUNTDOWN (visual only — server enforces the real limit)
   ========================================================= */

function stopCountdown() {

    if (countdownIntervalId) {

        clearInterval(countdownIntervalId);
        countdownIntervalId = null;
    }
}


function renderCountdown() {

    const timerEl = document.getElementById("selfIntroTimer");

    if (!timerEl) {
        return;
    }

    timerEl.textContent = formatClock(remainingSeconds);
    timerEl.classList.toggle("is-low", remainingSeconds <= 15);
}


function startCountdown(initialRemainingSeconds) {

    stopCountdown();

    remainingSeconds = Math.max(0, Math.floor(initialRemainingSeconds));
    renderCountdown();

    countdownIntervalId = setInterval(function () {

        remainingSeconds -= 1;
        renderCountdown();

        if (remainingSeconds <= 0) {

            stopCountdown();

            // Auto-stop at 2 minutes. The backend independently
            // confirms/enforces the real elapsed time regardless
            // of what this client-side clock says.
            submitSelfIntro(true);
        }

    }, 1000);
}


/* =========================================================
   RENDER: SELF-INTRO STATES
   ========================================================= */

function renderSelfIntroNotStarted() {

    showAssessmentSection("selfIntroIntroSection");
}


async function renderSelfIntroInProgress(selfIntro) {

    // a reload in the middle of the introduction lost the share: ask again (the clock is already running on the server)
    await requireScreenShare();

    showAssessmentSection("selfIntroRecordingSection");

    const seed =
        selfIntro.remaining_seconds !== null &&
        selfIntro.remaining_seconds !== undefined
            ? selfIntro.remaining_seconds
            : 120;

    startCountdown(seed);

    await startCameraPreview();
}


function renderSelfIntroDone(sessionData) {

    stopCountdown();
    stopCameraPreview();

    showAssessmentSection("selfIntroDoneSection");

    const selfIntro = sessionData.session.self_intro;

    const messageEl = document.getElementById("selfIntroDoneMessage");
    const durationEl = document.getElementById("selfIntroDoneDuration");
    const dateEl = document.getElementById("selfIntroDoneDate");
    const nextStageEl = document.getElementById("assessmentNextStageAction");

    if (selfIntro && selfIntro.status === "expired") {

        // Task 4D-SELF-INTRO-FIX: an expired self-introduction is
        // no longer a dead end — the backend already advances the
        // assessment past this stage, so the next stage's action
        // must still render below. This message is honest about
        // what happened without leaving the candidate stuck.
        if (messageEl) {

            messageEl.textContent =
                "The 2-minute time limit for your self-" +
                "introduction was reached. Your response (if any) " +
                "has been recorded, and you may continue to the " +
                "next step.";
        }

        if (durationEl) {

            durationEl.textContent =
                selfIntro.duration_seconds !== null &&
                selfIntro.duration_seconds !== undefined
                    ? `${selfIntro.duration_seconds}s`
                    : "-";
        }

        if (dateEl) {

            dateEl.textContent = formatDisplayDate(
                selfIntro.completed_at
            );
        }

        if (nextStageEl) {
            renderAptitudeNextStage(nextStageEl, sessionData);
        }

        return;
    }

    if (messageEl) {

        messageEl.textContent =
            "Thank you for your introduction. Your next step is " +
            "shown below and unlocks automatically.";
    }

    if (durationEl) {

        durationEl.textContent =
            selfIntro && selfIntro.duration_seconds !== null &&
            selfIntro.duration_seconds !== undefined
                ? `${selfIntro.duration_seconds}s`
                : "-";
    }

    if (dateEl) {

        dateEl.textContent = formatDisplayDate(
            selfIntro ? selfIntro.completed_at : null
        );
    }

    if (nextStageEl) {
        renderAptitudeNextStage(nextStageEl, sessionData);
    }
}


/* =========================================================
   APTITUDE (Task 4B — AI-personalized aptitude integration)

   The actual question-answering experience is entirely the
   pre-existing aptitude-test.html/js — this page only ever
   shows readiness + a personalization preview, then hands off
   via a plain link. Nothing here ever sees a question or a
   correct answer.
   ========================================================= */

const APTITUDE_READY_STAGES = new Set([
    "aptitude",
    "programming",
    "ai_interview",
    "completed",
]);


/* =========================================================
   AUTOMATIC PIPELINE (Task 6A)

   Everything here is rendered from sessionData.pipeline, which the
   backend derives from the persisted stage results
   (stage_progression_service.determine_next_stage). The frontend
   never decides which stage is next or unlocked; it only shows it
   and links to the URL the backend returned.
   ========================================================= */

function renderAptitudeNextStage(nextStageEl, sessionData) {

    renderAptitudeNextStageInner(nextStageEl, sessionData);

    if (sessionData && sessionData.pipeline) {
        nextStageEl.insertAdjacentHTML(
            "afterbegin",
            buildPipelineTrackerHtml(sessionData.pipeline)
        );
    }
}


function buildPipelineTrackerHtml(pipeline) {

    const badge = {
        completed: ["success", "bi-check-circle-fill", "Completed"],
        current: ["primary", "bi-play-circle-fill", "Current"],
        locked: ["secondary", "bi-lock-fill", "Locked"],
        failed: ["danger", "bi-x-circle-fill", "Not passed"],
    };

    const rows = (pipeline.stages || []).map((stage) => {

        const b = badge[stage.status] || badge.locked;

        return `
            <li class="list-group-item d-flex justify-content-between align-items-center"
                data-pipeline-stage="${escapeHtml(stage.stage)}"
                data-pipeline-status="${escapeHtml(stage.status)}">
                <span><i class="bi ${b[1]} text-${b[0]} me-2"></i>${escapeHtml(stage.label)}</span>
                <span class="badge text-bg-${b[0]}">${b[2]}</span>
            </li>
        `;
    }).join("");

    let footer = "";

    if (pipeline.outcome === "completed") {

        footer = `
            <div class="alert alert-success mt-3 mb-0" id="pipelineOutcome">
                <i class="bi bi-trophy-fill me-1"></i>
                Assessment complete. Your results have been recorded for the hiring team.
            </div>
        `;

    } else if (pipeline.outcome === "failed") {

        footer = `
            <div class="alert alert-danger mt-3 mb-0" id="pipelineOutcome">
                The assessment ended at this stage. No further stages will be assigned.
            </div>
        `;

    } else if (
        pipeline.next_action &&
        (pipeline.next_action.stage === "programming" ||
         pipeline.next_action.stage === "ai_interview")
    ) {

        footer = `
            <a id="pipelineContinueButton"
               class="btn btn-primary rounded-pill px-4 mt-3"
               href="${escapeHtml(pipeline.next_action.url)}">
                <i class="bi bi-arrow-right-circle me-1"></i>
                Continue to ${escapeHtml(pipeline.next_action.label)}
            </a>
        `;
    }

    return `
        <div class="mb-3 text-start" id="pipelineTracker">
            <div class="fw-bold mb-2">Your assessment progress</div>
            <ul class="list-group">${rows}</ul>
            ${footer}
        </div>
    `;
}


function renderAptitudeNextStageInner(nextStageEl, sessionData) {

    const aptitude = sessionData.aptitude || {};

    if (!APTITUDE_READY_STAGES.has(sessionData.session.current_stage)) {

        nextStageEl.innerHTML = `
            <div class="text-muted small">
                The next stage of your assessment is not yet
                available. Please check back later.
            </div>
        `;

        return;
    }

    if (!aptitude.exists) {

        nextStageEl.innerHTML = `
            <div class="text-start">
                <div class="fw-bold mb-1">
                    <i class="bi bi-stars me-1"></i>
                    Aptitude Assessment Ready
                </div>
                <p class="text-muted small mb-3">
                    Your aptitude assessment will be personalized
                    for this role based on your profile and the
                    job's requirements.
                </p>
            </div>
            <button
                type="button"
                id="startAptitudeButton"
                class="btn btn-primary rounded-pill px-4"
            >
                <i class="bi bi-play-fill me-1"></i>
                Start Aptitude Test
            </button>
        `;

        const startButton = document.getElementById("startAptitudeButton");

        if (startButton) {

            startButton.addEventListener("click", async function () {

                startButton.disabled = true;

                if (window.IntegrityMode) {
                    await window.IntegrityMode.runCameraReadinessCheck();
                }

                startButton.innerHTML = `
                    <span class="spinner-border spinner-border-sm me-2"></span>
                    Personalizing your assessment...
                `;

                startAptitude();
            });
        }

        return;
    }

    if (aptitude.status !== "completed") {

        // Not finished yet (in_progress / expired) — aptitude-
        // only view, exactly as before.
        nextStageEl.innerHTML = buildAptitudeCardHtml(aptitude);
        return;
    }

    // Aptitude is completed — show its result, and (only if
    // passed) the programming stage beneath it.
    // Task 6A: programming and the AI interview are reached through
    // the backend-authored pipeline link (V3 programming page / V2
    // interview page); the legacy gate is only used if a backend
    // without a pipeline block answered.
    nextStageEl.innerHTML =
        buildAptitudeCardHtml(aptitude) +
        (sessionData.pipeline
            ? ""
            : buildProgrammingGateHtml(
                aptitude,
                sessionData.programming || {},
                sessionData.interview || {}
            ));

    const startInterviewButton = document.getElementById("startInterviewButton");

    if (startInterviewButton) {

        startInterviewButton.addEventListener("click", async function () {

            startInterviewButton.disabled = true;

            if (window.IntegrityMode) {
                await window.IntegrityMode.runCameraReadinessCheck();
            }

            startInterviewButton.innerHTML = `
                <span class="spinner-border spinner-border-sm me-2"></span>
                Personalizing your interview...
            `;

            startInterview();
        });
    }

    const startProgButton = document.getElementById("startProgrammingButton");

    if (startProgButton) {

        startProgButton.addEventListener("click", async function () {

            startProgButton.disabled = true;

            if (window.IntegrityMode) {
                await window.IntegrityMode.runCameraReadinessCheck();
            }

            startProgButton.innerHTML = `
                <span class="spinner-border spinner-border-sm me-2"></span>
                Personalizing your assessment...
            `;

            startProgramming();
        });
    }
}


function buildAptitudeCardHtml(aptitude) {

    const preview = aptitude.preview || {};

    const isCompleted = aptitude.status === "completed";
    const isExpired = aptitude.status === "expired";

    const sourceLabel = aptitude.ai_personalized
        ? "AI Personalized Assessment"
        : "Standard Assessment";

    const focusChips = (preview.focus_areas || [])
        .map(
            (area) =>
                `<span class="assessment-chip">${escapeHtml(area)}</span>`
        )
        .join("");

    let resultHtml = "";

    if (isCompleted) {

        resultHtml = `
            <div class="alert alert-${
                aptitude.passed ? "success" : "danger"
            } text-start small mt-3 mb-0">
                Result: ${aptitude.passed ? "Passed" : "Not Passed"}
                ${
                    aptitude.percentage !== null &&
                    aptitude.percentage !== undefined
                        ? ` — ${escapeHtml(String(aptitude.percentage))}%`
                        : ""
                }
            </div>
        `;

    } else if (isExpired) {

        resultHtml = `
            <div class="alert alert-warning text-start small mt-3 mb-0">
                The 24-hour completion window for this aptitude
                test has passed.
            </div>
        `;
    }

    const buttonHtml = isCompleted
        ? ""
        : `
            <a
                href="aptitude-test.html?application_id=${encodeURIComponent(applicationId)}"
                class="btn btn-primary rounded-pill px-4 mt-3"
            >
                <i class="bi bi-arrow-right-circle me-1"></i>
                Continue to Aptitude Test
            </a>
        `;

    return `
        <div class="assessment-aptitude-preview text-start">

            <div class="small fw-bold text-uppercase text-muted mb-2">
                <i class="bi bi-stars me-1"></i>
                ${escapeHtml(sourceLabel)}
            </div>

            <p class="text-muted small mb-2">
                Your assessment has been personalized for this role.
            </p>

            <div class="small mb-1">
                <strong>Job:</strong> ${escapeHtml(preview.job_title || "")}
            </div>

            <div class="small mb-1">
                <strong>Questions:</strong> ${preview.total_questions ?? "-"}
            </div>

            <div class="small mb-2">
                <strong>Approximate difficulty:</strong>
                ${escapeHtml(preview.difficulty_label || "-")}
            </div>

            <div class="small fw-bold text-muted mb-1">
                Key areas being assessed
            </div>

            <div class="mb-1">
                ${focusChips}
            </div>

            ${resultHtml}

        </div>

        ${buttonHtml}
    `;
}


/* =========================================================
   PROGRAMMING (Task 4C — AI-personalized programming
   assessment). Only unlocked once the aptitude assessment is
   completed AND passed — the backend independently enforces
   this on every mutating call, this is purely a display gate.
   ========================================================= */

function buildProgrammingGateHtml(aptitude, programming, interview) {

    // finishing the aptitude test is enough, pass or not: the hiring team makes the final decision
    if (aptitude.status !== "completed") {

        return `
            <div class="alert alert-secondary text-start small mt-3 mb-0">
                The next assessment unlocks once you finish the
                aptitude assessment.
            </div>
        `;
    }

    if (!programming.assigned) {

        return `
            <div class="alert alert-secondary text-start small mt-3 mb-0">
                <i class="bi bi-hourglass-split me-1"></i>
                Programming assessment has not been assigned yet.
                Your recruiter will enable this stage when you're
                ready to continue.
            </div>
        `;
    }

    if (!programming.ready) {

        return `
            <div class="text-muted small mt-3">
                The programming assessment is not yet available.
                Please check back later.
            </div>
        `;
    }

    if (!programming.exists) {

        return `
            <div class="assessment-aptitude-preview text-start mt-3">
                <div class="fw-bold mb-1">
                    <i class="bi bi-code-slash me-1"></i>
                    Programming Assessment Ready
                </div>
                <p class="text-muted small mb-3">
                    Your programming assessment will be
                    personalized for this role based on your
                    profile and the job's requirements. Once
                    started, you will have
                    ${(programming.preview && programming.preview.time_limit_minutes) || 45}
                    minutes to complete it.
                </p>
                <button
                    type="button"
                    id="startProgrammingButton"
                    class="btn btn-primary rounded-pill px-4"
                >
                    <i class="bi bi-play-fill me-1"></i>
                    Start Programming Test
                </button>
            </div>
        `;
    }

    return buildProgrammingCardHtml(programming, interview);
}


function buildProgrammingCardHtml(programming, interview) {

    const preview = programming.preview || {};

    const isCompleted = programming.status === "completed";
    const isExpired = programming.status === "expired";

    const sourceLabel = programming.ai_personalized
        ? "AI Personalized Assessment"
        : "Standard Assessment";

    const focusChips = (preview.focus_areas || [])
        .map(
            (area) =>
                `<span class="assessment-chip">${escapeHtml(area)}</span>`
        )
        .join("");

    let resultHtml = "";
    let interviewGateHtml = "";

    if (isCompleted) {

        resultHtml = `
            <div class="alert alert-${
                programming.passed ? "success" : "danger"
            } text-start small mt-3 mb-0">
                Programming Assessment Complete — Result:
                ${programming.passed ? "Passed" : "Not Passed"}
                ${
                    programming.percentage !== null &&
                    programming.percentage !== undefined
                        ? ` — ${escapeHtml(String(programming.percentage))}%`
                        : ""
                }
            </div>
        `;

        interviewGateHtml = buildInterviewGateHtml(
            programming,
            interview || {}
        );

    } else if (isExpired) {

        resultHtml = `
            <div class="alert alert-warning text-start small mt-3 mb-0">
                The ${preview.time_limit_minutes || 45}-minute
                completion window for this programming assessment
                has passed.
            </div>
        `;
    }

    const buttonHtml = (isCompleted || isExpired)
        ? ""
        : `
            <a
                href="programming-test.html?application_id=${encodeURIComponent(applicationId)}"
                class="btn btn-primary rounded-pill px-4 mt-3"
            >
                <i class="bi bi-arrow-right-circle me-1"></i>
                Continue to Programming Test
            </a>
        `;

    return `
        <div class="assessment-aptitude-preview text-start mt-3">

            <div class="small fw-bold text-uppercase text-muted mb-2">
                <i class="bi bi-code-slash me-1"></i>
                ${escapeHtml(sourceLabel)}
            </div>

            <div class="small mb-1">
                <strong>Job:</strong> ${escapeHtml(preview.job_title || "")}
            </div>

            <div class="small mb-1">
                <strong>Questions:</strong> ${preview.total_questions ?? "-"}
            </div>

            <div class="small mb-2">
                <strong>Time limit:</strong>
                ${preview.time_limit_minutes || 45} minutes
            </div>

            <div class="small fw-bold text-muted mb-1">
                Key areas being assessed
            </div>

            <div class="mb-1">
                ${focusChips}
            </div>

            ${resultHtml}

        </div>

        ${buttonHtml}

        ${interviewGateHtml}
    `;
}


/* =========================================================
   AI INTERVIEW (Task 4D). Only unlocked once the programming
   assessment is completed AND passed — the backend
   independently enforces this on every mutating call, this is
   purely a display gate.
   ========================================================= */

function buildInterviewGateHtml(programming, interview) {

    // finishing the previous assessment is enough, pass or not: the hiring team makes the final decision
    if (programming.status !== "completed") {

        return `
            <div class="alert alert-secondary text-start small mt-3 mb-0">
                The AI interview unlocks once you finish the
                previous assessment.
            </div>
        `;
    }

    if (!interview.assigned) {

        return `
            <div class="alert alert-secondary text-start small mt-3 mb-0">
                <i class="bi bi-hourglass-split me-1"></i>
                AI Interview has not been assigned yet. Your
                recruiter will enable this stage when you're
                ready to continue.
            </div>
        `;
    }

    if (!interview.ready) {

        return `
            <div class="text-muted small mt-3">
                The AI interview is not yet available. Please
                check back later.
            </div>
        `;
    }

    if (!interview.exists) {

        return `
            <div class="assessment-aptitude-preview text-start mt-3">
                <div class="fw-bold mb-1">
                    <i class="bi bi-chat-dots me-1"></i>
                    AI Interview Ready
                </div>
                <p class="text-muted small mb-3">
                    Your AI interview will be personalized for
                    this role based on your profile, the job's
                    requirements, and your assessment results so
                    far. Once started, you will have
                    ${(interview.preview && interview.preview.time_limit_minutes) || 30}
                    minutes to complete it.
                </p>
                <button
                    type="button"
                    id="startInterviewButton"
                    class="btn btn-primary rounded-pill px-4"
                >
                    <i class="bi bi-play-fill me-1"></i>
                    Start AI Interview
                </button>
            </div>
        `;
    }

    return buildInterviewCardHtml(interview);
}


function buildInterviewCardHtml(interview) {

    const preview = interview.preview || {};

    const isCompleted = interview.status === "completed";
    const isExpired = interview.status === "expired";

    const sourceLabel = interview.ai_personalized
        ? "AI Personalized Interview"
        : "Standard Interview";

    const focusChips = (preview.focus_areas || [])
        .map(
            (area) =>
                `<span class="assessment-chip">${escapeHtml(area)}</span>`
        )
        .join("");

    let resultHtml = "";

    if (isCompleted) {

        resultHtml = `
            <div class="alert alert-${
                interview.passed ? "success" : "danger"
            } text-start small mt-3 mb-0">
                AI Interview Complete — Result:
                ${interview.passed ? "Passed" : "Not Passed"}
                ${
                    interview.overall_score !== null &&
                    interview.overall_score !== undefined
                        ? ` — ${escapeHtml(String(interview.overall_score))}%`
                        : ""
                }
                ${
                    interview.recommendation
                        ? ` (${escapeHtml(interview.recommendation)})`
                        : ""
                }
            </div>
            <div class="text-muted small mt-2 mb-0">
                This completes the assessment pipeline. Your
                recruiter will follow up on next steps.
            </div>
        `;

    } else if (isExpired) {

        resultHtml = `
            <div class="alert alert-warning text-start small mt-3 mb-0">
                The ${preview.time_limit_minutes || 30}-minute
                completion window for this interview has passed.
            </div>
        `;
    }

    const buttonHtml = (isCompleted || isExpired)
        ? ""
        : `
            <a
                href="ai-interview-v2.html?application_id=${encodeURIComponent(applicationId)}"
                class="btn btn-primary rounded-pill px-4 mt-3"
            >
                <i class="bi bi-arrow-right-circle me-1"></i>
                Continue to AI Interview
            </a>
        `;

    return `
        <div class="assessment-aptitude-preview text-start mt-3">

            <div class="small fw-bold text-uppercase text-muted mb-2">
                <i class="bi bi-chat-dots me-1"></i>
                ${escapeHtml(sourceLabel)}
            </div>

            <div class="small mb-1">
                <strong>Job:</strong> ${escapeHtml(preview.job_title || "")}
            </div>

            <div class="small mb-1">
                <strong>Questions:</strong> ${preview.total_questions ?? "-"}
            </div>

            <div class="small mb-2">
                <strong>Time limit:</strong>
                ${preview.time_limit_minutes || 30} minutes
            </div>

            <div class="small fw-bold text-muted mb-1">
                Key areas being assessed
            </div>

            <div class="mb-1">
                ${focusChips}
            </div>

            ${resultHtml}

        </div>

        ${buttonHtml}
    `;
}


async function startInterview() {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/interview/start`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
            }
        );

        const data = await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to start the AI interview."
            );
        }

        renderSessionState(data);

    } catch (error) {

        console.error("Start interview error:", error);

        showAssessmentMessage(
            error.message ||
            "Unable to start the AI interview."
        );

        const startInterviewButton = document.getElementById("startInterviewButton");

        if (startInterviewButton) {

            startInterviewButton.disabled = false;

            startInterviewButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Start AI Interview
            `;
        }
    }
}


async function startProgramming() {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/start`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
            }
        );

        const data = await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to start the programming assessment."
            );
        }

        renderSessionState(data);

    } catch (error) {

        console.error("Start programming error:", error);

        showAssessmentMessage(
            error.message ||
            "Unable to start the programming assessment."
        );

        const startProgButton = document.getElementById("startProgrammingButton");

        if (startProgButton) {

            startProgButton.disabled = false;

            startProgButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Start Programming Test
            `;
        }
    }
}


async function startAptitude() {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/aptitude/start`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
            }
        );

        const data = await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to start the aptitude assessment."
            );
        }

        renderSessionState(data);

    } catch (error) {

        console.error("Start aptitude error:", error);

        showAssessmentMessage(
            error.message ||
            "Unable to start the aptitude assessment."
        );

        const startButton = document.getElementById("startAptitudeButton");

        if (startButton) {

            startButton.disabled = false;

            startButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Start Aptitude Test
            `;
        }
    }
}


function renderUnsupportedStage(stage) {

    hideAllAssessmentSections();

    showAssessmentMessage(
        `This stage of the assessment (${stage}) is not yet ` +
        "available. Please check back later.",
        "warning"
    );

    document.getElementById("assessmentLoading").style.display = "none";
}


/* =========================================================
   RENDER SESSION STATE (single entry point)
   ========================================================= */

function renderSessionState(sessionData) {

    clearAssessmentMessage();

    renderJobHeader(sessionData.job);
    renderProgressTracker(sessionData.session.current_stage);

    const stage = sessionData.session.current_stage;
    const selfIntro = sessionData.session.self_intro;

    if (stage === "self_intro") {

        if (!selfIntro || selfIntro.status === "not_started") {

            renderSelfIntroNotStarted();

        } else if (selfIntro.status === "in_progress") {

            renderSelfIntroInProgress(selfIntro);

        } else {

            // completed / expired while stage is still self_intro
            // shouldn't normally happen (completion advances the
            // stage), but is handled defensively.
            renderSelfIntroDone(sessionData);
        }

        return;
    }

    if (
        stage === "aptitude" ||
        stage === "programming" ||
        stage === "ai_interview" ||
        stage === "completed"
    ) {

        renderSelfIntroDone(sessionData);
        return;
    }

    renderUnsupportedStage(stage);
}


/* =========================================================
   API CALLS
   ========================================================= */

async function startAssessmentSession() {

    const response = await fetch(
        `${API_BASE_URL}/api/assessment/sessions/${applicationId}/start`,
        {
            method: "POST",
            credentials: "include",
            headers: {
                "Content-Type": "application/json",
            },
        }
    );

    const data = await response.json();

    if (!response.ok) {

        throw new Error(
            data.detail || "Unable to start this assessment."
        );
    }

    return data;
}


// The entire screen is shared (and recorded) from the self introduction on. Asked BEFORE the server starts the 2-minute clock, so
// choosing a screen never costs the candidate speaking time. The self introduction has no strict mode, so nothing is reported here.
async function requireScreenShare() {

    // The self introduction is camera-only: the screen share starts with the role test (each test page asks for it itself).
    return;

    // eslint-disable-next-line no-unreachable
    if (window.ScreenProctor && !window.ScreenProctor.isActive()) {
        await window.ScreenProctor.gate({ assessmentType: "self_intro", applicationId: applicationId }, function () {});
    }
}


async function startSelfIntro() {

    // the full rules and security briefing, with a tick box, before anything is shared or recorded
    if (window.AssessmentInstructions) {
        const accepted = await window.AssessmentInstructions.ensure({ apiBase: API_BASE_URL, applicationId: applicationId, type: "self_intro" });
        if (!accepted) return;
    }

    await requireScreenShare();

    const startButton = document.getElementById("startSelfIntroButton");

    if (startButton) {

        startButton.disabled = true;

        startButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            Starting...
        `;
    }

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/self-intro/start`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
            }
        );

        const data = await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail || "Unable to start your self-introduction."
            );
        }

        renderSessionState(data);

    } catch (error) {

        console.error("Start self-intro error:", error);

        showAssessmentMessage(
            error.message || "Unable to start your self-introduction."
        );

        if (startButton) {

            startButton.disabled = false;

            startButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Begin Self Introduction
            `;
        }
    }
}


async function submitSelfIntro(isAutoSubmit) {

    if (submitInFlight) {
        return;
    }

    submitInFlight = true;
    stopCountdown();

    const submitButton = document.getElementById("submitSelfIntroButton");

    if (submitButton) {

        submitButton.disabled = true;

        submitButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            ${isAutoSubmit ? "Time's up — submitting..." : "Submitting..."}
        `;
    }

    const textInput = document.getElementById("selfIntroTextInput");
    const textIntroduction = textInput ? textInput.value.trim() : "";

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/self-intro/complete`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    text_introduction: textIntroduction || null,
                }),
            }
        );

        const data = await response.json();

        if (!response.ok) {

            stopCameraPreview();

            throw new Error(
                data.detail || "Unable to submit your self-introduction."
            );
        }

        // Task 4 / Step 7: the self-intro stage itself is now
        // confirmed complete on the server — only NOW does the
        // recording stop and upload. stop() finalizes the Blob
        // from whatever was captured; stopCameraPreview() (called
        // after) tears down the stream/UI but never touches the
        // already-finalized Blob (see its cleanup()).
        let videoUploadResult = null;

        if (selfIntroVideoRecorder) {

            try {

                await selfIntroVideoRecorder.stop();

                videoUploadResult = await selfIntroVideoRecorder.upload(
                    applicationId,
                    "self_intro"
                );

            } catch (videoError) {

                console.warn(
                    "Self-intro video finalize/upload error:",
                    videoError
                );

                videoUploadResult = {
                    ok: false,
                    error: "Unexpected error while saving the recording.",
                };
            }
        }

        stopCameraPreview();

        if (window.ScreenProctor) {
            try { await window.ScreenProctor.finish(); } catch (screenError) { /* the spooled chunks are on the server */ }
        }

        submitInFlight = false;
        renderSessionState(data);

        // The video is evidence attached to this stage, not a
        // gate on progression — a failed upload never blocks the
        // candidate or creates a duplicate record (the backend's
        // unique constraint + 409 handling already guarantee
        // that). We still surface a clear, honest error state
        // rather than silently pretending the video was saved.
        if (videoUploadResult && !videoUploadResult.ok) {

            showAssessmentMessage(
                "Your self-introduction was submitted successfully, " +
                "but the video recording for this stage could not " +
                "be saved.",
                "warning"
            );
        }

    } catch (error) {

        console.error("Submit self-intro error:", error);

        submitInFlight = false;

        showAssessmentMessage(
            error.message || "Unable to submit your self-introduction."
        );

        if (submitButton) {

            submitButton.disabled = false;

            submitButton.innerHTML = `
                <i class="bi bi-send-check me-1"></i>
                Stop &amp; Submit
            `;
        }
    }
}


/* =========================================================
   INITIALIZE
   ========================================================= */

document.addEventListener("DOMContentLoaded", async function () {

    currentUser = getLoggedInUser();
    applicationId = getApplicationIdFromUrl();

    if (!currentUser || !currentUser.id) {

        showAssessmentMessage(
            "Please login again to access your assessment."
        );

        document.getElementById("assessmentLoading").style.display = "none";
        return;
    }

    if (!applicationId) {

        showAssessmentMessage(
            "No application was specified for this assessment."
        );

        document.getElementById("assessmentLoading").style.display = "none";
        return;
    }

    try {

        setLoadingText("Preparing your assessment...");

        const sessionData = await startAssessmentSession();

        renderSessionState(sessionData);

    } catch (error) {

        console.error("Load assessment error:", error);

        showAssessmentMessage(
            error.message || "Unable to load this assessment."
        );

        document.getElementById("assessmentLoading").style.display = "none";
    }

    const startButton = document.getElementById("startSelfIntroButton");

    if (startButton) {
        startButton.addEventListener("click", startSelfIntro);
    }

    const submitButton = document.getElementById("submitSelfIntroButton");

    if (submitButton) {

        submitButton.addEventListener("click", function () {
            submitSelfIntro(false);
        });
    }
});


window.addEventListener("beforeunload", function () {

    stopCameraPreview();
    stopCountdown();
});

/* =========================================================
   JOBPILOT AI - APTITUDE TEST
   ========================================================= */

const API_BASE_URL = "http://127.0.0.1:8000";

let currentTestId = null;
let currentQuestions = [];
let currentApplicationId = null;
let currentUserForStrictMode = null;

let examTimerIntervalId = null;
let examRemainingSeconds = null;
let examAutoSubmitTriggered = false;

// Answer autosave (debounced) — persists selections while the
// candidate is still answering, so a dropped connection or
// accidental refresh mid-exam doesn't lose them.
let autosaveDebounceId = null;
const AUTOSAVE_DEBOUNCE_MS = 800;

// Task 4: shared video recorder attached to IntegrityMode's
// existing camera stream (see assessment-integrity.js's
// getCameraStream()) — this stage never opens a second stream.
let aptitudeVideoRecorder = null;


/* =========================================================
   GET LOGGED-IN USER
   ========================================================= */

function getLoggedInUser() {

    try {

        const storedUser =
            localStorage.getItem("jobpilot_user");

        if (!storedUser) {
            return null;
        }

        return JSON.parse(storedUser);

    } catch (error) {

        console.error(
            "Unable to read logged-in user:",
            error
        );

        return null;
    }
}


/* =========================================================
   GET APPLICATION ID FROM URL
   ========================================================= */

function getApplicationIdFromUrl() {

    const params =
        new URLSearchParams(
            window.location.search
        );

    const value =
        params.get("application_id");

    const applicationId =
        Number(value);

    return (
        value &&
        Number.isInteger(applicationId) &&
        applicationId > 0
    )
        ? applicationId
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
   SHOW MESSAGE
   ========================================================= */

function showTestMessage(message, type = "danger") {

    const container =
        document.getElementById("testMessage");

    if (!container) {
        return;
    }

    container.innerHTML = `
        <div class="alert alert-${type}" role="alert">
            ${escapeHtml(message)}
        </div>
    `;
}


/* =========================================================
   SECTION VISIBILITY
   ========================================================= */

function showSection(sectionId) {

    const sections = [
        "testIntroSection",
        "testQuestionsSection",
        "testResultSection",
        "testTerminatedSection",
    ];

    sections.forEach(function (id) {

        const element =
            document.getElementById(id);

        if (element) {

            element.style.display =
                id === sectionId
                    ? "block"
                    : "none";
        }
    });

    const loading =
        document.getElementById("testLoading");

    if (loading) {
        loading.style.display = "none";
    }
}


/* =========================================================
   SHOW LOADING STATE (with a custom message)
   ========================================================= */

function showLoadingState(message) {

    const sections = [
        "testIntroSection",
        "testQuestionsSection",
        "testResultSection",
        "testTerminatedSection",
    ];

    sections.forEach(function (id) {

        const element =
            document.getElementById(id);

        if (element) {
            element.style.display = "none";
        }
    });

    const loadingText =
        document.getElementById(
            "testLoadingText"
        );

    if (loadingText && message) {
        loadingText.textContent = message;
    }

    const loading =
        document.getElementById("testLoading");

    if (loading) {
        loading.style.display = "block";
    }
}


/* =========================================================
   FORMAT DATE
   ========================================================= */

function formatResultDate(value) {

    if (!value) {
        return "-";
    }

    // The backend stores UTC without a timezone marker; without "Z" the browser
    // would read it as local time (My Applications already adds the "Z").
    let isoValue = String(value);

    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(isoValue)) {
        isoValue += "Z";
    }

    const date =
        new Date(isoValue);

    if (Number.isNaN(date.getTime())) {
        return "-";
    }

    return date.toLocaleString(
        "en-US",
        {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
        }
    );
}


/* =========================================================
   RENDER JOB HEADER
   ========================================================= */

function renderJobHeader(job) {

    const title =
        (job && job.title) ||
        "Job Application";

    const company =
        (job && job.company) ||
        "Company";

    [
        "introJobTitle",
        "questionsJobTitle",
        "resultJobTitle",
    ].forEach(function (id) {

        const element =
            document.getElementById(id);

        if (element) {
            element.textContent = title;
        }
    });

    [
        "introCompanyName",
        "questionsCompanyName",
        "resultCompanyName",
    ].forEach(function (id) {

        const element =
            document.getElementById(id);

        if (element) {
            element.textContent = company;
        }
    });
}


/* =========================================================
   APTITUDE ASSIGNMENT COUNTDOWN

   expires_at is backend-authoritative (assigned_at + 24
   hours) — this only renders a live label from it. The real
   deadline is always enforced server-side.
   ========================================================= */

function computeRemainingSeconds(expiresAtIso) {

    if (!expiresAtIso) {
        return null;
    }

    let isoValue = expiresAtIso;

    if (!isoValue.endsWith("Z")) {
        isoValue += "Z";
    }

    const expiresAt =
        new Date(isoValue).getTime();

    if (Number.isNaN(expiresAt)) {
        return null;
    }

    return Math.max(
        0,
        Math.floor((expiresAt - Date.now()) / 1000)
    );
}


function formatRemainingTime(seconds) {

    if (
        seconds === null ||
        seconds === undefined ||
        seconds < 0
    ) {

        return null;
    }

    const totalMinutes = Math.floor(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;

    if (hours <= 0 && minutes <= 0) {
        return "< 1m";
    }

    if (hours <= 0) {
        return `${minutes}m`;
    }

    return `${hours}h ${minutes}m`;
}


function updateAptitudeCountdownLabel() {

    const label =
        document.getElementById(
            "aptitudeRemainingLabel"
        );

    if (!label) {
        return;
    }

    const iso =
        label.getAttribute("data-expires-at");

    const remaining =
        computeRemainingSeconds(iso);

    if (remaining === null) {
        return;
    }

    if (remaining <= 0) {

        label.textContent =
            "Remaining: deadline passed";

        return;
    }

    label.textContent =
        `Remaining: ${formatRemainingTime(remaining)}`;
}


setInterval(
    updateAptitudeCountdownLabel,
    30000
);


/* =========================================================
   STRICT ASSESSMENT MODE (Task 4D-STRICT-1)

   Wires the reusable IntegrityMode module (assessment-
   integrity.js) into this page. The exam-duration countdown
   below is purely visual — exam_remaining_seconds comes from
   the server on every load/resume, and the server independently
   re-checks its own deadline (AptitudeTest.exam_expires_at) on
   every start/submit call regardless of what this timer shows.
   ========================================================= */

function formatExamClock(totalSeconds) {

    const safeSeconds =
        Math.max(0, Math.floor(totalSeconds));

    const minutes =
        Math.floor(safeSeconds / 60);

    const seconds =
        safeSeconds % 60;

    return `${minutes}:${String(seconds).padStart(2, "0")}`;
}


function stopExamTimer() {

    if (examTimerIntervalId) {

        clearInterval(examTimerIntervalId);
        examTimerIntervalId = null;
    }
}


function renderExamTimer() {

    const timerEl =
        document.getElementById("examTimer");

    if (!timerEl || examRemainingSeconds === null) {
        return;
    }

    timerEl.textContent =
        formatExamClock(examRemainingSeconds);

    timerEl.classList.toggle(
        "is-low",
        examRemainingSeconds <= 60
    );
}


function startExamTimer(initialRemainingSeconds, onExpire) {

    stopExamTimer();

    examRemainingSeconds =
        Math.max(0, Math.floor(initialRemainingSeconds));

    examAutoSubmitTriggered = false;

    renderExamTimer();

    examTimerIntervalId = setInterval(function () {

        examRemainingSeconds -= 1;
        renderExamTimer();

        if (
            examRemainingSeconds <= 0 &&
            !examAutoSubmitTriggered
        ) {

            examAutoSubmitTriggered = true;
            stopExamTimer();

            if (onExpire) {
                onExpire();
            }
        }

    }, 1000);
}


/* =========================================================
   INTEGRITY BANNER / WARNING UI
   ========================================================= */

function updateIntegrityWarningCount(violationCount, maxViolations) {

    const el =
        document.getElementById("integrityWarningCount");

    if (!el) {
        return;
    }

    el.textContent =
        (maxViolations ? `Integrity ${violationCount}/${maxViolations}` : `Integrity warnings: ${violationCount}`);

    el.classList.toggle(
        "has-warnings",
        violationCount > 0
    );
}


function showIntegrityWarning(title, message) {

    const alertEl =
        document.getElementById("integrityWarningAlert");

    const titleEl =
        document.getElementById("integrityWarningTitle");

    const textEl =
        document.getElementById("integrityWarningText");

    if (!alertEl) {
        return;
    }

    if (titleEl) {

        titleEl.innerHTML = `
            <i class="bi bi-exclamation-triangle-fill me-1"></i>
            ${escapeHtml(title)}
        `;
    }

    if (textEl) {
        textEl.textContent = message;
    }

    alertEl.style.display = "block";
}


function hideIntegrityWarning() {

    const alertEl =
        document.getElementById("integrityWarningAlert");

    if (alertEl) {
        alertEl.style.display = "none";
    }
}


/* =========================================================
   HANDLE AN INTEGRITY STATE UPDATE FROM THE SERVER

   Called after every reported event, and once on page load
   (from GET /api/assessment/integrity/state) to restore state
   after a refresh — never trusts a client-side violation count.
   ========================================================= */

function handleIntegrityStateChange(state) {

    if (!state) {
        return;
    }

    updateIntegrityWarningCount(state.violation_count, state.max_violations);

    if (state.warning_level === "warning") {

        showIntegrityWarning(
            "Assessment Warning",
            "Assessment integrity violation detected. You left " +
            "the assessment screen or exited fullscreen. Please " +
            "remain on this screen until you submit."
        );

    } else if (state.warning_level === "final_warning") {

        showIntegrityWarning(
            "Final Warning",
            "Assessment integrity violation detected again. One " +
            "more violation will automatically terminate this " +
            "assessment."
        );

    } else if (state.warning_level === "none") {

        hideIntegrityWarning();
    }

    if (
        state.termination_required === true ||
        state.assessment_active === false
    ) {

        handleAssessmentTerminated();
    }
}


function handleAssessmentTerminated() {

    stopExamTimer();

    // Task 4: a termination is not a successful stage completion,
    // so we only stop/discard the in-progress recording here —
    // never upload a partial video from this path.
    if (aptitudeVideoRecorder) {
        aptitudeVideoRecorder.cleanup();
        aptitudeVideoRecorder = null;
    }

    stopPhoneDetection();

    if (window.IntegrityMode) {
        window.IntegrityMode.cleanup();
    }

    showSection("testTerminatedSection");
}


/* =========================================================
   TASK 5 - VISIBLE MOBILE PHONE DETECTION (thin wiring only;
   all detection/confirmation/cooldown/UI logic lives in the one
   shared phone-detector.js — see that file's own header).
   ========================================================= */

function startPhoneDetection() {

    if (!window.PhoneDetector || !window.IntegrityMode) {
        return;
    }

    const videoEl =
        window.IntegrityMode.getCameraVideoElement &&
        window.IntegrityMode.getCameraVideoElement();

    if (!videoEl) {
        return;
    }

    window.PhoneDetector.start(videoEl, {

        onConfirmedDetection: function (detection, meta) {

            if (meta.withinCooldown) {
                // Still shown to the candidate (phone-detector.js
                // already did that) - just not re-reported to the
                // backend for this same continuous streak.
                return;
            }

            window.IntegrityMode.reportIntegrityEvent(
                "mobile_phone_detected",
                {
                    object: detection.object,
                    confidence: detection.confidence,
                    source: "browser_object_detector",
                    track_id: detection.track_id,
                    duration_ms: detection.duration_ms,
                    detections: detection.detections,
                    detector: detection.detector,
                    tracker: detection.tracker,
                }
            );
        },
    });
}

function stopPhoneDetection() {

    if (window.PhoneDetector) {
        window.PhoneDetector.stop();
    }
}


/* =========================================================
   ENTER STRICT MODE FOR THE ACTIVE EXAM
   ========================================================= */

async function activateStrictModeForExam() {

    if (!window.IntegrityMode) {
        return;
    }

    await window.IntegrityMode.enterStrictMode({

        userId: currentUserForStrictMode.id,
        applicationId: currentApplicationId,
        assessmentType: "aptitude",

        onWarning: function (message) {

            showIntegrityWarning(
                "Strict Mode Notice",
                message
            );
        },

        onFullscreenExit: function () {

            showIntegrityWarning(
                "Assessment Warning",
                "Please return to fullscreen to continue your " +
                "assessment."
            );
        },

        onStateChange: function (state) {
            handleIntegrityStateChange(state);
        },
    });

    // Camera/face integrity signal (Task 4E) — non-blocking; a
    // denied/unsupported camera never prevents the exam itself,
    // it is only reported as a signal (see assessment-integrity.js).
    await window.IntegrityMode.startCameraMonitoring();

    // Task 4: attach the shared video recorder to the SAME
    // camera stream IntegrityMode just acquired above — never a
    // second getUserMedia() call. Non-blocking: an unsupported
    // MediaRecorder or a denied camera never prevents the exam.
    const aptitudeCameraStream =
        window.IntegrityMode.getCameraStream &&
        window.IntegrityMode.getCameraStream();

    if (window.AssessmentVideoRecorder && aptitudeCameraStream) {

        aptitudeVideoRecorder = new window.AssessmentVideoRecorder();

        if (aptitudeVideoRecorder.isSupported()) {
            aptitudeVideoRecorder.start(aptitudeCameraStream, "aptitude");
        } else {

            console.warn(
                "AssessmentVideoRecorder is not supported in this " +
                "browser — aptitude recording will not be captured."
            );
        }
    }

    // Task 5: visible mobile phone detection, reusing the exact
    // same camera <video> element IntegrityMode already plays the
    // stream into for face detection — never a second stream.
    startPhoneDetection();

    // Restore the candidate's real violation state from the
    // server (never assume 0 after a refresh).
    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/integrity/state` +
            `?application_id=${encodeURIComponent(currentApplicationId)}` +
            `&user_id=${encodeURIComponent(currentUserForStrictMode.id)}` +
            `&assessment_type=aptitude`,
            {
                credentials: "include",
            }
        );

        if (response.ok) {

            const state = await response.json();
            handleIntegrityStateChange(state);
        }

    } catch (error) {

        console.warn(
            "Unable to restore integrity state:",
            error
        );
    }
}


/* =========================================================
   RENDER APTITUDE ASSIGNMENT PANEL (shown on the intro
   screen before the candidate clicks Start — "assigned" but
   not yet "in_progress")
   ========================================================= */

function renderAssignmentPanel(test) {

    const existing =
        document.getElementById(
            "aptitudeAssignmentPanel"
        );

    if (existing) {
        existing.remove();
    }

    const assignedLabel =
        formatResultDate(test.assigned_at);

    const deadlineLabel =
        formatResultDate(test.expires_at);

    const remaining =
        computeRemainingSeconds(test.expires_at);

    const remainingLabel =
        remaining !== null
            ? formatRemainingTime(remaining)
            : null;

    const panel =
        document.createElement("div");

    panel.id = "aptitudeAssignmentPanel";
    panel.className = "alert alert-info text-start";

    panel.innerHTML = `
        <div class="fw-bold mb-1">
            <i class="bi bi-clock-history me-1"></i>
            Complete this assessment within 24 hours.
        </div>
        <div class="small">
            Assigned: ${escapeHtml(assignedLabel)}
        </div>
        <div class="small">
            Deadline: ${escapeHtml(deadlineLabel)}
        </div>
        <div
            class="small fw-bold mt-1"
            id="aptitudeRemainingLabel"
            data-expires-at="${escapeHtml(test.expires_at || "")}"
        >
            ${
                remainingLabel
                    ? `Remaining: ${escapeHtml(remainingLabel)}`
                    : ""
            }
        </div>
    `;

    const startButton =
        document.getElementById(
            "startTestButton"
        );

    if (startButton && startButton.parentElement) {

        startButton.parentElement.insertBefore(
            panel,
            startButton
        );

    }
}


/* =========================================================
   RENDER RESULT
   ========================================================= */

function renderResult(test) {

    const passed =
        test.passed === true;

    const badge =
        document.getElementById("resultBadge");

    if (badge) {

        badge.innerHTML = `
            <span
                class="result-badge completed"
            >
                <i
                    class="bi ${
                        "bi-bar-chart-fill"
                    }"
                ></i>
                Aptitude Test Completed
            </span>
        `;
    }

    const scoreElement =
        document.getElementById("resultScore");

    if (scoreElement) {

        scoreElement.textContent =
            `${test.score ?? "-"} / ${
                test.total_questions ?? "-"
            }`;
    }

    const percentageElement =
        document.getElementById("resultPercentage");

    if (percentageElement) {

        percentageElement.textContent =
            test.percentage !== null &&
            test.percentage !== undefined
                ? `${test.percentage}%`
                : "-";
    }

    const dateElement =
        document.getElementById("resultDate");

    if (dateElement) {

        dateElement.textContent =
            formatResultDate(
                test.completed_at
            );
    }

    const icon =
        document.getElementById("resultIcon");

    if (icon) {

        icon.innerHTML = `
            <i class="bi ${
                "bi-bar-chart-fill"
            }"></i>
        `;
    }

    showSection("testResultSection");
}


/* =========================================================
   RENDER QUESTIONS
   ========================================================= */

function collectCurrentAnswers() {

    return currentQuestions.map(
        function (question) {

            const selected =
                document.querySelector(
                    `input[name="question_${
                        question.id
                    }"]:checked`
                );

            return {
                question_id: question.id,
                selected_answer:
                    selected
                        ? selected.value
                        : null
            };
        }
    );
}


async function autosaveAnswers(user) {

    if (!currentTestId || !user) {
        return;
    }

    try {

        await fetch(
            `${API_BASE_URL}/api/aptitude-tests/${currentTestId}/autosave`,
            {
                method: "PUT",
                credentials: "include",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    user_id: user.id,
                    answers: collectCurrentAnswers()
                })
            }
        );

    } catch (error) {

        // Best-effort only — a failed autosave never blocks the
        // candidate from continuing or submitting normally.
        console.warn(
            "Aptitude answer autosave failed:",
            error
        );
    }
}


function scheduleAutosave(user) {

    if (autosaveDebounceId) {
        clearTimeout(autosaveDebounceId);
    }

    autosaveDebounceId = setTimeout(
        function () {
            autosaveAnswers(user);
        },
        AUTOSAVE_DEBOUNCE_MS
    );
}


function renderQuestions(test) {

    currentTestId = test.id;
    currentQuestions = test.questions || [];

    const draftAnswerByQuestionId = new Map(
        (test.draft_answers || []).map(
            function (answer) {
                return [
                    String(answer.question_id),
                    answer.selected_answer
                ];
            }
        )
    );

    const container =
        document.getElementById(
            "questionsContainer"
        );

    if (!container) {
        return;
    }

    container.innerHTML = currentQuestions
        .map(function (question, index) {

            const optionsHtml = (
                question.options || []
            )
                .map(function (option) {

                    const isChecked =
                        draftAnswerByQuestionId.get(
                            String(question.id)
                        ) === option;

                    return `
                        <label class="option-label">
                            <input
                                type="radio"
                                name="question_${
                                    question.id
                                }"
                                value="${escapeHtml(
                                    option
                                )}"
                                ${isChecked ? "checked" : ""}
                            >
                            <span>
                                ${escapeHtml(option)}
                            </span>
                        </label>
                    `;
                })
                .join("");

            return `
                <div class="question-card">

                    <div class="question-number">
                        Question ${index + 1} of
                        ${currentQuestions.length}
                    </div>

                    <div class="question-text">
                        ${escapeHtml(
                            question.question
                        )}
                    </div>

                    <div>
                        ${optionsHtml}
                    </div>

                </div>
            `;
        })
        .join("");

    // One delegated change listener covers every question's
    // radio inputs (including ones rendered later) and debounces
    // a PUT /autosave so selections survive a refresh/reconnect.
    container.addEventListener(
        "change",
        function (event) {

            if (
                event.target &&
                event.target.type === "radio"
            ) {
                scheduleAutosave(currentUserForStrictMode);
            }
        }
    );

    showSection("testQuestionsSection");

    // Strict Assessment Mode + exam timer activate the moment
    // the candidate is actually looking at the questions —
    // covers both a fresh start and a resumed (refreshed)
    // in-progress test.
    activateStrictModeForExam();

    if (
        test.exam_remaining_seconds !== null &&
        test.exam_remaining_seconds !== undefined
    ) {

        startExamTimer(
            test.exam_remaining_seconds,
            function () {

                showTestMessage(
                    "Time is up. Submitting your assessment...",
                    "warning"
                );

                submitTest(currentUserForStrictMode, true);
            }
        );
    }
}


/* =========================================================
   START TEST
   ========================================================= */

async function startTest(
    applicationId,
    user
) {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/aptitude-tests/application/${applicationId}/start`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    user_id: user.id
                })
            }
        );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to start the aptitude test."
            );
        }

        renderQuestions(data);

    } catch (error) {

        console.error(
            "Start aptitude test error:",
            error
        );

        showTestMessage(
            error.message ||
            "Unable to start the aptitude test."
        );

        // Restore the intro screen (rather than leaving the
        // page stuck on the loading spinner) so the
        // candidate can see the error and try again.
        showSection("testIntroSection");

        const startButton =
            document.getElementById(
                "startTestButton"
            );

        if (startButton) {

            startButton.disabled = false;

            startButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Start Aptitude Test
            `;
        }
    }
}


/* =========================================================
   SUBMIT TEST
   ========================================================= */

async function submitTest(user, forceSubmit) {

    const submitButton =
        document.getElementById(
            "submitTestButton"
        );

    const unanswered =
        currentQuestions.filter(
            function (question) {

                const selected =
                    document.querySelector(
                        `input[name="question_${
                            question.id
                        }"]:checked`
                    );

                return !selected;
            }
        );

    if (unanswered.length > 0 && !forceSubmit) {

        const hint =
            document.getElementById(
                "submitHint"
            );

        if (hint) {

            hint.textContent =
                `Please answer all questions. ` +
                `${unanswered.length} question(s) remaining.`;

            hint.className =
                "small text-danger mt-2";
        }

        return;
    }

    const answers = collectCurrentAnswers();

    if (submitButton) {

        submitButton.disabled = true;

        submitButton.innerHTML = `
            <span
                class="spinner-border spinner-border-sm me-2"
            ></span>
            Submitting...
        `;
    }

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/aptitude-tests/${currentTestId}/submit`,
            {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    user_id: user.id,
                    answers: answers
                })
            }
        );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                data.detail ||
                "Unable to submit the aptitude test."
            );
        }

        // Task 4 / Step 8: recording is an additional layer only,
        // and must finalize+upload BEFORE IntegrityMode.cleanup()
        // stops the underlying camera stream/tracks below.
        if (aptitudeVideoRecorder) {

            try {

                await aptitudeVideoRecorder.stop();

                const uploadResult = await aptitudeVideoRecorder.upload(
                    currentApplicationId,
                    "aptitude"
                );

                if (!uploadResult.ok) {

                    console.warn(
                        "Aptitude video upload failed:",
                        uploadResult.error
                    );
                }

            } catch (videoError) {

                console.warn(
                    "Aptitude video finalize/upload error:",
                    videoError
                );
            }

            aptitudeVideoRecorder.cleanup();
        }

        stopPhoneDetection();

        stopExamTimer();

        if (window.IntegrityMode) {
            window.IntegrityMode.cleanup();
        }

        renderResult(data);

    } catch (error) {

        console.error(
            "Submit aptitude test error:",
            error
        );

        showTestMessage(
            error.message ||
            "Unable to submit the aptitude test."
        );

        if (submitButton) {

            submitButton.disabled = false;

            submitButton.innerHTML = `
                <i class="bi bi-send-check me-1"></i>
                Submit Test
            `;
        }
    }
}


/* =========================================================
   INITIALIZE
   ========================================================= */

document.addEventListener(
    "DOMContentLoaded",
    async function () {

        const user =
            getLoggedInUser();

        const applicationId =
            getApplicationIdFromUrl();

        if (!user || !user.id) {

            showTestMessage(
                "Please login again to access your aptitude test."
            );

            document.getElementById(
                "testLoading"
            ).style.display = "none";

            return;
        }

        if (!applicationId) {

            showTestMessage(
                "No application was specified for this test."
            );

            document.getElementById(
                "testLoading"
            ).style.display = "none";

            return;
        }

        currentApplicationId = applicationId;
        currentUserForStrictMode = user;

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/aptitude-tests/application/${applicationId}?user_id=${encodeURIComponent(
                    user.id
                )}`,
                {
                    credentials: "include"
                }
            );

            const data =
                await response.json();

            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to load this aptitude test."
                );
            }

            renderJobHeader(data.job);

            if (!data.eligible) {

                showTestMessage(
                    "The aptitude test is not available for " +
                    "this application yet. It becomes " +
                    "available once your application has " +
                    "been shortlisted.",
                    "warning"
                );

                document.getElementById(
                    "testLoading"
                ).style.display = "none";

                return;
            }

            if (
                !data.self_intro_completed &&
                (!data.test || data.test.status === "assigned")
            ) {

                // Task 4D-SELF-INTRO-FIX: direct navigation to
                // this page (or an aptitude test assigned before
                // Self Introduction was ever completed — the
                // older HR-assign path has no such dependency)
                // must never let the candidate reach the
                // question-answering flow. The backend
                // independently rejects the actual start request
                // regardless of this message (see
                // is_self_intro_completed() in aptitude_tests.py)
                // — this is purely a clear, upfront explanation.
                showTestMessage(
                    "Complete Self Introduction First. Please " +
                    "finish your 2-minute self-introduction " +
                    "before starting the Aptitude Test.",
                    "warning"
                );

                const backButtonHtml = `
                    <a
                        href="assessment.html?application_id=${encodeURIComponent(applicationId)}"
                        class="btn btn-primary rounded-pill px-4 mt-2"
                    >
                        Go to Self Introduction
                    </a>
                `;

                const messageContainer =
                    document.getElementById("testMessage");

                if (messageContainer) {
                    messageContainer.innerHTML += backButtonHtml;
                }

                document.getElementById(
                    "testLoading"
                ).style.display = "none";

            } else if (
                !data.test
            ) {

                // Eligible status-wise, but the recruiter
                // has not sent this test yet — there is
                // nothing to start.
                showTestMessage(
                    "This aptitude test has not been sent " +
                    "by the recruiter yet. Check back once " +
                    "your recruiter assigns it.",
                    "warning"
                );

                document.getElementById(
                    "testLoading"
                ).style.display = "none";

            } else if (
                data.test.status ===
                "assigned"
            ) {

                // Sent by the recruiter, not started yet —
                // show the intro screen with the 24-hour
                // deadline before the candidate starts.
                showSection(
                    "testIntroSection"
                );

                renderAssignmentPanel(
                    data.test
                );

            } else if (
                data.test.status ===
                "in_progress"
            ) {

                renderQuestions(
                    data.test
                );

            } else if (
                data.test.status ===
                "expired"
            ) {

                showTestMessage(
                    "This aptitude test has expired. Either " +
                    "the 24-hour assignment window or the " +
                    "20-minute exam time limit has passed.",
                    "warning"
                );

                document.getElementById(
                    "testLoading"
                ).style.display = "none";

            } else if (
                data.test.status ===
                "terminated"
            ) {

                showSection("testTerminatedSection");

            } else {

                renderResult(
                    data.test
                );
            }

        } catch (error) {

            console.error(
                "Load aptitude test error:",
                error
            );

            showTestMessage(
                error.message ||
                "Unable to load this aptitude test."
            );

            document.getElementById(
                "testLoading"
            ).style.display = "none";
        }


        // --------------------------------------------------
        // START BUTTON
        // --------------------------------------------------

        const startButton =
            document.getElementById(
                "startTestButton"
            );

        if (startButton) {

            startButton.addEventListener(
                "click",
                async function () {

                    // instructions + tick box first (the 20-minute clock starts only after this)
                    if (window.AssessmentInstructions) {
                        const accepted = await window.AssessmentInstructions.ensure({
                            apiBase: API_BASE_URL, applicationId, type: "aptitude",
                        });
                        if (!accepted) return;
                    }

                    startButton.disabled = true;

                    if (window.IntegrityMode) {
                        await window.IntegrityMode.runCameraReadinessCheck();
                    }

                    showLoadingState(
                        "Preparing your personalized AI " +
                        "aptitude test..."
                    );

                    startTest(
                        applicationId,
                        user
                    );
                }
            );
        }


        // --------------------------------------------------
        // SUBMIT BUTTON
        // --------------------------------------------------

        const submitButton =
            document.getElementById(
                "submitTestButton"
            );

        if (submitButton) {

            submitButton.addEventListener(
                "click",
                function () {

                    submitTest(user);
                }
            );
        }
    }
);

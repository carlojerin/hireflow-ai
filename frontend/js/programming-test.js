/* =========================================================
   JOBPILOT AI - PROGRAMMING TEST

   Mirrors aptitude-test.js's conventions exactly: same
   API_BASE_URL, same getLoggedInUser()/getApplicationIdFromUrl()
   helpers, same fetch(credentials:"include") pattern. CSRF
   headers are injected automatically by csrf-fetch.js, which is
   loaded before this script.

   The countdown shown here is purely visual. The backend
   records the real start time and independently enforces the
   45-minute deadline on every action — this script can never
   extend it by manipulating the client clock.

   KNOWN LIMITATION (documented, not a bug): code typed into the
   editor is held only in this page's own memory (codeByQuestion)
   until "Submit Final Assessment" is clicked. A page refresh or
   navigation away before final submission loses unsaved code for
   the current session, though the server-side timer and one-
   attempt state are unaffected either way.
   ========================================================= */

const API_BASE_URL = "http://127.0.0.1:8000";

let applicationId = null;
let currentUser = null;
let currentTest = null;
let currentQuestions = [];
let activeQuestionId = null;
let submitInFlight = false;

const codeByQuestion = {};
const answeredQuestionIds = new Set();

let countdownIntervalId = null;
let remainingSeconds = 0;
let terminatedByIntegrity = false;

// Task 4: shared video recorder attached to IntegrityMode's
// existing camera stream — never a second getUserMedia() call.
let programmingVideoRecorder = null;


/* =========================================================
   HELPERS (identical to aptitude-test.js)
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


function escapeHtml(value) {

    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function showProgMessage(message, type = "danger") {

    const container = document.getElementById("progMessage");

    if (!container) {
        return;
    }

    container.innerHTML = `
        <div class="alert alert-${type}" role="alert">
            ${escapeHtml(message)}
        </div>
    `;
}


function hideLoading() {

    const loading = document.getElementById("progLoading");

    if (loading) {
        loading.style.display = "none";
    }
}


function formatClock(totalSeconds) {

    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    const minutes = Math.floor(safeSeconds / 60);
    const seconds = safeSeconds % 60;

    return `${minutes}:${String(seconds).padStart(2, "0")}`;
}


function renderJobHeader(job) {

    const titleEl = document.getElementById("progJobTitle");
    const companyEl = document.getElementById("progCompanyName");

    if (titleEl) {
        titleEl.textContent = (job && job.title) || "Programming Assessment";
    }

    if (companyEl) {
        companyEl.textContent = (job && job.company) || "";
    }
}


/* =========================================================
   COUNTDOWN
   ========================================================= */

function stopCountdown() {

    if (countdownIntervalId) {

        clearInterval(countdownIntervalId);
        countdownIntervalId = null;
    }
}


function renderCountdown() {

    const timerEl = document.getElementById("progTimer");

    if (!timerEl) {
        return;
    }

    timerEl.textContent = formatClock(remainingSeconds);
    timerEl.classList.toggle("is-low", remainingSeconds <= 120);
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

            showProgMessage(
                "Your time has run out. Submitting your current " +
                "answers automatically...",
                "warning"
            );

            submitFinalAssessment(true);
        }

    }, 1000);
}


/* =========================================================
   STRICT ASSESSMENT MODE + CAMERA INTEGRITY (Task 4E)

   Mirrors aptitude-test.js's activateStrictModeForExam() /
   handleIntegrityStateChange() exactly, adapted to this page's
   own element ids. See assessment-integrity.js for the shared
   module and docs/strict-assessment-mode.md for the full
   architecture this reuses without duplicating.
   ========================================================= */

function handleIntegrityStateChange(state) {

    if (!state) {
        return;
    }

    if (state.warning_level === "warning") {

        showProgMessage(
            "Assessment integrity violation detected. You left the " +
            "assessment screen, exited fullscreen, or a camera/face " +
            "signal was flagged. Please remain on this screen until " +
            "you submit.",
            "warning"
        );

    } else if (state.warning_level === "final_warning") {

        showProgMessage(
            "Assessment integrity violation detected again. One " +
            "more violation will automatically terminate this " +
            "assessment.",
            "warning"
        );
    }

    if (
        state.termination_required === true ||
        state.assessment_active === false
    ) {

        handleAssessmentTerminated();
    }
}


function handleAssessmentTerminated() {

    if (terminatedByIntegrity) {
        return;
    }

    terminatedByIntegrity = true;

    stopCountdown();

    // Task 4: a termination is not a successful stage completion —
    // stop/discard the in-progress recording, never upload it.
    if (programmingVideoRecorder) {
        programmingVideoRecorder.cleanup();
        programmingVideoRecorder = null;
    }

    stopPhoneDetection();

    if (window.IntegrityMode) {
        window.IntegrityMode.cleanup();
    }

    const headerCard = document.getElementById("progHeaderCard");
    const nav = document.getElementById("progNav");
    const questionsContainer = document.getElementById("progQuestionsContainer");
    const finalSubmitCard = document.getElementById("progFinalSubmitCard");

    if (headerCard) headerCard.style.display = "none";
    if (nav) nav.style.display = "none";
    if (questionsContainer) questionsContainer.style.display = "none";
    if (finalSubmitCard) finalSubmitCard.style.display = "none";

    showProgMessage(
        "Assessment Terminated — this assessment was automatically " +
        "terminated because the maximum number of assessment " +
        "integrity violations was reached.",
        "danger"
    );
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


async function activateStrictModeForProgramming() {

    if (!window.IntegrityMode) {
        return;
    }

    await window.IntegrityMode.enterStrictMode({

        userId: currentUser.id,
        applicationId: applicationId,
        assessmentType: "programming",

        onWarning: function (message) {
            showProgMessage(message, "warning");
        },

        onFullscreenExit: function () {

            showProgMessage(
                "Please return to fullscreen to continue your " +
                "assessment.",
                "warning"
            );
        },

        onStateChange: function (state) {
            handleIntegrityStateChange(state);
        },
    });

    await window.IntegrityMode.startCameraMonitoring();

    // Task 4: attach the shared video recorder to the SAME camera
    // stream IntegrityMode just acquired above. Non-blocking: an
    // unsupported MediaRecorder or a denied camera never prevents
    // the exam itself.
    const programmingCameraStream =
        window.IntegrityMode.getCameraStream &&
        window.IntegrityMode.getCameraStream();

    if (window.AssessmentVideoRecorder && programmingCameraStream) {

        programmingVideoRecorder = new window.AssessmentVideoRecorder();

        if (programmingVideoRecorder.isSupported()) {

            programmingVideoRecorder.start(
                programmingCameraStream,
                "programming"
            );

        } else {

            console.warn(
                "AssessmentVideoRecorder is not supported in this " +
                "browser — programming recording will not be " +
                "captured."
            );
        }
    }

    // Task 5: visible mobile phone detection, reusing the exact
    // same camera <video> element IntegrityMode already plays the
    // stream into for face detection — never a second stream.
    startPhoneDetection();

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/integrity/state` +
            `?application_id=${encodeURIComponent(applicationId)}` +
            `&user_id=${encodeURIComponent(currentUser.id)}` +
            `&assessment_type=programming`,
            { credentials: "include" }
        );

        if (response.ok) {

            const state = await response.json();
            handleIntegrityStateChange(state);
        }

    } catch (error) {

        console.warn("Unable to restore integrity state:", error);
    }
}


/* =========================================================
   RENDER: HEADER / NAVIGATION / QUESTIONS
   ========================================================= */

function renderNav() {

    const nav = document.getElementById("progNav");

    if (!nav) {
        return;
    }

    nav.innerHTML = currentQuestions
        .map(function (question, index) {

            const isActive = question.question_id === activeQuestionId;
            const isAnswered = answeredQuestionIds.has(question.question_id);

            return `
                <div
                    class="programming-nav-item ${isActive ? "is-active" : ""} ${isAnswered ? "is-answered" : ""}"
                    data-question-id="${question.question_id}"
                >
                    ${index + 1}
                </div>
            `;
        })
        .join("");

    nav.style.display = "flex";

    nav.querySelectorAll(".programming-nav-item").forEach(function (el) {

        el.addEventListener("click", function () {

            const questionId = Number(el.getAttribute("data-question-id"));
            switchToQuestion(questionId);
        });
    });
}


function switchToQuestion(questionId) {

    if (activeQuestionId !== null) {

        const currentTextarea = document.getElementById("progCodeEditor");

        if (currentTextarea) {
            codeByQuestion[activeQuestionId] = currentTextarea.value;
        }
    }

    activeQuestionId = questionId;

    renderNav();
    renderActiveQuestion();
}


function renderActiveQuestion() {

    const container = document.getElementById("progQuestionsContainer");

    if (!container) {
        return;
    }

    const question = currentQuestions.find(
        (q) => q.question_id === activeQuestionId
    );

    if (!question) {
        return;
    }

    const index = currentQuestions.findIndex(
        (q) => q.question_id === activeQuestionId
    );

    const examplesHtml = (question.examples || [])
        .map(function (example, exIndex) {

            return `
                <div class="programming-example-block">
Example ${exIndex + 1}
Input: ${escapeHtml(example.input)}
Output: ${escapeHtml(example.output)}${
                example.explanation
                    ? `\nExplanation: ${escapeHtml(example.explanation)}`
                    : ""
            }</div>
            `;
        })
        .join("");

    const code =
        codeByQuestion[question.question_id] !== undefined
            ? codeByQuestion[question.question_id]
            : (question.starter_code || "");

    codeByQuestion[question.question_id] = code;

    container.innerHTML = `
        <div class="programming-card">

            <div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-2">
                <div class="small text-muted fw-bold text-uppercase">
                    Question ${index + 1} of ${currentQuestions.length}
                </div>
                <span class="programming-difficulty-badge difficulty-${escapeHtml(question.difficulty)}">
                    ${escapeHtml(question.difficulty)}
                </span>
            </div>

            <h4 class="fw-bold mb-2">${escapeHtml(question.title)}</h4>

            <p style="white-space:pre-wrap;">${escapeHtml(question.description)}</p>

            <div class="row g-3 mb-3">
                <div class="col-md-6">
                    <div class="small fw-bold text-muted">Input</div>
                    <div class="small">${escapeHtml(question.input_description)}</div>
                </div>
                <div class="col-md-6">
                    <div class="small fw-bold text-muted">Output</div>
                    <div class="small">${escapeHtml(question.output_description)}</div>
                </div>
            </div>

            ${
                question.constraints
                    ? `<div class="small mb-3"><strong>Constraints:</strong> ${escapeHtml(question.constraints)}</div>`
                    : ""
            }

            <div class="small fw-bold text-muted mb-1">Examples</div>
            ${examplesHtml}

            <div class="d-flex justify-content-between align-items-center mt-3 mb-1">
                <div class="small fw-bold text-muted">Language</div>
                <span class="badge bg-light text-dark border">Python 3</span>
            </div>

            <textarea
                id="progCodeEditor"
                class="programming-code-editor"
                spellcheck="false"
            >${escapeHtml(code)}</textarea>

            <div class="d-flex gap-2 mt-3">
                <button type="button" id="progRunButton" class="btn btn-outline-primary rounded-pill px-4">
                    <i class="bi bi-play-fill me-1"></i>
                    Run Code
                </button>
            </div>

            <div id="progRunResult"></div>

        </div>
    `;

    const editor = document.getElementById("progCodeEditor");

    if (editor) {

        editor.addEventListener("input", function () {
            codeByQuestion[question.question_id] = editor.value;
        });
    }

    const runButton = document.getElementById("progRunButton");

    if (runButton) {

        runButton.addEventListener("click", function () {
            runCode(question.question_id);
        });
    }

    container.style.display = "block";
}


function renderRunResult(data) {

    const resultEl = document.getElementById("progRunResult");

    if (!resultEl) {
        return;
    }

    if (data.execution_status === "rejected") {

        resultEl.innerHTML = `
            <div class="programming-run-result is-failed">
                <strong>Restricted operation:</strong>
                ${escapeHtml(data.message || "This code cannot be executed.")}
            </div>
        `;

        return;
    }

    const passedCount = (data.results || []).filter((r) => r.passed).length;
    const totalCount = (data.results || []).length;
    const allPassed = data.execution_status === "passed";

    const examplesHtml = (data.results || [])
        .map(function (result, index) {

            return `
                <div class="programming-run-example">
Example ${index + 1}: ${result.passed ? "PASSED" : "FAILED"}
${result.stdout ? `Output: ${escapeHtml(result.stdout.trim())}` : ""}
${result.stderr ? `Error: ${escapeHtml(result.stderr.trim())}` : ""}
                </div>
            `;
        })
        .join("");

    resultEl.innerHTML = `
        <div class="programming-run-result ${allPassed ? "is-passed" : "is-failed"}">
            <strong>${passedCount} / ${totalCount} visible example(s) passed</strong>
            (execution time: ${data.execution_time_ms ?? "-"} ms)
            ${examplesHtml}
        </div>
    `;
}


/* =========================================================
   API CALLS
   ========================================================= */

async function runCode(questionId) {

    const runButton = document.getElementById("progRunButton");
    const editor = document.getElementById("progCodeEditor");

    if (!editor) {
        return;
    }

    const sourceCode = editor.value;
    codeByQuestion[questionId] = sourceCode;

    if (sourceCode.trim()) {
        answeredQuestionIds.add(questionId);
        renderNav();
    }

    if (runButton) {

        runButton.disabled = true;

        runButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            Running...
        `;
    }

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/programming-tests/${currentTest.id}/run`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    user_id: currentUser.id,
                    question_id: questionId,
                    language: "python",
                    source_code: sourceCode,
                }),
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Unable to run your code.");
        }

        renderRunResult(data);

    } catch (error) {

        console.error("Run code error:", error);

        const resultEl = document.getElementById("progRunResult");

        if (resultEl) {

            resultEl.innerHTML = `
                <div class="programming-run-result is-failed">
                    ${escapeHtml(error.message || "Unable to run your code.")}
                </div>
            `;
        }

    } finally {

        if (runButton) {

            runButton.disabled = false;

            runButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Run Code
            `;
        }
    }
}


function renderFinalResult(data) {

    document.getElementById("progHeaderCard").style.display = "none";
    document.getElementById("progNav").style.display = "none";
    document.getElementById("progQuestionsContainer").style.display = "none";
    document.getElementById("progFinalSubmitCard").style.display = "none";

    const passed = data.passed === true;

    const badge = document.getElementById("progResultBadge");

    if (badge) {

        badge.innerHTML = `
            <span class="fs-4 fw-bold ${passed ? "text-success" : "text-danger"}">
                ${passed ? "Passed" : "Not Passed"} — ${data.percentage ?? "-"}%
            </span>
        `;
    }

    const icon = document.getElementById("progResultIcon");

    if (icon) {

        icon.innerHTML = `
            <i class="bi ${passed ? "bi-award-fill" : "bi-emoji-frown"}"></i>
        `;
    }

    const resultsEl = document.getElementById("progQuestionResults");

    if (resultsEl) {

        resultsEl.innerHTML = (data.question_results || [])
            .map(function (q) {

                return `
                    <div class="programming-question-result-row">
                        <span>${escapeHtml(q.title)} (${escapeHtml(q.difficulty)})</span>
                        <span class="fw-bold">${q.passed_count}/${q.total_count} hidden tests — ${q.percentage}%</span>
                    </div>
                `;
            })
            .join("");
    }

    document.getElementById("progResultSection").style.display = "block";
}


async function submitFinalAssessment(isAutoSubmit) {

    if (submitInFlight) {
        return;
    }

    if (!isAutoSubmit) {

        const confirmed = window.confirm(
            "Submit your final programming assessment? This " +
            "cannot be undone or changed afterward."
        );

        if (!confirmed) {
            return;
        }
    }

    submitInFlight = true;
    stopCountdown();

    if (activeQuestionId !== null) {

        const editor = document.getElementById("progCodeEditor");

        if (editor) {
            codeByQuestion[activeQuestionId] = editor.value;
        }
    }

    const submitButton = document.getElementById("progSubmitFinalButton");

    if (submitButton) {

        submitButton.disabled = true;

        submitButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            Submitting...
        `;
    }

    const answers = currentQuestions.map(function (question) {

        return {
            question_id: question.question_id,
            language: "python",
            source_code: codeByQuestion[question.question_id] || "",
        };
    });

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/programming-tests/${currentTest.id}/submit`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    user_id: currentUser.id,
                    answers: answers,
                }),
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Unable to submit your assessment.");
        }

        submitInFlight = false;

        // Task 4 / Step 9: finalize+upload BEFORE
        // IntegrityMode.cleanup() stops the camera stream below.
        if (programmingVideoRecorder) {

            try {

                await programmingVideoRecorder.stop();

                const uploadResult = await programmingVideoRecorder.upload(
                    applicationId,
                    "programming"
                );

                if (!uploadResult.ok) {

                    console.warn(
                        "Programming video upload failed:",
                        uploadResult.error
                    );
                }

            } catch (videoError) {

                console.warn(
                    "Programming video finalize/upload error:",
                    videoError
                );
            }

            programmingVideoRecorder.cleanup();
        }

        stopPhoneDetection();

        if (window.IntegrityMode) {
            window.IntegrityMode.cleanup();
        }

        renderFinalResult(data);

    } catch (error) {

        console.error("Submit final assessment error:", error);

        submitInFlight = false;

        showProgMessage(
            error.message || "Unable to submit your assessment."
        );

        if (submitButton) {

            submitButton.disabled = false;

            submitButton.innerHTML = `
                <i class="bi bi-send-check me-1"></i>
                Submit Final Assessment
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

        showProgMessage("Please login again to access your assessment.");
        hideLoading();
        return;
    }

    if (!applicationId) {

        showProgMessage("No application was specified for this assessment.");
        hideLoading();
        return;
    }

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/programming-tests/application/${applicationId}?user_id=${encodeURIComponent(currentUser.id)}`,
            { credentials: "include" }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Unable to load this programming assessment.");
        }

        renderJobHeader(data.job);

        if (!data.test) {

            showProgMessage(
                "The programming assessment has not been started yet. " +
                "Start it from your assessment page first.",
                "warning"
            );
            hideLoading();
            return;
        }

        currentTest = data.test;

        if (data.test.status === "expired") {

            showProgMessage(
                "This programming assessment has expired. The " +
                "45-minute completion window has passed.",
                "warning"
            );
            hideLoading();
            return;
        }

        if (data.test.status === "completed") {

            hideLoading();
            renderFinalResult({
                passed: data.test.passed,
                percentage: data.test.percentage,
                question_results: [],
            });
            return;
        }

        // in_progress
        currentQuestions = data.test.questions || [];

        hideLoading();

        document.getElementById("progHeaderCard").style.display = "block";
        document.getElementById("progFinalSubmitCard").style.display = "block";

        startCountdown(data.test.remaining_seconds || 0);

        if (currentQuestions.length > 0) {
            switchToQuestion(currentQuestions[0].question_id);
        }

        activateStrictModeForProgramming();

    } catch (error) {

        console.error("Load programming test error:", error);
        showProgMessage(error.message || "Unable to load this programming assessment.");
        hideLoading();
    }

    const submitFinalButton = document.getElementById("progSubmitFinalButton");

    if (submitFinalButton) {

        submitFinalButton.addEventListener("click", function () {
            submitFinalAssessment(false);
        });
    }
});


window.addEventListener("beforeunload", function () {

    stopCountdown();

    // Task 4 / Step 11: cleanup only — an unreliable beforeunload
    // upload could corrupt the recording, so we never attempt one.
    if (programmingVideoRecorder) {
        programmingVideoRecorder.cleanup();
        programmingVideoRecorder = null;
    }

    stopPhoneDetection();

    if (window.IntegrityMode) {
        window.IntegrityMode.cleanup();
    }
});

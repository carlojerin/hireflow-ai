/* =========================================================
   JOBPILOT AI - PROGRAMMING ASSESSMENT V2 (MULTI-LANGUAGE)
   Task 5D

   Mirrors programming-test.js's conventions exactly (same
   API_BASE_URL constant, same getLoggedInUser()/
   getApplicationIdFromUrl() helpers, same fetch(credentials:
   "include") pattern, same IntegrityMode/AssessmentVideoRecorder/
   PhoneDetector wiring) — this is a NEW, separate page for the
   NEW /programming/v2/* endpoints, standing ALONGSIDE
   programming-test.js. Nothing here touches or replaces the
   legacy page.

   THE BACKEND IS ALWAYS AUTHORITATIVE:
   - The set of selectable languages for a question comes ONLY
     from question.allowed_languages, as returned by GET
     /programming/v2 — never a hardcoded list on this page.
   - The countdown shown here is computed from the server's own
     expires_at timestamp and is purely a DISPLAY of that value.
     It never decides expiry itself — the moment it reaches zero,
     or whenever the tab becomes visible again / the page is
     restored from bfcache, this script re-fetches GET
     /programming/v2, and it is the SERVER's expire_if_due_v2()
     (already wired into every V2 endpoint) that is the one and
     only source of truth for "has this actually expired".
   - Running code or submitting always sends the question_id/
     language/source_code the candidate actually chose; the
     server independently re-validates the language against the
     question's own allowed_languages and its real execution
     capability (language_selection.py) — this page never assumes
     a Run/Submit will succeed just because the dropdown showed
     the language as an option.

   DRAFTS: kept in this page's own memory only, keyed by
   "questionId::language" — switching question or language always
   saves the CURRENTLY EDITED code under its own (question,
   language) key before loading the next one, so a candidate who
   writes Python then explores Java then returns to Python still
   has their Python code exactly as they left it. Nothing is sent
   to the server until Run or Submit is clicked.
   ========================================================= */

const API_BASE_URL = "http://127.0.0.1:8000";

let applicationId = null;
let currentUser = null;
let currentView = null; // last GET /programming/v2 response
let activeQuestionId = null;
let submitInFlight = false;
let runInFlight = false;
let assessmentLocked = false; // true once completed/expired/terminated

// "questionId::language" -> source code the candidate typed
const drafts = {};
// questionId -> the language currently selected for that question
const selectedLanguageByQuestion = {};

let countdownIntervalId = null;
let expiresAtMs = null;

let terminatedByIntegrity = false;
let programmingVideoRecorder = null;
let cameraStreamActive = false;


/* =========================================================
   DISPLAY-ONLY LANGUAGE METADATA

   Cosmetic only (display name, file-style label, a minimal
   starter shell shown when a draft is empty) — NEVER the source
   of which languages are selectable. That always comes from
   question.allowed_languages, returned by the backend. A
   language name the backend returns that isn't in this map still
   works correctly — it just falls back to a capitalized label and
   an empty editor instead of a starter comment.
   ========================================================= */

const LANGUAGE_DISPLAY_NAMES = {
    python: "Python",
    javascript: "JavaScript",
    typescript: "TypeScript",
    java: "Java",
    c: "C",
    cpp: "C++",
    csharp: "C#",
    go: "Go",
    rust: "Rust",
    php: "PHP",
    ruby: "Ruby",
    kotlin: "Kotlin",
    swift: "Swift",
};

const LANGUAGE_STARTER_SHELLS = {
    python: "# Read input with input(), print your answer with print()\n",
    javascript: "// Read stdin: require('fs').readFileSync(0, 'utf-8')\n// Print your answer with console.log(...)\n",
    typescript: "// Read stdin: require('fs').readFileSync(0, 'utf-8')\n// Print your answer with console.log(...)\n",
    java: "import java.util.Scanner;\n\npublic class Solution {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        // read input, then System.out.println(...)\n    }\n}\n",
    c: "#include <stdio.h>\n\nint main() {\n    // read input with scanf, print with printf\n    return 0;\n}\n",
    cpp: "#include <iostream>\nusing namespace std;\n\nint main() {\n    // read input with cin, print with cout\n    return 0;\n}\n",
    csharp: "using System;\n\nclass Program {\n    static void Main() {\n        // read input with Console.ReadLine(), print with Console.WriteLine\n    }\n}\n",
    go: "package main\n\nimport \"fmt\"\n\nfunc main() {\n    // read input with fmt.Scan, print with fmt.Println\n}\n",
    rust: "use std::io;\n\nfn main() {\n    // read input with io::stdin().read_line(...), print with println!\n}\n",
    php: "<?php\n// read input with fgets(STDIN), print with echo\n",
    ruby: "# read input with gets, print with puts\n",
    kotlin: "fun main() {\n    // read input with readLine(), print with println(...)\n}\n",
    swift: "// read input with readLine(), print with print(...)\n",
};

function languageDisplayName(language) {

    return LANGUAGE_DISPLAY_NAMES[language] ||
        (language.charAt(0).toUpperCase() + language.slice(1));
}


/* =========================================================
   HELPERS (identical conventions to programming-test.js)
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


function showMessage(message, type = "danger") {

    const container = document.getElementById("pv2Message");

    if (!container) {
        return;
    }

    container.innerHTML = `
        <div class="alert alert-${type}" role="alert">
            ${escapeHtml(message)}
        </div>
    `;
}


function clearMessage() {

    const container = document.getElementById("pv2Message");

    if (container) {
        container.innerHTML = "";
    }
}


function hideLoading() {

    const loading = document.getElementById("pv2Loading");

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


function parseServerTimestamp(isoString) {

    // The backend serializes naive UTC datetimes via .isoformat()
    // (no "Z"/offset suffix) — without forcing UTC interpretation
    // here, the browser would parse it as LOCAL time and the
    // displayed countdown would be wrong by the browser's own UTC
    // offset. This is a DISPLAY-ONLY correction; actual expiry is
    // always re-verified against the server (see module header).
    if (!isoString) {
        return null;
    }

    const hasTimezone = /[zZ]|[+-]\d{2}:\d{2}$/.test(isoString);

    return new Date(hasTimezone ? isoString : `${isoString}Z`).getTime();
}


function renderJobHeader(job) {

    const titleEl = document.getElementById("pv2JobTitle");
    const companyEl = document.getElementById("pv2CompanyName");

    if (titleEl) {
        titleEl.textContent = (job && job.title) || "Programming Assessment";
    }

    if (companyEl) {
        companyEl.textContent = (job && job.company) || "";
    }
}


function draftKey(questionId, language) {

    return `${questionId}::${language}`;
}


function questionHasAnyDraft(question) {

    return (question.allowed_languages || []).some(function (language) {

        const code = drafts[draftKey(question.question_id, language)];
        return code !== undefined && code.trim().length > 0;
    });
}


/* =========================================================
   COUNTDOWN (server-authoritative — see module header)
   ========================================================= */

function stopCountdown() {

    if (countdownIntervalId) {

        clearInterval(countdownIntervalId);
        countdownIntervalId = null;
    }
}


function renderCountdown() {

    const timerEl = document.getElementById("pv2Timer");

    if (!timerEl || expiresAtMs === null) {
        return;
    }

    const remainingSeconds = (expiresAtMs - Date.now()) / 1000;

    timerEl.textContent = formatClock(remainingSeconds);
    timerEl.classList.toggle("is-low", remainingSeconds <= 120);

    if (remainingSeconds <= 0) {

        stopCountdown();

        showMessage(
            "Your time appears to be up. Checking with the server…",
            "warning"
        );

        loadAssessmentState(true);
    }
}


function startCountdown(expiresAtIso) {

    stopCountdown();

    expiresAtMs = parseServerTimestamp(expiresAtIso);

    renderCountdown();

    countdownIntervalId = setInterval(renderCountdown, 1000);
}


/* =========================================================
   STRICT ASSESSMENT MODE + CAMERA INTEGRITY

   Identical wiring to programming-test.js's own
   activateStrictModeForProgramming() — same IntegrityMode /
   AssessmentVideoRecorder / PhoneDetector calls, same
   assessmentType: "programming" (the backend's integrity-active
   check now recognizes EITHER a legacy ProgrammingTest OR a V2
   ProgrammingAssessmentV2 row for this exact type string — see
   assessment_integrity.py's _verify_programming_active(), no new
   assessment type was invented for V2). Reusing "programming" as
   the video stage string is required for the same reason: the
   video table's unique constraint and stage vocabulary are
   shared with the legacy flow.
   ========================================================= */

function setIntegrityDot(kind, label) {

    const dot = document.getElementById("pv2IntegrityDot");
    const statusEl = document.getElementById("pv2IntegrityStatus");

    if (dot) {
        dot.className = `pv2-integrity-dot ${kind ? `is-${kind}` : ""}`;
    }

    if (statusEl && label) {
        statusEl.textContent = label;
    }
}


function handleIntegrityStateChange(state) {

    if (!state) {
        return;
    }

    if (state.warning_level === "warning") {

        setIntegrityDot("warning", "Integrity warning");

        showMessage(
            "Assessment integrity violation detected. You left the " +
            "assessment screen, exited fullscreen, or a camera/face " +
            "signal was flagged. Please remain on this screen until " +
            "you submit.",
            "warning"
        );

    } else if (state.warning_level === "final_warning") {

        setIntegrityDot("warning", "Final integrity warning");

        showMessage(
            "Assessment integrity violation detected again. One " +
            "more violation will automatically terminate this " +
            "assessment.",
            "warning"
        );

    } else if (!terminatedByIntegrity) {

        // Second real bug found during Task 5D's browser test,
        // same root cause as activateStrictModeForProgrammingV2()'s
        // fix above: this restores the dot to "ready" on every
        // no-warning integrity state callback, which used to
        // silently overwrite the correct "camera unavailable"
        // state the moment the FIRST integrity-state poll resolved
        // — this function's job is warnings, not camera status, so
        // it must defer to the camera's own actual state instead of
        // re-asserting "ready" itself.
        if (cameraStreamActive) {
            setIntegrityDot("ready", "Camera ready — monitoring active");
        } else {
            setIntegrityDot("error", "Camera unavailable — proceeding without video monitoring");
        }
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
    assessmentLocked = true;

    stopCountdown();
    setIntegrityDot("error", "Terminated");

    // A termination is not a successful stage completion — stop/
    // discard the in-progress recording, never upload it.
    if (programmingVideoRecorder) {
        programmingVideoRecorder.cleanup();
        programmingVideoRecorder = null;
    }

    stopPhoneDetection();

    if (window.IntegrityMode) {
        window.IntegrityMode.cleanup();
    }

    const headerCard = document.getElementById("pv2HeaderCard");
    const nav = document.getElementById("pv2Nav");
    const workspace = document.getElementById("pv2Workspace");
    const finalSubmitCard = document.getElementById("pv2FinalSubmitCard");

    if (headerCard) headerCard.style.display = "none";
    if (nav) nav.style.display = "none";
    if (workspace) workspace.style.display = "none";
    if (finalSubmitCard) finalSubmitCard.style.display = "none";

    showMessage(
        "Assessment Terminated — this assessment was automatically " +
        "terminated because the maximum number of assessment " +
        "integrity violations was reached. Your result up to this " +
        "point has been preserved.",
        "danger"
    );
}


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


async function activateStrictModeForProgrammingV2() {

    if (!window.IntegrityMode) {
        return;
    }

    setIntegrityDot("", "Requesting camera access…");

    await window.IntegrityMode.enterStrictMode({

        userId: currentUser.id,
        applicationId: applicationId,
        assessmentType: "programming",

        onWarning: function (message) {
            showMessage(message, "warning");
        },

        onFullscreenExit: function () {

            showMessage(
                "Please return to fullscreen to continue your assessment.",
                "warning"
            );
        },

        onStateChange: function (state) {
            handleIntegrityStateChange(state);
        },
    });

    await window.IntegrityMode.startCameraMonitoring();

    const cameraStream =
        window.IntegrityMode.getCameraStream &&
        window.IntegrityMode.getCameraStream();

    // Bug found during Task 5D's real browser test: this used to
    // unconditionally report "Camera ready" the instant
    // startCameraMonitoring() resolved, even when it resolved
    // WITHOUT a stream (camera permission denied/unavailable) —
    // IntegrityMode itself tolerates that failure and continues in
    // a degraded mode rather than throwing, so the dot must check
    // the actual stream, never assume success from the promise
    // merely settling.
    cameraStreamActive = !!cameraStream;

    if (cameraStreamActive) {
        setIntegrityDot("ready", "Camera ready — monitoring active");
    } else {
        setIntegrityDot("error", "Camera unavailable — proceeding without video monitoring");
    }

    if (window.AssessmentVideoRecorder && cameraStream) {

        programmingVideoRecorder = new window.AssessmentVideoRecorder();

        if (programmingVideoRecorder.isSupported()) {

            programmingVideoRecorder.start(cameraStream, "programming");

        } else {

            console.warn(
                "AssessmentVideoRecorder is not supported in this " +
                "browser — programming recording will not be captured."
            );
        }
    }

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
   RENDER: NAVIGATION / QUESTION / EDITOR
   ========================================================= */

function renderNav() {

    const nav = document.getElementById("pv2Nav");

    if (!nav || !currentView) {
        return;
    }

    nav.innerHTML = currentView.questions
        .map(function (question, index) {

            const isActive = question.question_id === activeQuestionId;
            const isAnswered = questionHasAnyDraft(question);

            return `
                <div
                    class="programming-nav-item ${isActive ? "is-active" : ""} ${isAnswered ? "is-answered" : ""}"
                    data-question-id="${question.question_id}"
                    title="Question ${index + 1}${isAnswered ? " (code entered)" : ""}"
                >
                    ${index + 1}
                </div>
            `;
        })
        .join("");

    nav.style.display = "flex";

    nav.querySelectorAll(".programming-nav-item").forEach(function (el) {

        el.addEventListener("click", function () {

            if (assessmentLocked) {
                return;
            }

            const questionId = Number(el.getAttribute("data-question-id"));
            switchToQuestion(questionId);
        });
    });
}


function saveCurrentEditorIntoDrafts() {

    if (activeQuestionId === null) {
        return;
    }

    const editor = document.getElementById("pv2CodeEditor");
    const language = selectedLanguageByQuestion[activeQuestionId];

    if (editor && language) {
        drafts[draftKey(activeQuestionId, language)] = editor.value;
    }
}


function switchToQuestion(questionId) {

    saveCurrentEditorIntoDrafts();

    activeQuestionId = questionId;

    renderNav();
    renderActiveQuestion();
}


function findActiveQuestion() {

    return (currentView.questions || []).find(
        (q) => q.question_id === activeQuestionId
    );
}


function renderLanguageOptions(question) {

    const select = document.getElementById("pv2LanguageSelect");

    if (!select) {
        return;
    }

    const allowed = question.allowed_languages || [];

    select.innerHTML = allowed
        .map(function (language) {
            return `<option value="${escapeHtml(language)}">${escapeHtml(languageDisplayName(language))}</option>`;
        })
        .join("");

    const currentSelection = selectedLanguageByQuestion[question.question_id];

    select.value = allowed.includes(currentSelection)
        ? currentSelection
        : allowed[0];

    selectedLanguageByQuestion[question.question_id] = select.value;

    select.disabled = assessmentLocked;
}


function loadEditorForActiveSelection() {

    const question = findActiveQuestion();
    const editor = document.getElementById("pv2CodeEditor");

    if (!question || !editor) {
        return;
    }

    const language = selectedLanguageByQuestion[question.question_id];
    const key = draftKey(question.question_id, language);

    const existingDraft = drafts[key];

    const code = existingDraft !== undefined
        ? existingDraft
        : (LANGUAGE_STARTER_SHELLS[language] || "");

    drafts[key] = code;
    editor.value = code;
    editor.disabled = assessmentLocked;
}


function renderActiveQuestion() {

    const container = document.getElementById("pv2QuestionContainer");

    if (!container || !currentView) {
        return;
    }

    const question = findActiveQuestion();

    if (!question) {
        return;
    }

    const index = currentView.questions.findIndex(
        (q) => q.question_id === activeQuestionId
    );

    const examplesHtml = (question.examples || [])
        .map(function (example, exIndex) {

            const input = example && (example.input ?? "");
            const output = example && (example.output ?? "");

            return `
                <div class="programming-example-block">
Example ${exIndex + 1}
Input: ${escapeHtml(input)}
Output: ${escapeHtml(output)}</div>
            `;
        })
        .join("");

    const subpartsHtml = (question.subparts || [])
        .map(function (subpart, spIndex) {

            return `
                <div class="pv2-subpart-row">
                    <span class="fw-semibold">Part ${spIndex + 1}.</span>
                    ${escapeHtml(subpart.description)}
                    <span class="text-muted small">(${Math.round(subpart.weight * 100)}% of this question)</span>
                </div>
            `;
        })
        .join("");

    container.innerHTML = `
        <div class="programming-card">

            <div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-2">
                <div class="small text-muted fw-bold text-uppercase">
                    Question ${index + 1} of ${currentView.questions.length}
                    ${question.points ? ` · ${question.points} points` : ""}
                </div>
                ${question.difficulty
                    ? `<span class="programming-difficulty-badge difficulty-${escapeHtml(question.difficulty)}">${escapeHtml(question.difficulty)}</span>`
                    : ""}
            </div>

            <h4 class="fw-bold mb-2">${escapeHtml(question.title)}</h4>

            <p style="white-space:pre-wrap;">${escapeHtml(question.description)}</p>

            <div class="row g-3 mb-3">
                <div class="col-md-6">
                    <div class="small fw-bold text-muted">Input Format</div>
                    <div class="small">${escapeHtml(question.input_specification)}</div>
                </div>
                <div class="col-md-6">
                    <div class="small fw-bold text-muted">Output Format</div>
                    <div class="small">${escapeHtml(question.output_specification)}</div>
                </div>
            </div>

            ${
                question.constraints
                    ? `<div class="small mb-3"><strong>Constraints:</strong> ${escapeHtml(question.constraints)}</div>`
                    : ""
            }

            ${examplesHtml ? `<div class="small fw-bold text-muted mb-1">Examples</div>${examplesHtml}` : ""}

            ${subpartsHtml ? `<div class="small fw-bold text-muted mt-3 mb-1">Structure</div>${subpartsHtml}` : ""}

        </div>
    `;

    renderLanguageOptions(question);
    loadEditorForActiveSelection();

    document.getElementById("pv2Workspace").style.display = "grid";
}


function renderRunResult(data) {

    const resultEl = document.getElementById("pv2RunResult");

    if (!resultEl) {
        return;
    }

    const results = data.results || [];

    if (results.length === 0) {

        resultEl.innerHTML = `
            <div class="programming-run-result">
                This question has no public examples to run against —
                use Submit when you are ready for full evaluation.
            </div>
        `;

        return;
    }

    const passedCount = results.filter((r) => r.success).length;
    const allPassed = passedCount === results.length;

    const rowsHtml = results
        .map(function (result, index) {

            let statusLine;

            if (result.error_type === "compile_error") {
                statusLine = "COMPILE ERROR";
            } else if (result.error_type === "timeout") {
                statusLine = "TIMEOUT";
            } else if (result.error_type === "runtime_error") {
                statusLine = "RUNTIME ERROR";
            } else if (result.success) {
                statusLine = "PASSED";
            } else {
                statusLine = "FAILED";
            }

            return `
                <div class="programming-run-example">
Example ${index + 1}: ${statusLine} (${result.execution_time_ms ?? "-"} ms)
Input: ${escapeHtml(result.stdin || "(none)")}
Expected: ${escapeHtml(result.expected_stdout)}
Your output: ${escapeHtml((result.actual_stdout || "").trim() || "(no output)")}
${result.stderr ? `Error output: ${escapeHtml(result.stderr.trim())}` : ""}
                </div>
            `;
        })
        .join("");

    resultEl.innerHTML = `
        <div class="programming-run-result ${allPassed ? "is-passed" : "is-failed"}">
            <strong>${passedCount} / ${results.length} public example(s) passed</strong>
            ${rowsHtml}
        </div>
    `;
}


/* =========================================================
   API CALLS
   ========================================================= */

async function parseJsonSafely(response) {

    try {
        return await response.json();
    } catch (error) {
        return {};
    }
}


async function handleApiError(response, data, fallbackMessage) {

    if (response.status === 401) {

        if (window.HireFlowSessionGuard) {

            const stopped = await window.HireFlowSessionGuard.handlePossibleAuthFailure(response);

            if (stopped) {
                return;
            }
        }

        showMessage("Please login again to continue your assessment.");
        return;
    }

    if (response.status === 403) {
        showMessage("You do not have access to this assessment.");
        return;
    }

    if (response.status === 404) {
        showMessage(data.detail || "This assessment could not be found.");
        return;
    }

    if (response.status === 409) {

        const detail = data.detail || "";

        if (/language/i.test(detail) && /(not permitted|not currently available|does not have|no working|unavailable|not a recognized)/i.test(detail)) {

            showMessage(
                "That language is currently unavailable for this " +
                "assessment. Please choose a different language from " +
                "the list provided.",
                "warning"
            );
            return;
        }

        showMessage(
            detail || "Your programming assessment is no longer active.",
            "warning"
        );
        return;
    }

    if (response.status === 410) {

        showMessage(
            "This programming assessment has expired.",
            "warning"
        );
        loadAssessmentState(true);
        return;
    }

    if (response.status === 422) {
        showMessage(data.detail || "Please check your submission and try again.");
        return;
    }

    if (response.status === 429) {
        showMessage("Too many requests — please wait a moment and try again.", "warning");
        return;
    }

    showMessage(data.detail || fallbackMessage || "Something went wrong. Please try again.");
}


async function runCode() {

    if (runInFlight || assessmentLocked) {
        return;
    }

    const question = findActiveQuestion();
    const editor = document.getElementById("pv2CodeEditor");

    if (!question || !editor) {
        return;
    }

    const language = selectedLanguageByQuestion[question.question_id];
    const sourceCode = editor.value;

    drafts[draftKey(question.question_id, language)] = sourceCode;
    renderNav();

    runInFlight = true;

    const runButton = document.getElementById("pv2RunButton");

    if (runButton) {

        runButton.disabled = true;

        runButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            Running…
        `;
    }

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/run`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question_id: question.question_id,
                    language: language,
                    source_code: sourceCode,
                }),
            }
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {
            await handleApiError(response, data, "Unable to run your code.");
            return;
        }

        clearMessage();
        renderRunResult(data);

    } catch (error) {

        console.error("Run code error:", error);

        showMessage(
            "Unable to reach the server to run your code. Please " +
            "check your connection and try again."
        );

    } finally {

        runInFlight = false;

        if (runButton) {

            runButton.disabled = assessmentLocked;

            runButton.innerHTML = `
                <i class="bi bi-play-fill me-1"></i>
                Run Code
            `;
        }
    }
}


function renderFinalResult(data) {

    document.getElementById("pv2HeaderCard").style.display = "none";
    document.getElementById("pv2Nav").style.display = "none";
    document.getElementById("pv2Workspace").style.display = "none";
    document.getElementById("pv2FinalSubmitCard").style.display = "none";

    const passed = data.passed === true;

    const badge = document.getElementById("pv2ResultBadge");

    if (badge) {

        const percentageLabel = data.percentage !== undefined && data.percentage !== null
            ? `${data.percentage}%`
            : "-";

        badge.innerHTML = `
            <span class="fs-4 fw-bold ${passed ? "text-success" : "text-danger"}">
                ${passed ? "Passed" : "Not Passed"} — ${percentageLabel}
            </span>
        `;
    }

    const icon = document.getElementById("pv2ResultIcon");

    if (icon) {

        icon.innerHTML = `
            <i class="bi ${passed ? "bi-award-fill" : "bi-emoji-frown"}"></i>
        `;
    }

    const resultsEl = document.getElementById("pv2QuestionResults");
    const questionReports = (data.report && data.report.questions) || [];

    if (resultsEl) {

        resultsEl.innerHTML = questionReports
            .map(function (q) {

                return `
                    <div class="programming-question-result-row">
                        <span>
                            ${escapeHtml(q.title || `Question ${q.question_id}`)}
                            <span class="text-muted small">(${escapeHtml(languageDisplayName(q.language_used || ""))})</span>
                        </span>
                        <span class="fw-bold">${q.earned_points}/${q.max_points} pts</span>
                    </div>
                `;
            })
            .join("");
    }

    document.getElementById("pv2ResultSection").style.display = "block";
}


async function submitAssessment() {

    if (submitInFlight || assessmentLocked) {
        return;
    }

    const confirmed = window.confirm(
        "You are about to submit your programming assessment. Continue?"
    );

    if (!confirmed) {
        return;
    }

    saveCurrentEditorIntoDrafts();

    submitInFlight = true;
    stopCountdown();

    const submitButton = document.getElementById("pv2SubmitFinalButton");

    if (submitButton) {

        submitButton.disabled = true;

        submitButton.innerHTML = `
            <span class="spinner-border spinner-border-sm me-2"></span>
            Submitting…
        `;
    }

    const answers = currentView.questions.map(function (question) {

        const language = selectedLanguageByQuestion[question.question_id] ||
            (question.allowed_languages || [])[0];

        return {
            question_id: question.question_id,
            language: language,
            source_code: drafts[draftKey(question.question_id, language)] || "",
        };
    });

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/submit`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answers: answers }),
            }
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {

            submitInFlight = false;

            await handleApiError(response, data, "Unable to submit your assessment.");

            if (submitButton) {

                submitButton.disabled = false;

                submitButton.innerHTML = `
                    <i class="bi bi-send-check me-1"></i>
                    Submit Assessment
                `;
            }

            return;
        }

        submitInFlight = false;
        assessmentLocked = true;

        // Finalize+upload BEFORE IntegrityMode.cleanup() stops the
        // camera stream below — identical ordering to
        // programming-test.js. The AI Interview assignment itself
        // is created entirely server-side (submit_assessment_v2 ->
        // _advance_after_programming_v2_result) — this page never
        // creates it.
        if (programmingVideoRecorder) {

            try {

                await programmingVideoRecorder.stop();

                const uploadResult = await programmingVideoRecorder.upload(
                    applicationId,
                    "programming"
                );

                if (!uploadResult.ok) {
                    console.warn("Programming video upload failed:", uploadResult.error);
                }

            } catch (videoError) {
                console.warn("Programming video finalize/upload error:", videoError);
            }

            programmingVideoRecorder.cleanup();
        }

        stopPhoneDetection();

        if (window.IntegrityMode) {
            window.IntegrityMode.cleanup();
        }

        renderFinalResult(data);

    } catch (error) {

        console.error("Submit assessment error:", error);

        submitInFlight = false;

        showMessage(
            "Unable to reach the server to submit your assessment. " +
            "Please check your connection and try again."
        );

        if (submitButton) {

            submitButton.disabled = false;

            submitButton.innerHTML = `
                <i class="bi bi-send-check me-1"></i>
                Submit Assessment
            `;
        }
    }
}


/* =========================================================
   LOAD / REFRESH ASSESSMENT STATE
   ========================================================= */

function lockAssessmentUi(message, type) {

    assessmentLocked = true;
    stopCountdown();

    const editor = document.getElementById("pv2CodeEditor");
    const runButton = document.getElementById("pv2RunButton");
    const languageSelect = document.getElementById("pv2LanguageSelect");
    const submitButton = document.getElementById("pv2SubmitFinalButton");

    if (editor) editor.disabled = true;
    if (runButton) runButton.disabled = true;
    if (languageSelect) languageSelect.disabled = true;
    if (submitButton) submitButton.disabled = true;

    if (message) {
        showMessage(message, type || "warning");
    }
}


async function loadAssessmentState(isRefresh) {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2`,
            { credentials: "include" }
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {

            if (isRefresh) {
                // A background refresh failing is not fatal by
                // itself (e.g. a transient network blip) — only a
                // genuine, confirmed 401 should redirect.
                if (response.status === 401 && window.HireFlowSessionGuard) {
                    await window.HireFlowSessionGuard.handlePossibleAuthFailure(response);
                }
                return;
            }

            await handleApiError(response, data, "Unable to load this programming assessment.");
            hideLoading();
            return;
        }

        currentView = data;

        if (data.status === "expired") {

            hideLoading();
            lockAssessmentUi(
                "This programming assessment has expired. The " +
                "completion window has passed.",
                "warning"
            );

            const headerCard = document.getElementById("pv2HeaderCard");
            const nav = document.getElementById("pv2Nav");
            const workspace = document.getElementById("pv2Workspace");
            const finalSubmitCard = document.getElementById("pv2FinalSubmitCard");

            if (headerCard) headerCard.style.display = "none";
            if (nav) nav.style.display = "none";
            if (workspace) workspace.style.display = "none";
            if (finalSubmitCard) finalSubmitCard.style.display = "none";

            return;
        }

        if (data.status === "completed") {

            hideLoading();
            assessmentLocked = true;
            renderFinalResult(data);
            return;
        }

        // in_progress
        hideLoading();
        clearMessage();
        renderJobHeader(data.job);

        document.getElementById("pv2HeaderCard").style.display = "block";
        document.getElementById("pv2FinalSubmitCard").style.display = "block";

        startCountdown(data.expires_at);

        if (activeQuestionId === null && data.questions.length > 0) {
            activeQuestionId = data.questions[0].question_id;
        }

        renderNav();
        renderActiveQuestion();

    } catch (error) {

        console.error("Load programming v2 state error:", error);

        if (!isRefresh) {

            showMessage(
                "Unable to reach the server to load your assessment. " +
                "Please check your connection and try again."
            );
            hideLoading();
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

        showMessage("Please login again to access your assessment.");
        hideLoading();
        return;
    }

    if (!applicationId) {

        showMessage("No application was specified for this assessment.");
        hideLoading();
        return;
    }

    try {

        // Start (idempotent — a candidate returning to an
        // already-started attempt gets the SAME snapshot back,
        // never a new one) then load the canonical state via GET,
        // exactly as this task's own instructions specify.
        const startResponse = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/start`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
            }
        );

        const startData = await parseJsonSafely(startResponse);

        if (!startResponse.ok) {
            await handleApiError(startResponse, startData, "Unable to start your programming assessment.");
            hideLoading();
            return;
        }

        await loadAssessmentState(false);

        if (currentView && currentView.status === "in_progress") {
            activateStrictModeForProgrammingV2();
        }

    } catch (error) {

        console.error("Start programming v2 error:", error);
        showMessage("Unable to reach the server to start your assessment.");
        hideLoading();
        return;
    }

    const runButton = document.getElementById("pv2RunButton");
    const submitButton = document.getElementById("pv2SubmitFinalButton");
    const languageSelect = document.getElementById("pv2LanguageSelect");
    const resetButton = document.getElementById("pv2ResetButton");
    const editor = document.getElementById("pv2CodeEditor");

    if (runButton) {
        runButton.addEventListener("click", runCode);
    }

    if (submitButton) {
        submitButton.addEventListener("click", submitAssessment);
    }

    if (languageSelect) {

        languageSelect.addEventListener("change", function () {

            if (assessmentLocked || activeQuestionId === null) {
                return;
            }

            saveCurrentEditorIntoDrafts();

            selectedLanguageByQuestion[activeQuestionId] = languageSelect.value;

            loadEditorForActiveSelection();

            const resultEl = document.getElementById("pv2RunResult");

            if (resultEl) {
                resultEl.innerHTML = "";
            }
        });
    }

    if (resetButton) {

        resetButton.addEventListener("click", function () {

            if (assessmentLocked || activeQuestionId === null) {
                return;
            }

            const language = selectedLanguageByQuestion[activeQuestionId];
            const key = draftKey(activeQuestionId, language);

            drafts[key] = LANGUAGE_STARTER_SHELLS[language] || "";

            loadEditorForActiveSelection();
        });
    }

    if (editor) {

        editor.addEventListener("keydown", function (event) {

            if (event.key === "Tab") {

                event.preventDefault();

                const start = editor.selectionStart;
                const end = editor.selectionEnd;

                editor.value = editor.value.substring(0, start) + "    " + editor.value.substring(end);
                editor.selectionStart = editor.selectionEnd = start + 4;
            }

            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {

                event.preventDefault();
                runCode();
            }
        });
    }

    document.addEventListener("visibilitychange", function () {

        if (document.visibilityState === "visible" && !assessmentLocked) {
            loadAssessmentState(true);
        }
    });

    window.addEventListener("pageshow", function (event) {

        if (event.persisted && !assessmentLocked) {
            loadAssessmentState(true);
        }
    });
});


window.addEventListener("beforeunload", function () {

    stopCountdown();

    if (programmingVideoRecorder) {
        programmingVideoRecorder.cleanup();
        programmingVideoRecorder = null;
    }

    stopPhoneDetection();

    if (window.IntegrityMode) {
        window.IntegrityMode.cleanup();
    }
});

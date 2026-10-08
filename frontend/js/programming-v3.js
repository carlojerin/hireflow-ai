// ============================================================
// JOBPILOT AI - PROGRAMMING V3 (PROFESSIONAL ASSESSMENT UX)
// (Task 5G)
//
// Talks to the EXACT SAME Programming V2 backend endpoints
// programming-v2.js already uses (start / get / run / submit) --
// this is a frontend UX upgrade only, never a second backend
// contract. programming-v2.html/js/css are left completely
// untouched; this file is additive and independent.
//
// Reuses, unchanged: session-guard.js, assessment-integrity.js
// (window.IntegrityMode), phone-detector.js (window.PhoneDetector),
// assessment-video-recorder.js (window.AssessmentVideoRecorder) --
// no second integrity/proctoring/recording system.
// ============================================================

const API_BASE_URL = "http://127.0.0.1:8000";

let applicationId = null;
let currentUser = null;
let currentView = null; // last GET /programming/v2 response
let activeQuestionId = null;
let submitInFlight = false;
let runInFlight = false;
let assessmentLocked = false;

// "questionId::language" -> source code. Client-side only, never
// auto-submitted across languages.
const drafts = {};
const selectedLanguageByQuestion = {};

// Per-question UI state, purely for the nav dot -- never
// authoritative (the server always re-derives the real score at
// submit time). "unanswered" | "passed" | "failed" | "submitted"
// -- matches programming-v3.css's .pv3-nav-dot.state-* classes.
const questionUiState = {};

let countdownIntervalId = null;
let expiresAtMs = null;

let terminatedByIntegrity = false;
let programmingVideoRecorder = null;
let cameraStreamActive = false;

let activeProblemTab = "description";
let activeBottomTab = "tests";
let lastRunResultByQuestion = {};

// Editor state
let monacoEditorInstance = null;
let monacoAvailable = false;
let editorFontSize = 13;
let editorThemeIsDark = true;
let suppressEditorChangeEvent = false;


const LANGUAGE_DISPLAY_NAMES = {
    python: "Python", javascript: "JavaScript", typescript: "TypeScript",
    java: "Java", c: "C", cpp: "C++", csharp: "C#", go: "Go",
    rust: "Rust", php: "PHP", ruby: "Ruby", kotlin: "Kotlin", swift: "Swift",
    sql: "SQL", html: "HTML / CSS / JS",
};

const MONACO_LANGUAGE_IDS = {
    python: "python", javascript: "javascript", typescript: "typescript",
    java: "java", c: "c", cpp: "cpp", csharp: "csharp", go: "go",
    rust: "rust", php: "php", ruby: "ruby", kotlin: "kotlin", swift: "swift",
    sql: "sql", html: "html",
};

const LANGUAGE_STARTER_SHELLS = {
    python: "# Write your solution below\n\n",
    javascript: "// Write your solution below\n\n",
    typescript: "// Write your solution below\n\n",
    java: "import java.util.Scanner;\n\npublic class Solution {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        // Write your solution below\n\n    }\n}\n",
    c: "#include <stdio.h>\n\nint main(void) {\n    // Write your solution below\n\n    return 0;\n}\n",
    cpp: "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    // Write your solution below\n\n    return 0;\n}\n",
    csharp: "using System;\n\nclass Program {\n    static void Main() {\n        // Write your solution below\n\n    }\n}\n",
    go: "package main\n\nimport (\n\t\"bufio\"\n\t\"fmt\"\n\t\"os\"\n)\n\nfunc main() {\n\treader := bufio.NewReader(os.Stdin)\n\t_ = reader\n\t// Write your solution below\n\n\tfmt.Println()\n}\n",
    rust: "use std::io::{self, Read};\n\nfn main() {\n    let mut input = String::new();\n    io::stdin().read_to_string(&mut input).unwrap();\n    // Write your solution below\n\n}\n",
    php: "<?php\n// Write your solution below\n\n",
    ruby: "# Write your solution below\n\n",
    kotlin: "fun main() {\n    // Write your solution below\n\n}\n",
    swift: "import Foundation\n\n// Write your solution below\n\n",
    sql: "-- Write ONE SELECT statement (SQLite syntax)\n\n",
    html: "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<title>Task</title>\n<style>\n/* your CSS here */\n</style>\n</head>\n<body>\n<!-- your HTML here -->\n<script>\n// your JavaScript here (if the task needs it)\n</script>\n</body>\n</html>\n",
};


function languageDisplayName(language) {
    return LANGUAGE_DISPLAY_NAMES[language] || (language ? language.charAt(0).toUpperCase() + language.slice(1) : "");
}


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

    const el = document.getElementById("pv3Message");
    el.innerHTML = `
        <div class="alert alert-${type} m-3" role="alert">
            ${escapeHtml(message)}
        </div>
    `;
}


function clearMessage() {
    document.getElementById("pv3Message").innerHTML = "";
}


function showCenterState(text) {

    document.getElementById("pv3CenterState").style.display = "flex";
    document.getElementById("pv3CenterStateText").textContent = text;
}


function hideCenterState() {
    document.getElementById("pv3CenterState").style.display = "none";
}


function formatClock(totalSeconds) {

    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    const minutes = Math.floor(safeSeconds / 60);
    const seconds = safeSeconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}


function parseServerTimestamp(isoString) {

    if (!isoString) return null;
    const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(isoString) ? isoString : `${isoString}Z`;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}


function renderTopbar(job) {

    document.getElementById("pv3JobTitle").textContent = (job && job.title) || "Job";
    document.getElementById("pv3CompanyName").textContent = (job && job.company) || "";
}


function draftKey(questionId, language) {
    return `${questionId}::${language}`;
}


// ============================================================
// TIMER (server-authoritative -- never client-computed)
// ============================================================

function stopCountdown() {

    if (countdownIntervalId) {
        clearInterval(countdownIntervalId);
        countdownIntervalId = null;
    }
}


function renderCountdown() {

    if (expiresAtMs === null) return;

    const remainingMs = expiresAtMs - Date.now();
    const remainingSeconds = Math.max(0, Math.floor(remainingMs / 1000));

    const timerEl = document.getElementById("pv3Timer");
    timerEl.textContent = formatClock(remainingSeconds);
    timerEl.classList.toggle("is-urgent", remainingSeconds <= 120);

    if (remainingSeconds <= 0) {
        stopCountdown();
        loadAssessmentState(true);
    }
}


function startCountdown(expiresAtIso) {

    stopCountdown();
    const expiresAtDate = parseServerTimestamp(expiresAtIso);
    expiresAtMs = expiresAtDate ? expiresAtDate.getTime() : null;

    renderCountdown();
    countdownIntervalId = setInterval(renderCountdown, 1000);
}


// ============================================================
// INTEGRITY (reused, unmodified system)
// ============================================================

function setIntegrityDot(kind, label) {

    const dot = document.getElementById("pv3IntegrityDot");
    dot.classList.remove("is-ready", "is-warning", "is-error");
    if (kind) dot.classList.add(`is-${kind}`);
    document.getElementById("pv3IntegrityStatus").textContent = label;
}


function handleIntegrityStateChange(state) {

    if (!state) return;

    if (state.warningLevel === "terminated" || state.terminated) {
        handleAssessmentTerminated();
        return;
    }

    if (state.warningLevel === "warning" || state.warningCount > 0) {

        setIntegrityDot(
            "warning",
            `Integrity warning (${state.warningCount || 1} of ${state.maxWarnings || 3})`,
        );

    } else if (!terminatedByIntegrity) {

        if (cameraStreamActive) {
            setIntegrityDot("ready", "Camera ready — monitoring active");
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

    setIntegrityDot("error", "Assessment terminated — integrity violation limit reached");

    if (window.IntegrityMode && typeof window.IntegrityMode.cleanup === "function") {
        window.IntegrityMode.cleanup();
    }

    lockAssessmentUi(
        "Your assessment was automatically terminated after repeated integrity violations. This has been reported to the employer.",
        "danger",
    );
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


async function activateStrictModeForProgrammingV3() {

    if (!window.IntegrityMode) return;

    window.IntegrityMode.enterStrictMode({
        userId: currentUser ? currentUser.id : null,
        applicationId,
        assessmentType: "programming",
        onWarning: handleIntegrityStateChange,
        onFullscreenExit: handleIntegrityStateChange,
        onStateChange: handleIntegrityStateChange,
    });

    try {

        await window.IntegrityMode.startCameraMonitoring();
        const cameraStream = window.IntegrityMode.getCameraStream && window.IntegrityMode.getCameraStream();
        cameraStreamActive = !!cameraStream;

        if (cameraStreamActive) {
            setIntegrityDot("ready", "Camera ready — monitoring active");
            startPhoneDetection();

            if (window.AssessmentVideoRecorder && window.AssessmentVideoRecorder.isSupported()) {
                programmingVideoRecorder = new window.AssessmentVideoRecorder();
                await programmingVideoRecorder.start(cameraStream, "programming");
            }

        } else {
            setIntegrityDot("error", "Camera unavailable — proceeding without video monitoring");
        }

    } catch (error) {
        console.warn("Integrity/camera activation failed:", error);
        setIntegrityDot("error", "Camera unavailable — proceeding without video monitoring");
    }
}


// ============================================================
// NAVIGATOR
// ============================================================

function computeQuestionUiState(question) {

    if (currentView && currentView.status === "completed") {
        return "submitted";
    }

    return questionUiState[question.question_id] || "unanswered";
}


function renderNav() {

    if (!currentView) return;

    const listEl = document.getElementById("pv3NavList");

    listEl.innerHTML = currentView.questions.map((question, index) => {

        const state = computeQuestionUiState(question);
        const isActive = question.question_id === activeQuestionId;

        return `
            <button
                type="button"
                class="pv3-nav-item ${isActive ? "is-active" : ""}"
                onclick="switchToQuestion(${question.question_id})"
                ${assessmentLocked ? "disabled" : ""}
            >
                <span class="pv3-nav-dot state-${state}"></span>
                <span class="pv3-nav-item-label">Q${index + 1}. ${escapeHtml(question.title)}</span>
                <span class="pv3-nav-item-points">${escapeHtml(String(question.points))}</span>
            </button>
        `;
    }).join("");
}


// ============================================================
// EDITOR (Monaco lazy-load, graceful textarea+gutter fallback)
// ============================================================

function getEditorValue() {

    if (monacoAvailable && monacoEditorInstance) {
        return monacoEditorInstance.getValue();
    }

    return document.getElementById("pv3FallbackTextarea").value;
}


function setEditorValue(value) {

    suppressEditorChangeEvent = true;

    if (monacoAvailable && monacoEditorInstance) {
        monacoEditorInstance.setValue(value);
    } else {
        const textarea = document.getElementById("pv3FallbackTextarea");
        textarea.value = value;
        syncFallbackLineNumbers();
    }

    suppressEditorChangeEvent = false;
}


function setEditorLanguage(language) {

    if (monacoAvailable && monacoEditorInstance && window.monaco) {
        const monacoLangId = MONACO_LANGUAGE_IDS[language] || "plaintext";
        window.monaco.editor.setModelLanguage(monacoEditorInstance.getModel(), monacoLangId);
    }
}


function syncFallbackLineNumbers() {

    const textarea = document.getElementById("pv3FallbackTextarea");
    const gutter = document.getElementById("pv3LineNumbers");
    const lineCount = textarea.value.split("\n").length;

    let html = "";
    for (let i = 1; i <= lineCount; i++) html += `${i}\n`;
    gutter.textContent = html;

    gutter.scrollTop = textarea.scrollTop;
}


function applyEditorFontSize() {

    if (monacoAvailable && monacoEditorInstance) {
        monacoEditorInstance.updateOptions({ fontSize: editorFontSize });
    } else {
        document.getElementById("pv3FallbackTextarea").style.fontSize = `${editorFontSize}px`;
        document.getElementById("pv3LineNumbers").style.fontSize = `${editorFontSize}px`;
    }
}


function applyEditorTheme() {

    if (monacoAvailable && window.monaco) {
        window.monaco.editor.setTheme(editorThemeIsDark ? "vs-dark" : "vs");
    }
    // The fallback textarea uses a single fixed dark palette by
    // design (see programming-v3.css) -- theme toggling is a
    // Monaco-only enhancement, not a requirement, since the
    // fallback already reads clearly in a code-editor context.
}


let autosaveCodeDebounceId = null;
const AUTOSAVE_CODE_DEBOUNCE_MS = 1000;

async function autosaveCurrentCode(questionId, language, sourceCode) {

    try {

        await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/autosave`,
            {
                method: "PUT",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question_id: questionId,
                    language: language,
                    source_code: sourceCode,
                }),
            },
        );

    } catch (error) {
        // Best-effort only -- a failed autosave never blocks the
        // candidate from continuing, running, or submitting normally.
        console.warn("Programming code autosave failed:", error);
    }
}


function scheduleCodeAutosave(questionId, language, sourceCode) {

    if (autosaveCodeDebounceId) {
        clearTimeout(autosaveCodeDebounceId);
    }

    autosaveCodeDebounceId = setTimeout(
        () => autosaveCurrentCode(questionId, language, sourceCode),
        AUTOSAVE_CODE_DEBOUNCE_MS,
    );
}


function onEditorContentChanged() {

    if (suppressEditorChangeEvent) return;

    const activeQuestion = findActiveQuestion();
    if (!activeQuestion) return;

    const language = selectedLanguageByQuestion[activeQuestion.question_id];
    if (!language) return;

    const sourceCode = getEditorValue();

    drafts[draftKey(activeQuestion.question_id, language)] = sourceCode;

    scheduleCodeAutosave(activeQuestion.question_id, language, sourceCode);

    schedulePreviewRefresh();

    if (!questionUiState[activeQuestion.question_id] || questionUiState[activeQuestion.question_id] === "unanswered") {
        // stays "unanswered" for nav purposes until a Run happens --
        // typing alone doesn't count as progress for the nav dot.
    }
}


function initFallbackEditor() {

    const textarea = document.getElementById("pv3FallbackTextarea");

    textarea.addEventListener("input", () => {
        syncFallbackLineNumbers();
        onEditorContentChanged();
    });

    textarea.addEventListener("scroll", () => {
        document.getElementById("pv3LineNumbers").scrollTop = textarea.scrollTop;
    });

    textarea.addEventListener("keydown", (event) => {

        if (event.key === "Tab") {
            event.preventDefault();
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.value = textarea.value.slice(0, start) + "    " + textarea.value.slice(end);
            textarea.selectionStart = textarea.selectionEnd = start + 4;
            syncFallbackLineNumbers();
            onEditorContentChanged();
        }

        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
            event.preventDefault();
            runCode();
        }
    });

    syncFallbackLineNumbers();
}


function initMonacoOrFallback() {

    initFallbackEditor(); // always ready as the safe baseline

    const MONACO_TIMEOUT_MS = 6000;
    let settled = false;

    const fallbackTimeout = setTimeout(() => {
        if (!settled) {
            settled = true;
            console.warn("Monaco editor failed to load in time — using the built-in editor.");
        }
    }, MONACO_TIMEOUT_MS);

    try {

        const loaderScript = document.createElement("script");
        loaderScript.src = "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.js";

        loaderScript.onload = () => {

            try {

                window.require.config({
                    paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs" },
                });

                window.require(["vs/editor/editor.main"], () => {

                    if (settled) return; // timeout already fell back
                    settled = true;
                    clearTimeout(fallbackTimeout);

                    try {
                        mountMonacoEditor();
                    } catch (mountError) {
                        console.warn("Monaco mount failed — using the built-in editor.", mountError);
                    }
                });

            } catch (configError) {
                console.warn("Monaco config failed — using the built-in editor.", configError);
            }
        };

        loaderScript.onerror = () => {
            console.warn("Monaco loader script failed to load — using the built-in editor.");
        };

        document.head.appendChild(loaderScript);

    } catch (error) {
        console.warn("Monaco could not be initialized — using the built-in editor.", error);
    }
}


function mountMonacoEditor() {

    const host = document.getElementById("pv3EditorHost");
    const fallbackWrap = document.getElementById("pv3FallbackWrap");

    const currentValue = getEditorValue();

    const monacoContainer = document.createElement("div");
    monacoContainer.style.position = "absolute";
    monacoContainer.style.inset = "0";
    monacoContainer.id = "pv3MonacoContainer";
    host.appendChild(monacoContainer);

    const activeQuestion = findActiveQuestion();
    const language = activeQuestion ? (selectedLanguageByQuestion[activeQuestion.question_id] || "python") : "python";

    monacoEditorInstance = window.monaco.editor.create(monacoContainer, {
        value: currentValue,
        language: MONACO_LANGUAGE_IDS[language] || "plaintext",
        theme: editorThemeIsDark ? "vs-dark" : "vs",
        fontSize: editorFontSize,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        tabSize: 4,
        insertSpaces: true,
    });

    monacoEditorInstance.addCommand(
        window.monaco.KeyMod.CtrlCmd | window.monaco.KeyCode.Enter,
        () => runCode(),
    );

    monacoEditorInstance.onDidChangeModelContent(() => onEditorContentChanged());

    fallbackWrap.style.display = "none";
    monacoAvailable = true;
}


// ============================================================
// QUESTION RENDERING
// ============================================================

function findActiveQuestion() {

    if (!currentView) return null;
    return currentView.questions.find((q) => q.question_id === activeQuestionId) || null;
}


function renderLanguageOptions(question) {

    const select = document.getElementById("pv3LanguageSelect");

    select.innerHTML = question.allowed_languages.map((lang) => `
        <option value="${escapeHtml(lang)}">${escapeHtml(languageDisplayName(lang))}</option>
    `).join("");

    const currentSelection = selectedLanguageByQuestion[question.question_id] || question.allowed_languages[0];
    select.value = currentSelection;
    selectedLanguageByQuestion[question.question_id] = currentSelection;
}


function loadEditorForActiveSelection() {

    const question = findActiveQuestion();
    if (!question) return;

    const language = selectedLanguageByQuestion[question.question_id];
    const key = draftKey(question.question_id, language);

    const draft = Object.prototype.hasOwnProperty.call(drafts, key)
        ? drafts[key]
        : (LANGUAGE_STARTER_SHELLS[language] || "");

    drafts[key] = draft;
    setEditorValue(draft);
    setEditorLanguage(language);
}


function renderProblemTabs(question) {

    document.getElementById("pv3ProblemTitle").textContent = question.title;

    const difficulty = question.difficulty || "medium";
    const badgesHtml = `
        <span class="pv3-badge difficulty-${escapeHtml(difficulty)}">${escapeHtml(difficulty)}</span>
        <span class="pv3-badge pv3-points">${escapeHtml(String(question.points))} pts</span>
        ${question.technology ? `<span class="pv3-badge" style="background:#f1f5f9;color:#475569;">${escapeHtml(question.technology)}</span>` : ""}
    `;
    document.getElementById("pv3ProblemBadges").innerHTML = badgesHtml;

    const scenarioHtml = question.scenario
        ? `<div class="pv3-scenario-box">${escapeHtml(question.scenario)}</div>`
        : "";

    const requirementsHtml = (question.requirements && question.requirements.length)
        ? `
            <div class="pv3-section-label">Requirements</div>
            <ul class="pv3-requirements-list">
                ${question.requirements.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}
            </ul>
        `
        : "";

    const complexityHtml = question.expected_complexity
        ? `<div class="pv3-section-label">Expected Complexity</div><div>${escapeHtml(question.expected_complexity)}</div>`
        : "";

    const notesHtml = question.notes
        ? `<div class="pv3-section-label">Notes</div><div>${escapeHtml(question.notes)}</div>`
        : "";

    document.getElementById("pv3TabDescription").innerHTML = `
        ${scenarioHtml}
        <div class="pv3-section-label">Description</div>
        <div>${escapeHtml(question.description).replace(/\n/g, "<br>")}</div>
        ${requirementsHtml}
        <div class="pv3-section-label">Input Format</div>
        <div>${escapeHtml(question.input_specification)}</div>
        <div class="pv3-section-label">Output Format</div>
        <div>${escapeHtml(question.output_specification)}</div>
        ${complexityHtml}
        ${notesHtml}
    `;

    const examples = question.examples || [];
    document.getElementById("pv3TabExamples").innerHTML = examples.length
        ? examples.map((ex, i) => `
            <div class="pv3-example-block">
                <span class="pv3-ex-label">Example ${i + 1} — Input</span>${escapeHtml(ex.input || "")}
            </div>
            <div class="pv3-example-block">
                <span class="pv3-ex-label">Example ${i + 1} — Output</span>${escapeHtml(ex.output || "")}
            </div>
        `).join("")
        : `<div class="text-muted">No worked examples for this question.</div>`;

    document.getElementById("pv3TabConstraints").innerHTML = question.constraints
        ? `<div>${escapeHtml(question.constraints)}</div>`
        : `<div class="text-muted">No additional constraints.</div>`;
}


function switchProblemTab(tabName) {

    activeProblemTab = tabName;

    document.querySelectorAll(".pv3-problem-panel .pv3-tab-btn").forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.pv3Tab === tabName);
    });

    document.querySelectorAll(".pv3-tab-pane").forEach((pane) => pane.classList.remove("is-active"));

    const map = {
        description: "pv3TabDescription", examples: "pv3TabExamples", constraints: "pv3TabConstraints",
    };
    document.getElementById(map[tabName]).classList.add("is-active");
}


// ============================================================
// LIVE PREVIEW ("compiler" for HTML / CSS / JavaScript questions)
//
// The candidate's page runs in a sandboxed iframe (scripts allowed, but no same-origin access to this assessment and,
// through a content-security-policy in the page itself, no network requests), refreshed as they type. console.log / errors
// from the page are shown in a console under the preview. The graded checks still run on the server in a real browser
// (Run Code); this is only the candidate's own view of what they are building.
// ============================================================

const PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data: blob:; font-src data:;";

const PREVIEW_CONSOLE_SHIM = `<script>(function(){
    function send(level, args){ try { parent.postMessage({ pv3Console: true, level: level, text: Array.prototype.map.call(args, function(a){
        try { return typeof a === "object" ? JSON.stringify(a) : String(a); } catch (e) { return String(a); } }).join(" ") }, "*"); } catch (e) {} }
    ["log","info","warn","error"].forEach(function(l){ var o = console[l]; console[l] = function(){ send(l, arguments); try { o.apply(console, arguments); } catch (e) {} }; });
    document.addEventListener("DOMContentLoaded", function(){ var b = document.body; var visible = b && Array.prototype.some.call(b.children, function(c){ return c.tagName !== "SCRIPT"; });
        try { parent.postMessage({ pv3Empty: !visible && !(b && b.innerText.trim()) }, "*"); } catch (e) {} });
    window.addEventListener("error", function(e){ send("error", [e.message + (e.lineno ? " (line " + e.lineno + ")" : "")]); });
    window.addEventListener("unhandledrejection", function(e){ send("error", ["Unhandled promise rejection: " + (e.reason && e.reason.message || e.reason)]); });
})();<\/script>`;

let previewTimer = null;

function activeQuestionIsWeb() {
    const question = findActiveQuestion();
    return !!question && selectedLanguageByQuestion[question.question_id] === "html";
}

function buildPreviewDocument(code) {
    const head = `<meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">${PREVIEW_CONSOLE_SHIM}`;
    if (/<head[^>]*>/i.test(code)) return code.replace(/<head[^>]*>/i, (m) => m + head);
    if (/<html[^>]*>/i.test(code)) return code.replace(/<html[^>]*>/i, (m) => m + "<head>" + head + "</head>");
    return "<!DOCTYPE html><html><head>" + head + "</head><body>" + code + "</body></html>";
}

function refreshPreview() {
    const frame = document.getElementById("pv3PreviewFrame");
    if (!frame || !activeQuestionIsWeb()) return;
    const consoleEl = document.getElementById("pv3PreviewConsole");
    if (consoleEl) consoleEl.textContent = "";
    frame.srcdoc = buildPreviewDocument(getEditorValue());
}

function schedulePreviewRefresh() {
    if (!activeQuestionIsWeb() || activeBottomTab !== "preview") return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(refreshPreview, 500);
}

// show the "Live Preview" tab only for HTML/CSS/JS questions; open it by default when such a question opens
function updatePreviewAvailability(openIt) {
    const tabBtn = document.getElementById("pv3PreviewTabBtn");
    if (!tabBtn) return;
    const isWeb = activeQuestionIsWeb();
    tabBtn.style.display = isWeb ? "" : "none";
    if (isWeb) {
        if (openIt) switchBottomTab("preview");
        refreshPreview();
    } else if (activeBottomTab === "preview") {
        switchBottomTab("tests");
    }
}

window.addEventListener("message", (event) => {
    const frame = document.getElementById("pv3PreviewFrame");
    if (frame && event.source === frame.contentWindow && event.data && event.data.pv3Empty === true) {
        const hint = document.getElementById("pv3PreviewConsole");
        if (hint) hint.textContent = "The page is empty so far. Write your HTML between <body> and </body> (and your CSS in <style>) to see it here.";
        return;
    }
    if (!frame || event.source !== frame.contentWindow || !event.data || event.data.pv3Console !== true) return;
    const consoleEl = document.getElementById("pv3PreviewConsole");
    if (!consoleEl) return;
    const line = document.createElement("div");
    line.className = event.data.level === "error" ? "is-error" : event.data.level === "warn" ? "is-warn" : "";
    line.textContent = "> " + String(event.data.text).slice(0, 500);
    consoleEl.appendChild(line);
    while (consoleEl.childNodes.length > 100) consoleEl.removeChild(consoleEl.firstChild);
    consoleEl.scrollTop = consoleEl.scrollHeight;
});

document.addEventListener("click", (event) => {
    if (event.target && event.target.closest && event.target.closest("#pv3PreviewRefresh")) refreshPreview();
});


function switchBottomTab(tabName) {

    activeBottomTab = tabName;

    document.querySelectorAll(".pv3-bottom-tabs .pv3-tab-btn").forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.pv3BottomTab === tabName);
    });

    document.querySelectorAll(".pv3-bottom-pane").forEach((pane) => pane.classList.remove("is-active"));

    const map = { tests: "pv3BottomTests", output: "pv3BottomOutput", errors: "pv3BottomErrors", preview: "pv3BottomPreview" };
    document.getElementById(map[tabName]).classList.add("is-active");
}


function saveCurrentEditorIntoDrafts() {

    const question = findActiveQuestion();
    if (!question) return;

    const language = selectedLanguageByQuestion[question.question_id];
    if (!language) return;

    drafts[draftKey(question.question_id, language)] = getEditorValue();
}


function switchToQuestion(questionId) {

    if (assessmentLocked) return;

    saveCurrentEditorIntoDrafts();
    activeQuestionId = questionId;

    const question = findActiveQuestion();
    if (!question) return;

    renderProblemTabs(question);
    renderLanguageOptions(question);
    loadEditorForActiveSelection();
    switchProblemTab("description");
    updatePreviewAvailability(true);

    const savedResult = lastRunResultByQuestion[questionId];
    if (savedResult) {
        renderRunResult(savedResult, questionId);
    } else {
        document.getElementById("pv3BottomTests").innerHTML = `
            <div class="text-muted small">Click "Run Code" to see how your solution performs against the sample tests.</div>
        `;
        document.getElementById("pv3BottomOutput").innerHTML = "";
        document.getElementById("pv3BottomErrors").innerHTML = "";
    }

    renderNav();
}


function handleLanguageChange() {

    saveCurrentEditorIntoDrafts();

    const question = findActiveQuestion();
    if (!question) return;

    const select = document.getElementById("pv3LanguageSelect");
    selectedLanguageByQuestion[question.question_id] = select.value;

    loadEditorForActiveSelection();
    updatePreviewAvailability(false);
    showMessage(`Switched to ${languageDisplayName(select.value)} — your ${languageDisplayName(select.value)} draft for this question was preserved.`, "info");
    setTimeout(clearMessage, 2500);
}


function handleResetCode() {

    const question = findActiveQuestion();
    if (!question) return;

    const language = selectedLanguageByQuestion[question.question_id];
    const starter = LANGUAGE_STARTER_SHELLS[language] || "";

    drafts[draftKey(question.question_id, language)] = starter;
    setEditorValue(starter);
}


// ============================================================
// RUN RESULT RENDERING
// ============================================================

function renderRunResult(data, questionId) {

    lastRunResultByQuestion[questionId] = data;

    const results = data.results || [];
    const passedCount = results.filter((r) => r.success && normalizeOutput(r.actual_stdout) === normalizeOutput(r.expected_stdout)).length;

    const summaryHtml = `
        <div class="pv3-run-summary">
            <span><strong>${passedCount}/${results.length}</strong> sample tests passed</span>
        </div>
    `;

    const testsHtml = results.map((r, i) => {

        const passed = r.success && normalizeOutput(r.actual_stdout) === normalizeOutput(r.expected_stdout);

        return `
            <div class="pv3-test-row ${passed ? "is-pass" : "is-fail"}" style="display:block;">
                <div class="d-flex justify-content-between align-items-center">
                    <strong>Test ${i + 1}</strong>
                    <span class="badge ${passed ? "bg-success" : "bg-danger"}">${passed ? "PASS" : "FAIL"}</span>
                </div>
                <div class="pv3-test-io"><strong>Input:</strong> ${escapeHtml(String(r.stdin || "").indexOf("{\"case\"") === 0 ? "(automatic page check)" : r.stdin)}</div>
                <div class="pv3-test-io"><strong>Expected:</strong> ${escapeHtml(r.expected_stdout)}</div>
                <div class="pv3-test-io"><strong>Actual:</strong> ${escapeHtml(r.actual_stdout)}</div>
                <div class="small text-muted mt-1">${escapeHtml(r.error_type)} · ${escapeHtml(String(r.execution_time_ms))}ms</div>
            </div>
        `;
    }).join("");

    document.getElementById("pv3BottomTests").innerHTML = summaryHtml + testsHtml;

    const outputText = results.map((r) => r.actual_stdout).join("\n---\n");
    document.getElementById("pv3BottomOutput").innerHTML = `<div class="pv3-output-block">${escapeHtml(outputText || "(no output)")}</div>`;

    const errorLines = results.filter((r) => !r.success).map((r) => `[${r.error_type}] ${r.stderr || r.error_type}`);
    document.getElementById("pv3BottomErrors").innerHTML = errorLines.length
        ? `<div class="pv3-output-block">${escapeHtml(errorLines.join("\n\n"))}</div>`
        : `<div class="text-muted small">No compile or runtime errors.</div>`;

    // Matches programming-v3.css's .pv3-nav-dot.state-* vocabulary
    // exactly (state-passed/state-failed/state-run/state-submitted/
    // state-locked) -- this reflects PUBLIC sample-test results
    // only, never a hidden-test verdict.
    questionUiState[questionId] = passedCount === results.length && results.length > 0 ? "passed" : "failed";

    if (errorLines.length) {
        switchBottomTab("errors");
    } else {
        switchBottomTab("tests");
    }

    renderNav();
}


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
        showMessage("You do not have permission to access this assessment.", "danger");
        return;
    }

    if (response.status === 404) {
        showMessage("This assessment could not be found.", "danger");
        return;
    }

    if (response.status === 409) {

        const detail = (data && data.detail) || "";

        if (/language/i.test(detail)) {
            showMessage("That language is currently unavailable for this question. Please choose a different language.", "warning");
        } else {
            showMessage(detail || fallbackMessage, "warning");
        }
        return;
    }

    if (response.status === 410) {
        showMessage("This assessment has expired or changed state. Refreshing…", "warning");
        await loadAssessmentState(true);
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
// RUN CODE
// ============================================================

async function runCode() {

    if (runInFlight || assessmentLocked || terminatedByIntegrity) return;

    const question = findActiveQuestion();
    if (!question) return;

    saveCurrentEditorIntoDrafts();

    const language = selectedLanguageByQuestion[question.question_id];
    const sourceCode = drafts[draftKey(question.question_id, language)] || "";

    runInFlight = true;
    const runButton = document.getElementById("pv3RunButton");
    runButton.disabled = true;
    runButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Running…`;

    switchBottomTab("tests");
    document.getElementById("pv3BottomTests").innerHTML = `<div class="text-muted small">Running your code…</div>`;

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/run`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    question_id: question.question_id, language, source_code: sourceCode,
                }),
            },
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {
            await handleApiError(response, data, "Unable to run your code right now.");
            document.getElementById("pv3BottomTests").innerHTML = `<div class="text-muted small">Run failed — see the message above.</div>`;
            return;
        }

        renderRunResult(data, question.question_id);

    } catch (error) {

        console.error("Run code error:", error);
        showMessage("A network error occurred while running your code.", "danger");

    } finally {

        runInFlight = false;
        runButton.disabled = false;
        runButton.innerHTML = `<i class="bi bi-play-fill"></i> Run Code`;
    }
}


// ============================================================
// SUBMIT ASSESSMENT (whole-assessment, matches real V2 API)
// ============================================================

function renderFinalResult(data) {

    document.getElementById("pv3Workspace").style.display = "none";
    document.getElementById("pv3ActionBar").style.display = "none";
    document.getElementById("pv3ResultSection").style.display = "block";

    const statusBadge = document.getElementById("pv3StatusBadge");
    if (statusBadge) statusBadge.textContent = "Completed";

    const passed = data.passed === true;

    // `percentage` is only ever present on the response to the
    // ORIGINAL POST /submit call, never on a later GET /programming/
    // v2 (e.g. after a reload) -- computing it from score/total_score
    // whenever it's missing keeps the badge correct in both cases,
    // instead of silently falling back to the raw (non-percentage)
    // score value, which only coincidentally matches when total_
    // score is exactly 100.
    const percentage = data.percentage !== undefined && data.percentage !== null
        ? data.percentage
        : (data.total_score ? Math.round((data.score / data.total_score) * 10000) / 100 : data.score);

    document.getElementById("pv3ResultBadge").innerHTML = `
        <span class="fs-4 fw-bold ${passed ? "text-success" : "text-danger"}">
            ${passed ? "Passed" : "Not Passed"} — ${escapeHtml(String(percentage ?? "-"))}%
        </span>
    `;

    // The per-question breakdown (`report.questions`, with earned_
    // points per question) is ONLY present on the original submit
    // response -- GET /programming/v2 never returns it for a
    // completed assessment. Rather than render a silently-empty
    // section (or fabricate numbers) after a reload, show an
    // honest note instead.
    const report = data.report || null;
    const questionResults = report ? (report.questions || []) : null;

    if (questionResults) {

        document.getElementById("pv3QuestionResults").innerHTML = questionResults.map((q, i) => `
            <div class="pv3-question-result-row">
                <span>Q${i + 1}. ${escapeHtml(q.title || "")} ${q.language_used ? `(${escapeHtml(languageDisplayName(q.language_used))})` : ""}</span>
                <strong>${escapeHtml(String(q.earned_points ?? 0))} / ${escapeHtml(String(q.max_points ?? 0))} pts</strong>
            </div>
        `).join("");

    } else {

        document.getElementById("pv3QuestionResults").innerHTML = `
            <div class="text-muted small text-center">
                Your submission has been scored. A per-question breakdown is shown
                immediately after submitting; your recruiter can also see the full
                per-question result.
            </div>
        `;
    }
}


function lockAssessmentUi(message, type) {

    assessmentLocked = true;
    stopCountdown();

    const runBtn = document.getElementById("pv3RunButton");
    const submitBtn = document.getElementById("pv3SubmitButton");
    if (runBtn) runBtn.disabled = true;
    if (submitBtn) submitBtn.disabled = true;

    const statusBadge = document.getElementById("pv3StatusBadge");
    if (statusBadge) statusBadge.textContent = "Expired";

    if (message) showMessage(message, type || "warning");
}


async function performFinalSubmit() {

    if (submitInFlight || assessmentLocked) return;

    saveCurrentEditorIntoDrafts();

    const answers = currentView.questions
        .filter((q) => questionHasAnyDraft(q))
        .map((q) => {
            const language = selectedLanguageByQuestion[q.question_id] || q.allowed_languages[0];
            return {
                question_id: q.question_id,
                language,
                source_code: drafts[draftKey(q.question_id, language)] || "",
            };
        });

    submitInFlight = true;
    const submitButton = document.getElementById("pv3SubmitButton");
    submitButton.disabled = true;
    submitButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Submitting…`;

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/submit`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answers }),
            },
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {
            await handleApiError(response, data, "Unable to submit your assessment right now.");
            return;
        }

        if (programmingVideoRecorder) {
            try {
                await programmingVideoRecorder.stop();
                await programmingVideoRecorder.upload(applicationId, "programming");
            } catch (recordingError) {
                console.warn("Video recording finalize/upload failed:", recordingError);
            }
        }

        if (window.IntegrityMode && typeof window.IntegrityMode.cleanup === "function") {
            window.IntegrityMode.cleanup();
        }

        assessmentLocked = true;
        stopCountdown();
        stopPhoneDetection();

        renderFinalResult(data);

    } catch (error) {

        console.error("Submit error:", error);
        showMessage("A network error occurred while submitting your assessment.", "danger");

    } finally {

        submitInFlight = false;
        submitButton.disabled = false;
        submitButton.innerHTML = `<i class="bi bi-send-check"></i> Submit Assessment`;
    }
}


function questionHasAnyDraft(question) {

    return question.allowed_languages.some((lang) => {
        const key = draftKey(question.question_id, lang);
        const value = drafts[key];
        const starter = LANGUAGE_STARTER_SHELLS[lang] || "";
        return value !== undefined && value.trim() !== "" && value !== starter;
    });
}


// Same rule as the server scorer: line endings and trailing spaces never decide a test.
function normalizeOutput(text) {
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    return lines.map((line) => line.replace(/\s+$/, "")).join("\n").replace(/\n+$/, "");
}


// ============================================================
// ASSESSMENT STATE LOAD
// ============================================================

async function loadAssessmentState(isRefresh) {

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2`,
            { credentials: "include" },
        );

        const data = await parseJsonSafely(response);

        if (!response.ok) {
            await handleApiError(response, data, "Unable to load your assessment.");
            return;
        }

        currentView = data;

        // Restore autosaved (not-yet-run/submitted) code per
        // question, so a refresh/reconnect doesn't drop whatever the
        // candidate had already typed. Only fills in drafts not
        // already held in memory -- never overwrites code the
        // candidate is actively looking at in this same page load.
        const savedDraftCode = data.draft_code || {};
        for (const questionId of Object.keys(savedDraftCode)) {
            const { language, source_code } = savedDraftCode[questionId];
            const key = draftKey(Number(questionId), language);
            if (!(key in drafts)) {
                drafts[key] = source_code;
            }
        }

        if (data.status === "in_progress") {

            hideCenterState();
            clearMessage();

            document.getElementById("pv3Topbar").style.display = "flex";
            document.getElementById("pv3Workspace").style.display = "grid";
            document.getElementById("pv3ActionBar").style.display = "flex";

            renderTopbar(data.job);
            startCountdown(data.expires_at);

            if (!activeQuestionId && data.questions.length) {
                activeQuestionId = data.questions[0].question_id;
            }

            if (!isRefresh || !monacoEditorInstance) {
                switchToQuestion(activeQuestionId);
            } else {
                renderNav();
            }

        } else if (data.status === "completed") {

            hideCenterState();
            assessmentLocked = true;
            stopCountdown();
            renderFinalResult(data);

        } else if (data.status === "expired") {

            hideCenterState();
            lockAssessmentUi("This assessment has expired.", "warning");

        } else {
            hideCenterState();
            showMessage(`Assessment status: ${data.status}`, "info");
        }

    } catch (error) {

        console.error("Load assessment state error:", error);
        showMessage("A network error occurred while loading your assessment.", "danger");
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
        showMessage("No application specified for this assessment.", "danger");
        hideCenterState();
        return;
    }

    // Instructions + tick box BEFORE anything starts (the 45-minute clock starts with the /start request below)
    if (window.AssessmentInstructions) {
        const accepted = await window.AssessmentInstructions.ensure({ apiBase: API_BASE_URL, applicationId, type: "programming" });
        if (!accepted) {
            window.location.href = "index.html";
            return;
        }
    }

    // Mandatory camera/microphone check -- also before the clock starts. This page
    // previously skipped straight to startCameraMonitoring() inside strict mode with
    // no mandatory pre-check, unlike aptitude/SJT/TDA, which was a real gap.
    if (window.IntegrityMode) {
        await window.IntegrityMode.runCameraReadinessCheck();
    }

    initMonacoOrFallback();

    // Toolbar wiring
    document.getElementById("pv3LanguageSelect").addEventListener("change", handleLanguageChange);
    document.getElementById("pv3ResetCodeBtn").addEventListener("click", handleResetCode);

    document.getElementById("pv3FontIncreaseBtn").addEventListener("click", () => {
        editorFontSize = Math.min(24, editorFontSize + 1);
        applyEditorFontSize();
    });

    document.getElementById("pv3FontDecreaseBtn").addEventListener("click", () => {
        editorFontSize = Math.max(10, editorFontSize - 1);
        applyEditorFontSize();
    });

    document.getElementById("pv3ThemeToggleBtn").addEventListener("click", () => {
        editorThemeIsDark = !editorThemeIsDark;
        applyEditorTheme();
    });

    document.querySelectorAll(".pv3-problem-panel .pv3-tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => switchProblemTab(btn.dataset.pv3Tab));
    });

    document.querySelectorAll(".pv3-bottom-tabs .pv3-tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => switchBottomTab(btn.dataset.pv3BottomTab));
    });

    document.getElementById("pv3RunButton").addEventListener("click", runCode);

    document.getElementById("pv3SubmitButton").addEventListener("click", () => {
        if (assessmentLocked) return;
        const modal = new bootstrap.Modal(document.getElementById("pv3FinalSubmitConfirmModal"));
        modal.show();
    });

    document.getElementById("pv3ConfirmFinalSubmitBtn").addEventListener("click", async () => {
        const modalEl = document.getElementById("pv3FinalSubmitConfirmModal");
        const modal = bootstrap.Modal.getInstance(modalEl);
        if (modal) modal.hide();
        await performFinalSubmit();
    });

    document.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !monacoAvailable) {
            // handled inside initFallbackEditor's own listener when
            // the textarea has focus; this is a harmless no-op
            // otherwise. Monaco's own command handles its case.
        }
    });

    try {

        const startResponse = await fetch(
            `${API_BASE_URL}/api/assessment/sessions/${applicationId}/programming/v2/start`,
            {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
            },
        );

        const startData = await parseJsonSafely(startResponse);

        if (!startResponse.ok) {
            await handleApiError(startResponse, startData, "Unable to start your programming assessment.");
            hideCenterState();
            return;
        }

    } catch (error) {
        console.error("Start assessment error:", error);
        showMessage("A network error occurred while starting your assessment.", "danger");
        hideCenterState();
        return;
    }

    await loadAssessmentState(false);
    await activateStrictModeForProgrammingV3();

    // Registered only AFTER the initial start+load sequence
    // completes -- registering these earlier let a visibilitychange
    // event fired during the tab's own initial load race ahead of
    // the very first /start call and hit GET .../programming/v2
    // before any assessment row existed yet, producing a genuine
    // (if harmless) 404 whose error message could then race with
    // and outlive the real, successful load.
    window.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && !assessmentLocked) {
            loadAssessmentState(true);
        }
    });

    window.addEventListener("pageshow", () => {
        if (!assessmentLocked) loadAssessmentState(true);
    });
});

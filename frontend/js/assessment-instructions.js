/* =========================================================
   HIREFLOW AI - PRE-ASSESSMENT INSTRUCTIONS + CONSENT

   A readable pop-up shown BEFORE the self introduction (the full security briefing) and again before the role test, the practical task and the
   AI interview (with that stage's own facts). It tells the candidate in plain words: how the assessment works, how they are watched, what
   counts as a strike, what they must do, and what happens when a rule is broken. It ends with a tick box:
       "I have read and understood ... and I promise to complete this assessment honestly, on my own."
   The Start button stays disabled until the box is ticked. Pressing it records the acceptance on the server (one row per
   application + stage, shown to the recruiter) and, because it is a real click, also puts the page into full screen.

   Nothing here starts the assessment clock: callers show it BEFORE their own start request. On a reload after the assessment began the
   server already has the acceptance, so the pop-up is not shown again.

   The candidate text deliberately gives NO timings, counts or limits (they would be a roadmap for cheating): only what is watched and that repeated strikes end the test.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.AssessmentInstructions = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    const CONTENT = {
        self_intro: {
            title: "Your assessment",
            icon: "bi-shield-check",
            facts: [
                ["Step 1", "Self introduction, 2 minutes"],
                ["Step 2", "Role test, multiple choice"],
                ["Step 3", "A practical task for the job"],
                ["Step 4", "Spoken AI interview"],
            ],
            about: [
                "It is <b>proctored</b>: your camera stays on throughout, and from the role test onward your whole screen is also shared.",
                "Each step has its own timer. Timers start when you press Start and cannot be paused.",
                "Your results and these records go to the recruiter in one report.",
            ],
            audio: "Your microphone <b>level</b> is checked for talking during the silent steps. Only the self introduction and the interview are recorded.",
        },
        aptitude: {
            title: "Role Test",
            icon: "bi-ui-checks",
            facts: [
                ["Questions", "10 multiple choice"],
                ["Time", "20 minutes"],
                ["Pass mark", "70%"],
                ["Topics", "Chosen for your role"],
            ],
            about: [
                "Questions are generated for you and differ from other candidates'.",
                "The 20 minute timer starts when you press Start and cannot be paused.",
                "You can move between questions and change answers until you submit.",
            ],
            audio: "Your microphone <b>level</b> is checked for talking during the test. Nothing is recorded or stored from the microphone.",
        },
        programming: {
            title: "Practical Task",
            icon: "bi-code-slash",
            facts: [
                ["Task", "Chosen for your role"],
                ["Time", "Shown on the next screen"],
                ["Pass mark", "60-70%"],
                ["Submit", "Only once"],
            ],
            about: [
                "This may be code, a technical lab, a written task, a data task, a design task or a call, depending on the job.",
                "Read the instructions on the next screen, then work in the tools provided.",
                "Press Submit only when you have finished: everything locks after submission.",
            ],
            audio: "Your microphone <b>level</b> is checked for talking during the task. Nothing is recorded or stored from the microphone.",
        },
        ai_interview: {
            title: "AI Interview",
            icon: "bi-mic",
            facts: [
                ["Format", "Spoken video call"],
                ["Questions", "8, based on the role and your answers"],
                ["Time", "Up to 30 minutes"],
                ["Answers", "Speak out loud"],
            ],
            about: [
                "The AI interviewer asks one short question at a time and listens until you stop speaking.",
                "Speak clearly and in your own words. You may ask it to repeat a question.",
                "If your microphone stops working you can type your answer instead.",
            ],
            audio: "Your voice is recorded during the interview and sent to a speech service (Google Gemini) to be turned into text; the text and recording are shown to the recruiter.",
        },
    };

    const MONITORING = [
        ["bi-camera-video", "Your <b>camera is on</b>. Your video may be recorded, and small photos are taken regularly during the assessment and whenever a check below is triggered."],
        ["bi-person-bounding-box", "We check that <b>your face stays visible</b>, that <b>you are alone</b>, that you keep looking at the screen, and that the camera is not covered."],
        ["bi-people", "<b>Your photos are compared with each other by a computer</b> to help the recruiter check it is the same person throughout. A person makes the decision."],
        ["bi-display", "<b>From the role test onward, your whole screen is shared and recorded.</b> Stopping the share pauses the test until you share again."],
        ["bi-fullscreen", "<b>Full screen and this window only.</b> Leaving full screen, switching tabs or switching to another application is detected at once."],
        ["bi-slash-circle", "<b>Copy, paste, right-click and developer-tool shortcuts are blocked</b> and recorded."],
    ];

    const STRIKES = [
        "Leaving full screen, switching to another tab or window",
        "Your face not being visible",
        "More than one person in view, or a phone in view",
        "Camera covered or disconnected",
        "Looking away from the screen",
        "Your eyes being off the screen for a long time",
        "Stopping the screen share",
        "Talking out loud during a silent step",
    ];

    const FLAGS = [
        "Pasting or copying text",
        "Blocked shortcuts and opening developer tools",
        "A second screen connected",
        "Typing faster than a person can (an automation tool)",
        "A different-sounding voice in the interview",
    ];

    const RULES = [
        "Take it <b>alone</b>, in a quiet, well-lit room, facing the camera.",
        "Do <b>not</b> use a phone, notes, other devices, other websites, or any AI tool or person to help you.",
        "Stay in <b>full screen</b> on this page. Do not refresh, go back, or close the window.",
        "Keep your camera on and your face clearly visible the whole time.",
        "Use a stable internet connection. Your timer keeps running if you are disconnected.",
    ];

    function consequences(maxViolations) {
        return "Every strike is <b>recorded and shown to the recruiter</b> with the photos. " +
            "<b>Repeated strikes end the assessment automatically.</b> " +
            "A flag alone never ends it; the recruiter just reads it. These are signals, not a verdict: the recruiter makes the decision.";
    }

    const CHECK_TEXT = "I have read and understood these instructions. I agree to be monitored as described, and " +
        "<b>I promise to complete this assessment honestly and on my own, without any help.</b>";

    function buildHtml(type, maxViolations) {

        const c = CONTENT[type];
        const li = (items) => items.map((t) => `<li style="margin:5px 0">${t}</li>`).join("");
        const card = (icon, title, body, tint) => `
            <section style="background:${tint || "#f8fafc"};border:1px solid #e2e8f0;border-radius:14px;padding:14px 16px;margin-top:12px">
                <div style="display:flex;align-items:center;gap:9px;font-weight:800;font-size:15px;margin-bottom:6px">
                    <i class="bi ${icon}" style="font-size:19px;color:#4f46e5"></i>${title}
                </div>${body}
            </section>`;

        const monitoring = MONITORING.map(([icon, text]) => `
            <div style="display:flex;gap:10px;align-items:flex-start;margin:7px 0">
                <i class="bi ${icon}" style="color:#4f46e5;font-size:17px;margin-top:2px;flex:0 0 auto"></i><span>${text}</span>
            </div>`).join("") +
            `<div style="margin-top:8px;font-size:14px;color:#475569">${c.audio}</div>`;

        const strikeBox = `
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px">
                <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:10px 14px">
                    <div style="font-weight:800;color:#b91c1c;font-size:13px;text-transform:uppercase;letter-spacing:.04em">Counts as a strike</div>
                    <ul style="margin:6px 0 0;padding-left:18px;font-size:14px">${li(STRIKES)}</ul>
                </div>
                <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:10px 14px">
                    <div style="font-weight:800;color:#1d4ed8;font-size:13px;text-transform:uppercase;letter-spacing:.04em">Only flagged for the recruiter</div>
                    <ul style="margin:6px 0 0;padding-left:18px;font-size:14px">${li(FLAGS)}</ul>
                </div>
            </div>`;

        return `
        <div style="background:#fff;color:#0f172a;border-radius:18px;max-width:780px;width:100%;max-height:92vh;overflow:auto;
                    box-shadow:0 24px 70px rgba(0,0,0,.5);padding:26px 28px;font-family:system-ui,Segoe UI,Arial,sans-serif;line-height:1.5;font-size:15px">
            <div style="display:flex;align-items:center;gap:14px">
                <div style="width:52px;height:52px;border-radius:14px;background:#eef2ff;color:#4f46e5;display:flex;align-items:center;justify-content:center;font-size:26px;flex:0 0 auto">
                    <i class="bi ${c.icon}"></i></div>
                <div>
                    <div style="font-size:12px;font-weight:700;color:#6366f1;text-transform:uppercase;letter-spacing:.08em">Before you begin</div>
                    <h2 id="aiTitle" style="margin:0;font-size:24px;font-weight:800">${c.title}: please read</h2>
                </div>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px">
                ${c.facts.map(([k, v]) => `<div style="background:#f1f5f9;border-radius:10px;padding:9px 12px">
                    <div style="font-size:11px;color:#64748b;text-transform:uppercase;font-weight:700">${k}</div>
                    <div style="font-weight:700;font-size:14px">${v}</div></div>`).join("")}
            </div>

            ${card("bi-info-circle", "How it works", `<ul style="margin:0;padding-left:20px">${li(c.about)}</ul>`)}
            ${card("bi-eye", "How you are monitored", monitoring)}
            ${card("bi-exclamation-triangle", "What counts as a strike", strikeBox, "#fffbeb")}
            ${card("bi-check2-square", "What you must do", `<ul style="margin:0;padding-left:20px">${li(RULES)}</ul>`)}
            ${card("bi-shield-exclamation", "If a rule is broken", `<div>${consequences(maxViolations)}</div>`, "#fef2f2")}

            <label style="display:flex;gap:12px;align-items:flex-start;margin:20px 0 4px;padding:14px;border:2px solid #c7d2fe;border-radius:12px;
                          background:#eef2ff;cursor:pointer">
                <input id="aiAgree" type="checkbox" style="width:22px;height:22px;margin-top:2px;flex:0 0 auto;cursor:pointer">
                <span style="font-size:15px">${CHECK_TEXT}</span>
            </label>

            <div id="aiError" style="color:#b91c1c;font-size:14px;min-height:20px;margin-top:6px"></div>

            <div style="display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap;margin-top:8px">
                <button id="aiCancel" type="button" style="border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:12px 22px;font-size:15px;font-weight:600;cursor:pointer">Not now</button>
                <button id="aiStart" type="button" disabled style="border:0;background:#16a34a;color:#fff;border-radius:999px;padding:12px 28px;font-size:16px;font-weight:700;cursor:pointer;opacity:.45">I agree, continue</button>
            </div>
        </div>`;
    }

    function show(type, options) {

        return new Promise(function (resolve) {

            const overlay = document.createElement("div");
            overlay.id = "assessmentInstructionsOverlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.setAttribute("aria-labelledby", "aiTitle");
            overlay.style.cssText = "position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;" +
                "background:rgba(15,23,42,.82);padding:16px;";
            overlay.innerHTML = buildHtml(type, options && options.maxViolations);
            document.body.appendChild(overlay);

            const box = overlay.querySelector("#aiAgree");
            const start = overlay.querySelector("#aiStart");

            box.addEventListener("change", function () {
                start.disabled = !box.checked;
                start.style.opacity = box.checked ? "1" : ".45";
            });

            overlay.querySelector("#aiCancel").addEventListener("click", function () {
                overlay.remove();
                resolve(false);
            });

            start.addEventListener("click", function () {
                if (!box.checked) return;

                // a real click: the only moment the browser lets us enter full screen
                try {
                    if (document.fullscreenEnabled && !document.fullscreenElement) {
                        const result = document.documentElement.requestFullscreen();
                        if (result && result.catch) result.catch(function () { /* the exam lock screen asks again */ });
                    }
                } catch (error) { /* ignore */ }

                overlay.remove();
                resolve(true);
            });

            box.focus();
        });
    }

    /* options: { apiBase, applicationId, type }  ->  Promise<boolean> (false = the candidate chose not to start) */
    async function ensure(options) {

        const type = options.type;

        if (!CONTENT[type]) return true;

        const url = `${options.apiBase}/api/assessment/consents/${options.applicationId}/${type}`;
        let maxViolations = null;

        try {
            const existing = await fetch(url, { credentials: "include" });
            if (existing.ok) {
                const data = await existing.json();
                if (data && data.accepted) return true;         // already accepted (for example a reload during the assessment)
                if (data && data.max_violations) maxViolations = data.max_violations;
            }
        } catch (error) { /* offline check failed: show the pop-up */ }

        const accepted = await show(type, { maxViolations: maxViolations });

        if (!accepted) return false;

        try {
            await fetch(url, {
                method: "POST", credentials: "include",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
            });
        } catch (error) { /* the acceptance is best-effort recorded; the assessment can still start */ }

        return true;
    }

    return { ensure: ensure, show: show, CONTENT: CONTENT };
});

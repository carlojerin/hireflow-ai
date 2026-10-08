/* =========================================================
   HIREFLOW AI - SCREEN PROCTORING (entire-screen sharing during a test)

   A browser agent, a second window or an AI assistant can help a candidate without any signal the page can read. The screen is the one
   thing they cannot hide: the candidate must SHARE THEIR ENTIRE SCREEN before an aptitude, programming or interview stage starts, and it
   is recorded next to the camera video, so the recruiter can see what was on screen.

     * Only "Entire screen" is accepted: a window or tab is refused (displaySurface must be "monitor") and the candidate is asked again.
     * The test does not start until a valid share exists. A browser that cannot share a screen is told to use Chrome or Edge on a computer.
     * If sharing stops mid-test the test is blocked, a violation is recorded, and the candidate must share the entire screen again.
     * The screen is recorded as its own recording per stage (self_intro_screen, aptitude_screen, programming_screen, ai_interview_screen; a second or third
       part if the candidate had to share again), spooled to the server by the shared recorder, so a candidate who leaves loses nothing.
     * An admin can switch the requirement off (Admin console, Security): the page asks the server and skips everything when it is off.

   Installed by IntegrityMode.enterStrictMode() for the test stages, and by assessment.js for the self introduction (which has no strict mode). The factory takes an environment so the logic is unit-tested in Node.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory;
    } else {
        root.ScreenProctor = factory({
            document: root.document, navigator: root.navigator, fetch: root.fetch ? root.fetch.bind(root) : null,
            Recorder: root.AssessmentVideoRecorder, apiBase: "http://127.0.0.1:8000", window: root,
        });
    }
})(typeof self !== "undefined" ? self : this, function (env) {

    "use strict";

    const SCREEN_BITRATE = 400000;           // 400 kbps: a 30-minute test stays near 90 MB
    const MAX_SEGMENTS = 3;                  // the server stores at most three screen parts per stage

    const BASE_STAGE = {
        self_intro: "self_intro", aptitude: "aptitude", programming: "programming", situational_judgment: "programming", technical_domain: "programming",
        ai_interview: "ai_interview",
    };

    /* ---- pure helpers (unit-tested) ---- */

    function baseStage(assessmentType) {
        return BASE_STAGE[assessmentType] || null;
    }

    function stageTag(assessmentType, segment) {
        const base = baseStage(assessmentType);
        if (!base || segment < 1 || segment > MAX_SEGMENTS) return null;
        return base + "_screen" + (segment > 1 ? "_" + segment : "");
    }

    // The browser tells us what was picked. Unknown (a browser that does not report it) is accepted: nothing better can be checked.
    function isEntireScreen(track) {
        const settings = (track && typeof track.getSettings === "function" && track.getSettings()) || {};
        return !settings.displaySurface || settings.displaySurface === "monitor";
    }

    /* ---- state ---- */

    let session = null;
    let overlay = null;
    let requiredPromise = null;

    function isSupported() {
        return Boolean(env.navigator && env.navigator.mediaDevices && typeof env.navigator.mediaDevices.getDisplayMedia === "function");
    }

    function isRequired() {

        if (!requiredPromise) {
            requiredPromise = Promise.resolve()
                .then(function () { return env.fetch(env.apiBase + "/api/assessment/videos/config/screen-share", { credentials: "include" }); })
                .then(function (response) { return response.json(); })
                .then(function (data) { return data.require_screen_share !== false; })
                .catch(function () { return true; });                  // when in doubt the requirement stands
        }

        return requiredPromise;
    }

    /* ---- the blocking screen ---- */

    function showOverlay(title, message, buttonText, onClick) {

        const doc = env.document;

        if (!overlay) {
            overlay = doc.createElement("div");
            overlay.id = "screenProctorOverlay";
            overlay.setAttribute("role", "alertdialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.style.cssText = "position:fixed;inset:0;z-index:2147483646;display:flex;align-items:center;justify-content:center;" +
                "background:#0b1220;color:#fff;font-family:system-ui,Segoe UI,Arial,sans-serif;text-align:center;padding:24px;";
            overlay.innerHTML =
                '<div style="max-width:620px">' +
                '<div style="font-size:54px;line-height:1">&#128421;</div>' +
                '<h1 id="screenProctorTitle" style="font-size:28px;margin:14px 0 10px;font-weight:700"></h1>' +
                '<p id="screenProctorMessage" style="font-size:17px;margin:0 0 10px;line-height:1.5"></p>' +
                '<p id="screenProctorSteps" style="font-size:14px;color:#94a3b8;margin:0 0 22px;line-height:1.55">' +
                'In the window your browser opens, choose <b>Entire Screen</b>, select your screen and click <b>Share</b>. ' +
                'Do not choose a window or a tab, and do not stop sharing until you submit. Your screen is recorded with your camera ' +
                'and reviewed by the employer.</p>' +
                '<button id="screenProctorButton" type="button" style="background:#16a34a;color:#fff;border:0;border-radius:999px;' +
                'padding:14px 30px;font-size:17px;font-weight:700;cursor:pointer"></button>' +
                '</div>';
            doc.body.appendChild(overlay);
        }

        overlay.querySelector("#screenProctorTitle").textContent = title;
        overlay.querySelector("#screenProctorMessage").textContent = message;

        const button = overlay.querySelector("#screenProctorButton");
        button.textContent = buttonText || "";
        button.style.display = buttonText ? "" : "none";
        button.onclick = onClick || null;
        overlay.style.display = "flex";
    }

    function hideOverlay() {
        if (overlay) overlay.style.display = "none";
    }

    /* ---- sharing + recording ---- */

    async function requestShare() {

        const stream = await env.navigator.mediaDevices.getDisplayMedia({
            video: { displaySurface: "monitor", frameRate: { ideal: 5, max: 10 }, width: { ideal: 1280 } },
            audio: false,
            selfBrowserSurface: "exclude",
            surfaceSwitching: "exclude",
            monitorTypeSurfaces: "include",
        });

        const track = stream.getVideoTracks()[0];

        if (!track || !isEntireScreen(track)) {
            stream.getTracks().forEach(function (t) { t.stop(); });
            return { ok: false };
        }

        return { ok: true, stream: stream, track: track };
    }

    function startSegment(stream) {

        const tag = stageTag(session.config.assessmentType, session.segment);

        session.recorder = null;
        session.tag = tag;

        if (!tag || !env.Recorder) return;

        const recorder = new env.Recorder();

        if (recorder.isSupported() && recorder.start(stream, tag, { videoBitsPerSecond: SCREEN_BITRATE })) {
            session.recorder = recorder;
        }
    }

    async function stopSegment() {

        const recorder = session && session.recorder;

        if (!recorder) return;

        session.recorder = null;

        try {
            await recorder.stop();
            await recorder.upload(session.config.applicationId, session.tag);
        } catch (error) {
            // the spooled chunks are on the server: they are assembled into a partial recording if this final step fails
        }

        try { recorder.cleanup(); } catch (error) { /* already clean */ }
    }

    function attach(result) {

        session.stream = result.stream;
        session.track = result.track;
        session.onEnded = function () { handleStopped(); };
        result.track.addEventListener("ended", session.onEnded);

        startSegment(result.stream);
    }

    async function handleStopped() {

        if (!session || session.finishing) return;

        session.report("screen_share_stopped");
        session.track = null;

        await stopSegment();

        session.segment += 1;

        promptToShare(
            "Screen sharing stopped",
            "The assessment is paused because your screen is no longer being shared. This has been recorded. Share your entire screen again to continue.",
            "Share entire screen again",
            null
        );
    }

    function promptToShare(title, message, buttonText, resolve) {

        showOverlay(title, message, buttonText, async function () {

            try {

                const result = await requestShare();

                if (!result.ok) {
                    session.report("screen_share_rejected");
                    showOverlay("Share your ENTIRE screen",
                        "You chose a window or a tab. Please choose \"Entire Screen\" instead, so the whole test environment is shared.",
                        buttonText, overlay.querySelector("#screenProctorButton").onclick);
                    return;
                }

                attach(result);
                hideOverlay();
                session.report("screen_share_started");

                try {
                    const root = env.document.documentElement;
                    if (!env.document.fullscreenElement && root.requestFullscreen) root.requestFullscreen().catch(function () {});
                } catch (error) { /* the exam lock asks again if needed */ }

                if (resolve) resolve(true);

            } catch (error) {
                showOverlay("Screen sharing is required",
                    "Sharing was cancelled. This assessment cannot start without sharing your entire screen. Click the button to try again.",
                    buttonText, overlay.querySelector("#screenProctorButton").onclick);
            }
        });
    }

    /* ---- public API ---- */

    // Resolves true once a valid entire-screen share exists (or straight away when it is not required / not an assessed stage).
    async function gate(config, report) {

        if (!baseStage(config && config.assessmentType) || !(await isRequired())) return true;

        report = report || function () {};

        if (!isSupported()) {
            report("screen_share_unsupported");
            showOverlay("This browser cannot share your screen",
                "Screen sharing is required for this assessment. Please open it on a computer in the latest version of Google Chrome or Microsoft Edge.",
                "", null);
            return new Promise(function () {});                         // the test stays blocked
        }

        session = { config: config, report: report, segment: 1, stream: null, track: null, recorder: null, tag: null, onEnded: null, finishing: false };

        return new Promise(function (resolve) {
            promptToShare("Share your entire screen to start",
                "This assessment is proctored. Before it begins you must share your entire screen. It is recorded together with your camera.",
                "Share entire screen", resolve);
        });
    }

    // The stage is over: stop watching, finish and upload the recording, release the screen.
    async function finish() {

        if (!session) return;

        session.finishing = true;

        if (session.track && session.onEnded) session.track.removeEventListener("ended", session.onEnded);

        await stopSegment();

        if (session.stream) session.stream.getTracks().forEach(function (t) { try { t.stop(); } catch (error) { /* ended */ } });

        hideOverlay();
        session = null;
    }

    // True while a valid entire-screen share is running for this page (the self-introduction asks again only after a page reload).
    function isActive() {
        return Boolean(session && session.track);
    }

    return { gate: gate, finish: finish, isActive: isActive, baseStage: baseStage, stageTag: stageTag, isEntireScreen: isEntireScreen, isSupported: isSupported };
});

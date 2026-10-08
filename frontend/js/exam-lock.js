/* =========================================================
   JOBPILOT AI - EXAM LOCK (aptitude + programming), the "government exam software" behaviour

   A web page cannot stop Alt+Tab or Ctrl+T (the browser and the OS keep those), so this does the next best thing, the way
   online exam portals do it:

     1. The exam runs in FULL SCREEN, and the Escape key is captured (Keyboard Lock API) so a single press no longer exits it.
     2. The moment the candidate leaves (fullscreen exited, another tab, another window / application), the whole exam is
        HIDDEN behind a lock screen: nothing on the page can be read, clicked or typed into.
     3. The lock only opens again when the candidate comes back AND returns to full screen (one click, a real user gesture).
     4. Every departure is already recorded and counted by IntegrityMode (3 warnings end the assessment); the lock screen
        shows the running count so the candidate knows exactly what happened.
     5. Browser Back / accidental navigation is trapped; refresh / close asks for confirmation (IntegrityMode).

   The exam timer is untouched: it is server-side, so time keeps running while the exam is locked.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.ExamLock = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    /* ---- pure decision: is the exam allowed to be visible right now? (unit-tested) ---- */
    function lockReasons(env) {

        const reasons = [];

        if (env.fullscreenRequired && !env.isFullscreen) reasons.push("fullscreen");
        if (env.hidden) reasons.push("tab");
        if (!env.hasFocus) reasons.push("window");

        return reasons;
    }

    const MESSAGES = {
        fullscreen: "You left full screen mode.",
        tab: "You switched to another tab.",
        window: "You left the assessment window.",
        start: "This assessment must be taken in full screen mode.",
    };

    let doc = null;
    let win = null;
    let overlay = null;
    let cleanups = [];
    let locked = false;
    let started = false;
    let warningText = "";
    let lastReasons = [];

    function fullscreenSupported() {
        return !!(doc && doc.fullscreenEnabled && doc.documentElement && doc.documentElement.requestFullscreen);
    }

    function environment() {

        return {
            fullscreenRequired: fullscreenSupported(),
            isFullscreen: !!doc.fullscreenElement,
            hidden: doc.visibilityState === "hidden",
            hasFocus: typeof doc.hasFocus === "function" ? doc.hasFocus() : true,
        };
    }

    function buildOverlay() {

        const el = doc.createElement("div");
        el.id = "examLockOverlay";
        el.setAttribute("role", "alertdialog");
        el.setAttribute("aria-modal", "true");
        el.setAttribute("aria-live", "assertive");
        el.style.cssText = "position:fixed;inset:0;z-index:2147483647;display:none;align-items:center;justify-content:center;" +
            "background:#0b1220;color:#fff;font-family:system-ui,Segoe UI,Arial,sans-serif;text-align:center;padding:24px;";
        el.innerHTML =
            '<div style="max-width:560px">' +
            '<div style="font-size:54px;line-height:1">&#128274;</div>' +
            '<h1 style="font-size:28px;margin:14px 0 8px;font-weight:700">Assessment locked</h1>' +
            '<p id="examLockReason" style="font-size:18px;margin:0 0 6px"></p>' +
            '<p id="examLockWarning" style="font-size:16px;color:#fca5a5;margin:0 0 18px"></p>' +
            '<p id="examLockCountdown" style="font-size:15px;color:#fde68a;margin:0 0 18px"></p>' +
            '<p style="font-size:14px;color:#94a3b8;margin:0 0 22px">Leaving the assessment is recorded and shown to the recruiter. ' +
            'Repeated violations end the assessment automatically. Your timer is still running.</p>' +
            '<button id="examLockResume" type="button" style="background:#16a34a;color:#fff;border:0;border-radius:999px;' +
            'padding:14px 30px;font-size:17px;font-weight:700;cursor:pointer">Return to full screen and continue</button>' +
            '</div>';
        doc.body.appendChild(el);
        return el;
    }

    function setHidden(hide) {

        // everything except the lock screen becomes unreadable and unclickable
        Array.prototype.forEach.call(doc.body.children, function (child) {
            if (child === overlay) return;
            if (hide) {
                child.setAttribute("data-exam-lock-hidden", "1");
                child.style.visibility = "hidden";
                child.setAttribute("inert", "");
            } else if (child.getAttribute("data-exam-lock-hidden") === "1") {
                child.removeAttribute("data-exam-lock-hidden");
                child.style.visibility = "";
                child.removeAttribute("inert");
            }
        });
    }

    // After 4 s on the lock screen the page tries to put the candidate back into full screen by itself. Browsers only
    // allow full screen from a real click, so when that is refused the button on this screen is still the way back.
    const AUTO_RELOCK_SECONDS = 4;
    let relockTimer = null;

    function clearRelock() {
        if (relockTimer) { clearInterval(relockTimer); relockTimer = null; }
        const note = overlay && overlay.querySelector("#examLockCountdown");
        if (note) note.textContent = "";
    }

    function startRelock() {

        if (relockTimer || !fullscreenSupported() || !started) return;

        let left = AUTO_RELOCK_SECONDS;
        const note = overlay.querySelector("#examLockCountdown");
        if (note) note.textContent = "Returning to full screen in " + left + " s...";

        relockTimer = setInterval(function () {
            left -= 1;
            if (left > 0) {
                if (note) note.textContent = "Returning to full screen in " + left + " s...";
                return;
            }
            clearInterval(relockTimer);
            relockTimer = null;
            if (note) note.textContent = "Your browser needs a click to go back to full screen: use the green button.";
            if (locked && !doc.fullscreenElement) resume();
        }, 1000);
    }

    function showLock(reasons, custom) {

        if (!overlay) overlay = buildOverlay();

        lastReasons = reasons;
        locked = true;

        overlay.querySelector("#examLockReason").textContent = custom || MESSAGES[reasons[0]] || MESSAGES.start;
        overlay.querySelector("#examLockWarning").textContent = warningText;
        overlay.style.display = "flex";
        setHidden(true);

        if (reasons.indexOf("fullscreen") !== -1 && !custom) startRelock();
    }

    function hideLock() {

        if (!overlay) return;

        clearRelock();
        overlay.style.display = "none";
        setHidden(false);
        locked = false;
        lastReasons = [];
    }

    function evaluate() {

        if (!started) return;

        const reasons = lockReasons(environment());

        if (reasons.length) {
            showLock(reasons);
        } else if (locked) {
            hideLock();
        }
    }

    function lockKeyboard() {

        try {
            if (win.navigator.keyboard && typeof win.navigator.keyboard.lock === "function" && doc.fullscreenElement) {
                win.navigator.keyboard.lock(["Escape"]).catch(function () { /* not available: the lock screen still works */ });
            }
        } catch (error) { /* ignore */ }
    }

    function resume() {

        // must run inside the click (user gesture)
        const done = function () {
            lockKeyboard();
            evaluate();
        };

        if (!fullscreenSupported() || doc.fullscreenElement) {
            done();
            return;
        }

        try {
            const result = doc.documentElement.requestFullscreen();
            if (result && typeof result.then === "function") {
                result.then(function () { setTimeout(done, 150); }).catch(function () { setTimeout(evaluate, 150); });
            } else {
                setTimeout(done, 150);
            }
        } catch (error) {
            setTimeout(evaluate, 150);
        }
    }

    function listen(target, type, handler) {
        target.addEventListener(type, handler);
        cleanups.push(function () { target.removeEventListener(type, handler); });
    }

    /* options: { document, window } for tests */
    function start(options) {

        stop();

        doc = (options && options.document) || document;
        win = (options && options.window) || window;
        started = true;

        listen(doc, "fullscreenchange", function () { lockKeyboard(); setTimeout(evaluate, 50); });
        listen(doc, "visibilitychange", evaluate);
        listen(win, "blur", function () { setTimeout(evaluate, 50); });
        listen(win, "focus", function () { setTimeout(evaluate, 50); });

        // Back button trap: staying on the exam page is the only way through
        try {
            win.history.pushState({ examLock: true }, "", win.location.href);
            listen(win, "popstate", function () {
                win.history.pushState({ examLock: true }, "", win.location.href);
            });
        } catch (error) { /* ignore */ }

        if (!overlay) overlay = buildOverlay();
        overlay.querySelector("#examLockResume").onclick = resume;

        // full screen is required from the first second: until the candidate is in it, only the start screen shows
        if (fullscreenSupported() && !doc.fullscreenElement) {
            showLock(["fullscreen"], MESSAGES.start);
        } else {
            lockKeyboard();
        }
    }

    function stop() {

        started = false;
        cleanups.forEach(function (fn) { try { fn(); } catch (error) { /* ignore */ } });
        cleanups = [];

        if (doc && overlay) {
            hideLock();
            overlay.remove();
        }

        overlay = null;
        locked = false;
        warningText = "";

        try {
            if (win && win.navigator.keyboard && typeof win.navigator.keyboard.unlock === "function") win.navigator.keyboard.unlock();
        } catch (error) { /* ignore */ }
    }

    // the server's running count of violations, shown on the lock screen
    function setWarning(count, max) {

        warningText = count > 0 ? `Violation ${count} recorded. Repeated violations end the assessment.` : "";

        if (overlay) overlay.querySelector("#examLockWarning").textContent = warningText;
    }

    function isLocked() { return locked; }

    return { start: start, stop: stop, setWarning: setWarning, isLocked: isLocked, lockReasons: lockReasons, MESSAGES: MESSAGES };
});

/* =========================================================
   JOBPILOT AI - BROWSER GUARDS (clipboard, developer-tool shortcuts, second screen)

   Installed by IntegrityMode for every assessment stage. Everything here is a FLAG for the recruiter (never counted
   toward the warnings by itself) and only ever reports sizes / key names, never the clipboard text.

     paste_detected / copy_detected / cut_detected
         Outside the code editor pasting, copying and cutting are BLOCKED (copying the question to ask an AI, pasting
         an answer written elsewhere) and reported. Inside the code editor they are allowed (refactoring your own
         code needs them) but reported with the number of characters, so a 400-character paste is visible to HR.
     blocked_shortcut     F12, Ctrl+Shift+I/J/C, Ctrl+U, Ctrl+S, Ctrl+P, PrintScreen and the right-click menu are blocked.
     devtools_opened      the window shows the size difference a docked developer-tools panel causes.
     multiple_monitors_detected   the browser reports an extended (second) screen.
     automated_typing_suspected   several keystrokes arrive within a few milliseconds of each other in an answer box or the code
                                  editor. A person cannot type that way; a browser agent or automation tool types a string at once.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.IntegrityGuards = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    /* ---- pure helpers (unit-tested) ---- */

    function shortcutName(event) {

        const key = String(event.key || "");
        const ctrl = !!(event.ctrlKey || event.metaKey);

        if (key === "F12") return "F12";
        if (key === "PrintScreen") return "PrintScreen";

        if (ctrl && event.shiftKey) {
            const upper = key.toUpperCase();
            if (upper === "I") return "Ctrl+Shift+I";
            if (upper === "J") return "Ctrl+Shift+J";
            if (upper === "C") return "Ctrl+Shift+C";
        }

        if (ctrl && !event.shiftKey && !event.altKey) {
            const lower = key.toLowerCase();
            if (lower === "u") return "Ctrl+U";
            if (lower === "s") return "Ctrl+S";
            if (lower === "p") return "Ctrl+P";
        }

        return null;
    }

    function devtoolsLikelyOpen(win) {

        const wide = (win.outerWidth || 0) - (win.innerWidth || 0);
        const tall = (win.outerHeight || 0) - (win.innerHeight || 0);

        return wide > 200 || tall > 320;         // ordinary browser chrome is ~15 px wide / ~130 px tall
    }

    // Keystrokes (ms timestamps) that arrived in one burst: BURST_KEYS or more keydowns inside BURST_WINDOW_MS. Fast human typing and
    // key rollover sit around 30-100 ms between keys; an agent's "type" action sends every character in the same millisecond.
    const BURST_KEYS = 4;
    const BURST_WINDOW_MS = 20;

    function isKeystrokeBurst(timestamps) {

        for (let i = 0; i + BURST_KEYS - 1 < timestamps.length; i++) {
            if (timestamps[i + BURST_KEYS - 1] - timestamps[i] <= BURST_WINDOW_MS) return true;
        }

        return false;
    }

    function isTextEntryTarget(node) {

        return Boolean(node && (/^(TEXTAREA|INPUT)$/.test(node.tagName || "") || node.isContentEditable ||
            (typeof node.closest === "function" && node.closest(".monaco-editor, .cm-editor, [data-code-editor]"))));
    }

    function clipboardTarget(node) {

        if (!node || typeof node.closest !== "function") return "page";
        if (node.closest(".monaco-editor, .cm-editor, #pv3EditorHost, #pv3FallbackWrap, [data-code-editor]")) return "editor";
        if (/^(TEXTAREA|INPUT)$/.test(node.tagName || "") || node.isContentEditable) return "answer_box";
        return "page";
    }

    /* ---- installation ---- */

    function install(report, doc, win) {

        doc = doc || document;
        win = win || window;

        const cleanups = [];
        let devtoolsReported = false;

        function listen(target, type, handler, options) {
            target.addEventListener(type, handler, options);
            cleanups.push(function () { target.removeEventListener(type, handler, options); });
        }

        function clipboardHandler(kind, eventName) {
            return function (event) {
                const target = clipboardTarget(event.target);
                let chars = 0;
                try {
                    if (kind === "paste") {
                        chars = String((event.clipboardData && event.clipboardData.getData("text")) || "").length;
                    } else {
                        chars = String((win.getSelection && win.getSelection().toString()) || "").length;
                    }
                } catch (error) { chars = 0; }

                if (target !== "editor") {
                    event.preventDefault();                       // blocked outside the code editor
                }

                report(eventName, { chars: chars, target: target });
            };
        }

        listen(doc, "paste", clipboardHandler("paste", "paste_detected"), true);
        listen(doc, "copy", clipboardHandler("copy", "copy_detected"), true);
        listen(doc, "cut", clipboardHandler("cut", "cut_detected"), true);

        listen(doc, "contextmenu", function (event) {
            event.preventDefault();
            report("blocked_shortcut", { key: "ContextMenu" });
        }, true);

        const keyTimes = [];
        let lastTypingReport = -Infinity;

        listen(doc, "keydown", function (event) {

            if (isTextEntryTarget(event.target) && !event.ctrlKey && !event.metaKey && String(event.key || "").length === 1) {
                const now = (win.performance && win.performance.now) ? win.performance.now() : Date.now();
                keyTimes.push(now);
                if (keyTimes.length > 12) keyTimes.shift();

                if (isKeystrokeBurst(keyTimes) && now - lastTypingReport > 30000) {
                    lastTypingReport = now;
                    report("automated_typing_suspected", { chars: keyTimes.length, target: clipboardTarget(event.target) });
                }
            }
        }, true);

        listen(doc, "keydown", function (event) {
            const name = shortcutName(event);
            if (name) {
                event.preventDefault();
                report("blocked_shortcut", { key: name });
            }
        }, true);

        const timer = win.setInterval(function () {
            const open = devtoolsLikelyOpen(win);
            if (open && !devtoolsReported) {
                devtoolsReported = true;
                report("devtools_opened");
            } else if (!open) {
                devtoolsReported = false;
            }
        }, 2000);
        cleanups.push(function () { win.clearInterval(timer); });

        try {
            if (win.screen && win.screen.isExtended === true) {
                report("multiple_monitors_detected");
            }
        } catch (error) { /* not reported by this browser */ }

        return function uninstall() {
            cleanups.forEach(function (fn) { try { fn(); } catch (error) { /* ignore */ } });
        };
    }

    return { install: install, shortcutName: shortcutName, devtoolsLikelyOpen: devtoolsLikelyOpen, clipboardTarget: clipboardTarget, isKeystrokeBurst: isKeystrokeBurst };
});

/* =========================================================
   HIREFLOW AI - CAMERA SETUP COACH (lighting and framing)

   Proctoring only works if the camera can SEE the candidate. A dark room, a window behind them or a face half out of the picture makes
   the face checks fail for reasons that are not the candidate's fault, and makes honest candidates look suspicious. This module looks at
   each camera frame and tells the candidate, in plain words, what to change: "The light is behind you...", "Move a little closer...".

     * analyzeFrame(imageData, faceBox)   measures lighting from the pixels (overall brightness, face brightness against the background,
                                          bright spots, left/right balance) and the face's size and position.
     * assessQuality(stats)               turns the measurements into a list of issues, most important first, each with a short fix.
     * createCoach(options)               smooths those results over time so a tip appears only after a problem has lasted a couple of
                                          seconds and disappears only after it has been fixed for a moment (no flicker).

   Everything here is advice. It never counts against the candidate, never blocks them (except a face that cannot be seen at all, decided by the
   caller), and nothing is uploaded: only the numbers computed here, in the browser.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.CameraQuality = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    "use strict";

    /* ---- thresholds (luma is 0-255) ---- */

    // Fairness: a face's own brightness depends on skin tone, so it is never judged on its own. Cameras auto-expose, which makes the whole
    // picture the reliable signal: "dim" and "too dark" need the SCENE to be dim as well, and "backlit" needs a bright background (a window or
    // lamp) far brighter than the face. A darker-skinned candidate in a well-exposed room gets no lighting advice.
    const T = {
        tooDarkFace: 40, tooDarkFrame: 75, dimFace: 60, dimFrame: 100,
        backlitBackground: 170, backlitGap: 70, backlitFaceMax: 130,
        glareFace: 190, glareBackgroundSlack: 10, glareClipped: 0.15, clipLevel: 245,
        sideLightGap: 55,
        tooFarWidth: 0.18, tooCloseWidth: 0.55,
        offCenter: 0.25, edgeMargin: 0.01,
        noFaceDarkFrame: 50,
    };

    const MESSAGES = {
        too_dark: "It is too dark to see your face. Turn on a light or sit facing a window.",
        dim: "The light is a little dim. Move closer to a window or put a lamp in front of you.",
        backlit: "The light is behind you, so your face is in shadow. Turn around so the window or lamp is in front of you.",
        glare: "Your face is too bright. Move back from the direct light or turn it down.",
        side_light: "The light hits only one side of your face. Face the light, or add a lamp on the other side.",
        too_far: "You are too far from the camera. Move a little closer.",
        too_close: "You are too close: your face fills the whole picture. Move back a little.",
        off_center: "Move to the middle of the picture.",
        cut_off: "Part of your head is cut off. Adjust the camera so your whole head is in view.",
        no_face_dark: "I cannot see you and the room is very dark. Turn on a light.",
    };

    // Higher = shown first. Only the most important couple are ever shown at once.
    const SEVERITY = { no_face_dark: 4, too_dark: 4, backlit: 3, glare: 3, cut_off: 2, dim: 2, too_far: 2, too_close: 2, side_light: 1, off_center: 1 };

    /* ---- measuring ---- */

    function luma(data, i) {
        return 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }

    // faceBox: {x0, y0, x1, y1} in 0-1 of the frame, or null when no face was found.
    function analyzeFrame(imageData, faceBox) {

        const { data, width, height } = imageData;
        const px0 = faceBox ? Math.max(0, Math.floor(faceBox.x0 * width)) : 0;
        const px1 = faceBox ? Math.min(width, Math.ceil(faceBox.x1 * width)) : 0;
        const py0 = faceBox ? Math.max(0, Math.floor(faceBox.y0 * height)) : 0;
        const py1 = faceBox ? Math.min(height, Math.ceil(faceBox.y1 * height)) : 0;
        const mid = (px0 + px1) / 2;

        let frameSum = 0, frameCount = 0;
        let faceSum = 0, faceCount = 0, faceClipped = 0, leftSum = 0, leftCount = 0, rightSum = 0, rightCount = 0;
        let bgSum = 0, bgCount = 0;

        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {

                const value = luma(data, (y * width + x) * 4);
                frameSum += value;
                frameCount++;

                const inFace = faceBox && x >= px0 && x < px1 && y >= py0 && y < py1;

                if (inFace) {
                    faceSum += value;
                    faceCount++;
                    if (value > T.clipLevel) faceClipped++;
                    if (x < mid) { leftSum += value; leftCount++; } else { rightSum += value; rightCount++; }
                } else {
                    bgSum += value;
                    bgCount++;
                }
            }
        }

        const faceWidth = faceBox ? faceBox.x1 - faceBox.x0 : null;

        return {
            frameLuma: frameCount ? frameSum / frameCount : 0,
            faceFound: Boolean(faceBox && faceCount),
            faceLuma: faceCount ? faceSum / faceCount : null,
            bgLuma: bgCount ? bgSum / bgCount : null,
            faceClippedFraction: faceCount ? faceClipped / faceCount : 0,
            sideDifference: leftCount && rightCount ? Math.abs(leftSum / leftCount - rightSum / rightCount) : 0,
            faceWidth: faceWidth,
            faceCenterX: faceBox ? (faceBox.x0 + faceBox.x1) / 2 : null,
            touchesEdge: faceBox ? (faceBox.x0 < T.edgeMargin || faceBox.y0 < T.edgeMargin || faceBox.x1 > 1 - T.edgeMargin || faceBox.y1 > 1 - T.edgeMargin) : false,
        };
    }

    /* ---- judging ---- */

    function assessQuality(stats) {

        const codes = [];

        if (!stats.faceFound) {

            if (stats.frameLuma < T.noFaceDarkFrame) codes.push("no_face_dark");

        } else {

            const face = stats.faceLuma;

            if (face < T.tooDarkFace && stats.frameLuma < T.tooDarkFrame) codes.push("too_dark");
            else if (face < T.dimFace && stats.frameLuma < T.dimFrame) codes.push("dim");

            if (stats.bgLuma !== null && stats.bgLuma > T.backlitBackground && stats.bgLuma - face > T.backlitGap && face < T.backlitFaceMax && !codes.includes("too_dark")) codes.push("backlit");

            // a blown-out face: very bright AND at least as bright as its surroundings (a light face in a bright room is just a bright room)
            const background = stats.bgLuma === null ? 0 : stats.bgLuma;
            if ((face > T.glareFace && face >= background - T.glareBackgroundSlack) || stats.faceClippedFraction > T.glareClipped) codes.push("glare");

            if (stats.sideDifference > T.sideLightGap && face >= T.tooDarkFace && face <= 215) codes.push("side_light");

            // a face that fills the picture is also cut off at the edges: "move back" is the more useful advice than "adjust the camera"
            if (stats.faceWidth > T.tooCloseWidth) codes.push("too_close");
            else if (stats.touchesEdge) codes.push("cut_off");
            else if (stats.faceWidth < T.tooFarWidth) codes.push("too_far");

            if (!stats.touchesEdge && stats.faceWidth <= T.tooCloseWidth && Math.abs(stats.faceCenterX - 0.5) > T.offCenter) codes.push("off_center");
        }

        codes.sort(function (a, b) { return SEVERITY[b] - SEVERITY[a]; });

        return { ok: codes.length === 0, issues: codes.map(function (code) { return { code: code, severity: SEVERITY[code], message: MESSAGES[code] }; }) };
    }

    /* ---- steadying the advice over time ---- */

    // feed(result) once per sample (every ~250 ms). onChange(issues) fires only when the SET of shown tips changes: after a problem has lasted
    // `appearAfter` samples, and after it has been gone for `clearAfter` samples. At most `maxShown` tips at once.
    function createCoach(options) {

        const opts = options || {};
        const appearAfter = opts.appearAfter || 8;
        const clearAfter = opts.clearAfter || 8;
        const maxShown = opts.maxShown || 2;
        const onChange = opts.onChange || function () {};

        const seen = {};          // code -> consecutive samples present
        const gone = {};          // code -> consecutive samples absent (while shown)
        let shown = [];

        function feed(result) {

            const present = new Set(result.issues.map(function (i) { return i.code; }));
            const byCode = {};
            result.issues.forEach(function (i) { byCode[i.code] = i; });

            Object.keys(SEVERITY).forEach(function (code) {
                if (present.has(code)) { seen[code] = (seen[code] || 0) + 1; gone[code] = 0; }
                else { seen[code] = 0; gone[code] = (gone[code] || 0) + 1; }
            });

            const active = Object.keys(SEVERITY).filter(function (code) {
                const isShown = shown.some(function (s) { return s.code === code; });
                return isShown ? gone[code] < clearAfter : seen[code] >= appearAfter;
            });

            active.sort(function (a, b) { return SEVERITY[b] - SEVERITY[a]; });

            const next = active.slice(0, maxShown).map(function (code) { return { code: code, severity: SEVERITY[code], message: MESSAGES[code] }; });

            if (next.map(function (i) { return i.code; }).join() !== shown.map(function (i) { return i.code; }).join()) {
                shown = next;
                onChange(shown.slice());
            }
        }

        return { feed: feed, current: function () { return shown.slice(); } };
    }

    /* ---- showing the advice ---- */

    // A small tip box. options.anchor: an element to put the box after (inline), or omit it for a box fixed at the bottom-left of the screen.
    // update(issues) shows the tips (or hides the box when there are none); `goodText`, when given, is shown instead of hiding once a problem was fixed.
    function createTip(options) {

        const opts = options || {};
        const doc = opts.document || (typeof document !== "undefined" ? document : null);

        if (!doc) return { update: function () {}, destroy: function () {} };

        const box = doc.createElement("div");
        box.className = "camera-tip";
        box.setAttribute("role", "status");
        box.setAttribute("aria-live", "polite");
        box.style.cssText = (opts.anchor ? "margin-top:8px;" : "position:fixed;left:16px;bottom:16px;max-width:340px;z-index:2147483000;") +
            "display:none;padding:10px 14px;border-radius:12px;font:13px/1.4 system-ui,Segoe UI,Arial,sans-serif;text-align:left;" +
            "background:#fffbeb;color:#78350f;border:1px solid #fcd34d;box-shadow:0 4px 14px rgba(0,0,0,.12);";

        if (opts.anchor && opts.anchor.insertAdjacentElement) opts.anchor.insertAdjacentElement("afterend", box);
        else doc.body.appendChild(box);

        let wasShowing = false;

        function update(issues) {

            if (issues && issues.length) {
                wasShowing = true;
                box.style.background = "#fffbeb"; box.style.color = "#78350f"; box.style.borderColor = "#fcd34d";
                box.textContent = "\uD83D\uDCA1 " + issues.map(function (i) { return i.message; }).join(" ");
                box.style.display = "block";
            } else if (opts.goodText && wasShowing) {
                box.style.background = "#ecfdf5"; box.style.color = "#065f46"; box.style.borderColor = "#6ee7b7";
                box.textContent = "\u2713 " + opts.goodText;
                box.style.display = "block";
                wasShowing = false;
                setTimeout(function () { box.style.display = "none"; }, 4000);
            } else {
                box.style.display = "none";
            }
        }

        return { update: update, destroy: function () { if (box.parentNode) box.parentNode.removeChild(box); } };
    }

    return { analyzeFrame: analyzeFrame, assessQuality: assessQuality, createCoach: createCoach, createTip: createTip, MESSAGES: MESSAGES, SEVERITY: SEVERITY, THRESHOLDS: T };
});

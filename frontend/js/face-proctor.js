/* =========================================================
   JOBPILOT AI - CAMERA PROCTORING (face presence, several people, looking away, covered camera)

   Why this exists: assessment-integrity.js relied on the browser's native FaceDetector (Shape Detection API),
   which Chrome ships switched off. On every real run the panel said "face check unavailable", so face_missing /
   multiple_faces_detected could never fire (no such event exists in the whole database). This module supplies the
   detector: MediaPipe Face Landmarker (WASM, runs entirely in the browser). One shared module for all assessment
   stages (aptitude, programming, AI interview) -- it is started by IntegrityMode, on the SAME camera stream.

   Privacy: frames are read from the existing <video> into memory for inference and thrown away. Nothing is stored or
   uploaded, no face recognition/identity/embedding is computed -- only "how many faces", "roughly where the head points"
   and "how bright is the picture".

   Signals (all debounced, one event per episode):
     face_missing              no face for >= 4 s
     multiple_faces_detected   two or more faces for >= 2.5 s
     looking_away              head turned/tilted away from the candidate's own calibrated pose for >= 10 s
     gaze_away                 eyes off the screen for >= 6 s while the head still faces it (flag)
                               (a flag for the recruiter, not a violation on its own -- a normal
                               glance at a second monitor or down to think is not cheating)
     gaze_away_sustained       eyes off the screen for >= 20 s continuously (a REAL violation --
                               this is no longer a glance, counted toward the 3-warning limit)
     camera_covered            picture almost black for >= 3 s (lens covered / camera blocked)
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.FaceProctor = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    const VERSION = "0.10.14";

    // The face model and its runtime are served from OUR OWN server (frontend/vendor/mediapipe, see vendor/README.md), so proctoring
    // does not depend on a candidate's network reaching a third-party CDN. The internet copies are only a fallback.
    const LOCAL_BASE = (typeof document !== "undefined" && document.baseURI) ? new URL("vendor/mediapipe/", document.baseURI).href : "vendor/mediapipe/";

    const SOURCES = [
        { name: "local", bundle: LOCAL_BASE + "vision_bundle.js", wasm: LOCAL_BASE + "wasm", model: LOCAL_BASE + "face_landmarker.task" },
        {
            name: "cdn",
            bundle: "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@" + VERSION + "/vision_bundle.mjs",
            wasm: "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@" + VERSION + "/wasm",
            model: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        },
    ];

    const DEFAULTS = Object.freeze({
        missingMs: 4000,          // no face this long -> face_missing
        multipleMs: 2500,         // 2+ faces this long -> multiple_faces_detected
        awayMs: 10000,            // head off its own baseline this long (continuously) -> looking_away
        awayCooldownMs: 20000,    // one looking_away per this window
        darkMs: 3000,             // black picture this long -> camera_covered
        darkLuma: 22,             // mean brightness (0..255) below which the picture counts as black
        yawLimit: 0.22,           // |nose offset| / face width away from the candidate's baseline
        pitchLimit: 0.24,         // same, vertical (looking down at a phone / notes)
        calibrationSamples: 10,   // single-face frames used to learn the candidate's normal pose
        gazeMs: 6000,             // eyes off the screen this long (head still facing it) -> gaze_away
        gazeXLimit: 0.5,          // sideways eye movement away from the candidate's own baseline (blendshape units 0..1)
        gazeYLimit: 0.55,         // vertical (eyes down at a phone / lap)
        gazeCooldownMs: 30000,
        gazeSustainedMs: 20000,   // eyes off the screen THIS long, continuously -> gaze_away_sustained (a real violation)
        gazeSustainedCooldownMs: 45000,
    });

    function median(values) {
        const sorted = values.slice().sort(function (a, b) { return a - b; });
        return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
    }

    /* ---- pure decision engine (no camera, no clock, no model): unit-tested with synthetic timelines ---- */
    function createEngine(overrides) {

        const cfg = Object.assign({}, DEFAULTS, overrides || {});

        const state = {
            missingSince: null, multipleSince: null, awaySince: null, darkSince: null,
            missingFired: false, multipleFired: false, awayFired: false, darkFired: false,
            lastAwayAt: -Infinity, gazeSince: null, gazeFired: false, lastGazeAt: -Infinity,
            gazeSustainedFired: false, lastGazeSustainedAt: -Infinity,
            calibGx: [], calibGy: [], baseGx: null, baseGy: null,
            calibYaw: [], calibPitch: [], baseYaw: null, basePitch: null,
            faceReported: false,
        };

        // sample: { faces: number|null (null = detector unavailable), yaw, pitch, luma }
        function step(sample, now) {

            const events = [];

            /* ---- covered camera (works even without the face model) ---- */
            if (typeof sample.luma === "number" && sample.luma < cfg.darkLuma) {
                if (state.darkSince === null) state.darkSince = now;
                if (!state.darkFired && now - state.darkSince >= cfg.darkMs) {
                    state.darkFired = true;
                    events.push("camera_covered");
                }
            } else {
                state.darkSince = null;
                state.darkFired = false;
            }

            if (sample.faces === null || sample.faces === undefined) {
                return events;
            }

            const dark = state.darkSince !== null;

            /* ---- nobody in front of the camera ---- */
            if (sample.faces === 0 && !dark) {
                if (state.missingSince === null) state.missingSince = now;
                if (!state.missingFired && now - state.missingSince >= cfg.missingMs) {
                    state.missingFired = true;
                    state.faceReported = false;
                    events.push("face_missing");
                }
            } else {
                state.missingSince = null;
                state.missingFired = false;
            }

            /* ---- somebody else in the picture ---- */
            if (sample.faces >= 2) {
                if (state.multipleSince === null) state.multipleSince = now;
                if (!state.multipleFired && now - state.multipleSince >= cfg.multipleMs) {
                    state.multipleFired = true;
                    state.faceReported = false;
                    events.push("multiple_faces_detected");
                }
            } else {
                state.multipleSince = null;
                state.multipleFired = false;
            }

            /* ---- exactly one face: learn the normal pose, then watch for the head turning away ---- */
            if (sample.faces === 1 && typeof sample.yaw === "number" && typeof sample.pitch === "number") {

                if (!state.faceReported) {
                    state.faceReported = true;
                    events.push("face_present");
                }

                if (state.baseYaw === null) {
                    state.calibYaw.push(sample.yaw);
                    state.calibPitch.push(sample.pitch);
                    state.calibGx.push(typeof sample.gazeX === "number" ? sample.gazeX : 0);
                    state.calibGy.push(typeof sample.gazeY === "number" ? sample.gazeY : 0);
                    if (state.calibYaw.length >= cfg.calibrationSamples) {
                        state.baseYaw = median(state.calibYaw);
                        state.basePitch = median(state.calibPitch);
                        state.baseGx = median(state.calibGx);
                        state.baseGy = median(state.calibGy);
                    }
                } else {

                    /* eyes off the screen while the head still faces it (a second monitor, a phone in the lap, notes) */
                    if (typeof sample.gazeX === "number" && typeof sample.gazeY === "number") {
                        const eyesAway = Math.abs(sample.gazeX - state.baseGx) > cfg.gazeXLimit ||
                                         Math.abs(sample.gazeY - state.baseGy) > cfg.gazeYLimit;
                        if (eyesAway) {
                            if (state.gazeSince === null) state.gazeSince = now;
                            if (!state.gazeFired && now - state.gazeSince >= cfg.gazeMs && now - state.lastGazeAt >= cfg.gazeCooldownMs) {
                                state.gazeFired = true;
                                state.lastGazeAt = now;
                                events.push("gaze_away");
                            }
                            // Same continuous away-streak, a much longer bar: this is no longer a
                            // glance, so (unlike gaze_away above) this one is a real counted violation.
                            if (!state.gazeSustainedFired && now - state.gazeSince >= cfg.gazeSustainedMs &&
                                now - state.lastGazeSustainedAt >= cfg.gazeSustainedCooldownMs) {
                                state.gazeSustainedFired = true;
                                state.lastGazeSustainedAt = now;
                                events.push("gaze_away_sustained");
                            }
                        } else {
                            state.gazeSince = null;
                            state.gazeFired = false;
                            state.gazeSustainedFired = false;
                        }
                    }

                    const away = Math.abs(sample.yaw - state.baseYaw) > cfg.yawLimit ||
                                 Math.abs(sample.pitch - state.basePitch) > cfg.pitchLimit;
                    if (away) {
                        if (state.awaySince === null) state.awaySince = now;
                        if (!state.awayFired && now - state.awaySince >= cfg.awayMs && now - state.lastAwayAt >= cfg.awayCooldownMs) {
                            state.awayFired = true;
                            state.lastAwayAt = now;
                            events.push("looking_away");
                        }
                    } else {
                        state.awaySince = null;
                        state.awayFired = false;
                    }
                }

            } else if (sample.faces !== 1) {
                state.awaySince = null;
                state.awayFired = false;
                state.gazeSince = null;
                state.gazeFired = false;
                state.gazeSustainedFired = false;
            }

            return events;
        }

        return { step: step, state: state, config: cfg };
    }

    /* ---- head pose from Face Landmarker landmarks (normalised image coordinates) ---- */
    function headPose(landmarks) {

        const nose = landmarks[1];
        const left = landmarks[234];
        const right = landmarks[454];
        const top = landmarks[10];
        const chin = landmarks[152];

        if (!nose || !left || !right || !top || !chin) return null;

        const width = Math.abs(right.x - left.x) || 1e-6;
        const height = Math.abs(chin.y - top.y) || 1e-6;

        return {
            yaw: (nose.x - (left.x + right.x) / 2) / width,
            pitch: (nose.y - (top.y + chin.y) / 2) / height,
        };
    }

    /* ---- eye direction from the Face Landmarker blendshapes (0..1 scores) ---- */
    function gazeFromBlendshapes(categories) {

        const score = {};
        (categories || []).forEach(function (c) { score[c.categoryName] = c.score; });

        const g = function (name) { return score[name] || 0; };

        // looking to the person's left = left eye OUT + right eye IN; the sign only matters relative to the baseline
        const x = ((g("eyeLookOutLeft") + g("eyeLookInRight")) - (g("eyeLookInLeft") + g("eyeLookOutRight"))) / 2;
        const y = ((g("eyeLookUpLeft") + g("eyeLookUpRight")) - (g("eyeLookDownLeft") + g("eyeLookDownRight"))) / 2;

        return { gazeX: x, gazeY: y };
    }

    /* ---- browser runtime ---- */
    let landmarker = null;
    let loading = null;

    function isSupported() {
        return typeof window !== "undefined" && typeof document !== "undefined" && !!navigator.mediaDevices;
    }

    function loadLandmarker() {

        if (landmarker) return Promise.resolve(landmarker);
        if (loading) return loading;

        function fromSource(source) {

            return import(source.bundle).then(function (vision) {
                return vision.FilesetResolver.forVisionTasks(source.wasm).then(function (fileset) {

                    const options = function (delegate) {
                        return {
                            baseOptions: { modelAssetPath: source.model, delegate: delegate },
                            runningMode: "VIDEO",
                            numFaces: 3,
                            outputFaceBlendshapes: true,
                        };
                    };

                    return vision.FaceLandmarker.createFromOptions(fileset, options("GPU")).catch(function () {
                        return vision.FaceLandmarker.createFromOptions(fileset, options("CPU"));
                    });
                });
            });
        }

        // our own copy first; if ANY step of it fails, the whole internet set is tried instead
        loading = fromSource(SOURCES[0]).catch(function (localError) {
            console.warn("FaceProctor: the local face model could not be loaded, trying the internet copy.", localError);
            return fromSource(SOURCES[1]);
        }).then(function (instance) {
            landmarker = instance;
            return instance;
        }).catch(function (error) {
            loading = null;
            console.warn("FaceProctor: face model unavailable, only the covered-camera check runs.", error);
            return null;
        });

        return loading;
    }

    // One camera frame, small, as pixels (the same frame feeds the covered-camera check and the lighting / framing coach).
    function readFrame(video, canvas) {

        try {
            const context = canvas.getContext("2d", { willReadFrequently: true });
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            return context.getImageData(0, 0, canvas.width, canvas.height);
        } catch (error) {
            return null;
        }
    }

    function averageLuma(imageData) {

        const data = imageData.data;
        let sum = 0;
        for (let i = 0; i < data.length; i += 4) sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        return sum / (data.length / 4);
    }

    // The face's bounding box (0-1 of the frame) from its landmarks.
    function faceBoxOf(landmarks) {

        let x0 = 1, y0 = 1, x1 = 0, y1 = 0;

        landmarks.forEach(function (p) {
            if (p.x < x0) x0 = p.x;
            if (p.x > x1) x1 = p.x;
            if (p.y < y0) y0 = p.y;
            if (p.y > y1) y1 = p.y;
        });

        return { x0: Math.max(0, x0), y0: Math.max(0, y0), x1: Math.min(1, x1), y1: Math.min(1, y1) };
    }

    function meanLuma(video, canvas) {

        try {
            const context = canvas.getContext("2d", { willReadFrequently: true });
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
            let sum = 0;
            for (let i = 0; i < data.length; i += 4) sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
            return sum / (data.length / 4);
        } catch (error) {
            return null;
        }
    }

    let timerId = null;
    let running = false;

    function start(videoEl, handlers) {

        stop();

        if (!isSupported() || !videoEl) return false;

        const onEvent = (handlers && handlers.onEvent) || function () {};
        const onStatus = (handlers && handlers.onStatus) || function () {};
        const engine = createEngine(handlers && handlers.config);
        const canvas = document.createElement("canvas");
        canvas.width = 64;
        canvas.height = 48;
        let lastVideoTime = -1;
        let busy = false;

        running = true;
        onStatus("loading");

        loadLandmarker().then(function (model) {
            if (running) onStatus(model ? "ready" : "limited");
        });

        timerId = setInterval(function () {

            if (!running || busy || videoEl.readyState < 2) return;
            busy = true;

            try {

                const now = performance.now();
                const frame = readFrame(videoEl, canvas);
                const sample = { faces: null, luma: frame ? averageLuma(frame) : null };
                let faceBox = null;

                if (landmarker && videoEl.currentTime !== lastVideoTime) {
                    lastVideoTime = videoEl.currentTime;
                    const result = landmarker.detectForVideo(videoEl, now);
                    const faces = (result && result.faceLandmarks) || [];
                    sample.faces = faces.length;
                    if (faces.length === 1) {
                        faceBox = faceBoxOf(faces[0]);
                        const pose = headPose(faces[0]);
                        if (pose) { sample.yaw = pose.yaw; sample.pitch = pose.pitch; }
                        const shapes = result.faceBlendshapes && result.faceBlendshapes[0];
                        if (shapes) Object.assign(sample, gazeFromBlendshapes(shapes.categories));
                    }
                }

                // lighting and framing advice for the candidate (see camera-quality.js); never counted against anyone
                if (frame && sample.faces !== null && sample.faces <= 1 && typeof window !== "undefined" && window.CameraQuality) {
                    sample.quality = window.CameraQuality.assessQuality(window.CameraQuality.analyzeFrame(frame, faceBox));
                }

                if (handlers && handlers.onSample) handlers.onSample(sample);
                engine.step(sample, now).forEach(function (type) { onEvent(type, sample); });

            } catch (error) {
                console.warn("FaceProctor frame error:", error);
            } finally {
                busy = false;
            }

        }, 250);

        return true;
    }

    function stop() {
        running = false;
        if (timerId) {
            clearInterval(timerId);
            timerId = null;
        }
    }

    return { start: start, stop: stop, isSupported: isSupported, createEngine: createEngine, headPose: headPose, gazeFromBlendshapes: gazeFromBlendshapes, DEFAULTS: DEFAULTS };
});

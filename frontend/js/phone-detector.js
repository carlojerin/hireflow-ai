/* =========================================================
   JOBPILOT AI - VISIBLE MOBILE PHONE DETECTION (Task 5 / 5B)

   ONE shared module reused by all four assessment stages
   (Self Intro / Aptitude / Programming / AI Interview) instead
   of four separate implementations — mirrors the exact same
   "one shared module, thin per-stage wiring" pattern already
   used by assessment-video-recorder.js (Task 4).

   Deliberately does NOT acquire its own camera stream — the
   caller passes in an existing, already-playing <video> element
   bound to the SAME MediaStream already used by camera
   monitoring / face detection / MediaRecorder (see
   IntegrityMode.getCameraVideoElement() in assessment-
   integrity.js, or assessment.js's own Self Intro camera
   preview). This module never calls getUserMedia() itself.

   ---------------------------------------------------------
   TASK 5B UPGRADE (temporal detection + tracking)
   ---------------------------------------------------------
   Task 5's first version ran TensorFlow.js + COCO-SSD and fired
   on any 3 consecutive same-class frames. That is frame-level
   confirmation only — it has no concept of "this is the SAME
   physical phone across time," so it cannot reason about
   persistence, occlusion, or reappearance. Task 5B replaces the
   detector AND adds a proper temporal layer:

       DETECTOR (YOLOv8n via ONNX Runtime Web)
             -> "what object is in this frame?"
       TRACKER (lightweight IoU-based, per-page-load)
             -> "is this the same physical object as last frame?"
       TEMPORAL DECISION ENGINE (rolling confidence window +
       minimum-persistence + one-confirmation-per-episode)
             -> "is this object persistent enough to count?"

   DETECTION TECHNOLOGY (see Task 5B final report for full
   rationale/measurements): YOLOv8n, exported to ONNX (standard
   Ultralytics 640x640 export, input "images" [1,3,640,640],
   output "output0" [1,84,8400] = 4 bbox coords + 80 COCO class
   scores per anchor), run entirely in the browser via the
   official ONNX Runtime Web runtime. COCO class index 67 is
   literally "cell phone" — this module decodes and tracks
   exactly that one class and nothing else. It cannot detect a
   hidden phone, a phone outside the camera's view, or any
   device/class the model was not trained on.

   Execution provider: WebGPU is tried first; if creating a
   WebGPU session fails for any reason (unsupported GPU/driver/
   browser), this module falls back to the WASM backend
   automatically. If neither backend can be created, the
   detector simply does not start — camera monitoring, face
   detection, and MediaRecorder are completely unaffected either
   way (see Step 16).

   PRIVACY: every video frame is read directly from the existing
   <video> element into an in-memory canvas/tensor for inference
   and discarded immediately — no frame, screenshot, or image is
   ever saved, uploaded, or persisted anywhere. No face
   recognition, embeddings, or biometric processing of any kind
   happens in this module.

   CANDIDATE WARNING UI (Step 8/12): this module owns and renders
   its OWN small, self-contained warning banner (created/removed
   the same way assessment-integrity.js owns its camera preview
   panel), so it never collides with each stage's existing
   cumulative "Assessment Warning / Final Warning" integrity
   banner. Exactly one warning per confirmed phone EPISODE (a
   continuous tracked appearance), never per frame.

   USAGE:

       PhoneDetector.start(videoEl, {
           onConfirmedDetection: function (detection, meta) {
               // detection: { object, confidence, track_id,
               //              duration_ms, detections, detector,
               //              tracker } - see Step 13 metadata shape
               // meta.withinCooldown: true if this episode's event
               // should NOT be re-reported to the backend (the
               // warning banner is still shown either way).
           },
       });

       PhoneDetector.stop();

   Also exposed (for the automated tests in Step 21 - a pure,
   dependency-free temporal decision engine with no camera/model
   involved at all):

       PhoneDetector.createTracker()
       PhoneDetector.computeIoU(boxA, boxB)
       PhoneDetector.getDiagnostics()   // dev-only perf counters,
                                        // never sent to the backend
   ========================================================= */

(function () {

    if (window.PhoneDetector) {
        // Defensive against accidental double-include, same
        // pattern as session-guard.js / assessment-video-recorder.js.
        return;
    }

    /* =====================================================
       CONSTANTS — every "magic number" this module uses is
       named and documented here, nowhere else. These are
       reasoned defaults (documented rationale below), tuned
       further against the real inference-latency measurements
       in Task 5B's final report rather than blind guesses.
       ===================================================== */

    // How often we sample a frame for inference. YOLOv8n
    // inference is the dominant cost per cycle (see Step 23's
    // measured latency in the final report) - this deliberately
    // does NOT run on every animation frame (Step 4/18).
    const PHONE_DETECTION_INTERVAL_MS = 250;   // (was 800) a phone is caught within about a second; the 'sampling' guard below stops inference piling up

    // Per-anchor YOLO class-score threshold for "cell phone" to
    // even be considered a candidate detection at all.
    const PHONE_CONFIDENCE_THRESHOLD = 0.3;

    // NMS IoU threshold for collapsing overlapping raw detections
    // of the SAME phone into one box before tracking.
    const PHONE_NMS_IOU_THRESHOLD = 0.45;

    // Rolling per-track window of recent qualifying-or-not
    // detection cycles (Step 7) - "is this a persistent object or
    // a single-frame false positive?"
    const PHONE_CONFIRMATION_WINDOW = 6;

    // Of that rolling window, how many cycles must have scored
    // >= PHONE_CONFIDENCE_THRESHOLD before the track is eligible
    // to confirm (Step 6/7). This is what rejects an unstable
    // "0.93, 0.12, 0.08, 0.04" pattern - only 1 of 4 would qualify.
    const PHONE_MIN_DETECTIONS = 2;

    // A track must have existed for at least this long before it
    // can confirm, regardless of how many cycles matched (Step 6)
    // - guards against a burst of fast, spurious re-detections in
    // a very short window.
    const PHONE_MIN_PERSISTENCE_MS = 300;

    // A clear sighting (score at or above this) confirms at once, on the FIRST frame: holding a phone up to the camera must be
    // caught in about one second, not after several slow cycles. Weaker sightings still need PHONE_MIN_DETECTIONS cycles.
    const PHONE_STRONG_CONFIDENCE = 0.55;

    // Minimum IoU between an existing track's last bbox and a new
    // detection for them to be considered the SAME object (Step
    // 8) - below this, a detection starts a NEW track instead of
    // incorrectly merging with a spatially distant one.
    const PHONE_TRACK_IOU_THRESHOLD = 0.3;

    // How many CONSECUTIVE missed cycles a track tolerates before
    // being closed (Step 9) - a phone briefly occluded by a hand
    // for one or two cycles reconnects to the SAME track instead
    // of resetting confirmation progress.
    const PHONE_TRACK_LOST_GRACE_CYCLES = 3;

    // Once a track's episode has been reported to the backend,
    // how long before a genuinely NEW episode (a new track,
    // confirmed after this one closed) is allowed to be reported
    // again (Step 5/9/11) - stops one held-up phone, or a phone
    // put down and picked back up quickly, from creating hundreds
    // of events. The candidate-facing warning still shows for
    // every confirmed episode regardless of this cooldown; only
    // the backend integrity-event report is throttled by it.
    const PHONE_EVENT_COOLDOWN_MS = 30000;

    // How long the candidate-facing warning banner stays visible
    // after a confirmed detection before auto-hiding, if the
    // phone/track has not already been lost first (Step 8/12).
    const PHONE_WARNING_AUTO_HIDE_MS = 6000;

    // The exact, single COCO class this module looks for, and
    // its index in the 80-class label order YOLOv8 was trained on
    // (verified directly against the model's own label list - see
    // Task 5B's final report). Nothing else is ever treated as a
    // "phone."
    const PHONE_DETECTION_TARGET_CLASS = "cell phone";
    const PHONE_CLASS_INDEX = 67;
    const COCO_NUM_CLASSES = 80;

    // YOLOv8n's fixed export input size (verified against the
    // model file itself - input "images": [1, 3, 640, 640]).
    const MODEL_INPUT_SIZE = 640;
    const MODEL_NUM_ANCHORS = 8400;

    // ONNX Runtime Web - the official browser runtime (Step 2).
    // "ort.all.min.js" bundles the wasm AND webgpu backends in
    // one script so this module can attempt WebGPU first and
    // fall back to WASM at session-creation time without a
    // second script load (Step 16).
    //
    // The runtime, its WebAssembly files and the model are served from OUR OWN server (frontend/vendor, see vendor/README.md): proctoring
    // must not depend on a candidate's network reaching a third-party CDN. The internet copies below are only a fallback, and ONE complete
    // set is chosen (never a mix of local script + CDN wasm). Chosen the first time the model loads (see chooseSources()).
    const VENDOR_BASE = (typeof document !== "undefined" && document.baseURI) ? new URL("vendor/", document.baseURI).href : "vendor/";

    const SOURCES = {
        local: {
            script: VENDOR_BASE + "onnx/ort.all.min.js",
            wasm: VENDOR_BASE + "onnx/",
            model: VENDOR_BASE + "models/yolov8n.onnx",
        },
        cdn: {
            script: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/ort.all.min.js",
            wasm: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/",
            model: "https://raw.githubusercontent.com/Hyuto/yolov8-onnxruntime-web/master/public/model/yolov8n.onnx",
        },
    };

    let ORT_SCRIPT_URL = SOURCES.local.script;
    let ORT_WASM_BASE_URL = SOURCES.local.wasm;

    // YOLOv8n, standard Ultralytics ONNX export (opset 12,
    // 640x640, no embedded NMS) - see Task 5B's final report for
    // exactly how this specific hosted file was verified
    // (reachability, byte size, and its input/output tensor
    // shapes independently parsed from the ONNX graph itself)
    // before being chosen.
    let YOLO_MODEL_URL = SOURCES.local.model;

    // Our own copy is used when it is really there (the model file is ~12 MB, so a missing file or an error page does not count);
    // otherwise the whole internet set.
    let sourcesChosen = null;

    function chooseSources() {

        if (!sourcesChosen) {

            sourcesChosen = fetch(SOURCES.local.model, { method: "HEAD" })
                .then(function (response) {
                    return response.ok && Number(response.headers.get("content-length")) > 1000000 ? SOURCES.local : SOURCES.cdn;
                })
                .catch(function () { return SOURCES.cdn; })
                .then(function (chosen) {
                    ORT_SCRIPT_URL = chosen.script;
                    ORT_WASM_BASE_URL = chosen.wasm;
                    YOLO_MODEL_URL = chosen.model;
                    return chosen;
                });
        }

        return sourcesChosen;
    }

    // Step 16 - maximum time given to a WebGPU session-creation
    // attempt before this module gives up on it and falls back to
    // WASM. Guards against the observed real-world failure mode
    // where navigator.gpu exists but adapter/device negotiation
    // hangs rather than rejecting.
    const WEBGPU_PROBE_TIMEOUT_MS = 6000;


    /* =====================================================
       LAZY SCRIPT / MODEL LOADING (Step 17 - loaded once per
       page lifecycle, cached at the module level; never
       re-fetched just because start()/stop() is called again on
       the same page).
       ===================================================== */

    let modelLoadPromise = null;
    let ortSession = null;
    let activeBackend = null; // "webgpu" | "wasm" | null

    function loadScriptOnce(url) {

        return new Promise(function (resolve, reject) {

            const existing = document.querySelector(
                `script[src="${url}"]`
            );

            if (existing) {
                resolve();
                return;
            }

            const script = document.createElement("script");
            script.src = url;
            script.async = true;

            script.addEventListener("load", function () {
                resolve();
            });

            script.addEventListener("error", function () {
                reject(new Error(`Failed to load script: ${url}`));
            });

            document.head.appendChild(script);
        });
    }

    // Step 16 - try WebGPU, fall back to WASM. Never throws; the
    // caller (loadModel) decides what "both failed" means.
    function withTimeout(promise, ms, timeoutMessage) {

        return new Promise(function (resolve, reject) {

            const timeoutId = setTimeout(function () {
                reject(new Error(timeoutMessage));
            }, ms);

            promise.then(
                function (value) {
                    clearTimeout(timeoutId);
                    resolve(value);
                },
                function (error) {
                    clearTimeout(timeoutId);
                    reject(error);
                }
            );
        });
    }

    // Step 16 - a lightweight, cheap probe of REAL WebGPU
    // availability via navigator.gpu.requestAdapter() directly,
    // BEFORE ever calling ort.InferenceSession.create() with the
    // webgpu execution provider. This matters: some browsers/
    // sandboxes expose navigator.gpu (so WebGPU LOOKS available)
    // but adapter/device negotiation can hang rather than reject
    // (observed directly while testing this module - see Task 5B's
    // final report). Racing a timeout around the FULL session-
    // creation call instead of this probe was tried first and
    // rejected: onnxruntime-web's webgpu attempt keeps running
    // after we stop waiting on it, and its abandoned internal wasm
    // initialization then collides with the later explicit wasm
    // session creation ("multiple calls to initWasm() detected").
    // Probing the adapter alone and never starting the heavier
    // ort webgpu path at all if it fails avoids that collision
    // entirely - only ONE ort.InferenceSession.create() call is
    // ever made, whichever backend is actually going to be used.
    async function probeWebGpuAvailable() {

        if (
            typeof navigator === "undefined" ||
            !navigator.gpu ||
            typeof navigator.gpu.requestAdapter !== "function"
        ) {
            return false;
        }

        try {

            const adapter = await withTimeout(
                navigator.gpu.requestAdapter(),
                WEBGPU_PROBE_TIMEOUT_MS,
                "WebGPU adapter request timed out"
            );

            return Boolean(adapter);

        } catch (probeError) {

            console.warn(
                "PhoneDetector: WebGPU adapter probe failed or timed " +
                "out, using WASM instead.",
                probeError
            );

            return false;
        }
    }

    async function createSessionWithFallback(ort) {

        ort.env.wasm.wasmPaths = ORT_WASM_BASE_URL;

        const webGpuAvailable = await probeWebGpuAvailable();

        if (webGpuAvailable) {

            try {

                const session = await ort.InferenceSession.create(
                    YOLO_MODEL_URL,
                    { executionProviders: ["webgpu"] }
                );

                activeBackend = "webgpu";
                return session;

            } catch (webgpuError) {

                console.warn(
                    "PhoneDetector: WebGPU session creation failed, " +
                    "falling back to WASM.",
                    webgpuError
                );
            }
        }

        const session = await ort.InferenceSession.create(
            YOLO_MODEL_URL,
            { executionProviders: ["wasm"] }
        );

        activeBackend = "wasm";
        return session;
    }

    function loadModel() {

        if (ortSession) {
            return Promise.resolve(ortSession);
        }

        if (modelLoadPromise) {
            return modelLoadPromise;
        }

        modelLoadPromise = chooseSources()
            .then(function () { return loadScriptOnce(ORT_SCRIPT_URL); })
            .then(function () {

                if (typeof window.ort === "undefined") {
                    throw new Error(
                        "onnxruntime-web failed to initialize after " +
                        "loading."
                    );
                }

                return createSessionWithFallback(window.ort);
            })
            .then(function (session) {
                ortSession = session;
                return session;
            })
            .catch(function (error) {
                // Allow a retry on the next start() call rather than
                // permanently caching a failed load.
                modelLoadPromise = null;
                activeBackend = null;
                throw error;
            });

        return modelLoadPromise;
    }


    /* =====================================================
       PREPROCESSING — letterbox-resize the current video frame
       to the model's fixed 640x640 input, normalized to [0,1],
       laid out CHW (channel-planar) as YOLOv8's ONNX export
       expects. Reuses one offscreen canvas across calls instead
       of allocating a new one every cycle.
       ===================================================== */

    let scratchCanvas = null;
    let scratchCtx = null;

    function preprocessFrame(videoEl) {

        if (!scratchCanvas) {
            scratchCanvas = document.createElement("canvas");
            scratchCanvas.width = MODEL_INPUT_SIZE;
            scratchCanvas.height = MODEL_INPUT_SIZE;
            scratchCtx = scratchCanvas.getContext("2d", {
                willReadFrequently: true,
            });
        }

        const videoWidth = videoEl.videoWidth;
        const videoHeight = videoEl.videoHeight;

        const scale = Math.min(
            MODEL_INPUT_SIZE / videoWidth,
            MODEL_INPUT_SIZE / videoHeight
        );

        const newWidth = Math.round(videoWidth * scale);
        const newHeight = Math.round(videoHeight * scale);
        const offsetX = Math.floor((MODEL_INPUT_SIZE - newWidth) / 2);
        const offsetY = Math.floor((MODEL_INPUT_SIZE - newHeight) / 2);

        scratchCtx.fillStyle = "black";
        scratchCtx.fillRect(0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE);

        scratchCtx.drawImage(
            videoEl,
            0, 0, videoWidth, videoHeight,
            offsetX, offsetY, newWidth, newHeight
        );

        const pixels = scratchCtx.getImageData(
            0, 0, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE
        ).data;

        const area = MODEL_INPUT_SIZE * MODEL_INPUT_SIZE;
        const chwData = new Float32Array(3 * area);

        for (let i = 0; i < area; i++) {

            const pixelOffset = i * 4;

            chwData[i] = pixels[pixelOffset] / 255;               // R plane
            chwData[area + i] = pixels[pixelOffset + 1] / 255;    // G plane
            chwData[2 * area + i] = pixels[pixelOffset + 2] / 255; // B plane
        }

        return { chwData: chwData, scale: scale, offsetX: offsetX, offsetY: offsetY };
    }


    /* =====================================================
       POSTPROCESSING — decode ONLY the "cell phone" class out
       of output0's [1, 84, 8400] tensor, then run a small greedy
       IoU-based NMS. Every other one of the 80 classes is never
       even read - this module has no use for them.
       ===================================================== */

    function computeIoU(boxA, boxB) {

        // Boxes are [x, y, w, h] in shared (e.g. original video
        // pixel) coordinates.
        const ax2 = boxA[0] + boxA[2];
        const ay2 = boxA[1] + boxA[3];
        const bx2 = boxB[0] + boxB[2];
        const by2 = boxB[1] + boxB[3];

        const ix1 = Math.max(boxA[0], boxB[0]);
        const iy1 = Math.max(boxA[1], boxB[1]);
        const ix2 = Math.min(ax2, bx2);
        const iy2 = Math.min(ay2, by2);

        const interWidth = Math.max(0, ix2 - ix1);
        const interHeight = Math.max(0, iy2 - iy1);
        const interArea = interWidth * interHeight;

        const areaA = boxA[2] * boxA[3];
        const areaB = boxB[2] * boxB[3];
        const unionArea = areaA + areaB - interArea;

        return unionArea <= 0 ? 0 : interArea / unionArea;
    }

    function decodePhoneDetections(output0Data, scale, offsetX, offsetY) {

        const results = [];
        const scoreRowOffset =
            (4 + PHONE_CLASS_INDEX) * MODEL_NUM_ANCHORS;

        for (let anchor = 0; anchor < MODEL_NUM_ANCHORS; anchor++) {

            const score = output0Data[scoreRowOffset + anchor];

            if (score < PHONE_CONFIDENCE_THRESHOLD) {
                continue;
            }

            const cx = output0Data[0 * MODEL_NUM_ANCHORS + anchor];
            const cy = output0Data[1 * MODEL_NUM_ANCHORS + anchor];
            const w = output0Data[2 * MODEL_NUM_ANCHORS + anchor];
            const h = output0Data[3 * MODEL_NUM_ANCHORS + anchor];

            // Undo the letterbox (padding + scale) back to
            // original video pixel coordinates.
            const x1 = (cx - w / 2 - offsetX) / scale;
            const y1 = (cy - h / 2 - offsetY) / scale;
            const boxW = w / scale;
            const boxH = h / scale;

            results.push({ bbox: [x1, y1, boxW, boxH], score: score });
        }

        return nonMaxSuppression(results, PHONE_NMS_IOU_THRESHOLD);
    }

    function nonMaxSuppression(boxes, iouThreshold) {

        const remaining = boxes.slice().sort(function (a, b) {
            return b.score - a.score;
        });

        const kept = [];

        while (remaining.length) {

            const best = remaining.shift();
            kept.push(best);

            for (let i = remaining.length - 1; i >= 0; i--) {

                if (computeIoU(best.bbox, remaining[i].bbox) > iouThreshold) {
                    remaining.splice(i, 1);
                }
            }
        }

        return kept;
    }

    async function runInference(videoEl) {

        const session = await loadModel();

        const { chwData, scale, offsetX, offsetY } = preprocessFrame(videoEl);

        const tensor = new window.ort.Tensor(
            "float32",
            chwData,
            [1, 3, MODEL_INPUT_SIZE, MODEL_INPUT_SIZE]
        );

        const results = await session.run({ images: tensor });

        return decodePhoneDetections(
            results.output0.data,
            scale,
            offsetX,
            offsetY
        );
    }


    /* =====================================================
       TRACKER — lightweight, dependency-free IoU-based temporal
       tracker (Step 4/5). A full ByteTrack implementation (Kalman
       motion prediction + a Hungarian assignment solver) is
       significantly heavier than a browser-side foundation
       proctoring feature needs; a greedy IoU association already
       satisfies every behavior this task actually asks for
       (persistent track IDs, occlusion tolerance, no incorrect
       merging across large spatial jumps - see Step 8/9) without
       adding a large new dependency. This can be swapped for a
       fuller ByteTrack later without changing the public API
       below (Step 19/20).

       Pure and dependency-free by design: no camera, no model, no
       DOM - .update(detections, now) can be driven directly and
       deterministically by the automated tests (Step 21) with
       synthetic detection arrays and synthetic timestamps.
       ===================================================== */

    function createTracker() {

        let tracks = [];
        let nextTrackId = 1;

        function update(detections, now) {

            const unmatched = detections.slice();
            const matchedTrackIds = [];

            for (let t = 0; t < tracks.length; t++) {

                const track = tracks[t];

                let bestIndex = -1;
                let bestIoU = 0;

                for (let i = 0; i < unmatched.length; i++) {

                    const iou = computeIoU(track.bbox, unmatched[i].bbox);

                    if (iou > bestIoU) {
                        bestIoU = iou;
                        bestIndex = i;
                    }
                }

                if (bestIndex !== -1 && bestIoU >= PHONE_TRACK_IOU_THRESHOLD) {

                    const detection = unmatched.splice(bestIndex, 1)[0];

                    track.bbox = detection.bbox;
                    track.lastSeenAt = now;
                    track.missedCycles = 0;

                    track.scoreWindow.push(detection.score);

                    if (track.scoreWindow.length > PHONE_CONFIRMATION_WINDOW) {
                        track.scoreWindow.shift();
                    }

                    matchedTrackIds.push(track.id);

                } else {

                    track.missedCycles += 1;
                }
            }

            // Every remaining, unmatched detection starts a NEW
            // track - never silently merged into a spatially
            // distant existing one (Step 8).
            for (let i = 0; i < unmatched.length; i++) {

                tracks.push({
                    id: nextTrackId++,
                    bbox: unmatched[i].bbox,
                    firstSeenAt: now,
                    lastSeenAt: now,
                    missedCycles: 0,
                    scoreWindow: [unmatched[i].score],
                    confirmedThisEpisode: false,
                });
            }

            // Close tracks that have exceeded the occlusion grace
            // period (Step 9) - a track surviving a brief missed
            // detection is NOT closed here.
            const stillActive = [];
            const closedTrackIds = [];

            for (let t = 0; t < tracks.length; t++) {

                if (tracks[t].missedCycles > PHONE_TRACK_LOST_GRACE_CYCLES) {
                    closedTrackIds.push(tracks[t].id);
                } else {
                    stillActive.push(tracks[t]);
                }
            }

            tracks = stillActive;

            // Temporal confirmation (Step 6/7) - never re-confirm
            // a track that already produced its one confirmation
            // this episode (Step 5/11).
            const newlyConfirmed = [];

            for (let t = 0; t < tracks.length; t++) {

                const track = tracks[t];

                if (track.confirmedThisEpisode) {
                    continue;
                }

                const qualifyingCount = track.scoreWindow.filter(
                    function (score) {
                        return score >= PHONE_CONFIDENCE_THRESHOLD;
                    }
                ).length;

                const persistenceMs = now - track.firstSeenAt;

                const strongSighting = track.scoreWindow.some(
                    function (score) {
                        return score >= PHONE_STRONG_CONFIDENCE;
                    }
                );

                if (
                    strongSighting ||
                    (
                        qualifyingCount >= PHONE_MIN_DETECTIONS &&
                        persistenceMs >= PHONE_MIN_PERSISTENCE_MS
                    )
                ) {

                    track.confirmedThisEpisode = true;
                    newlyConfirmed.push(track);
                }
            }

            return {
                activeTracks: tracks,
                newlyConfirmed: newlyConfirmed,
                closedTrackIds: closedTrackIds,
                anyActive: tracks.length > 0,
            };
        }

        return {
            update: update,
            reset: function () {
                tracks = [];
            },
        };
    }


    /* =====================================================
       CANDIDATE WARNING BANNER — a small, self-contained UI
       element this module owns end-to-end (created/removed the
       same way assessment-integrity.js owns its camera preview
       panel), so it never collides with each stage's own,
       separate cumulative "Assessment Warning / Final Warning"
       integrity banner (Step 8/12).
       ===================================================== */

    let warningBannerEl = null;
    let warningAutoHideTimeoutId = null;

    function showWarningBanner(message) {

        if (!warningBannerEl) {

            warningBannerEl = document.createElement("div");
            warningBannerEl.id = "phoneDetectionWarningBanner";
            warningBannerEl.setAttribute("role", "alert");

            warningBannerEl.style.cssText = [
                "position:fixed",
                "top:16px",
                "left:50%",
                "transform:translateX(-50%)",
                "z-index:2147483000",
                "max-width:92vw",
                "padding:12px 20px",
                "border-radius:8px",
                "background:#fff3cd",
                "color:#664d03",
                "border:1px solid #ffe69c",
                "box-shadow:0 4px 14px rgba(0,0,0,0.15)",
                "font-family:inherit",
                "font-size:0.95rem",
                "text-align:center",
            ].join(";");

            document.body.appendChild(warningBannerEl);
        }

        warningBannerEl.textContent = message;
        warningBannerEl.style.display = "block";

        if (warningAutoHideTimeoutId !== null) {
            clearTimeout(warningAutoHideTimeoutId);
        }

        warningAutoHideTimeoutId = setTimeout(function () {
            hideWarningBanner();
        }, PHONE_WARNING_AUTO_HIDE_MS);
    }

    function hideWarningBanner() {

        if (warningAutoHideTimeoutId !== null) {
            clearTimeout(warningAutoHideTimeoutId);
            warningAutoHideTimeoutId = null;
        }

        if (warningBannerEl) {
            warningBannerEl.style.display = "none";
        }
    }

    function removeWarningBanner() {

        hideWarningBanner();

        if (warningBannerEl && warningBannerEl.parentElement) {
            warningBannerEl.parentElement.removeChild(warningBannerEl);
        }

        warningBannerEl = null;
    }


    /* =====================================================
       DEV-ONLY PERFORMANCE DIAGNOSTICS (Step 15) — never sent to
       the backend, never shown to HR. Purely for local/dev
       tuning via PhoneDetector.getDiagnostics() in the console.
       ===================================================== */

    function createDiagnostics() {

        const inferenceMsSamples = [];
        let cycleCount = 0;
        let confirmedEpisodeCount = 0;

        return {

            recordInference: function (ms) {

                inferenceMsSamples.push(ms);

                if (inferenceMsSamples.length > 30) {
                    inferenceMsSamples.shift();
                }

                cycleCount += 1;
            },

            recordConfirmedEpisode: function () {
                confirmedEpisodeCount += 1;
            },

            snapshot: function () {

                const avgInferenceMs = inferenceMsSamples.length
                    ? inferenceMsSamples.reduce(function (a, b) { return a + b; }, 0) /
                        inferenceMsSamples.length
                    : null;

                return {
                    backend: activeBackend,
                    detector: "yolov8n",
                    tracker: "iou",
                    sampling_interval_ms: PHONE_DETECTION_INTERVAL_MS,
                    average_inference_ms: avgInferenceMs,
                    approx_detector_fps: avgInferenceMs
                        ? Math.round((1000 / avgInferenceMs) * 100) / 100
                        : null,
                    total_cycles: cycleCount,
                    confirmed_episodes: confirmedEpisodeCount,
                };
            },
        };
    }

    let diagnostics = createDiagnostics();


    /* =====================================================
       DETECTION LOOP
       ===================================================== */

    let activeIntervalId = null;
    let activeTracker = null;
    let cooldownState = null;

    function isRunning() {
        return activeIntervalId !== null;
    }

    function isSupported() {

        return Boolean(
            typeof document !== "undefined" &&
            document.createElement("canvas").getContext &&
            typeof WebAssembly !== "undefined"
        );
    }

    // detectFn is overridable purely for testing (Step 21) - real
    // callers never pass it; it defaults to the real YOLOv8n/ONNX
    // Runtime Web pipeline. A test can pass a fake that resolves
    // to a synthetic detections array instead of ever touching a
    // camera or downloading/running a model.
    function start(videoEl, options) {

        options = options || {};

        if (isRunning()) {
            return true;
        }

        if (!videoEl) {

            console.warn(
                "PhoneDetector.start() called with no video element " +
                "- detection not started."
            );

            return false;
        }

        if (!isSupported()) {

            console.warn(
                "PhoneDetector is not supported in this browser - " +
                "phone detection will not run."
            );

            return false;
        }

        const onConfirmedDetection = options.onConfirmedDetection || function () {};
        const detectFn = options.detectFn || runInference;

        activeTracker = createTracker();
        cooldownState = { lastEventAt: null };
        diagnostics = createDiagnostics();

        let sampling = false;

        activeIntervalId = setInterval(function () {

            // Step 12/15/18: never let inference pile up if one
            // sample is still running when the next tick fires,
            // and skip entirely while the tab is hidden - saves
            // CPU/GPU without touching the existing, separate
            // visibility-monitoring integrity system in
            // assessment-integrity.js.
            if (sampling || document.hidden) {
                return;
            }

            if (videoEl.readyState < 2) {
                // Video has no frame data yet.
                return;
            }

            sampling = true;

            const inferenceStartedAt =
                (typeof performance !== "undefined" && performance.now)
                    ? performance.now()
                    : Date.now();

            Promise.resolve(detectFn(videoEl))
                .then(function (detections) {

                    diagnostics.recordInference(
                        ((typeof performance !== "undefined" && performance.now)
                            ? performance.now()
                            : Date.now()) - inferenceStartedAt
                    );

                    const now = Date.now();
                    const result = activeTracker.update(detections || [], now);

                    if (!result.anyActive) {
                        hideWarningBanner();
                    }

                    result.newlyConfirmed.forEach(function (track) {

                        diagnostics.recordConfirmedEpisode();

                        showWarningBanner(
                            "Mobile phone detected. Please remove " +
                            "unauthorized devices."
                        );

                        const withinCooldown = Boolean(
                            cooldownState.lastEventAt !== null &&
                            (now - cooldownState.lastEventAt) < PHONE_EVENT_COOLDOWN_MS
                        );

                        if (!withinCooldown) {
                            cooldownState.lastEventAt = now;
                        }

                        const averageConfidence =
                            track.scoreWindow.reduce(
                                function (a, b) { return a + b; }, 0
                            ) / track.scoreWindow.length;

                        onConfirmedDetection(
                            {
                                object: PHONE_DETECTION_TARGET_CLASS,
                                confidence:
                                    Math.round(averageConfidence * 100) / 100,
                                track_id: track.id,
                                duration_ms: now - track.firstSeenAt,
                                detections: track.scoreWindow.length,
                                detector: "yolov8n",
                                tracker: "iou",
                            },
                            { withinCooldown: withinCooldown }
                        );
                    });
                })
                .catch(function (error) {

                    console.warn(
                        "PhoneDetector inference error:",
                        error
                    );
                })
                .finally(function () {
                    sampling = false;
                });

        }, PHONE_DETECTION_INTERVAL_MS);

        return true;
    }

    function stop() {

        if (activeIntervalId !== null) {
            clearInterval(activeIntervalId);
            activeIntervalId = null;
        }

        activeTracker = null;
        cooldownState = null;

        removeWarningBanner();
    }


    window.PhoneDetector = {
        isSupported: isSupported,
        isRunning: isRunning,
        loadModel: loadModel,
        start: start,
        stop: stop,
        getDiagnostics: function () {
            return diagnostics.snapshot();
        },
        // Exposed deliberately (not "private") so the temporal
        // decision engine (tracking/confirmation/cooldown) can be
        // unit-tested directly with synthetic input - see module
        // header and Task 5B's automated tests.
        createTracker: createTracker,
        computeIoU: computeIoU,
    };

})();

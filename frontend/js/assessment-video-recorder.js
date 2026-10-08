/* =========================================================
   JOBPILOT AI - ASSESSMENT VIDEO RECORDING (Task 4)

   ONE shared MediaRecorder wrapper reused by all four stages
   (Self Intro / Aptitude / Programming / AI Interview) instead
   of four separate implementations. Deliberately does NOT
   acquire its own camera stream — the caller passes in the
   MediaStream that already exists (assessment.js's own
   getUserMedia() for Self Intro, or
   window.IntegrityMode.getCameraStream() for the other three) —
   see Task 4E's assessment-integrity.js, which this never
   duplicates.

   PRIVACY: only ever produces ONE completed video Blob per
   stage, uploaded once, when that stage finishes. Never uploads
   individual frames, never takes screenshots, never streams
   continuously to the server. No AI analysis of any kind touches
   this module or the file it produces.

   Usage from any stage's own script:

       const recorder = new AssessmentVideoRecorder();

       if (!recorder.isSupported()) {
           // show a clear "recording unavailable" message —
           // never pretend recording is active.
       }

       recorder.start(cameraStream, "self_intro");
       ...
       const blob = await recorder.stop();     // finalizes cleanly
       const result = await recorder.upload(applicationId, "self_intro");
       // result: { ok: true, data } | { ok: false, error }
   ========================================================= */

(function () {

    if (window.AssessmentVideoRecorder) {
        // Defensive against accidental double-include, same
        // pattern as session-guard.js.
        return;
    }

    const VIDEO_API_BASE_URL = "http://" + (window.location.hostname === "localhost" ? "localhost" : "127.0.0.1") + ":8000";

    // Tried in order; the first one the browser actually
    // supports wins. Never hard-codes a single codec (Step 21).
    const CANDIDATE_MIME_TYPES = [
        "video/webm;codecs=vp9,opus",
        "video/webm;codecs=vp8,opus",
        "video/webm;codecs=vp8",
        "video/webm",
        "video/mp4",
    ];

    function pickSupportedMimeType() {

        if (
            typeof MediaRecorder === "undefined" ||
            typeof MediaRecorder.isTypeSupported !== "function"
        ) {
            return null;
        }

        for (let i = 0; i < CANDIDATE_MIME_TYPES.length; i++) {

            if (MediaRecorder.isTypeSupported(CANDIDATE_MIME_TYPES[i])) {
                return CANDIDATE_MIME_TYPES[i];
            }
        }

        return null;
    }


    function AssessmentVideoRecorder() {

        this._recorder = null;
        this._chunks = [];
        this._mimeType = null;
        this._startedAt = null;
        this._endedAt = null;
        this._lastBlob = null;
        this._stopPromise = null;
        this._spool = null;                 // see "SPOOLING" below
    }


    /* =====================================================
       SUPPORT CHECK — call before start(); if this returns
       false, show a clear message and do not pretend
       recording is active (Step 6/21).
       ===================================================== */

    AssessmentVideoRecorder.prototype.isSupported = function () {

        return Boolean(
            typeof MediaRecorder !== "undefined" &&
            pickSupportedMimeType()
        );
    };


    AssessmentVideoRecorder.prototype.isRecording = function () {

        return Boolean(
            this._recorder &&
            this._recorder.state === "recording"
        );
    };


    AssessmentVideoRecorder.prototype.getDuration = function () {

        if (!this._startedAt) {
            return 0;
        }

        const endPoint =
            this._endedAt ||
            Date.now();

        return Math.max(
            0,
            (endPoint - this._startedAt) / 1000
        );
    };


    /* =====================================================
       START — stream is an existing MediaStream the caller
       already owns (see module header). This never calls
       getUserMedia() itself.
       ===================================================== */

    AssessmentVideoRecorder.prototype.start = function (stream, stage, options) {

        if (!stream) {

            console.warn(
                "AssessmentVideoRecorder.start() called with no " +
                "stream — recording not started."
            );

            return false;
        }

        if (!this.isSupported()) {

            console.warn(
                "MediaRecorder is not supported in this browser " +
                "- recording not started."
            );

            return false;
        }

        if (this.isRecording()) {
            return true;
        }

        const mimeType = pickSupportedMimeType();

        this._chunks = [];
        this._mimeType = mimeType;
        this._endedAt = null;
        this._lastBlob = null;
        this._stopPromise = null;
        this._stage = stage || null;

        try {

            const recorderOptions = { mimeType: mimeType };

            if (options && options.videoBitsPerSecond) {
                recorderOptions.videoBitsPerSecond = options.videoBitsPerSecond;
            }

            this._recorder = new MediaRecorder(stream, recorderOptions);

        } catch (error) {

            console.warn(
                "MediaRecorder could not be created:",
                error
            );

            this._recorder = null;
            return false;
        }

        const self = this;

        this._recorder.addEventListener(
            "dataavailable",
            function (event) {

                if (event.data && event.data.size > 0) {
                    self._chunks.push(event.data);
                }
            }
        );

        this._recorder.addEventListener(
            "error",
            function (event) {

                console.warn(
                    "MediaRecorder error:",
                    event.error || event
                );
            }
        );

        try {

            // A timeslice keeps dataavailable firing periodically
            // so a crash/unexpected stop still leaves whatever
            // chunks were already captured - see stopSafely().
            this._recorder.start(1000);

        } catch (error) {

            console.warn(
                "MediaRecorder failed to start:",
                error
            );

            this._recorder = null;
            return false;
        }

        this._startedAt = Date.now();
        this._startSpool(stage);

        return true;
    };


    /* =====================================================
       STOP — resolves with the finalized Blob once
       MediaRecorder's own "stop" event has actually fired
       (its LAST dataavailable chunk is only guaranteed to
       exist by then - see Step 12). Idempotent: calling this
       when nothing is recording just resolves with whatever
       was already captured (or null).
       ===================================================== */

    AssessmentVideoRecorder.prototype.stop = function () {

        const self = this;

        if (this._stopPromise) {
            return this._stopPromise;
        }

        if (!this._recorder || this._recorder.state === "inactive") {

            this._endedAt = this._endedAt || Date.now();

            this._lastBlob = this._chunks.length
                ? new Blob(this._chunks, { type: this._mimeType || "video/webm" })
                : this._lastBlob;

            return Promise.resolve(this._lastBlob);
        }

        this._stopPromise = new Promise(function (resolve) {

            self._recorder.addEventListener(
                "stop",
                function onStop() {

                    self._recorder.removeEventListener("stop", onStop);

                    self._endedAt = Date.now();
                    self._stopSpoolTimer();

                    const blob = self._chunks.length
                        ? new Blob(
                            self._chunks,
                            { type: self._mimeType || "video/webm" }
                        )
                        : null;

                    self._lastBlob = blob;

                    resolve(blob);
                },
                { once: true }
            );

            try {

                self._recorder.stop();

            } catch (error) {

                console.warn(
                    "Error stopping MediaRecorder:",
                    error
                );

                self._endedAt = Date.now();

                const blob = self._chunks.length
                    ? new Blob(
                        self._chunks,
                        { type: self._mimeType || "video/webm" }
                    )
                    : null;

                self._lastBlob = blob;

                resolve(blob);
            }
        });

        return this._stopPromise;
    };


    /* =====================================================
       UPLOAD — call AFTER stop() has resolved. Never uploads
       chunk-by-chunk; always the one finalized Blob for this
       stage. Returns {ok:true, data} on success, {ok:false,
       error, alreadyUploaded} on failure - a 409 (this stage
       was already uploaded, e.g. a retried request that
       actually succeeded the first time) is reported as
       alreadyUploaded:true rather than a hard failure, so the
       caller can treat it as "the video is safely saved" and
       continue.
       ===================================================== */

    AssessmentVideoRecorder.prototype._uploadWhole = function (
        applicationId,
        stage
    ) {

        const self = this;

        if (!this._lastBlob || this._lastBlob.size === 0) {

            return Promise.resolve({
                ok: false,
                error: "No recording is available to upload.",
            });
        }

        const formData = new FormData();

        const extension =
            (this._mimeType || "video/webm").indexOf("mp4") !== -1
                ? "mp4"
                : "webm";

        formData.append(
            "file",
            this._lastBlob,
            `${stage || "recording"}.${extension}`
        );

        if (this._startedAt) {

            formData.append(
                "started_at",
                new Date(this._startedAt).toISOString()
            );
        }

        if (this._endedAt) {

            formData.append(
                "ended_at",
                new Date(this._endedAt).toISOString()
            );
        }

        formData.append(
            "duration_seconds",
            String(this.getDuration())
        );

        return fetch(
            `${VIDEO_API_BASE_URL}/api/assessment/videos/` +
            `${encodeURIComponent(applicationId)}/` +
            `${encodeURIComponent(stage)}`,
            {
                method: "POST",
                credentials: "include",
                body: formData,
            }
        )
            .then(function (response) {

                return response.json()
                    .catch(function () {
                        return {};
                    })
                    .then(function (data) {

                        if (response.ok) {
                            return { ok: true, data: data };
                        }

                        if (response.status === 409) {

                            return {
                                ok: true,
                                alreadyUploaded: true,
                                data: data,
                            };
                        }

                        return {
                            ok: false,
                            error: data.detail || "Video upload failed.",
                        };
                    });
            })
            .catch(function (error) {

                console.warn(
                    "Video upload network error:",
                    error
                );

                return {
                    ok: false,
                    error: "Network error while uploading the recording.",
                };
            });
    };


    /* =====================================================
       SPOOLING — a candidate who closes the tab, loses the connection or crashes mid-stage must not lose the video. While
       recording, every ~10 s the chunks not yet sent go to the server (POST .../chunk). At the end of the stage upload() asks the
       server to assemble them (POST .../finalize): ONE upload in total, not two. If anything is missing or fails, upload() falls
       back to sending the whole video the normal way (_uploadWhole). Chunks the server still holds after a candidate left are
       turned into a "partial" recording by the server itself.
       ===================================================== */

    const SPOOL_INTERVAL_MS = 10000;

    function currentApplicationId() {

        const value = new URLSearchParams(window.location.search).get("application_id");

        return value && /^\d+$/.test(value) ? value : null;
    }

    AssessmentVideoRecorder.prototype._startSpool = function (stage) {

        this._stopSpoolTimer();

        const applicationId = currentApplicationId();

        // The server assembles chunks into a .webm: a browser that records MP4 (Safari) uploads the whole video as before.
        if (!applicationId || !stage || typeof fetch !== "function" || (this._mimeType || "").indexOf("webm") === -1) {
            this._spool = null;
            return;
        }

        const self = this;

        this._spool = { applicationId: applicationId, stage: stage, seq: 0, sent: 0, busy: null, failed: false, timer: null };

        this._spool.timer = setInterval(function () { self._spoolFlush(); }, SPOOL_INTERVAL_MS);
    };

    AssessmentVideoRecorder.prototype._stopSpoolTimer = function () {

        if (this._spool && this._spool.timer) {
            clearInterval(this._spool.timer);
            this._spool.timer = null;
        }
    };

    // Sends the chunks recorded since the last successful send. Resolves true when everything recorded so far is on the server.
    AssessmentVideoRecorder.prototype._spoolFlush = function () {

        const spool = this._spool;

        if (!spool) {
            return Promise.resolve(false);
        }

        if (spool.busy) {
            return spool.busy;
        }

        const self = this;
        const pending = this._chunks.slice(spool.sent);

        if (!pending.length) {
            return Promise.resolve(true);
        }

        const form = new FormData();
        form.append("seq", String(spool.seq));
        form.append("file", new Blob(pending, { type: this._mimeType || "video/webm" }), "chunk.webm");

        spool.busy = fetch(
            `${VIDEO_API_BASE_URL}/api/assessment/videos/${encodeURIComponent(spool.applicationId)}/${encodeURIComponent(spool.stage)}/chunk`,
            { method: "POST", credentials: "include", body: form }
        )
            .then(function (response) {

                if (!response.ok) {
                    spool.failed = true;                                  // e.g. the stage was already saved: stop spooling
                    return false;
                }

                spool.sent += pending.length;
                spool.seq += 1;
                spool.failed = false;
                return true;
            })
            .catch(function () {
                return false;                                             // network hiccup: the next tick retries the same chunks
            })
            .then(function (ok) {
                spool.busy = null;
                return ok;
            });

        return spool.busy;
    };

    // Everything recorded is on the server and the server can assemble it: no second upload is needed.
    AssessmentVideoRecorder.prototype._finalizeFromSpool = async function (applicationId, stage) {

        const spool = this._spool;

        if (!spool || String(spool.applicationId) !== String(applicationId) || spool.stage !== stage || spool.failed) {
            return null;
        }

        this._stopSpoolTimer();

        for (let attempt = 0; attempt < 3 && spool.sent < this._chunks.length; attempt++) {
            await this._spoolFlush();
        }

        if (spool.sent !== this._chunks.length || spool.seq === 0) {
            return null;
        }

        const form = new FormData();
        form.append("expected_chunks", String(spool.seq));
        if (this._startedAt) form.append("started_at", new Date(this._startedAt).toISOString());
        if (this._endedAt) form.append("ended_at", new Date(this._endedAt).toISOString());
        form.append("duration_seconds", String(this.getDuration()));

        try {

            const response = await fetch(
                `${VIDEO_API_BASE_URL}/api/assessment/videos/${encodeURIComponent(applicationId)}/${encodeURIComponent(stage)}/finalize`,
                { method: "POST", credentials: "include", body: form }
            );

            const data = await response.json().catch(function () { return {}; });

            if (response.ok) {
                return { ok: true, data: data };
            }

            if (response.status === 409 && /already/i.test(data.detail || "")) {
                return { ok: true, alreadyUploaded: true, data: data };
            }

        } catch (error) {
            console.warn("Video finalize network error:", error);
        }

        return null;                                                      // anything else: upload the whole video instead
    };

    AssessmentVideoRecorder.prototype.upload = async function (applicationId, stage) {

        if (!this._lastBlob || this._lastBlob.size === 0) {
            return { ok: false, error: "No recording is available to upload." };
        }

        const spooled = await this._finalizeFromSpool(applicationId, stage);

        return spooled || this._uploadWhole(applicationId, stage);
    };


    /* =====================================================
       CLEANUP — removes references only; never stops the
       camera stream itself (the caller owns that lifecycle -
       Step 12).
       ===================================================== */

    AssessmentVideoRecorder.prototype.cleanup = function () {

        this._stopSpoolTimer();
        this._spool = null;
        this._recorder = null;
        this._chunks = [];
        this._stopPromise = null;
    };


    window.AssessmentVideoRecorder = AssessmentVideoRecorder;

})();

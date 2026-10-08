/* =========================================================
   JOBPILOT AI - AUDIO PROCTORING for the SILENT stages (aptitude, programming)

   Nobody should be talking during an aptitude or programming test. This listens to the microphone LEVEL only (an
   AnalyserNode's amplitude): no audio is recorded, stored, uploaded or transcribed. When speech-level sound continues
   for a while it reports ONE flag, `voice_detected`, for the recruiter (never a violation on its own). The AI interview
   is excluded: there the candidate is supposed to talk.

   Permission: a separate microphone prompt. If the candidate declines, nothing else changes (no event, no penalty here;
   the recruiter already sees camera/permission state in the integrity timeline).
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.AudioProctor = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    const DEFAULTS = Object.freeze({
        speechRms: 0.035,        // level above which a 100 ms sample counts as "sound louder than a quiet room"
        windowMs: 10000,         // look at the last 10 s ...
        minLoudMs: 4000,         // ... if at least this much of it was loud, somebody is talking
        cooldownMs: 60000,       // one flag per minute at most
        calibrationMs: 3000,     // the first seconds set the room's own noise level
    });

    /* pure engine: feed (rms, elapsedMs, now) samples, get back true when a flag should be raised */
    function createEngine(overrides) {

        const cfg = Object.assign({}, DEFAULTS, overrides || {});
        const samples = [];               // [now, loud(0|1), elapsedMs]
        let floorSum = 0;
        let floorCount = 0;
        let floor = null;
        let calibrated = 0;
        let lastFlagAt = -Infinity;

        function step(rms, elapsedMs, now) {

            if (calibrated < cfg.calibrationMs) {
                calibrated += elapsedMs;
                floorSum += rms;
                floorCount += 1;
                floor = floorSum / floorCount;
                return false;
            }

            // speech must clearly exceed the room's own noise, not only the absolute level
            const threshold = Math.max(cfg.speechRms, (floor || 0) * 3);
            samples.push([now, rms >= threshold ? 1 : 0, elapsedMs]);

            while (samples.length && now - samples[0][0] > cfg.windowMs) samples.shift();

            const loudMs = samples.reduce(function (sum, s) { return sum + (s[1] ? s[2] : 0); }, 0);

            if (loudMs >= cfg.minLoudMs && now - lastFlagAt >= cfg.cooldownMs) {
                lastFlagAt = now;
                samples.length = 0;
                return true;
            }

            return false;
        }

        return { step: step, config: cfg };
    }

    /* ---- browser runtime ---- */
    let stream = null;
    let context = null;
    let timerId = null;

    function start(onFlag) {

        stop();

        if (typeof navigator === "undefined" || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            return Promise.resolve(false);
        }

        return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: false }, video: false })
            .then(function (audioStream) {

                stream = audioStream;

                const Ctor = window.AudioContext || window.webkitAudioContext;
                context = new Ctor();
                const analyser = context.createAnalyser();
                analyser.fftSize = 1024;
                context.createMediaStreamSource(audioStream).connect(analyser);

                const buffer = new Float32Array(analyser.fftSize);
                const engine = createEngine();
                let last = performance.now();

                timerId = setInterval(function () {
                    const now = performance.now();
                    analyser.getFloatTimeDomainData(buffer);
                    let sum = 0;
                    for (let i = 0; i < buffer.length; i++) sum += buffer[i] * buffer[i];
                    const rms = Math.sqrt(sum / buffer.length);
                    if (engine.step(rms, now - last, now)) onFlag();
                    last = now;
                }, 100);

                return true;
            })
            .catch(function () {
                return false;                      // declined / no microphone: nothing else changes
            });
    }

    function stop() {

        if (timerId) { clearInterval(timerId); timerId = null; }
        if (stream) { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { /* ignore */ } }); stream = null; }
        if (context) { try { context.close(); } catch (e) { /* ignore */ } context = null; }
    }

    return { start: start, stop: stop, createEngine: createEngine, DEFAULTS: DEFAULTS };
});

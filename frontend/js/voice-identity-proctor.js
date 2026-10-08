/* =========================================================
   JOBPILOT AI - VOICE IDENTITY PROCTORING (AI interview only)

   Why this exists: audio-proctor.js only answers "is anyone talking" (a
   volume/amplitude check), which is exactly right for the SILENT stages
   (aptitude/programming/SJT/TDA, where no talking at all is expected --
   see integrity_service.py, voice_detected is now a real violation there)
   but useless during the AI interview, where the candidate is SUPPOSED to
   talk constantly. This module answers a different, narrower question:
   "does this sustained voice sound like a different person than the one
   who calibrated at the start of the interview?"

   This is a HEURISTIC, not real speaker verification (no cloud service,
   no trained model) -- same honesty principle as role_classification.py's
   own module header ("a heuristic, not a claim of deep role understanding").
   It compares the fundamental pitch (F0) of sustained voiced speech against
   a baseline range learned from the candidate's own first ~15s of real
   speech. A deviation has to be LARGE and SUSTAINED (several continuous
   seconds, not one word) before it fires -- normal pitch variation from
   emotion, stress, or a slightly different mic/headset is NOT sustained
   like a genuinely different speaker would be.

   Deliberately NEVER a counted violation (see integrity_service.py:
   "possible_third_party_voice" stays in INFO_EVENT_TYPES, same as the
   original softer gaze_away) -- a heuristic this uncertain must only ever
   be a flag for HR to review, never something that can end an assessment
   on its own. Even serious commercial proctoring platforms do not fully
   automate true speaker verification; they flag it for human review.

   Privacy: no audio is recorded or uploaded by this module. Only a single
   number (the estimated pitch in Hz) is ever computed, compared, and
   discarded every ~100 ms. Nothing here can reconstruct speech or identify
   a real person -- it is a single scalar, the same deliberate minimalism
   already used by audio-proctor.js (amplitude only) and face-proctor.js
   (count/pose/brightness only, no face recognition).
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.VoiceIdentityProctor = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    const DEFAULTS = Object.freeze({
        calibrationSamples: 60,       // ~15s of voiced samples at ~4/s, while CANDIDATE_SPEAKING
        sampleIntervalMs: 250,        // how often step() is expected to be called
        deviationCents: 500,          // 500 cents = 5 semitones away from the candidate's own range counts as "different"
        sustainedMs: 4000,            // deviation must hold for this long, continuously, before it fires
        cooldownMs: 60000,            // one flag per minute of genuinely sustained deviation
    });

    function median(values) {
        const sorted = values.slice().sort(function (a, b) { return a - b; });
        return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
    }

    // Pitch distance in "cents" (the same musical-interval unit tuners use): a
    // ratio-based distance, not a raw Hz difference, since the perceptually/
    // physiologically meaningful gap between two voices scales with ratio, not
    // absolute Hz (an 20 Hz gap matters enormously at 100 Hz, barely at 300 Hz).
    function centsDistance(hzA, hzB) {
        if (!hzA || !hzB) return 0;
        return Math.abs(1200 * Math.log2(hzA / hzB));
    }

    /* ---- pure decision engine (no audio, no clock): unit-tested with synthetic pitch sequences ---- */
    function createEngine(overrides) {

        const cfg = Object.assign({}, DEFAULTS, overrides || {});

        const state = {
            calibSamples: [],
            baselineHz: null,
            deviationSince: null,
            fired: false,
            lastFiredAt: -Infinity,
        };

        // pitchHz: estimated fundamental frequency for this sample, or null (unvoiced/silence/AI-speaking/not-calibrating)
        function step(pitchHz, now) {

            const events = [];

            if (typeof pitchHz !== "number" || !isFinite(pitchHz) || pitchHz <= 0) {
                // No usable voiced sample this tick (silence, AI speaking, unvoiced consonant): the
                // deviation streak does NOT reset here -- a few silent gaps mid-sentence are normal
                // speech, not evidence the deviation stopped. It resets only when a genuine
                // same-speaker-range sample is seen again (below).
                return events;
            }

            if (state.baselineHz === null) {

                state.calibSamples.push(pitchHz);

                if (state.calibSamples.length >= cfg.calibrationSamples) {
                    state.baselineHz = median(state.calibSamples);
                }

                return events;
            }

            const distance = centsDistance(pitchHz, state.baselineHz);

            if (distance > cfg.deviationCents) {

                if (state.deviationSince === null) state.deviationSince = now;

                if (!state.fired && now - state.deviationSince >= cfg.sustainedMs && now - state.lastFiredAt >= cfg.cooldownMs) {
                    state.fired = true;
                    state.lastFiredAt = now;
                    events.push("possible_third_party_voice");
                }

            } else {
                state.deviationSince = null;
                state.fired = false;
            }

            return events;
        }

        return { step: step, state: state, config: cfg };
    }

    // ---- Autocorrelation-based pitch (F0) estimator ----
    // A standard, well-known approach for monophonic speech: find the lag (in samples) at which the
    // signal best correlates with a delayed copy of itself; that lag's corresponding frequency is the
    // fundamental. Returns null for silence/unvoiced audio (no clear periodicity found).
    function estimatePitch(samples, sampleRate) {

        const SIZE = samples.length;

        let rms = 0;
        for (let i = 0; i < SIZE; i++) rms += samples[i] * samples[i];
        rms = Math.sqrt(rms / SIZE);

        if (rms < 0.01) return null;   // too quiet to be voiced speech

        const MIN_HZ = 70;    // below typical human voice range
        const MAX_HZ = 400;   // above typical human voice range (covers low male to high female/child)
        const minLag = Math.floor(sampleRate / MAX_HZ);
        const maxLag = Math.floor(sampleRate / MIN_HZ);

        let bestLag = -1;
        let bestCorrelation = 0;

        for (let lag = minLag; lag <= maxLag && lag < SIZE; lag++) {

            let correlation = 0;
            for (let i = 0; i < SIZE - lag; i++) {
                correlation += samples[i] * samples[i + lag];
            }

            if (correlation > bestCorrelation) {
                bestCorrelation = correlation;
                bestLag = lag;
            }
        }

        if (bestLag <= 0) return null;

        // Normalise against the signal's own energy: a weak best-correlation relative to total
        // energy means no clear periodicity (unvoiced consonant, noise), not a real pitch.
        const normalized = bestCorrelation / (SIZE * rms * rms);
        if (normalized < 0.3) return null;

        return sampleRate / bestLag;
    }

    /* ---- browser runtime ----
       Deliberately takes an ALREADY-OPEN stream rather than calling getUserMedia itself: the AI
       interview page already has exactly one persistent mic stream (InterviewVoiceV2.ensureMicStream()),
       and this module must reuse it, never open a second one (same "ONE path" principle already
       enforced elsewhere in this codebase). */
    let context = null;
    let timerId = null;

    // stream: the existing MediaStream to listen on (NOT opened here).
    // isCandidateSpeaking: a function returning true only while the voice turn-logic state machine
    //   says CANDIDATE_SPEAKING -- pitch samples while the AI is talking, or during silence, are
    //   meaningless for this comparison and must never calibrate or count toward a deviation streak.
    // onFlag: called (with no arguments) when a sustained deviation is confirmed.
    function start(stream, isCandidateSpeaking, onFlag, overrides) {

        stop();

        if (!stream || typeof window === "undefined") return false;

        const audioTracks = stream.getAudioTracks ? stream.getAudioTracks() : [];
        if (!audioTracks.length) return false;

        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return false;

        try {

            context = new Ctor();
            const analyser = context.createAnalyser();
            analyser.fftSize = 2048;
            context.createMediaStreamSource(stream).connect(analyser);

            const buffer = new Float32Array(analyser.fftSize);
            const engine = createEngine(overrides);

            timerId = setInterval(function () {

                if (typeof isCandidateSpeaking === "function" && !isCandidateSpeaking()) return;

                analyser.getFloatTimeDomainData(buffer);
                const pitchHz = estimatePitch(buffer, context.sampleRate);
                const events = engine.step(pitchHz, performance.now());

                if (events.indexOf("possible_third_party_voice") !== -1) onFlag();

            }, engine.config.sampleIntervalMs);

            return true;

        } catch (error) {
            stop();
            return false;
        }
    }

    function stop() {

        if (timerId) { clearInterval(timerId); timerId = null; }
        // The stream itself is owned by InterviewVoiceV2, never stopped/closed here.
        if (context) { try { context.close(); } catch (error) { /* ignore */ } context = null; }
    }

    return { createEngine: createEngine, estimatePitch: estimatePitch, centsDistance: centsDistance, start: start, stop: stop };
});

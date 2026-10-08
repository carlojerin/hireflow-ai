// ============================================================
// JOBPILOT AI - REALTIME TURN-TAKING: PURE DECISION LOGIC (Task 5J)
//
// Deliberately dependency-free and DOM-free: no AudioContext, no
// fetch, no MediaRecorder, no SpeechRecognition. Every function
// here takes plain numbers/strings/objects and returns a plain
// result — nothing here can touch a microphone, play audio, or
// talk to the interview brain. That's what makes this module
// runnable both in the browser (interview-voice-v2.js uses it
// directly) and in plain Node for the deterministic test suite
// (tests/frontend/test_interview_voice_turn_logic_v2.js), with no
// build step, no jsdom, no test framework install.
//
// UMD-lite export: attaches to `window` in the browser, exports
// via `module.exports` under Node. No bundler needed either way.
// ============================================================

(function (root, factory) {

    const exportsObject = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObject;
    }

    if (root) {
        root.InterviewVoiceTurnLogicV2 = exportsObject;
    }

})(typeof window !== "undefined" ? window : (typeof global !== "undefined" ? global : null), function () {

    // ============================================================
    // STATE MACHINE — pure transition table. Every entry is
    // (fromState -> event -> toState). An event not listed for the
    // current state is a no-op transition (returns the SAME state,
    // never throws) — callers are never required to pre-filter
    // which events are "valid right now".
    // ============================================================

    const STATES = Object.freeze({
        IDLE: "IDLE",
        REQUESTING_MIC: "REQUESTING_MIC",
        AI_PREPARING: "AI_PREPARING",
        AI_SPEAKING: "AI_SPEAKING",
        AI_INTERRUPTED: "AI_INTERRUPTED",
        CANDIDATE_LISTENING: "CANDIDATE_LISTENING",
        CANDIDATE_SPEAKING: "CANDIDATE_SPEAKING",
        CANDIDATE_SILENCE: "CANDIDATE_SILENCE",
        TRANSCRIBING: "TRANSCRIBING",
        SUBMITTING: "SUBMITTING",
        AI_THINKING: "AI_THINKING",
        COMPLETED: "COMPLETED",
        ERROR: "ERROR",
    });

    const EVENTS = Object.freeze({
        REQUEST_MIC: "REQUEST_MIC",
        MIC_GRANTED: "MIC_GRANTED",
        MIC_DENIED: "MIC_DENIED",
        SPEAK_REQUESTED: "SPEAK_REQUESTED",
        AI_AUDIO_READY: "AI_AUDIO_READY",
        AI_AUDIO_ENDED: "AI_AUDIO_ENDED",
        AI_AUDIO_FAILED: "AI_AUDIO_FAILED",
        CANDIDATE_VOICE_DETECTED: "CANDIDATE_VOICE_DETECTED",
        CANDIDATE_SILENCE_DETECTED: "CANDIDATE_SILENCE_DETECTED",
        TRANSCRIPT_FINALIZED: "TRANSCRIPT_FINALIZED",
        TRANSCRIPT_EMPTY: "TRANSCRIPT_EMPTY",
        SUBMIT_STARTED: "SUBMIT_STARTED",
        BRAIN_RESPONDED: "BRAIN_RESPONDED",
        INTERVIEW_COMPLETED: "INTERVIEW_COMPLETED",
        ERROR_OCCURRED: "ERROR_OCCURRED",
        RESET: "RESET",
    });

    // Only the transitions this module actually needs to make
    // deterministic and testable are listed explicitly. Anything
    // not listed is a no-op (see nextState()) rather than an
    // exception — a late/duplicate event arriving after the state
    // already moved on must never crash the caller.
    const TRANSITIONS = {
        [STATES.IDLE]: {
            [EVENTS.REQUEST_MIC]: STATES.REQUESTING_MIC,
            [EVENTS.SPEAK_REQUESTED]: STATES.AI_PREPARING,
        },
        [STATES.REQUESTING_MIC]: {
            [EVENTS.MIC_GRANTED]: STATES.IDLE,
            [EVENTS.MIC_DENIED]: STATES.ERROR,
        },
        [STATES.AI_PREPARING]: {
            [EVENTS.AI_AUDIO_READY]: STATES.AI_SPEAKING,
            [EVENTS.AI_AUDIO_FAILED]: STATES.CANDIDATE_LISTENING, // recovery: text fallback still works
            [EVENTS.CANDIDATE_VOICE_DETECTED]: STATES.AI_INTERRUPTED, // barge-in while TTS was still loading
        },
        [STATES.AI_SPEAKING]: {
            [EVENTS.AI_AUDIO_ENDED]: STATES.CANDIDATE_LISTENING,
            [EVENTS.CANDIDATE_VOICE_DETECTED]: STATES.AI_INTERRUPTED,
            [EVENTS.AI_AUDIO_FAILED]: STATES.CANDIDATE_LISTENING,
        },
        [STATES.AI_INTERRUPTED]: {
            [EVENTS.CANDIDATE_VOICE_DETECTED]: STATES.CANDIDATE_SPEAKING,
        },
        [STATES.CANDIDATE_LISTENING]: {
            [EVENTS.CANDIDATE_VOICE_DETECTED]: STATES.CANDIDATE_SPEAKING,
        },
        [STATES.CANDIDATE_SPEAKING]: {
            [EVENTS.CANDIDATE_SILENCE_DETECTED]: STATES.CANDIDATE_SILENCE,
        },
        [STATES.CANDIDATE_SILENCE]: {
            // Task 4: one request does STT + brain + TTS, so the candidate's
            // utterance goes straight to AI_THINKING (no separate transcript step).
            [EVENTS.SUBMIT_STARTED]: STATES.AI_THINKING,
            [EVENTS.TRANSCRIPT_FINALIZED]: STATES.TRANSCRIBING,
            [EVENTS.TRANSCRIPT_EMPTY]: STATES.CANDIDATE_LISTENING, // never submit empty — go back to listening
            [EVENTS.CANDIDATE_VOICE_DETECTED]: STATES.CANDIDATE_SPEAKING, // brief pause, not a real end-of-turn
        },
        [STATES.TRANSCRIBING]: {
            [EVENTS.TRANSCRIPT_FINALIZED]: STATES.SUBMITTING,
            [EVENTS.TRANSCRIPT_EMPTY]: STATES.CANDIDATE_LISTENING,
            [EVENTS.ERROR_OCCURRED]: STATES.ERROR,
        },
        [STATES.SUBMITTING]: {
            [EVENTS.SUBMIT_STARTED]: STATES.AI_THINKING,
            [EVENTS.ERROR_OCCURRED]: STATES.ERROR,
        },
        [STATES.AI_THINKING]: {
            [EVENTS.TRANSCRIPT_EMPTY]: STATES.CANDIDATE_LISTENING, // server heard no speech: never advance the interview
            [EVENTS.BRAIN_RESPONDED]: STATES.AI_PREPARING,
            [EVENTS.INTERVIEW_COMPLETED]: STATES.COMPLETED,
            [EVENTS.ERROR_OCCURRED]: STATES.ERROR,
        },
        [STATES.ERROR]: {
            [EVENTS.RESET]: STATES.CANDIDATE_LISTENING,
        },
        [STATES.COMPLETED]: {},
    };

    function nextState(currentState, event) {

        const stateTransitions = TRANSITIONS[currentState];

        if (!stateTransitions || !(event in stateTransitions)) {
            return currentState; // no-op: unrecognized/late event, never throws
        }

        // COMPLETED is a true terminal state — nothing (including a
        // stray late CANDIDATE_VOICE_DETECTED from a barge-in
        // monitor that hadn't torn down yet) can move out of it
        // except an explicit RESET, which isn't wired for COMPLETED
        // on purpose: once an interview is over, voice is over.
        if (currentState === STATES.COMPLETED) {
            return STATES.COMPLETED;
        }

        return stateTransitions[event];
    }


    // ============================================================
    // VOICE ACTIVITY DETECTION (amplitude-based) — decision only.
    // The actual AnalyserNode/getFloatTimeDomainData sampling lives
    // in interview-voice-v2.js; this function only answers "given
    // this RMS reading and how long we've already been continuously
    // above/below threshold, what should happen next".
    // ============================================================

    const DEFAULT_VAD_CONFIG = Object.freeze({
        voiceThreshold: 0.02,         // RMS amplitude (0..1) above which audio counts as "voice-like"
        minVoicedMsToTrigger: 150,    // sustained voiced duration required before treating it as real speech (filters clicks/pops/mic-init noise)
        silenceMsToEndTurn: 900,      // sustained silence after real speech required to consider the turn over (was 1200: the AI felt slow to answer)
        dipToleranceMs: 0,            // Task 4: a dip below the threshold shorter than this does not reset the "sustained voice" counter
    });

    /**
     * One VAD tick. Pure function: (previous tracking state, new
     * sample, elapsed ms since last tick, config) -> (updated
     * tracking state, decision). The caller (interview-voice-v2.js)
     * owns the setInterval/rAF loop and persists `tracking` between
     * calls; this function never touches a clock or a timer itself,
     * which is exactly what makes it possible to unit test with
     * synthetic timelines instead of real wall-clock waits.
     */
    function vadTick(tracking, rms, elapsedMs, config) {

        const cfg = Object.assign({}, DEFAULT_VAD_CONFIG, config || {});
        const isVoiced = rms >= cfg.voiceThreshold;

        // Real speech dips below the threshold between syllables. A short dip
        // (< dipToleranceMs) pauses the sustained-voice counter instead of
        // zeroing it; the end-of-turn silence counter is unaffected.
        const dipMs = isVoiced ? 0 : (tracking.dipMs || 0) + elapsedMs;

        const next = {
            voicedMs: isVoiced
                ? tracking.voicedMs + elapsedMs
                : (dipMs <= cfg.dipToleranceMs ? tracking.voicedMs : 0),
            silenceMs: isVoiced ? 0 : tracking.silenceMs + elapsedMs,
            hasTriggeredSpeech: tracking.hasTriggeredSpeech,
            dipMs,
        };

        let decision = "NONE";

        if (!next.hasTriggeredSpeech && next.voicedMs >= cfg.minVoicedMsToTrigger) {

            next.hasTriggeredSpeech = true;
            decision = "VOICE_DETECTED";

        } else if (next.hasTriggeredSpeech && next.silenceMs >= cfg.silenceMsToEndTurn) {

            decision = "SILENCE_DETECTED";
            next.hasTriggeredSpeech = false; // ready to detect the NEXT utterance fresh
            next.voicedMs = 0;
            next.silenceMs = 0;
        }

        return { tracking: next, decision };
    }

    // ============================================================
    // ADAPTIVE NOISE FLOOR. A fixed 0.02 threshold never sees "silence" in a noisy room / with a hissy mic, so the
    // turn only ended at the hard maximum. The floor follows the quietest recent level (drops fast, rises slowly),
    // and speech has to be clearly above it. Pure functions: callers feed synthetic timelines in tests.
    // ============================================================

    const NOISE_FLOOR_CONFIG = Object.freeze({
        riseMsPerDoubling: 4000,   // how slowly the floor is allowed to climb while it is being exceeded
        multiplier: 2.5,           // speech must be this many times the floor
        margin: 0.006,             // ... plus this much absolute RMS
        maxThreshold: 0.06,        // never demand shouting
        maxFloor: 0.022,           // a loud, continuous talker must not drag the floor up until nothing counts as voice
    });

    function updateNoiseFloor(floor, rms, elapsedMs, config) {

        const cfg = Object.assign({}, NOISE_FLOOR_CONFIG, config || {});

        if (floor === null || floor === undefined) return rms;      // first sample
        if (rms <= floor) return rms;                                  // quieter: adopt at once

        return Math.min(cfg.maxFloor, floor * Math.pow(2, elapsedMs / cfg.riseMsPerDoubling)); // louder: creep upwards slowly
    }

    function adaptiveVoiceThreshold(floor, baseThreshold, config) {

        const cfg = Object.assign({}, NOISE_FLOOR_CONFIG, config || {});
        const base = typeof baseThreshold === "number" ? baseThreshold : DEFAULT_VAD_CONFIG.voiceThreshold;

        if (floor === null || floor === undefined) return base;

        return Math.min(cfg.maxThreshold, Math.max(base, floor * cfg.multiplier + cfg.margin));
    }


    function initialVadTracking() {
        return { voicedMs: 0, silenceMs: 0, hasTriggeredSpeech: false, dipMs: 0 };
    }


    // ============================================================
    // UTTERANCE GUARDS (Task 4) -- lightweight, amplitude-only.
    // Decides whether a recorded utterance is worth sending and when
    // a recording must be force-stopped. Still pure: callers feed
    // synthetic timelines in tests.
    // ============================================================

    const DEFAULT_UTTERANCE_CONFIG = Object.freeze({
        minSpeechMs: 400,          // less total voiced time than this is a click/cough/noise: never sent
        maxUtteranceMs: 120000,    // hard stop for one answer (also bounds upload size and STT time)
        noSpeechTimeoutMs: 90000,  // nobody spoke for this long: stop recording, return to a waiting state (was 30s: candidates who think first were cut off)
    });

    // While the AI's own audio is playing, the mic can hear the speakers
    // (echo). Barge-in therefore needs LOUDER and LONGER voice than
    // ordinary end-of-turn detection before it is allowed to cut the AI off.
    const BARGE_IN_VAD_CONFIG = Object.freeze({
        voiceThreshold: 0.045,
        minVoicedMsToTrigger: 300,
        silenceMsToEndTurn: 1200,
        dipToleranceMs: 150,          // natural speech has syllable gaps: measured in a real browser, without this a 300 ms trigger took ~800 ms
    });

    function initialUtteranceStats() {
        return { elapsedMs: 0, voicedMs: 0 };
    }

    /**
     * One tick of utterance accounting. Returns the new stats and a
     * decision: "NONE" | "MAX_DURATION" | "NO_SPEECH_TIMEOUT".
     */
    function utteranceTick(stats, rms, elapsedMs, voiceThreshold, config) {

        const cfg = Object.assign({}, DEFAULT_UTTERANCE_CONFIG, config || {});
        const threshold = typeof voiceThreshold === "number" ? voiceThreshold : DEFAULT_VAD_CONFIG.voiceThreshold;

        const next = {
            elapsedMs: stats.elapsedMs + elapsedMs,
            voicedMs: stats.voicedMs + (rms >= threshold ? elapsedMs : 0),
        };

        let decision = "NONE";

        if (next.elapsedMs >= cfg.maxUtteranceMs) {
            decision = "MAX_DURATION";
        } else if (next.voicedMs < cfg.minSpeechMs && next.elapsedMs >= cfg.noSpeechTimeoutMs) {
            decision = "NO_SPEECH_TIMEOUT";
        }

        return { stats: next, decision };
    }

    /** Is this finished recording worth sending to the server? */
    function evaluateUtterance(stats, blobSize, config) {

        const cfg = Object.assign({}, DEFAULT_UTTERANCE_CONFIG, config || {});

        if (!blobSize) {
            return { send: false, reason: "empty" };
        }

        if (stats.voicedMs < cfg.minSpeechMs) {
            return { send: false, reason: "too_short" };
        }

        return { send: true, reason: null };
    }

    /** RMS (0..~0.5 for speech) -> 0..1 for a level meter. */
    function micLevel(rms) {
        if (!(rms > 0)) return 0;
        return Math.min(1, Math.sqrt(rms) * 1.6);
    }


    // ============================================================
    // STALE-RESPONSE / RACE-CONDITION GUARDS
    // ============================================================

    /**
     * Monotonic token check: a response is stale if the token it
     * was issued under no longer matches the currently-active
     * token. Covers every race in Step 9 — a candidate interrupts
     * while TTS is loading, a new speak() supersedes a pending one,
     * a transcribe resolves after the candidate already moved on.
     */
    function isStaleToken(responseToken, currentToken) {
        return responseToken !== currentToken;
    }

    /**
     * A transcript is only ever safe to submit once: non-empty
     * after trimming, and not already submitted under this exact
     * turn token. Centralizing this in one pure function is what
     * makes "duplicate submission prevention" and "empty transcript
     * rejection" independently testable instead of only observable
     * as an emergent property of async DOM code.
     */
    function shouldSubmitTranscript(transcript, turnToken, lastSubmittedTurnToken) {

        const trimmed = (transcript || "").trim();

        if (!trimmed) {
            return { submit: false, reason: "empty" };
        }

        if (turnToken === lastSubmittedTurnToken) {
            return { submit: false, reason: "duplicate" };
        }

        return { submit: true, reason: null };
    }


    // ============================================================
    // TASK 6: AI-AUDIO TRANSPORT MODE
    // ============================================================

    /**
     * "livekit" only when it was asked for AND the interview room is
     * connected right now; everything else is the local path. Decided
     * ONCE per turn, before the request, and never changed mid-turn --
     * so one response can only ever be delivered one way.
     */
    function chooseTransport(requested, roomConnected) {
        return requested === "livekit" && roomConnected === true ? "livekit" : "local";
    }

    /**
     * What to do with a server-audio data message given the seq the
     * browser is currently waiting for (null = nothing awaited yet).
     * "accept": it is for the awaited response. "buffer": no response
     * is awaited yet (the message can beat the HTTP reply). "stale":
     * it belongs to an older response and must be dropped.
     */
    function routeServerAudioMessage(messageSeq, awaitedSeq) {
        if (awaitedSeq === null || awaitedSeq === undefined) return "buffer";
        if (messageSeq === awaitedSeq) return "accept";
        return messageSeq < awaitedSeq ? "stale" : "buffer";
    }


    return {
        STATES,
        EVENTS,
        nextState,
        DEFAULT_VAD_CONFIG,
        DEFAULT_UTTERANCE_CONFIG,
        BARGE_IN_VAD_CONFIG,
        vadTick,
        initialVadTracking,
        updateNoiseFloor,
        adaptiveVoiceThreshold,
        NOISE_FLOOR_CONFIG,
        initialUtteranceStats,
        utteranceTick,
        evaluateUtterance,
        micLevel,
        isStaleToken,
        shouldSubmitTranscript,
        chooseTransport,
        routeServerAudioMessage,
    };
});

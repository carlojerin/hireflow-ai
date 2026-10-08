// ============================================================
// JOBPILOT AI - INTERVIEWER IMAGE VISUAL STATE (Task 8)
//
// The AI interviewer is a FIXED IMAGE (not a live avatar). This module only
// maps the EXISTING voice-turn controller state (InterviewVoiceV2 STATES) to a
// small visual treatment: a state label and a data-state attribute the CSS
// styles. It owns NO state machine: it never decides anything, never plays
// audio and never touches the interview. "Speaking" therefore appears exactly
// when the existing controller enters AI_SPEAKING, i.e. when real (Kokoro)
// audio actually starts, and disappears when that audio ends or is interrupted.
//
// Pure mapping + a tiny render helper that only sets attributes/text on the
// elements it is handed (unit-testable with fake elements).
// ============================================================

(function (root, factory) {

    const exportsObject = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObject;
    }

    if (root) {
        root.InterviewVisualStateV2 = exportsObject;
    }

})(typeof window !== "undefined" ? window : (typeof global !== "undefined" ? global : null), function () {

    // The one place the fixed image is named (see frontend/assets/interviewer/README.md).
    const INTERVIEWER_IMAGE_SRC = "./assets/interviewer/interviewer.svg";

    const VISUAL_KEYS = Object.freeze({
        IDLE: "idle",
        PREPARING: "preparing",
        SPEAKING: "speaking",
        LISTENING: "listening",
        CANDIDATE_SPEAKING: "candidate-speaking",
        THINKING: "thinking",
        ERROR: "error",
        COMPLETED: "completed",
    });

    // voice controller state name -> visual state
    const VIEW_BY_STATE = Object.freeze({
        IDLE: { key: VISUAL_KEYS.IDLE, label: "Ready" },
        REQUESTING_MIC: { key: VISUAL_KEYS.IDLE, label: "Getting ready" },
        AI_PREPARING: { key: VISUAL_KEYS.PREPARING, label: "Preparing" },
        AI_SPEAKING: { key: VISUAL_KEYS.SPEAKING, label: "Speaking" },
        AI_INTERRUPTED: { key: VISUAL_KEYS.LISTENING, label: "Listening" },
        CANDIDATE_LISTENING: { key: VISUAL_KEYS.LISTENING, label: "Listening" },
        CANDIDATE_SPEAKING: { key: VISUAL_KEYS.CANDIDATE_SPEAKING, label: "Listening to you" },
        CANDIDATE_SILENCE: { key: VISUAL_KEYS.THINKING, label: "Thinking" },
        TRANSCRIBING: { key: VISUAL_KEYS.THINKING, label: "Thinking" },
        SUBMITTING: { key: VISUAL_KEYS.THINKING, label: "Thinking" },
        AI_THINKING: { key: VISUAL_KEYS.THINKING, label: "Thinking" },
        COMPLETED: { key: VISUAL_KEYS.COMPLETED, label: "Interview complete" },
        ERROR: { key: VISUAL_KEYS.ERROR, label: "Something went wrong" },
    });

    function viewFor(voiceState) {
        return VIEW_BY_STATE[voiceState] || VIEW_BY_STATE.IDLE;
    }

    /**
     * Applies the visual state to the elements it is given (all optional):
     *   frame          the interviewer frame (gets data-state)
     *   stateText      element whose text is the state label
     *   statusText     the room status-bar text ("AI Interviewer · Speaking")
     *   candidateFrame the candidate camera frame (gets .is-speaking while they speak)
     *   image          the <img>; its src is set once to the fixed image
     * Returns the view that was applied.
     */
    function render(voiceState, els) {

        const view = viewFor(voiceState);
        const target = els || {};

        if (target.frame && target.frame.setAttribute) target.frame.setAttribute("data-state", view.key);
        if (target.stateText) target.stateText.textContent = view.label;
        if (target.statusText) target.statusText.textContent = `AI Interviewer · ${view.label}`;

        if (target.candidateFrame && target.candidateFrame.classList) {
            target.candidateFrame.classList.toggle("is-speaking", view.key === VISUAL_KEYS.CANDIDATE_SPEAKING);
        }

        if (target.image && target.image.getAttribute && target.image.getAttribute("src") !== INTERVIEWER_IMAGE_SRC) {
            target.image.setAttribute("src", INTERVIEWER_IMAGE_SRC);
        }

        return view;
    }

    return {
        INTERVIEWER_IMAGE_SRC,
        VISUAL_KEYS,
        VIEW_BY_STATE,
        viewFor,
        render,
    };
});

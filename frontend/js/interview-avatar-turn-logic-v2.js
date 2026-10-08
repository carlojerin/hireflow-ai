// ============================================================
// JOBPILOT AI - AVATAR EVENT -> VISUAL STATE MAPPING (Task 5K)
//
// Pure, dependency-free, same UMD-lite pattern as
// interview-voice-turn-logic-v2.js (Task 5J). Maps the EXISTING
// Task 5J voice-layer events onto avatar UI states -- contains NO
// LiveKit/WebRTC/DOM code and NO interview decision logic. This is
// what makes "the avatar follows the real voice state, never fakes
// continued speaking" (Task 5K Step 11/12) a testable property
// instead of only an emergent one.
// ============================================================

(function (root, factory) {

    const exportsObject = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObject;
    }

    if (root) {
        root.InterviewAvatarTurnLogicV2 = exportsObject;
    }

})(typeof window !== "undefined" ? window : (typeof global !== "undefined" ? global : null), function () {

    const AVATAR_STATES = Object.freeze({
        AVATAR_CONNECTING: "AVATAR_CONNECTING",
        AVATAR_CONNECTED: "AVATAR_CONNECTED",
        AVATAR_UNAVAILABLE: "AVATAR_UNAVAILABLE", // not configured / config error -- honest, never fake video
        AVATAR_ERROR: "AVATAR_ERROR",             // configured but connection failed
        AI_SPEAKING: "AI_SPEAKING",
        AI_IDLE: "AI_IDLE",
        AI_INTERRUPTED: "AI_INTERRUPTED",
        COMPLETED: "COMPLETED",
    });

    // Voice-layer event name -> resulting avatar state, GIVEN the
    // avatar is currently connected. A disconnected/unavailable
    // avatar ignores speaking/idle events entirely (see
    // nextAvatarState) -- there is nothing for it to visually
    // reflect if there is no real video track.
    const CONNECTED_EVENT_TO_STATE = Object.freeze({
        AI_STARTED_SPEAKING: AVATAR_STATES.AI_SPEAKING,
        AI_STOPPED_SPEAKING: AVATAR_STATES.AI_IDLE,
        AI_INTERRUPTED: AVATAR_STATES.AI_INTERRUPTED,
        CANDIDATE_STARTED_SPEAKING: AVATAR_STATES.AI_IDLE, // avatar listens, never talks over the candidate
        INTERVIEW_COMPLETED: AVATAR_STATES.COMPLETED,
    });

    /**
     * Pure decision: given the avatar's current connection state and
     * an incoming voice-layer event name, what should the avatar's
     * visual state become? Returns `null` when the event should be
     * ignored (e.g. a speaking event arriving before the avatar
     * finished connecting, or any event after COMPLETED -- a
     * terminal state, matching interview-voice-turn-logic-v2.js's
     * own COMPLETED handling).
     */
    function nextAvatarState(currentAvatarState, eventName) {

        if (currentAvatarState === AVATAR_STATES.COMPLETED) {
            return null; // terminal -- cleanup already happened, never re-armed by a late event
        }

        if (eventName === "INTERVIEW_COMPLETED") {
            return AVATAR_STATES.COMPLETED; // always honored, even mid-connect
        }

        const isConnected = currentAvatarState === AVATAR_STATES.AI_SPEAKING
            || currentAvatarState === AVATAR_STATES.AI_IDLE
            || currentAvatarState === AVATAR_STATES.AI_INTERRUPTED
            || currentAvatarState === AVATAR_STATES.AVATAR_CONNECTED;

        if (!isConnected) {
            return null; // no real video track to reflect speaking/idle on yet
        }

        const mapped = CONNECTED_EVENT_TO_STATE[eventName];

        return mapped === undefined ? null : mapped;
    }

    /**
     * A stale event guard identical in spirit to Task 5J's
     * isStaleToken(): if the interview has already reached
     * COMPLETED (or the avatar session's own connectionToken no
     * longer matches), any event still in flight from a superseded
     * connection must be ignored rather than resurrecting a dead
     * avatar session.
     */
    function isStaleAvatarEvent(eventToken, currentToken) {
        return eventToken !== currentToken;
    }


    return {
        AVATAR_STATES,
        nextAvatarState,
        isStaleAvatarEvent,
    };
});

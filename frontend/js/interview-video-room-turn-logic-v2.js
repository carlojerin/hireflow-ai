// ============================================================
// JOBPILOT AI - VIDEO ROOM PURE LOGIC (Task 5L)
//
// Same UMD-lite, DOM-free pattern as interview-voice-turn-logic-v2.js
// (Task 5J) and interview-avatar-turn-logic-v2.js (Task 5K). Contains
// NO LiveKit/WebRTC/DOM code and NO interview decision logic -- only
// answers: "given what just happened, what should the room's visual
// state become, and is it safe to publish the candidate's tracks yet."
// ============================================================

(function (root, factory) {

    const exportsObject = factory();

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObject;
    }

    if (root) {
        root.InterviewVideoRoomTurnLogicV2 = exportsObject;
    }

})(typeof window !== "undefined" ? window : (typeof global !== "undefined" ? global : null), function () {

    const ROOM_STATES = Object.freeze({
        IDLE: "IDLE",
        CONNECTING: "CONNECTING",
        CONNECTED: "CONNECTED",
        RECONNECTING: "RECONNECTING",
        DISCONNECTED: "DISCONNECTED",
        ERROR: "ERROR",
        COMPLETED: "COMPLETED",
    });

    const TRANSITIONS = {
        [ROOM_STATES.IDLE]: { CONNECT_STARTED: ROOM_STATES.CONNECTING },
        [ROOM_STATES.CONNECTING]: {
            CONNECT_SUCCEEDED: ROOM_STATES.CONNECTED,
            CONNECT_FAILED: ROOM_STATES.ERROR,
        },
        [ROOM_STATES.CONNECTED]: {
            RECONNECTING: ROOM_STATES.RECONNECTING,
            DISCONNECTED: ROOM_STATES.DISCONNECTED,
        },
        [ROOM_STATES.RECONNECTING]: {
            RECONNECTED: ROOM_STATES.CONNECTED,
            DISCONNECTED: ROOM_STATES.DISCONNECTED,
        },
        [ROOM_STATES.DISCONNECTED]: {
            CONNECT_STARTED: ROOM_STATES.CONNECTING,
        },
        [ROOM_STATES.ERROR]: {
            CONNECT_STARTED: ROOM_STATES.CONNECTING,
        },
        [ROOM_STATES.COMPLETED]: {},
    };

    function nextRoomState(currentState, roomEvent) {

        if (currentState === ROOM_STATES.COMPLETED) {
            return ROOM_STATES.COMPLETED; // terminal, matches the other Task 5J/5K state machines
        }

        if (roomEvent === "INTERVIEW_COMPLETED") {
            return ROOM_STATES.COMPLETED; // always honored immediately, from any state
        }

        const stateTransitions = TRANSITIONS[currentState];

        if (!stateTransitions || !(roomEvent in stateTransitions)) {
            return currentState; // no-op: unrecognized/late event, never throws
        }

        return stateTransitions[roomEvent];
    }


    /**
     * Candidate tracks are safe to publish only once ALL THREE are
     * true: the LiveKit room is connected, a camera stream already
     * exists (reused from assessment-integrity.js -- Task 5L never
     * calls getUserMedia a second time), and a microphone stream
     * already exists (reused from interview-voice-v2.js). Pure
     * decision, no timers/promises -- the caller re-evaluates this
     * every time one of the three readiness flags changes.
     */
    function canPublishCandidateTracks(readiness) {
        return !!(readiness.roomConnected && readiness.cameraReady && readiness.micReady);
    }


    /**
     * Camera/mic toggle: pure state transformation, given the
     * candidate's current enabled/disabled flags and which control
     * was pressed. Returns the new {cameraEnabled, micEnabled} pair
     * -- never mutates anything itself.
     */
    function toggleControl(currentControls, control) {

        if (control === "camera") {
            return { ...currentControls, cameraEnabled: !currentControls.cameraEnabled };
        }

        if (control === "mic") {
            return { ...currentControls, micEnabled: !currentControls.micEnabled };
        }

        return currentControls;
    }


    function isStaleRoomEvent(eventToken, currentToken) {
        return eventToken !== currentToken;
    }


    return {
        ROOM_STATES,
        nextRoomState,
        canPublishCandidateTracks,
        toggleControl,
        isStaleRoomEvent,
    };
});

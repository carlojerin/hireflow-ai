// ============================================================
// JOBPILOT AI - CANDIDATE VIDEO ROOM CONTROLLER (Task 5L)
//
// Publishes the candidate's ALREADY-CAPTURED camera and microphone
// tracks onto the SAME LiveKit room Task 5K's avatar controller
// already connects (via InterviewAvatarV2.getRoom()) -- this module
// never calls Room.connect() itself and never creates a second
// LiveKit connection.
//
// Camera reuse: the video track comes from window.IntegrityMode
// .getCameraStream() (assessment-integrity.js, unmodified) -- the
// SAME stream already used for proctoring and for
// AssessmentVideoRecorder. Task 5L does NOT call getUserMedia() for
// video a second time.
//
// Microphone reuse: the audio track comes from
// window.InterviewVoiceV2's persistent mic stream (Task 5I/5J,
// unmodified) -- the SAME stream already used for VAD/STT. Task 5L
// does NOT call getUserMedia() for audio a second time.
//
// Publishing uses LiveKit's `localParticipant.publishTrack(track,
// options)`, which accepts a raw MediaStreamTrack directly (current
// LiveKit JS client API, verified during this task's audit) -- no
// track-wrapping/cloning needed.
//
// This module NEVER imports or calls the interview brain, NEVER
// decides questions/competencies, and NEVER modifies
// AIInterviewSessionV2/AIInterviewTurnV2. It only publishes/mutes
// media and reports connection-health state.
// ============================================================

window.InterviewVideoRoomV2 = (function () {

    const Logic = window.InterviewVideoRoomTurnLogicV2;
    const STATES = Logic.ROOM_STATES;

    let onStateChange = null;
    let onError = null;

    let currentState = STATES.IDLE;
    let roomToken = 0;

    let cameraPublication = null;
    let micPublication = null;

    let readiness = { roomConnected: false, cameraReady: false, micReady: false };
    let controls = { cameraEnabled: true, micEnabled: true };

    let publishAttempted = false;


    function setState(next) {
        currentState = next;
        if (typeof onStateChange === "function") onStateChange(next, controls);
    }


    function init(config) {
        onStateChange = config.onStateChange || null;
        onError = config.onError || null;
    }


    // Called by ai-interview-v2.js's avatar onRoomEvent wiring (Task
    // 5K's InterviewAvatarV2.init({ onRoomEvent })) -- translates raw
    // LiveKit connection-health events into this module's own room
    // state machine, entirely separate from avatar presentation
    // state (AI_SPEAKING/AI_IDLE etc, which stays in Task 5K).
    function handleRoomEvent(eventName) {

        const myToken = roomToken;

        const next = Logic.nextRoomState(currentState, eventName);

        if (Logic.isStaleRoomEvent(myToken, roomToken)) return;

        setState(next);

        if (eventName === "CONNECT_SUCCEEDED") {
            readiness.roomConnected = true;
            _maybePublish();
        }

        if (eventName === "DISCONNECTED") {
            readiness.roomConnected = false;

            // The old connection is gone (LiveKit unpublished everything), but the
            // underlying camera/mic tracks are shared with proctoring/VAD and must
            // stay LIVE. Forget the dead publications so a reconnect republishes.
            cameraPublication = null;
            micPublication = null;
            publishAttempted = false;
        }
    }


    // Called once the candidate's camera stream exists (from
    // window.IntegrityMode.getCameraStream(), already started by
    // ai-interview-v2.js's activateStrictModeForInterviewV2()).
    function notifyCameraReady(cameraStream) {

        if (!cameraStream || currentState === STATES.COMPLETED) return; // Task 5M: a delayed readiness signal after cleanup must never resurrect publishing

        _cameraStream = cameraStream;
        readiness.cameraReady = true;
        _maybePublish();
    }


    // Called once the candidate's microphone stream exists (from
    // window.InterviewVoiceV2.ensureMicStream(), already the Task
    // 5I/5J persistent stream).
    function notifyMicReady(micStream) {

        if (!micStream || currentState === STATES.COMPLETED) return; // Task 5M: same guard as notifyCameraReady

        _micStream = micStream;
        readiness.micReady = true;
        _maybePublish();
    }


    let _cameraStream = null;
    let _micStream = null;


    // Task 9: the camera track died and the camera was reacquired: swap the PUBLISHED track for the new one
    // (never a second publication, never a second camera). Without a live publication it just records the
    // new stream so the normal publish picks it up.
    async function replaceCameraStream(newStream) {

        if (!newStream || currentState === STATES.COMPLETED) return false;

        const oldPublication = cameraPublication;
        _cameraStream = newStream;
        readiness.cameraReady = true;

        const room = window.InterviewAvatarV2 && window.InterviewAvatarV2.getRoom();
        const newTrack = newStream.getVideoTracks()[0];

        if (!oldPublication || !room || !newTrack) {
            _maybePublish();
            return true;
        }

        try {
            try { await room.localParticipant.unpublishTrack(oldPublication.track || oldPublication, false); } catch (error) { /* already gone */ }
            cameraPublication = await room.localParticipant.publishTrack(newTrack, {
                name: "candidate-camera",
                source: window.LivekitClient.Track.Source.Camera,
            });
            return true;
        } catch (error) {
            cameraPublication = null;
            if (typeof onError === "function") onError("publish_failed", "Could not re-share your camera with the interview room.");
            return false;
        }
    }


    async function _maybePublish() {

        if (currentState === STATES.COMPLETED) return; // defensive: cleanup already ran
        if (publishAttempted) return; // publish exactly once per interview -- never republish on every readiness tick
        if (!Logic.canPublishCandidateTracks(readiness)) return;

        const room = window.InterviewAvatarV2 && window.InterviewAvatarV2.getRoom();
        if (!room) return; // room not actually connected yet, despite readiness.roomConnected (defensive)

        publishAttempted = true;

        try {

            const Track = window.LivekitClient.Track;

            const videoTrack = _cameraStream.getVideoTracks()[0];
            const audioTrack = _micStream.getAudioTracks()[0];

            if (videoTrack) {
                cameraPublication = await room.localParticipant.publishTrack(videoTrack, {
                    name: "candidate-camera",
                    source: Track.Source.Camera,
                });
            }

            if (audioTrack) {
                micPublication = await room.localParticipant.publishTrack(audioTrack, {
                    name: "candidate-microphone",
                    source: Track.Source.Microphone,
                });
            }

        } catch (error) {
            publishAttempted = false; // allow a future readiness change to retry
            if (typeof onError === "function") {
                onError("publish_failed", "Could not share your camera/microphone with the interview room.");
            }
        }
    }


    // Mutes/unmutes the PUBLISHED room copy of the track. Because
    // Task 5L reuses the SAME underlying MediaStreamTrack that
    // proctoring/recording also read from, muting the room
    // publication here also disables the physical camera/mic feed --
    // an intentional, honest choice (Task 5L Step 11/13: never show
    // "camera on" while actually withholding video from the room).
    function setControlEnabled(control, enabled) {

        controls = { ...controls, [`${control}Enabled`]: enabled };

        const publication = control === "camera" ? cameraPublication : control === "mic" ? micPublication : null;

        if (publication && typeof publication.setMuted === "function") {
            publication.setMuted(!enabled);
        }

        if (typeof onStateChange === "function") onStateChange(currentState, controls);
    }


    function getControls() {
        return { ...controls };
    }


    function cleanup() {

        roomToken++; // any in-flight handleRoomEvent for the old token becomes stale

        if (cameraPublication) {
            try { cameraPublication.track && cameraPublication.track.stop(); } catch (error) { /* ignore */ }
            cameraPublication = null;
        }

        if (micPublication) {
            try { micPublication.track && micPublication.track.stop(); } catch (error) { /* ignore */ }
            micPublication = null;
        }

        readiness = { roomConnected: false, cameraReady: false, micReady: false };
        publishAttempted = false;
        _cameraStream = null;
        _micStream = null;

        setState(STATES.COMPLETED);
    }


    return {
        STATES,
        init,
        handleRoomEvent,
        notifyCameraReady,
        replaceCameraStream,
        notifyMicReady,
        setControlEnabled,
        getControls,
        cleanup,
    };

})();

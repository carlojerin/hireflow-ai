// ============================================================
// JOBPILOT AI - AI INTERVIEWER AVATAR CONTROLLER (Task 5K)
//
// Presentation layer ONLY. Never imports/calls the interview brain.
// Consumes the EXISTING, unmodified Task 5J voice-layer events
// (AI_STARTED_SPEAKING, AI_AUDIO_AVAILABLE, AI_STOPPED_SPEAKING,
// AI_INTERRUPTED, CANDIDATE_STARTED_SPEAKING, INTERVIEW_COMPLETED)
// via a small wiring shim in ai-interview-v2.js -- this module does
// not modify interview-voice-v2.js at all.
//
// LiveKit client (loaded from CDN in ai-interview-v2.html, only
// when this controller actually needs it) is the transport for the
// avatar's video/audio track, matching current LiveKit Agents
// avatar architecture: an avatar provider (Tavus, in this project's
// backend reference worker -- avatar_worker_v2.py, NOT started by
// this web app) publishes synchronized audio+video into a LiveKit
// room; this controller only ever SUBSCRIBES to that room as the
// candidate participant. It never publishes the candidate's own
// camera/mic here -- that is explicitly Task 5L's scope.
//
// HONESTY REQUIREMENT (Task 5K Step 14): if no avatar provider is
// configured (this project's current, real state -- no
// LiveKit/Tavus credentials exist in this environment) or the
// connection fails, this controller shows a clear, honest
// "AI interviewer video is temporarily unavailable" state. It NEVER
// fabricates avatar video or pretends a connection succeeded.
// ============================================================

window.InterviewAvatarV2 = (function () {

    const Logic = window.InterviewAvatarTurnLogicV2;
    const STATES = Logic.AVATAR_STATES;

    let apiBaseUrl = "";
    let applicationId = null;
    let onStateChange = null;
    let onRoomEvent = null; // Task 5L: raw room connection-health events (see connect())
    let onSpeechMessage = null; // Task 5N-R: worker 'jobpilot.speech' data messages
    let onAvatarStatus = null;  // Task 7: (state, error) of the SERVER-side Tavus avatar
    let avatarVideoSubscribed = false;
    let avatarAudioSubscribed = false;
    let avatarVideoElement = null;

    let currentState = STATES.AVATAR_CONNECTING;
    let connectionToken = 0;

    let livekitRoom = null;

    // Task 5: bounded automatic reconnect after the LiveKit SDK itself has
    // given up (transient drops are retried inside the SDK and only surface
    // as Reconnecting/Reconnected). Attempts are cancelled by cleanup().
    const RECONNECT_DELAYS_MS = [2000, 5000, 10000];
    let reconnectAttempts = 0;
    let reconnectTimer = null;

    // Task 6: the AI interviewer's audio (the server publishes Kokoro speech
    // into this room). ONE <audio> element per remote participant identity:
    // a new subscription for the same participant REPLACES the old element
    // (a duplicate track can never play twice), and an unsubscribe only
    // removes the element of the track it belongs to (a stale unsubscribe
    // can never kill the current track).
    let roomConnected = false;
    let remoteAudioMuted = false;
    const remoteAudio = new Map(); // identity -> { el, track, sid }

    function isConnected() {
        return roomConnected && !!livekitRoom;
    }

    function _announceMedia() {
        try {
            window.dispatchEvent(new CustomEvent("jobpilot-avatar-media", {
                detail: { video: avatarVideoSubscribed, audio: avatarAudioSubscribed },
            }));
        } catch (e) { /* not in a browser */ }
    }

    function setRemoteAudioMuted(muted) {
        remoteAudioMuted = !!muted;
        remoteAudio.forEach((entry) => { entry.el.muted = remoteAudioMuted; });
    }

    function _removeRemoteAudio(identity, sid) {
        const entry = remoteAudio.get(identity);
        if (!entry) return;
        if (sid && entry.sid !== sid) return; // an OLDER track's unsubscribe: the current one keeps playing
        try { entry.track.detach(entry.el); } catch (e) { /* already detached */ }
        entry.el.remove();
        remoteAudio.delete(identity);
        avatarAudioSubscribed = remoteAudio.size > 0;
    }

    function _removeAllRemoteAudio() {
        Array.from(remoteAudio.keys()).forEach((identity) => _removeRemoteAudio(identity));
        document.querySelectorAll("[data-jobpilot-avatar-audio]").forEach((el) => el.remove());
        avatarAudioSubscribed = false;
    }

    function _attachRemoteAudio(track, participant) {
        const identity = (participant && participant.identity) || "unknown";
        const existing = remoteAudio.get(identity);
        if (existing && existing.sid === track.sid) return; // the same track announced twice
        if (existing) _removeRemoteAudio(identity);         // replaced by a new track: never two elements
        const el = track.attach();
        el.setAttribute("data-jobpilot-avatar-audio", "1");
        el.muted = remoteAudioMuted;
        document.body.appendChild(el);
        remoteAudio.set(identity, { el, track, sid: track.sid });
        avatarAudioSubscribed = true;

        // the interview recording mixes the interviewer's voice in (see ai-interview-v2.js buildRecordingAudioMix)
        try {
            if (track.mediaStreamTrack) {
                window.dispatchEvent(new CustomEvent("jobpilot-ai-audio-track", { detail: { track: track.mediaStreamTrack } }));
            }
        } catch (e) { /* not in a browser */ }
    }

    function getRemoteAudioTracks() {
        const tracks = [];
        remoteAudio.forEach((entry) => { if (entry.track && entry.track.mediaStreamTrack) tracks.push(entry.track.mediaStreamTrack); });
        return tracks;
    }


    function setState(next) {
        if (next === null) return; // Logic said "ignore this event", nothing to render
        currentState = next;
        if (typeof onStateChange === "function") onStateChange(next);
    }


    let apiPath = "interview/v2/avatar";

    function init(config) {
        apiBaseUrl = config.apiBaseUrl || "";
        applicationId = config.applicationId;
        onStateChange = config.onStateChange || null;
        onRoomEvent = config.onRoomEvent || null;
        onSpeechMessage = config.onSpeechMessage || null;
        onAvatarStatus = config.onAvatarStatus || null;
        apiPath = config.apiPath || "interview/v2/avatar";          // the role-play call uses "roleplay/v2/avatar"
    }


    // ------------------------------------------------------------
    // Task 7: the Tavus avatar is started and driven by the SERVER (it shows
    // the same Kokoro response the voice flow already produces). The browser
    // only asks for it to be attached to this interview's room (idempotent) and
    // follows its status honestly. If it cannot start, nothing else changes:
    // the voice interview keeps working.
    // ------------------------------------------------------------
    const AVATAR_POLL_MS = 1500;
    const AVATAR_POLL_LIMIT_MS = 75000;
    let avatarPollTimer = null;
    let avatarStartToken = 0;

    function _reportAvatarStatus(state, error) {
        if (typeof onAvatarStatus === "function") {
            try { onAvatarStatus(state, error || null); } catch (e) { /* ignore */ }
        }
    }

    function _cancelAvatarPoll() {
        avatarStartToken++;
        if (avatarPollTimer) { clearTimeout(avatarPollTimer); avatarPollTimer = null; }
    }

    async function _startServerAvatar(connectionAtStart) {

        _cancelAvatarPoll();
        const myStart = avatarStartToken;
        const stale = () => myStart !== avatarStartToken || Logic.isStaleAvatarEvent(connectionAtStart, connectionToken);
        const base = `${apiBaseUrl}/api/assessment/sessions/${applicationId}/${apiPath}`;

        let body = null;

        try {
            const response = await fetch(`${base}/start`, { method: "POST", credentials: "include" });
            if (stale()) return;
            if (!response.ok) { _reportAvatarStatus("unavailable", response.status === 503 ? "not configured" : "refused"); return; }
            body = await response.json();
        } catch (error) {
            if (!stale()) _reportAvatarStatus("failed", "network");
            return;
        }

        if (body && body.state === "disabled") {            // Task 8: fixed-image mode -- no live avatar, nothing to follow
            _reportAvatarStatus("disabled", null);
            return;
        }

        const startedAt = Date.now();

        const poll = async () => {
            avatarPollTimer = null;
            if (stale()) return;
            _reportAvatarStatus(body.state, body.error);
            if (body.state === "ready" || body.state === "failed") return;
            if (Date.now() - startedAt > AVATAR_POLL_LIMIT_MS) { _reportAvatarStatus("failed", "timeout"); return; }
            avatarPollTimer = setTimeout(async () => {
                avatarPollTimer = null;
                try {
                    const r = await fetch(`${base}/status`, { credentials: "include" });
                    if (stale()) return;
                    if (r.ok) body = await r.json();
                } catch (e) { /* keep polling until the limit */ }
                poll();
            }, AVATAR_POLL_MS);
        };

        poll();
    }


    // Task 5L: exposes the SAME Room connection this controller
    // already established, so the video-room layer can publish the
    // candidate's camera/mic onto it instead of opening a second
    // LiveKit connection (Task 5L Step 3/16: "do not create duplicate
    // room participants"). Returns null before connect() succeeds.
    function getRoom() {
        return livekitRoom;
    }


    function _cancelReconnect() {
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    }


    function _scheduleReconnect(myToken) {

        if (reconnectAttempts >= RECONNECT_DELAYS_MS.length) return; // give up: the local voice pipeline keeps working

        const delay = RECONNECT_DELAYS_MS[reconnectAttempts++];

        _cancelReconnect();

        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (Logic.isStaleAvatarEvent(myToken, connectionToken)) return; // cleanup()/a newer connect() superseded this
            connect(true);
        }, delay);
    }


    async function connect(isRetry) {

        const myToken = ++connectionToken;

        _cancelReconnect();
        if (!isRetry) reconnectAttempts = 0;

        const fireRoomEventEarly = (name) => {
            if (!Logic.isStaleAvatarEvent(myToken, connectionToken) && typeof onRoomEvent === "function") {
                onRoomEvent(name);
            }
        };

        setState(STATES.AVATAR_CONNECTING);
        fireRoomEventEarly("CONNECT_STARTED");

        let tokenResponse;

        try {

            const response = await fetch(
                `${apiBaseUrl}/api/assessment/sessions/${applicationId}/${apiPath}/token`,
                { method: "POST", credentials: "include" },
            );

            if (Logic.isStaleAvatarEvent(myToken, connectionToken)) return;

            if (response.status === 503) {
                // Honest, expected state in this environment: no avatar
                // provider is configured. NOT an error -- the interview
                // continues perfectly well through Task 5H/5I/5J's
                // existing text/voice UI regardless.
                setState(STATES.AVATAR_UNAVAILABLE);
                fireRoomEventEarly("CONNECT_FAILED");
                return;
            }

            if (!response.ok) {
                setState(STATES.AVATAR_ERROR);
                fireRoomEventEarly("CONNECT_FAILED");
                return;
            }

            tokenResponse = await response.json();

        } catch (error) {
            if (!Logic.isStaleAvatarEvent(myToken, connectionToken)) {
                setState(STATES.AVATAR_ERROR);
                fireRoomEventEarly("CONNECT_FAILED");
            }
            return;
        }

        if (Logic.isStaleAvatarEvent(myToken, connectionToken)) return;

        if (typeof window.LivekitClient === "undefined") {
            // The LiveKit client SDK script (loaded from CDN, see
            // ai-interview-v2.html) failed to load or was never added
            // -- fail honestly rather than pretending to connect.
            setState(STATES.AVATAR_ERROR);
            fireRoomEventEarly("CONNECT_FAILED");
            return;
        }

        // A previous (finished) room object must never linger: one live Room at a time.
        if (livekitRoom) {
            try { livekitRoom.disconnect(); } catch (error) { /* already closed */ }
            livekitRoom = null;
        }

        // Local reference: a stale attempt must only ever touch ITS OWN room,
        // never the newer attempt's (livekitRoom may already point at that).
        const room = new window.LivekitClient.Room();
        livekitRoom = room;

        try {

            // LiveKit's Room already retries transient network loss
            // internally; Disconnected only fires once that retry
            // genuinely gives up (or the server ends the room), so
            // this remains a real, final failure -- not a blip.
            room.on(window.LivekitClient.RoomEvent.Disconnected, () => {
                if (livekitRoom === room) { roomConnected = false; _removeAllRemoteAudio(); }
                fireRoomEventEarly("DISCONNECTED");
                if (!Logic.isStaleAvatarEvent(myToken, connectionToken)) {
                    setState(STATES.AVATAR_ERROR);
                    _scheduleReconnect(myToken);
                }
            });

            // Task 5L Step 16: surfaced so the video-room UI can show
            // "Reconnecting..." instead of silently doing nothing --
            // the interview brain/session is never touched by either
            // of these, only the room's own transport health.
            room.on(window.LivekitClient.RoomEvent.Reconnecting, () => {
                if (livekitRoom === room) roomConnected = false;      // not usable for audio delivery while reconnecting
                fireRoomEventEarly("RECONNECTING");
            });
            // LiveKit reports a signal-level (websocket) reconnect with its own event
            room.on(window.LivekitClient.RoomEvent.SignalReconnecting, () => {
                if (livekitRoom === room) roomConnected = false;
                fireRoomEventEarly("RECONNECTING");
            });
            room.on(window.LivekitClient.RoomEvent.Reconnected, () => {
                if (livekitRoom === room) roomConnected = true;
                fireRoomEventEarly("RECONNECTED");
            });

            // Task 6: the AI interviewer's audio + turn-boundary messages. Registered
            // per room, BEFORE connecting, so nothing subscribed early is missed and a
            // reconnect (new Room object) is handled the same way. A stale room's
            // events are ignored.
            room.on(window.LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
                if (livekitRoom !== room || track.kind !== "audio") return;
                _attachRemoteAudio(track, participant);
                _announceMedia();
            });
            room.on(window.LivekitClient.RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
                if (livekitRoom !== room || track.kind !== "audio") return;
                _removeRemoteAudio((participant && participant.identity) || "unknown", track.sid);
                _announceMedia();
            });
            room.on(window.LivekitClient.RoomEvent.ParticipantDisconnected, (participant) => {
                if (livekitRoom !== room) return;
                _removeRemoteAudio((participant && participant.identity) || "unknown");
                _announceMedia();
            });
            room.on(window.LivekitClient.RoomEvent.DataReceived, (payload, participant, kind, topic) => {
                if (livekitRoom !== room || topic !== "jobpilot.speech" || typeof onSpeechMessage !== "function") return;
                try {
                    const msg = JSON.parse(new TextDecoder().decode(payload));
                    if (msg && (msg.channel === "server-audio" || msg.channel === "realtime")) onSpeechMessage(msg);
                } catch (e) { /* ignore malformed */ }
            });

            await room.connect(tokenResponse.url, tokenResponse.token);

            if (Logic.isStaleAvatarEvent(myToken, connectionToken)) {
                room.disconnect();                            // ITS OWN room, not the newer attempt's
                if (livekitRoom === room) livekitRoom = null;
                return;
            }

            reconnectAttempts = 0;                            // a healthy connection resets the retry budget
            roomConnected = true;
            _startServerAvatar(myToken);                      // Task 7: idempotent on the server, a fresh avatar after a reconnect
            fireRoomEventEarly("CONNECT_SUCCEEDED");
            setState(STATES.AVATAR_CONNECTED);

        } catch (error) {

            if (livekitRoom === room && Logic.isStaleAvatarEvent(myToken, connectionToken)) livekitRoom = null;

            fireRoomEventEarly("CONNECT_FAILED");

            if (!Logic.isStaleAvatarEvent(myToken, connectionToken)) {
                setState(STATES.AVATAR_ERROR);
                if (isRetry) _scheduleReconnect(myToken);     // a retry that failed keeps walking the backoff
            }
        }
    }


    // Attach the LiveKit room's remote video track (published by the
    // avatar worker, per avatar_worker_v2.py's documented
    // architecture) to a real <video> element, once one becomes
    // available. No-op, never throws, if no such track exists yet
    // (e.g. avatar not configured in this environment) -- the caller
    // (ai-interview-v2.js) is responsible for showing the honest
    // fallback UI in that case.
    let videoListenerAttachedForRoom = null; // prevents a duplicate listener if attachVideo() is ever called more than once for the same room

    function attachVideo(videoElement) {

        if (!livekitRoom || !videoElement) return;
        if (videoListenerAttachedForRoom === livekitRoom) return; // already wired for this exact room instance

        videoListenerAttachedForRoom = livekitRoom;
        avatarVideoElement = videoElement;

        const Ev = window.LivekitClient.RoomEvent;

        livekitRoom.on(Ev.TrackSubscribed, (track) => {
            if (track.kind === "video") {
                track.attach(videoElement);
                avatarVideoSubscribed = true;
            }
            // (audio tracks are attached per room in connect(): one element per participant)
        });

        livekitRoom.on(Ev.TrackUnsubscribed, (track) => {
            if (track.kind === "video") avatarVideoSubscribed = false;
        });

        livekitRoom.on(Ev.DataReceived, (payload, participant, kind, topic) => {
            if (topic !== "jobpilot.speech" || typeof onSpeechMessage !== "function") return;
            try {
                const msg = JSON.parse(new TextDecoder().decode(payload));
                if (msg && (msg.channel === "server-audio" || msg.channel === "realtime")) return; // the per-room handler in connect() owns these
                onSpeechMessage(msg);
            } catch (e) { /* ignore malformed */ }
        });

        // Browsers block remote audio until a user gesture; retry on the
        // first click/keypress so the avatar can actually be heard.
        const announce = _announceMedia;
        livekitRoom.on(Ev.TrackSubscribed, announce);
        livekitRoom.on(Ev.TrackUnsubscribed, announce);

        // Tracks that were already flowing before this listener existed (e.g. page refresh/rejoin)
        livekitRoom.remoteParticipants.forEach((p) => {
            p.trackPublications.forEach((pub) => {
                if (pub.isSubscribed && pub.track) {
                    if (pub.track.kind === "video") { pub.track.attach(videoElement); avatarVideoSubscribed = true; }
                    if (pub.track.kind === "audio") _attachRemoteAudio(pub.track, p);   // replaces, never duplicates
                }
            });
        });
        announce();

        const unlock = () => { try { livekitRoom && livekitRoom.startAudio(); } catch (e) { /* ignore */ } };
        window.addEventListener("click", unlock, { once: true });
        window.addEventListener("keydown", unlock, { once: true });
        unlock();
    }


    // ---- Task 5N-R: speak/interrupt requests to the avatar worker ----
    // Only the turn number travels; the worker reads the persisted
    // question itself (no client-supplied text, no open TTS proxy).
    function _publish(topic, body) {
        if (!livekitRoom) throw new Error("room not connected");
        const data = new TextEncoder().encode(JSON.stringify(body));
        return livekitRoom.localParticipant.publishData(data, { reliable: true, topic });
    }

    function sayTurn(turnNumber, seq) { return _publish("jobpilot.say", { turn_number: turnNumber, seq }); }
    function interruptSpeech(seq) { return _publish("jobpilot.interrupt", { seq }); }

    // True only when the avatar's real media is actually flowing.
    function isRemoteSpeakerReady() {
        return currentState !== STATES.COMPLETED && !!livekitRoom && avatarVideoSubscribed && avatarAudioSubscribed;
    }


    // Called by ai-interview-v2.js's voice-event fan-out (see
    // handleVoiceStateChange's onEvent wiring) -- this is the ONLY
    // place voice-layer events reach the avatar, and they only ever
    // change VISUAL state here, never interview state.
    function handleVoiceEvent(eventName) {
        const next = Logic.nextAvatarState(currentState, eventName);
        setState(next);
    }


    function cleanup() {

        connectionToken++; // any in-flight connect() becomes stale
        _cancelReconnect();
        _cancelAvatarPoll();
        reconnectAttempts = RECONNECT_DELAYS_MS.length; // and nothing may reconnect after the interview ended

        if (livekitRoom) {
            try { livekitRoom.disconnect(); } catch (error) { /* ignore */ }
            livekitRoom = null;
        }

        videoListenerAttachedForRoom = null;
        avatarVideoSubscribed = false;
        roomConnected = false;
        _removeAllRemoteAudio();

        setState(STATES.COMPLETED);
    }


    return {
        STATES,
        init,
        connect,
        attachVideo,
        handleVoiceEvent,
        cleanup,
        getRoom,
        sayTurn,
        interruptSpeech,
        isRemoteSpeakerReady,
        isConnected,
        setRemoteAudioMuted,
        getRemoteAudioTracks,
    };

})();

/* =========================================================
   JOBPILOT AI - PRE-INTERVIEW DEVICE-CHECK LOBBY

   Shown once, after the candidate accepts the AI interview's
   instructions pop-up and BEFORE the interview/start request
   fires (so this never eats into the 30-minute clock). Lets the
   candidate see their own camera, watch a live microphone level
   meter while they say a test sentence, and pick a different
   camera/microphone if their machine has more than one -- so a
   bad device is caught here, not mid-interview.

   The selected device IDs are handed to the real acquisition
   paths via IntegrityMode.setPreferredCameraDevice() and
   InterviewVoiceV2.setPreferredMicDevice() (both a no-op/default
   behaviour if never called). This module's own preview stream
   is a SEPARATE, short-lived getUserMedia call that is always
   fully stopped before resolving, so it never holds the camera/
   mic open once the real interview's own acquisition begins.
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) {
        module.exports = factory();
    } else {
        root.DeviceCheckLobby = factory();
    }
})(typeof self !== "undefined" ? self : this, function () {

    let previewStream = null;
    let audioContext = null;
    let analyser = null;
    let meterRafId = null;

    function stopPreviewStream() {

        if (meterRafId) { cancelAnimationFrame(meterRafId); meterRafId = null; }

        if (previewStream) {
            previewStream.getTracks().forEach((track) => track.stop());
            previewStream = null;
        }

        if (audioContext) {
            try { audioContext.close(); } catch (error) { /* ignore */ }
            audioContext = null;
            analyser = null;
        }
    }

    function buildHtml() {

        return `
        <div style="background:#fff;color:#0f172a;border-radius:16px;max-width:560px;width:100%;max-height:92vh;overflow:auto;
                    box-shadow:0 20px 60px rgba(0,0,0,.45);padding:28px 30px;font-family:system-ui,Segoe UI,Arial,sans-serif;line-height:1.5">

            <div style="font-size:12px;font-weight:700;color:#6366f1;text-transform:uppercase;letter-spacing:.08em">Before you begin</div>
            <h2 id="dclTitle" style="margin:4px 0 14px;font-size:24px;font-weight:800">Check your camera &amp; microphone</h2>
            <p style="margin:0 0 18px;color:#475569;font-size:14px.5">
                Make sure we can see and hear you clearly before the interview's 30-minute timer starts.
                This does not count toward your interview time.
            </p>

            <div style="position:relative;background:#111827;border-radius:12px;overflow:hidden;aspect-ratio:4/3;max-height:260px;margin:0 auto 16px;
                        display:flex;align-items:center;justify-content:center">
                <video id="dclPreviewVideo" autoplay playsinline muted style="width:100%;height:100%;object-fit:cover;transform:scaleX(-1);display:none"></video>
                <div id="dclVideoPlaceholder" style="color:#94a3b8;font-size:13px;text-align:center;padding:0 20px">Starting camera preview…</div>
            </div>

            <div style="display:grid;gap:10px;margin-bottom:16px">

                <label style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.04em">Camera</label>
                <select id="dclCameraSelect" style="width:100%;padding:9px 10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px" disabled>
                    <option>Detecting cameras…</option>
                </select>

                <label style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-top:4px">Microphone</label>
                <select id="dclMicSelect" style="width:100%;padding:9px 10px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px" disabled>
                    <option>Detecting microphones…</option>
                </select>

            </div>

            <div style="background:#f8fafc;border-radius:10px;padding:12px 14px;margin-bottom:16px">
                <div style="font-size:12.5px;font-weight:700;color:#334155;margin-bottom:8px">
                    <i class="bi bi-mic"></i> Say a sentence out loud — watch the bar move
                </div>
                <div style="height:10px;border-radius:999px;background:#e2e8f0;overflow:hidden">
                    <div id="dclMicBar" style="height:100%;width:0%;background:#16a34a;transition:width .08s linear"></div>
                </div>
                <div id="dclMicHint" style="font-size:12px;color:#94a3b8;margin-top:6px">Waiting for microphone…</div>
            </div>

            <div id="dclError" style="color:#b91c1c;font-size:13.5px;min-height:18px;margin-bottom:4px"></div>

            <div style="display:flex;gap:12px;justify-content:flex-end;flex-wrap:wrap;margin-top:8px">
                <button id="dclSkip" type="button" style="border:1px solid #cbd5e1;background:#fff;border-radius:999px;padding:12px 20px;font-size:14px;font-weight:600;cursor:pointer">
                    Skip this check
                </button>
                <button id="dclStart" type="button" style="border:0;background:#16a34a;color:#fff;border-radius:999px;padding:12px 28px;font-size:15px;font-weight:700;cursor:pointer">
                    Start Interview
                </button>
            </div>

        </div>`;
    }

    async function populateDeviceLists(cameraSelect, micSelect) {

        let devices = [];
        try {
            devices = await navigator.mediaDevices.enumerateDevices();
        } catch (error) {
            return;
        }

        const cameras = devices.filter((d) => d.kind === "videoinput");
        const mics = devices.filter((d) => d.kind === "audioinput");

        const fillSelect = (select, list, kindLabel) => {
            select.innerHTML = "";
            if (list.length === 0) {
                select.innerHTML = `<option value="">No ${kindLabel} found</option>`;
                select.disabled = true;
                return;
            }
            list.forEach((d, i) => {
                const option = document.createElement("option");
                option.value = d.deviceId;
                option.textContent = d.label || `${kindLabel} ${i + 1}`;
                select.appendChild(option);
            });
            select.disabled = list.length <= 1;   // nothing to choose if there's only one
        };

        fillSelect(cameraSelect, cameras, "Camera");
        fillSelect(micSelect, mics, "Microphone");
    }

    async function startPreview(overlay, cameraDeviceId, micDeviceId) {

        stopPreviewStream();

        const video = overlay.querySelector("#dclPreviewVideo");
        const placeholder = overlay.querySelector("#dclVideoPlaceholder");
        const micHint = overlay.querySelector("#dclMicHint");
        const errorBox = overlay.querySelector("#dclError");

        try {

            previewStream = await navigator.mediaDevices.getUserMedia({
                video: cameraDeviceId ? { deviceId: { exact: cameraDeviceId } } : true,
                audio: micDeviceId ? { deviceId: { exact: micDeviceId } } : true,
            });

            video.srcObject = previewStream;
            video.style.display = "block";
            placeholder.style.display = "none";
            errorBox.textContent = "";

            await populateDeviceLists(overlay.querySelector("#dclCameraSelect"), overlay.querySelector("#dclMicSelect"));

            const audioTrack = previewStream.getAudioTracks()[0];
            if (audioTrack) {
                micHint.textContent = "Listening… speak now";
                startMicMeter(overlay, previewStream);
            } else {
                micHint.textContent = "No microphone detected.";
            }

        } catch (error) {

            placeholder.textContent = "Camera/microphone could not be started. You can still continue — " +
                "the interview itself will ask for permission again.";
            micHint.textContent = "Microphone unavailable.";
            errorBox.textContent = "";
        }
    }

    function startMicMeter(overlay, stream) {

        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;

        try {

            audioContext = new AudioContextClass();
            const source = audioContext.createMediaStreamSource(stream);
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);

            const data = new Uint8Array(analyser.frequencyBinCount);
            const bar = overlay.querySelector("#dclMicBar");

            const tick = () => {
                if (!analyser) return;
                analyser.getByteFrequencyData(data);
                const avg = data.reduce((sum, v) => sum + v, 0) / data.length;
                const pct = Math.min(100, Math.round((avg / 90) * 100));
                if (bar) bar.style.width = pct + "%";
                meterRafId = requestAnimationFrame(tick);
            };

            tick();

        } catch (error) { /* meter is a nicety, never block the lobby on it */ }
    }

    function show() {

        return new Promise(function (resolve) {

            const overlay = document.createElement("div");
            overlay.id = "deviceCheckLobbyOverlay";
            overlay.setAttribute("role", "dialog");
            overlay.setAttribute("aria-modal", "true");
            overlay.setAttribute("aria-labelledby", "dclTitle");
            overlay.style.cssText = "position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;" +
                "background:rgba(15,23,42,.82);padding:16px;";
            overlay.innerHTML = buildHtml();
            document.body.appendChild(overlay);

            const cameraSelect = overlay.querySelector("#dclCameraSelect");
            const micSelect = overlay.querySelector("#dclMicSelect");

            startPreview(overlay, null, null);

            cameraSelect.addEventListener("change", () => startPreview(overlay, cameraSelect.value || null, micSelect.value || null));
            micSelect.addEventListener("change", () => startPreview(overlay, cameraSelect.value || null, micSelect.value || null));

            function finish() {

                if (window.IntegrityMode && typeof window.IntegrityMode.setPreferredCameraDevice === "function" && cameraSelect.value) {
                    window.IntegrityMode.setPreferredCameraDevice(cameraSelect.value);
                }
                if (window.InterviewVoiceV2 && typeof window.InterviewVoiceV2.setPreferredMicDevice === "function" && micSelect.value) {
                    window.InterviewVoiceV2.setPreferredMicDevice(micSelect.value);
                }

                stopPreviewStream();
                overlay.remove();
                resolve(true);
            }

            overlay.querySelector("#dclStart").addEventListener("click", finish);
            overlay.querySelector("#dclSkip").addEventListener("click", finish);
        });
    }

    return { show: show };
});

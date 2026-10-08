// ============================================================
// JOBPILOT AI - START ASSESSMENT (HR-sourced candidate invite, Phase 1)
//
// The whole page's job: read ?token= from the emailed magic link, POST it
// to /api/invites/{token}/enter (auto-creates the account + application
// on first visit, just re-authenticates on later visits), then redirect
// straight into the existing assessment flow. No form, no password.
// ============================================================

(function () {

    // Matches every candidate-facing page's API base (assessment.html, aptitude-test.html,
    // programming-*.html, ai-interview-v2.html all hardcode 127.0.0.1) -- the session cookie this
    // page's /enter call receives must live at the same origin those pages call afterward.
    const API_BASE_URL = "http://127.0.0.1:8000";

    function markStep(id, state) {

        const el = document.getElementById(id);
        if (!el) return;

        el.classList.remove("is-pending", "is-active", "is-done");
        el.classList.add(state);

        // The icon slot is a stable wrapper (never itself replaced), so its inner markup can be
        // swapped freely between states without losing the element a later call needs to find.
        const iconSlot = el.querySelector(".sa-step-icon");
        if (!iconSlot) return;

        if (state === "is-done") {
            iconSlot.innerHTML = '<i class="bi bi-check-circle-fill"></i>';
        } else if (state === "is-active") {
            iconSlot.innerHTML = '<div class="sa-spinner sa-spinner-sm"></div>';
        } else {
            iconSlot.innerHTML = '<i class="bi bi-circle"></i>';
        }
    }

    function showError(message, isExpired) {
        document.getElementById("saLoading").style.display = "none";
        const errorBox = document.getElementById("saError");
        errorBox.style.display = "block";
        if (message) {
            document.getElementById("saErrorText").textContent = message;
        }
        if (isExpired) {
            document.getElementById("saErrorIcon").textContent = "⏰";
            document.getElementById("saErrorTitle").textContent = "Link Expired — Deadline Finished";
        }
    }

    async function start() {

        const params = new URLSearchParams(window.location.search);
        const token = params.get("token");

        if (!token) {
            showError("No invite token was found in this link.");
            return;
        }

        markStep("saStepVerify", "is-active");

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/invites/${encodeURIComponent(token)}/enter`,
                { method: "POST", credentials: "include" },
            );

            let data = null;
            try {
                data = await response.json();
            } catch (_e) {
                data = null;
            }

            if (!response.ok) {
                const isExpired = response.status === 410;
                showError(
                    (data && data.detail) || "This invite link could not be used.",
                    isExpired,
                );
                return;
            }

            markStep("saStepVerify", "is-done");

            // Candidate-facing pages downstream (assessment.html etc.) gate on this localStorage
            // shape, normally set by login.js -- this magic-link flow never goes through login.js.
            if (data && data.user) {
                localStorage.setItem("jobpilot_user", JSON.stringify(data.user));
            }

            markStep("saStepAccount", "is-done");
            markStep("saStepRedirect", "is-active");

            window.location.replace(data.redirect_url || "index.html");

        } catch (_error) {
            showError("A network error occurred. Please check your connection and try again.");
        }
    }

    start();

})();

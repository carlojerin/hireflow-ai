// ============================================================
// JOBPILOT AI - SCHEDULE ASSESSMENT (candidate-facing, magic-link token)
//
// Reads ?token= from the emailed "Schedule It For Later" link, shows the
// job/company + deadline, lets the candidate pick a time (constrained to
// now..deadline), and posts it to /api/invites/{token}/schedule. Never
// creates the account/application itself -- that still only happens when
// the candidate actually clicks a start link (here or in the emails).
// ============================================================

(function () {

    const API_BASE_URL = "http://127.0.0.1:8000";

    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");

    const loadingEl = document.getElementById("saLoading");
    const formEl = document.getElementById("saForm");
    const confirmedEl = document.getElementById("saConfirmed");
    const errorEl = document.getElementById("saError");
    const errorTextEl = document.getElementById("saErrorText");
    const errorBoxEl = document.getElementById("saErrorBox");

    const candidateNameEl = document.getElementById("saCandidateName");
    const jobTitleEl = document.getElementById("saJobTitle");
    const companyNameEl = document.getElementById("saCompanyName");
    const deadlineHintEl = document.getElementById("saDeadlineHint");
    const dateTimeInput = document.getElementById("saDateTimeInput");
    const confirmButton = document.getElementById("saConfirmButton");
    const startNowLink = document.getElementById("saStartNowLink");
    const confirmedTimeEl = document.getElementById("saConfirmedTime");
    const confirmedStartLink = document.getElementById("saConfirmedStartLink");

    function showError(message, isExpired) {
        loadingEl.style.display = "none";
        formEl.style.display = "none";
        errorEl.style.display = "block";
        if (message) errorTextEl.textContent = message;
        if (isExpired) {
            document.getElementById("saErrorIcon").textContent = "⏰";
            document.getElementById("saErrorTitle").textContent = "Link Expired — Deadline Finished";
        }
    }

    function showFormError(message) {
        errorBoxEl.innerHTML = `<div class="sa-danger-banner">${message}</div>`;
    }

    function toDateTimeLocalValue(date) {
        const pad = (n) => String(n).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    }

    function formatFriendly(date) {
        return date.toLocaleString(undefined, {
            weekday: "short", year: "numeric", month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    }

    async function init() {

        if (!token) {
            showError("No invite token was found in this link.");
            return;
        }

        const startUrl = `start-assessment.html?token=${encodeURIComponent(token)}`;
        startNowLink.href = startUrl;
        confirmedStartLink.href = startUrl;

        try {

            const response = await fetch(`${API_BASE_URL}/api/invites/${encodeURIComponent(token)}`);
            const data = await response.json().catch(() => null);

            if (!response.ok) {
                showError(
                    (data && data.detail) || "This invite link could not be used.",
                    response.status === 410,
                );
                return;
            }

            candidateNameEl.textContent = data.candidate_name || "there";
            jobTitleEl.textContent = data.job_title || "this role";
            companyNameEl.textContent = data.company_name || "the company";

            const expiresAt = new Date(data.expires_at);
            const now = new Date();

            dateTimeInput.min = toDateTimeLocalValue(new Date(now.getTime() + 5 * 60000));
            dateTimeInput.max = toDateTimeLocalValue(expiresAt);
            dateTimeInput.value = dateTimeInput.min;

            deadlineHintEl.textContent = `Your deadline is ${formatFriendly(expiresAt)}.`;

            if (data.scheduled_at) {
                const scheduledDate = new Date(data.scheduled_at);
                showConfirmed(scheduledDate);
                return;
            }

            loadingEl.style.display = "none";
            formEl.style.display = "block";

        } catch (_error) {
            showError("A network error occurred. Please check your connection and try again.");
        }
    }

    function showConfirmed(scheduledDate) {
        loadingEl.style.display = "none";
        formEl.style.display = "none";
        confirmedEl.style.display = "block";
        confirmedTimeEl.textContent = formatFriendly(scheduledDate);
    }

    confirmButton.addEventListener("click", async () => {

        errorBoxEl.innerHTML = "";

        const rawValue = dateTimeInput.value;

        if (!rawValue) {
            showFormError("Please pick a date and time.");
            return;
        }

        const scheduledDate = new Date(rawValue);

        confirmButton.disabled = true;
        confirmButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Scheduling…';

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/invites/${encodeURIComponent(token)}/schedule`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ scheduled_at: scheduledDate.toISOString() }),
                },
            );

            const data = await response.json().catch(() => null);

            if (!response.ok) {

                if (response.status === 410) {
                    showError((data && data.detail) || "This invite link has expired.", true);
                    return;
                }

                showFormError((data && data.detail) || "Could not schedule your assessment.");
                confirmButton.disabled = false;
                confirmButton.innerHTML = '<i class="bi bi-calendar-check me-1"></i>Confirm Schedule';
                return;
            }

            showConfirmed(scheduledDate);

        } catch (_error) {
            showFormError("A network error occurred. Please try again.");
            confirmButton.disabled = false;
            confirmButton.innerHTML = '<i class="bi bi-calendar-check me-1"></i>Confirm Schedule';
        }
    });

    init();

})();

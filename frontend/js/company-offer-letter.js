// Offer letter from the candidate report: opens an editable draft email (candidate name, role and company
// filled in), lets HR change it and attach files, and sends it to the candidate's own email address.
document.addEventListener("DOMContentLoaded", () => {

    const API_BASE_URL = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_company";

    const openButton = document.getElementById("offerLetterBtn");
    const modalEl = document.getElementById("offerLetterModal");

    if (!openButton || !modalEl || !window.bootstrap) return;

    const toEl = document.getElementById("offerTo");
    const subjectEl = document.getElementById("offerSubject");
    const bodyEl = document.getElementById("offerBody");
    const filesEl = document.getElementById("offerFiles");
    const fileListEl = document.getElementById("offerFileList");
    const statusEl = document.getElementById("offerStatus");
    const sendButton = document.getElementById("offerSendBtn");
    const resetButton = document.getElementById("offerResetBtn");
    const lastSentEl = document.getElementById("offerLastSent");

    const modal = new bootstrap.Modal(modalEl);

    let company = null;
    let draft = null;
    let chosen = [];

    try {
        company = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch (error) {
        company = null;
    }

    const applicationId = Number(new URLSearchParams(window.location.search).get("application_id"));

    function say(message, kind) {
        statusEl.textContent = message || "";
        statusEl.className = "small mt-2 " + (kind === "error" ? "text-danger" : kind === "ok" ? "text-success" : "text-muted");
    }

    function sizeLabel(bytes) {
        return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(bytes / 1024)) + " KB";
    }

    function renderFiles() {

        fileListEl.innerHTML = "";

        chosen.forEach((file, index) => {

            const row = document.createElement("div");
            row.className = "d-flex align-items-center justify-content-between gap-2 py-1 small";

            const label = document.createElement("span");
            label.textContent = file.name + "  (" + sizeLabel(file.size) + ")";

            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "btn btn-sm btn-outline-secondary";
            remove.textContent = "Remove";
            remove.addEventListener("click", () => {
                chosen.splice(index, 1);
                renderFiles();
            });

            row.append(label, remove);
            fileListEl.appendChild(row);
        });
    }

    function fillFromDraft() {

        toEl.value = draft.to_email ? `${draft.candidate_name || "Candidate"} <${draft.to_email}>` : "";
        subjectEl.value = draft.subject;
        bodyEl.value = draft.body;

        lastSentEl.textContent = draft.last_sent_at
            ? "An offer letter was already sent to this candidate on " + new Date(draft.last_sent_at).toLocaleString() + "."
            : "";
    }

    async function openEditor() {

        if (!company || !company.id || !applicationId) return;

        chosen = [];
        renderFiles();
        say("Preparing your draft...", "muted");
        sendButton.disabled = true;
        modal.show();

        try {

            const response = await fetch(`${API_BASE_URL}/api/offer-letters/${company.id}/${applicationId}/draft`, { credentials: "include" });
            const data = await response.json();

            if (!response.ok) throw new Error(data.detail || "Could not prepare the draft.");

            draft = data;
            fillFromDraft();
            say("Fill in the [square bracket] details, change anything you like, and attach files if needed.", "muted");
            sendButton.disabled = !draft.to_email;

            if (!draft.to_email) say("This candidate has no email address on file, so a letter cannot be sent.", "error");

        } catch (error) {
            say(error.message || "Could not prepare the draft.", "error");
        }
    }

    filesEl.addEventListener("change", () => {

        const limit = draft && draft.limits ? draft.limits : { max_files: 5, max_file_mb: 10 };

        for (const file of Array.from(filesEl.files)) {

            if (file.size > limit.max_file_mb * 1048576) {
                say(`${file.name} is larger than ${limit.max_file_mb} MB.`, "error");
                continue;
            }

            if (chosen.length >= limit.max_files) {
                say(`You can attach up to ${limit.max_files} files.`, "error");
                break;
            }

            chosen.push(file);
        }

        filesEl.value = "";
        renderFiles();
    });

    resetButton.addEventListener("click", () => {
        if (!draft) return;
        if (!window.confirm("Replace what you wrote with a fresh draft?")) return;
        fillFromDraft();
        say("Draft restored.", "muted");
    });

    async function send(allowPlaceholders) {

        const form = new FormData();

        form.append("subject", subjectEl.value);
        form.append("body", bodyEl.value);
        form.append("allow_placeholders", allowPlaceholders ? "true" : "false");
        chosen.forEach((file) => form.append("attachments", file, file.name));

        sendButton.disabled = true;
        say("Sending...", "muted");

        try {

            const response = await fetch(`${API_BASE_URL}/api/offer-letters/${company.id}/${applicationId}/send`, {
                method: "POST",
                credentials: "include",
                body: form,
            });

            const data = await response.json().catch(() => ({}));

            if (response.status === 409 && !allowPlaceholders) {

                sendButton.disabled = false;

                if (window.confirm((data.detail || "The letter still has blanks.") + "\n\nSend it anyway?")) {
                    return send(true);
                }

                say("Not sent. Fill in the blanks and try again.", "error");
                return;
            }

            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The letter could not be sent.");

            say(`Offer letter sent to ${data.to_email}.`, "ok");
            lastSentEl.textContent = "An offer letter was just sent to this candidate.";
            sendButton.textContent = "Send again";
            sendButton.disabled = false;

        } catch (error) {
            sendButton.disabled = false;
            say(error.message || "The letter could not be sent.", "error");
        }
    }

    sendButton.addEventListener("click", () => {

        if (!subjectEl.value.trim() || bodyEl.value.trim().length < 20) {
            say("Write a subject and the message before sending.", "error");
            return;
        }

        send(false);
    });

    openButton.addEventListener("click", openEditor);
});

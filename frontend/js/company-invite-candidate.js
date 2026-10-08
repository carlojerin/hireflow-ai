document.addEventListener("DOMContentLoaded", () => {

    // Matches every other company-*.js file's API base (they all hardcode 127.0.0.1, which is where
    // company-login.js sets the session cookie) -- using the dynamic localhost/127.0.0.1 pattern here
    // alone would mismatch that cookie's origin and 401 every request.
    const API_BASE_URL = "http://127.0.0.1:8000";

    const stored = localStorage.getItem("jobpilot_company");
    const company = stored ? JSON.parse(stored) : null;

    const navbarCompanyName = document.getElementById("navbarCompanyName");
    const messageBox = document.getElementById("icMessage");
    const form = document.getElementById("icForm");
    const submitButton = document.getElementById("icSubmitButton");
    const resumeFileInput = document.getElementById("icResumeFile");
    const candidateNameInput = document.getElementById("icCandidateName");
    const candidateEmailInput = document.getElementById("icCandidateEmail");
    const jobTitleInput = document.getElementById("icJobTitle");
    const jobRequirementInput = document.getElementById("icJobRequirement");
    const roleShapeSelect = document.getElementById("icRoleShape");
    const roleShapeHint = document.getElementById("icRoleShapeHint");

    if (!company || !company.id) {
        window.location.replace("company-login.html");
        return;
    }

    if (navbarCompanyName) {
        navbarCompanyName.textContent = company.company_name || "Company";
    }

    function showMessage(text, type) {

        if (type === "success") {
            messageBox.innerHTML = `
                <div class="ic-success-banner">
                    <i class="bi bi-check-circle-fill"></i>
                    <span>${text}</span>
                </div>
            `;
            return;
        }

        messageBox.innerHTML = `
            <div class="ic-danger-banner">${text}</div>
        `;
    }

    resumeFileInput.addEventListener("change", async () => {

        const file = resumeFileInput.files[0];

        if (!file) return;

        const originalNamePlaceholder = candidateNameInput.placeholder;
        const originalEmailPlaceholder = candidateEmailInput.placeholder;

        candidateNameInput.placeholder = "Detecting from resume…";
        candidateEmailInput.placeholder = "Detecting from resume…";

        const formData = new FormData();
        formData.append("file", file);

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/company/${company.id}/extract-resume-contact`,
                { method: "POST", credentials: "include", body: formData },
            );

            if (!response.ok) return;

            const data = await response.json();

            // Auto-fill only -- never overwrite something HR already typed themselves.
            if (data.candidate_name && !candidateNameInput.value.trim()) {
                candidateNameInput.value = data.candidate_name;
            }

            if (data.candidate_email && !candidateEmailInput.value.trim()) {
                candidateEmailInput.value = data.candidate_email;
            }

        } catch (_error) {
            // Best-effort only -- HR can always type these in manually.
        } finally {
            candidateNameInput.placeholder = originalNamePlaceholder;
            candidateEmailInput.placeholder = originalEmailPlaceholder;
        }
    });

    // Live auto-detection preview: shown as the editable "Assessment type" field's
    // hint so HR always sees what the pipeline will decide, and can override it with
    // one click (role_shape_override on the Job row) if it ever gets a role wrong --
    // never a silent decision. Debounced so it fires once typing pauses, not per keystroke.
    let detectRoleTimer = null;
    let lastAutoDetectedShape = null;   // tracks the auto value so we don't clobber an HR override

    function scheduleRoleTypeDetection() {

        if (detectRoleTimer) clearTimeout(detectRoleTimer);

        detectRoleTimer = setTimeout(async () => {

            const jobTitle = jobTitleInput.value.trim();
            const jobRequirement = jobRequirementInput.value.trim();

            if (!jobTitle && !jobRequirement) {
                roleShapeHint.textContent = "HireFlow AI will detect this automatically once you describe the role above. You can override it here if it gets it wrong.";
                return;
            }

            roleShapeHint.textContent = "Detecting…";

            const formData = new FormData();
            formData.append("job_title", jobTitle);
            formData.append("job_requirement", jobRequirement);

            try {

                const response = await fetch(
                    `${API_BASE_URL}/api/company/${company.id}/detect-role-type`,
                    { method: "POST", credentials: "include", body: formData },
                );

                if (!response.ok) return;

                const data = await response.json();

                roleShapeHint.textContent = `AI detected: ${data.label}. Change the dropdown above if this is wrong.`;

                // Only move the dropdown when HR hasn't manually overridden it themselves --
                // an HR pick always wins over a later re-detection.
                if (roleShapeSelect.value === "auto" || roleShapeSelect.value === lastAutoDetectedShape) {
                    roleShapeSelect.value = data.detected_shape;
                    lastAutoDetectedShape = data.detected_shape;
                }

            } catch (_error) {
                // Best-effort only -- HR can always pick the dropdown manually.
            }

        }, 600);
    }

    // ------------------------------------------------------------------
    // The assessments the AI recommends for this role (shown before anything is sent). HR can keep the standard tests instead.
    // ------------------------------------------------------------------
    const planCard = document.getElementById("icPlanCard");
    const planList = document.getElementById("icPlanList");
    const planSummary = document.getElementById("icPlanSummary");
    const planChoices = document.getElementById("icPlanChoices");
    let currentPlan = null;
    let planTimer = null;
    let planRequestId = 0;

    const STANDARD_NAMES = { aptitude: "Aptitude Test", programming: "Programming / Technical", technical_domain: "Technical Assessment", situational_judgment: "Situational Judgment Test" };

    function escapeText(value) {
        const div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    }

    function renderPlan(data) {

        currentPlan = data.plan;
        const [aptitude, stage3] = data.stages;

        const nameOf = (stage) => stage.title || STANDARD_NAMES[stage.kind] || STANDARD_NAMES[stage.slot];
        const ENVIRONMENT_NAMES = { written: "practical written task", data: "practical data task", roleplay: "practical call simulation", design: "practical web design task", lab: "practical technical lab" };
        const areasOf = (stage) => {
            if (stage.kind === "practical") {
                const parts = [ENVIRONMENT_NAMES[stage.environment] || "practical task"].concat(stage.focus || []);
                return ` <span class="text-muted">(${parts.map(escapeText).join(", ")})</span>`;
            }
            return (stage.areas || []).length ? ` <span class="text-muted">(${stage.areas.map((a) => escapeText(a.label)).join(", ")})</span>` : "";
        };
        const reasonOf = (stage) => stage.reason ? `<div class="text-muted small">${escapeText(stage.reason)}</div>` : "";

        planSummary.textContent = data.summary || "";
        planList.innerHTML = `
            <li class="mb-1">Self introduction</li>
            <li class="mb-1"><strong>${escapeText(nameOf(aptitude))}</strong>${areasOf(aptitude)}${reasonOf(aptitude)}</li>
            <li class="mb-1"><strong>${escapeText(nameOf(stage3))}</strong>${areasOf(stage3)}${reasonOf(stage3)}</li>
            <li>AI interview</li>`;

        const choices = [];
        if (aptitude.custom) {
            choices.push(`<div class="col-md-6"><label class="small fw-semibold">Second assessment</label>
                <select class="form-select form-select-sm" id="icAptitudeChoice">
                    <option value="ai">${escapeText(aptitude.title)} (recommended)</option>
                    <option value="standard">General aptitude test instead</option></select></div>`);
        }
        if (stage3.custom) {
            choices.push(`<div class="col-md-6"><label class="small fw-semibold">Third assessment</label>
                <select class="form-select form-select-sm" id="icStage3Choice">
                    <option value="ai">${escapeText(stage3.title)} (recommended)</option>
                    <option value="standard">Standard scenario test instead</option></select></div>`);
        }
        planChoices.innerHTML = choices.join("");
        planCard.hidden = false;
    }

    function schedulePlanPreview() {

        if (planTimer) clearTimeout(planTimer);

        planTimer = setTimeout(async () => {

            const jobTitle = jobTitleInput.value.trim();
            const jobRequirement = jobRequirementInput.value.trim();

            if (!jobTitle || jobRequirement.length < 20) { planCard.hidden = true; currentPlan = null; return; }

            const requestId = ++planRequestId;
            planCard.hidden = false;
            planSummary.textContent = "The AI is choosing the right assessments for this role…";
            planList.innerHTML = "";
            planChoices.innerHTML = "";

            const formData = new FormData();
            formData.append("job_title", jobTitle);
            formData.append("job_requirement", jobRequirement);
            formData.append("role_shape_override", roleShapeSelect.value === "auto" ? "" : roleShapeSelect.value);

            try {
                const response = await fetch(`${API_BASE_URL}/api/company/${company.id}/assessment-plan`, { method: "POST", credentials: "include", body: formData });
                if (requestId !== planRequestId) return;                 // a newer description is already being planned
                if (!response.ok) { planCard.hidden = true; currentPlan = null; return; }
                renderPlan(await response.json());
            } catch (_error) {
                if (requestId === planRequestId) { planCard.hidden = true; currentPlan = null; }
            }

        }, 1500);
    }

    jobTitleInput.addEventListener("input", scheduleRoleTypeDetection);
    jobRequirementInput.addEventListener("input", scheduleRoleTypeDetection);
    jobTitleInput.addEventListener("input", schedulePlanPreview);
    jobRequirementInput.addEventListener("input", schedulePlanPreview);
    roleShapeSelect.addEventListener("change", schedulePlanPreview);

    form.addEventListener("submit", async (event) => {

        event.preventDefault();

        const jobTitle = document.getElementById("icJobTitle").value.trim();
        const jobRequirement = document.getElementById("icJobRequirement").value.trim();
        const candidateName = document.getElementById("icCandidateName").value.trim();
        const candidateEmail = document.getElementById("icCandidateEmail").value.trim();
        const deadlineDays = document.getElementById("icDeadlineDays").value;
        const roleShape = roleShapeSelect.value;
        const fileInput = document.getElementById("icResumeFile");
        const file = fileInput.files[0];

        if (!jobTitle || !jobRequirement || !candidateName || !candidateEmail || !file) {
            showMessage("Please fill in every field and choose a resume file.", "warning");
            return;
        }

        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Analyzing resume…';

        const formData = new FormData();
        formData.append("job_title", jobTitle);
        formData.append("job_requirement", jobRequirement);
        formData.append("role_shape_override", roleShape === "auto" ? "" : roleShape);
        if (currentPlan) {
            formData.append("assessment_plan", JSON.stringify(currentPlan));
            const aptitudeChoice = document.getElementById("icAptitudeChoice");
            const stage3Choice = document.getElementById("icStage3Choice");
            formData.append("aptitude_choice", aptitudeChoice ? aptitudeChoice.value : "ai");
            formData.append("stage3_choice", stage3Choice ? stage3Choice.value : "ai");
        }
        formData.append("candidate_name", candidateName);
        formData.append("candidate_email", candidateEmail);
        formData.append("deadline_days", deadlineDays);
        formData.append("file", file);

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/company/${company.id}/invite-candidate`,
                { method: "POST", credentials: "include", body: formData },
            );

            const data = await response.json();

            if (!response.ok) {
                showMessage(data.detail || "Could not send the invite.", "danger");
                return;
            }

            showMessage(
                `Invite sent to ${data.candidate_email}. They can click the emailed link at any time to start the assessment.`,
                "success",
            );
            form.reset();
            currentPlan = null;
            planCard.hidden = true;
            lastAutoDetectedShape = null;
            roleShapeHint.textContent = "HireFlow AI will detect this automatically once you describe the role above. You can override it here if it gets it wrong.";

        } catch (_error) {
            showMessage("A network error occurred while sending the invite.", "danger");
        } finally {
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="bi bi-send me-1"></i>Analyze &amp; Send Invite';
        }
    });

});

document.addEventListener("DOMContentLoaded", () => {

    const API_BASE_URL = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_admin";

    const admin = (() => {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : null;
        } catch (error) {
            return null;
        }
    })();

    if (!admin || admin.role !== "admin") {
        window.location.replace("admin-login.html");
        return;
    }

    document.getElementById("adminEmail").textContent = admin.email || admin.username || "Admin";
    document.getElementById("adminAvatar").textContent = (admin.email || "A").charAt(0).toUpperCase();

    document.getElementById("logoutChip").addEventListener("click", () => {
        const confirmed = window.confirm("Log out of the admin console?");
        if (!confirmed) return;
        fetch(`${API_BASE_URL}/api/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});
        localStorage.removeItem(STORAGE_KEY);
        window.location.href = "admin-login.html";
    });

    function escapeHtml(value) {
        return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function formatDate(isoString) {
        if (!isoString) return "—";
        const date = new Date(isoString);
        return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }

    function formatDateTime(isoString) {
        if (!isoString) return "—";
        const date = new Date(isoString);
        return date.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }

    async function apiFetch(path, options) {
        const response = await fetch(`${API_BASE_URL}${path}`, { credentials: "include", ...options });
        if (response.status === 401 || response.status === 403) {
            localStorage.removeItem(STORAGE_KEY);
            window.location.href = "admin-login.html";
            throw new Error("Not authorized");
        }
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "Request failed.");
        return data;
    }

    // ------------------------------------------------------------
    // NAVIGATION
    // ------------------------------------------------------------

    const titles = {
        overview: ["Overview", "Platform-wide snapshot"],
        companies: ["HR / Company Registry", "Every registered company — suspend, warn, or delete"],
        invites: ["Candidate Invites", "Every invite link sent, across every company"],
        connectors: ["System Connectors", "Which AI provider powers each assessment stage, checked live"],
        llm: ["LLM Usage", "Live Groq limits and Gemini usage, with tokens"],
        settings: ["Settings", "Manage your own admin account"],
    };

    document.querySelectorAll(".nav-link").forEach((link) => {
        link.addEventListener("click", () => {
            document.querySelectorAll(".nav-link").forEach((l) => l.classList.remove("active"));
            document.querySelectorAll(".section").forEach((s) => s.classList.remove("active"));
            link.classList.add("active");
            const target = link.dataset.target;
            document.getElementById("sec-" + target).classList.add("active");
            document.getElementById("pageTitle").textContent = titles[target][0];
            document.getElementById("pageSub").textContent = titles[target][1];

            if (target === "companies" && !companiesLoaded) loadCompanies();
            if (target === "invites" && !invitesLoaded) loadInvites();
            if (target === "connectors") loadConnectors();   // always re-check: it's meant to be live
            if (target === "llm") { startLlmUsage(); loadLlmSettings(); } else stopLlmUsage();
        });
    });

    // ------------------------------------------------------------
    // OVERVIEW
    // ------------------------------------------------------------

    let allCompanies = [];
    let allInvites = [];
    let companiesLoaded = false;
    let invitesLoaded = false;

    function renderKpis() {
        document.getElementById("kpiCompanies").textContent = allCompanies.length;
        document.getElementById("kpiJobs").textContent = allCompanies.reduce((sum, c) => sum + c.jobs_posted, 0);
        document.getElementById("kpiInvites").textContent = allCompanies.reduce((sum, c) => sum + c.candidates_invited, 0);
        document.getElementById("kpiSuspended").textContent = allCompanies.filter((c) => !c.account_active).length;
    }

    // ------------------------------------------------------------
    // COMPANIES
    // ------------------------------------------------------------

    function renderCompanies() {

        const query = document.getElementById("companySearch").value.trim().toLowerCase();
        const filtered = allCompanies.filter((c) =>
            !query || c.company_name.toLowerCase().includes(query) || (c.email || "").toLowerCase().includes(query)
        );

        const body = document.getElementById("companiesTableBody");

        if (filtered.length === 0) {
            body.innerHTML = `<tr class="empty-row"><td colspan="9">No companies found.</td></tr>`;
            return;
        }

        body.innerHTML = filtered.map((c) => `
            <tr>
                <td class="name-cell">${escapeHtml(c.company_name)}</td>
                <td>${escapeHtml(c.hr_name || "—")}</td>
                <td>${escapeHtml(c.email)}</td>
                <td class="mono">${c.jobs_posted}</td>
                <td class="mono">${c.candidates_invited}</td>
                <td class="mono">${c.warning_count}</td>
                <td>${c.account_active ? '<span class="pill ok">Active</span>' : '<span class="pill bad">Suspended</span>'}</td>
                <td class="mono">${formatDate(c.registered_at)}</td>
                <td class="row-actions">
                    <div class="icon-btn" title="${c.account_active ? 'Suspend' : 'Reactivate'}" onclick="window.__adminToggleStatus(${c.id}, ${!c.account_active})">
                        <i class="bi ${c.account_active ? 'bi-slash-circle' : 'bi-arrow-counterclockwise'}"></i>
                    </div>
                    <div class="icon-btn warn" title="Issue warning" onclick="window.__adminWarn(${c.id}, '${escapeHtml(c.company_name)}')"><i class="bi bi-exclamation-triangle"></i></div>
                    <div class="icon-btn danger" title="Delete permanently" onclick="window.__adminDelete(${c.id}, '${escapeHtml(c.company_name)}')"><i class="bi bi-trash3"></i></div>
                </td>
            </tr>
        `).join("");
    }

    async function loadCompanies() {
        try {
            const data = await apiFetch("/api/admin/companies");
            allCompanies = data.companies;
            companiesLoaded = true;
            renderCompanies();
            renderKpis();
        } catch (error) {
            document.getElementById("companiesTableBody").innerHTML = `<tr class="empty-row"><td colspan="9">${escapeHtml(error.message)}</td></tr>`;
        }
    }

    document.getElementById("companySearch").addEventListener("input", renderCompanies);

    window.__adminToggleStatus = async (companyId, nextActive) => {
        const action = nextActive ? "reactivate" : "suspend";
        if (!window.confirm(`Are you sure you want to ${action} this company? ${nextActive ? "" : "They will lose access to their HR dashboard immediately."}`)) return;
        try {
            await apiFetch(`/api/admin/companies/${companyId}/status`, {
                method: "PATCH", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ account_active: nextActive }),
            });
            await loadCompanies();
        } catch (error) {
            window.alert(error.message);
        }
    };

    window.__adminWarn = async (companyId, companyName) => {
        const message = window.prompt(`Warning message for ${companyName}:`);
        if (!message || !message.trim()) return;
        try {
            await apiFetch(`/api/admin/companies/${companyId}/warn`, {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: message.trim() }),
            });
            await loadCompanies();
            window.alert("Warning recorded. Note: this is not emailed automatically — reach out to the company directly if needed.");
        } catch (error) {
            window.alert(error.message);
        }
    };

    window.__adminDelete = async (companyId, companyName) => {
        const confirmed = window.confirm(
            `Permanently delete "${companyName}"? This removes the company, every job they posted, every application and assessment result, and every candidate invite they sent. This CANNOT be undone.`
        );
        if (!confirmed) return;
        const typed = window.prompt(`Type the company name exactly to confirm: "${companyName}"`);
        if (typed !== companyName) {
            window.alert("Name did not match — deletion cancelled.");
            return;
        }
        try {
            await apiFetch(`/api/admin/companies/${companyId}`, { method: "DELETE" });
            await loadCompanies();
        } catch (error) {
            window.alert(error.message);
        }
    };

    // ------------------------------------------------------------
    // INVITES
    // ------------------------------------------------------------

    function renderInvites() {

        const query = document.getElementById("inviteSearch").value.trim().toLowerCase();
        const filtered = allInvites.filter((i) => !query || i.candidate_email.toLowerCase().includes(query));

        const body = document.getElementById("invitesTableBody");

        if (filtered.length === 0) {
            body.innerHTML = `<tr class="empty-row"><td colspan="6">No invites found.</td></tr>`;
            return;
        }

        const statusPill = (status) => {
            const map = { completed: "ok", in_progress: "warn", pending: "neutral", expired: "bad" };
            return `<span class="pill ${map[status] || "neutral"}">${escapeHtml(status.replace("_", " "))}</span>`;
        };

        body.innerHTML = filtered.map((i) => `
            <tr>
                <td class="name-cell">${escapeHtml(i.candidate_email)}</td>
                <td>${escapeHtml(i.job_title)}</td>
                <td>${escapeHtml(i.company_name)}</td>
                <td class="mono">${formatDate(i.sent_at)}</td>
                <td class="mono">${formatDate(i.expires_at)}</td>
                <td>${statusPill(i.status)}</td>
            </tr>
        `).join("");
    }

    async function loadInvites() {
        try {
            const data = await apiFetch("/api/admin/invites");
            allInvites = data.invites;
            invitesLoaded = true;
            renderInvites();
        } catch (error) {
            document.getElementById("invitesTableBody").innerHTML = `<tr class="empty-row"><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
        }
    }

    document.getElementById("inviteSearch").addEventListener("input", renderInvites);

    // ------------------------------------------------------------
    // SYSTEM CONNECTORS
    // ------------------------------------------------------------

    async function loadConnectors() {

        document.getElementById("connectorsCheckedAt").textContent = "Checking…";

        try {

            const data = await apiFetch("/api/admin/system-status");

            document.getElementById("connectorsCheckedAt").textContent = "Checked " + formatDateTime(data.checked_at);

            const providerRows = Object.entries(data.providers).map(([key, info]) => `
                <tr>
                    <td class="name-cell">${escapeHtml(key)}</td>
                    <td>${info.used_by.map(escapeHtml).join(", ")}${info.note ? ` <span class="text-muted">(${escapeHtml(info.note)})</span>` : ""}</td>
                    <td>${info.configured ? '<span class="pill ok">● Configured</span>' : '<span class="pill bad">● Not configured</span>'}</td>
                </tr>
            `).join("");
            document.getElementById("providersTableBody").innerHTML = providerRows;

            const sandbox = data.sandbox;
            const sandboxRows = `
                <tr>
                    <td class="name-cell">Docker sandbox</td>
                    <td>${sandbox.docker.languages.join(", ")}</td>
                    <td>${sandbox.docker.reachable ? '<span class="pill ok">● Reachable</span>' : '<span class="pill bad">● Not reachable right now</span>'}</td>
                </tr>
                <tr>
                    <td class="name-cell">SQL</td>
                    <td>${escapeHtml(sandbox.sql.note)}</td>
                    <td><span class="pill ok">● Reachable</span></td>
                </tr>
                <tr>
                    <td class="name-cell">HTML / Web</td>
                    <td>${escapeHtml(sandbox.html.note)}</td>
                    <td><span class="pill ok">● Reachable</span></td>
                </tr>
            `;
            document.getElementById("sandboxTableBody").innerHTML = sandboxRows;

        } catch (error) {
            document.getElementById("connectorsCheckedAt").textContent = "Check failed";
            document.getElementById("providersTableBody").innerHTML = `<tr class="empty-row"><td colspan="3">${escapeHtml(error.message)}</td></tr>`;
            document.getElementById("sandboxTableBody").innerHTML = "";
        }
    }

    // ------------------------------------------------------------
    // LLM USAGE (Groq + Gemini), refreshed every 30 s while the tab is open
    // ------------------------------------------------------------

    let llmTimer = null;

    function startLlmUsage() {
        loadLlmUsage(false);
        if (!llmTimer) llmTimer = setInterval(() => loadLlmUsage(false), 30000);
    }

    function stopLlmUsage() {
        if (llmTimer) { clearInterval(llmTimer); llmTimer = null; }
    }

    function fmtNum(value) {
        return Number(value || 0).toLocaleString();
    }

    function meterHtml(label, remaining, limit, resetText) {
        const rem = Number(remaining), lim = Number(limit);
        if (!Number.isFinite(rem) || !Number.isFinite(lim) || lim <= 0) return "";
        const pct = Math.max(0, Math.min(100, (rem / lim) * 100));
        const color = pct > 30 ? "var(--success)" : pct > 10 ? "var(--warn)" : "var(--danger)";
        return `
            <div class="llm-meter">
                <div class="row"><span>${escapeHtml(label)}</span><span><b>${fmtNum(rem)}</b> of ${fmtNum(lim)} left${resetText ? ` · resets in ${escapeHtml(resetText)}` : ""}</span></div>
                <div class="llm-bar"><i style="width:${pct.toFixed(1)}%;background:${color}"></i></div>
            </div>`;
    }

    function usageStatsHtml(today, week) {
        const limited = today.rate_limited ? ` <small style="color:var(--danger)">(${fmtNum(today.rate_limited)} rate-limited)</small>` : "";
        const models = today.models.length ? " · " + today.models.map((m) => `${escapeHtml(m.model)}: ${fmtNum(m.requests)} req`).join(", ") : "";
        return `
            <div class="llm-stats">
                <div><span>Requests today</span><b>${fmtNum(today.requests)}</b></div>
                <div><span>Tokens today</span><b>${fmtNum(today.total_tokens)}</b></div>
                <div><span>Errors today</span><b>${fmtNum(today.errors)}${limited}</b></div>
                <div><span>Tokens, last 7 days</span><b>${fmtNum(week.total_tokens)}</b></div>
            </div>
            <div class="llm-note">In ${fmtNum(today.prompt_tokens)} · out ${fmtNum(today.output_tokens)} tokens today${models}</div>`;
    }

    function planHtml(plan) {

        if (!plan) return "";

        const used = plan.used_today, limits = plan.limits, live = plan.live;
        const meters = [];
        if (limits.requests_per_day) meters.push(usedMeterHtml("Text requests today", used.text_requests, limits.requests_per_day));
        if (limits.tokens_per_day) meters.push(usedMeterHtml("Tokens today", used.tokens, limits.tokens_per_day));
        if (limits.live_sessions_per_day) meters.push(usedMeterHtml("Live voice sessions today", used.live_sessions, limits.live_sessions_per_day));

        const left = plan.interviews_left
            ? `<div class="llm-stats"><div style="grid-column:1/-1"><span>Estimated interviews left today</span><b>${fmtNum(plan.interviews_left.interviews)}</b><span>${escapeHtml(plan.interviews_left.basis)}</span></div></div>`
            : '<div class="llm-note">Enter your plan limits below (Model controls, "Your Gemini plan limits") and this shows how many interviews are left today.</div>';

        const limit = plan.last_rate_limit
            ? `<div class="llm-note" style="color:var(--danger)">Last refusal from Google (${escapeHtml(formatDateTime(plan.last_rate_limit.at))}, ${escapeHtml(plan.last_rate_limit.model)}): ${escapeHtml(plan.last_rate_limit.message)}</div>`
            : "";

        return `
            ${meters.join("")}
            <div class="llm-stats">
                <div><span>Live interviews today</span><b>${fmtNum(live.today.sessions)}</b></div>
                <div><span>Tokens per interview (7-day avg)</span><b>${live.last_7_days.avg_tokens_per_session ? fmtNum(live.last_7_days.avg_tokens_per_session) : "no data yet"}</b></div>
                <div><span>Minutes per interview (7-day avg)</span><b>${live.last_7_days.avg_minutes_per_session || "no data yet"}</b></div>
                <div><span>Text requests today</span><b>${fmtNum(used.text_requests)}</b></div>
                <div><span>Tokens per minute, one interview</span><b>${plan.tokens_per_minute_per_interview ? fmtNum(plan.tokens_per_minute_per_interview) : "no data yet"}</b></div>
                <div><span>Interviews at the same time</span><b>${plan.simultaneous_interviews !== null && plan.simultaneous_interviews !== undefined ? "about " + fmtNum(plan.simultaneous_interviews) : (limits.tokens_per_minute ? "no data yet" : "enter tokens per minute")}</b></div>
            </div>
            ${left}
            ${limit}
            <div class="llm-note"><a href="https://aistudio.google.com/rate-limit" target="_blank" rel="noopener">Open your real limits and usage in AI Studio</a></div>`;
    }

    function usedMeterHtml(label, used, limit) {
        const pct = Math.max(0, Math.min(100, (used / limit) * 100));
        const color = pct < 70 ? "var(--success)" : pct < 90 ? "var(--warn)" : "var(--danger)";
        return `
            <div class="llm-meter">
                <div class="row"><span>${escapeHtml(label)}</span><span><b>${fmtNum(used)}</b> of ${fmtNum(limit)} used</span></div>
                <div class="llm-bar"><i style="width:${pct.toFixed(1)}%;background:${color}"></i></div>
            </div>`;
    }

    async function loadLlmUsage(forceCheck) {

        const stamp = document.getElementById("llmCheckedAt");
        stamp.textContent = "Checking…";

        try {

            const data = await apiFetch("/api/admin/llm-usage" + (forceCheck ? "?refresh=true" : ""));
            const groq = data.providers.groq, gemini = data.providers.gemini;
            stamp.textContent = "Updated " + formatDateTime(data.checked_at);

            const limits = groq.live_limits;
            const groqBody = !groq.configured
                ? '<div class="llm-note">GROQ_API_KEY is not set.</div>'
                : (limits
                    ? meterHtml("Requests per day", limits.requests_remaining, limits.requests_limit, limits.requests_reset)
                        + meterHtml("Tokens per minute", limits.tokens_remaining, limits.tokens_limit, limits.tokens_reset)
                    : '<div class="llm-note">Waiting for Groq to report its limits…</div>');

            const check = gemini.key_check || {};
            const keyPill = check.key_valid
                ? '<span class="pill ok">● Accepted by Google</span>'
                : '<span class="pill bad">● Rejected / unreachable</span>';
            const geminiBody = !gemini.configured
                ? '<div class="llm-note">GEMINI_API_KEY is not set.</div>'
                : `<div class="llm-meter"><div class="row"><span>API key</span><span>${keyPill}</span></div>
                   <div class="llm-note" style="margin-top:0">${escapeHtml(check.detail || "")}</div></div>`;

            const configuredPill = (ok) => ok ? '<span class="pill ok">● Configured</span>' : '<span class="pill bad">● Not configured</span>';

            document.getElementById("llmProviders").innerHTML = `
                <div class="llm-card">
                    <h3><i class="bi bi-lightning-charge"></i> Groq ${configuredPill(groq.configured)}</h3>
                    <div class="model">${escapeHtml(groq.model)} · aptitude, programming, interview brain, summaries</div>
                    ${groqBody}
                    ${usageStatsHtml(groq.today, groq.last_7_days)}
                    <div class="llm-note">${escapeHtml(groq.note)}</div>
                </div>
                <div class="llm-card">
                    <h3><i class="bi bi-stars"></i> Gemini ${configuredPill(gemini.configured)}</h3>
                    <div class="model">${escapeHtml(gemini.model || "")} · live voice, speech, resume analysis</div>
                    ${geminiBody}
                    ${planHtml(gemini.plan)}
                    ${usageStatsHtml(gemini.today, gemini.last_7_days)}
                    <div class="llm-note">${escapeHtml(gemini.note)}</div>
                </div>`;

            document.getElementById("llmHistoryBody").innerHTML = data.history.slice().reverse().map((d) => `
                <tr>
                    <td class="name-cell">${escapeHtml(d.date)}</td>
                    <td>${fmtNum(d.groq.requests)}</td><td>${fmtNum(d.groq.tokens)}</td>
                    <td>${fmtNum(d.gemini.requests)}</td><td>${fmtNum(d.gemini.tokens)}</td>
                </tr>`).join("");

        } catch (error) {
            stamp.textContent = "Check failed";
            document.getElementById("llmProviders").innerHTML = `<div class="llm-note">${escapeHtml(error.message)}</div>`;
        }
    }

    document.getElementById("llmRefreshButton").addEventListener("click", () => loadLlmUsage(true));

    // ------------------------------------------------------------
    // MODEL CONTROLS: change the Groq / Gemini models (and a few related knobs) at runtime
    // ------------------------------------------------------------

    const GROUP_TITLES = { security: "Test security", groq: "Groq", gemini: "Gemini", limits: "Your Gemini plan limits (copy them from aistudio.google.com/rate-limit)" };

    function settingInputHtml(s) {
        const id = "set-" + s.key;
        if (s.kind === "select") {
            const options = s.choices.map((c) => `<option value="${escapeHtml(c)}"${c === s.value ? " selected" : ""}>${escapeHtml(c)}</option>`).join("");
            const extra = s.value && !s.choices.includes(s.value) ? `<option value="${escapeHtml(s.value)}" selected>${escapeHtml(s.value)}</option>` : "";
            return `<select class="set-input" id="${id}">${extra}${options}</select>`;
        }
        if (s.kind === "int") {
            return `<input class="set-input" id="${id}" type="number" min="${s.min}" max="${s.max}" value="${escapeHtml(s.value)}">`;
        }
        const list = s.group === "groq" ? "groqModelList" : "geminiModelList";
        return `<input class="set-input" id="${id}" type="text" list="${list}" value="${escapeHtml(s.value)}" autocomplete="off" spellcheck="false">`;
    }

    function renderLlmSettings(data) {

        document.getElementById("groqModelList").innerHTML = data.available.groq.map((m) => `<option value="${escapeHtml(m)}">`).join("");
        document.getElementById("geminiModelList").innerHTML = data.available.gemini.map((m) => `<option value="${escapeHtml(m)}">`).join("");

        const html = ["security", "groq", "gemini", "limits"].map((group) => {
            const rows = data.settings.filter((s) => s.group === group).map((s) => `
                <div class="set-row" data-key="${escapeHtml(s.key)}">
                    <div>
                        <div class="name">${escapeHtml(s.label)} ${s.overridden ? '<span class="pill warn">changed by admin</span>' : ""}</div>
                        <div class="help">${escapeHtml(s.help)}</div>
                        <div class="default">Default from server setup: ${escapeHtml(s.default || "(none)")}</div>
                    </div>
                    <div>
                        ${settingInputHtml(s)}
                        <div class="status" data-status></div>
                    </div>
                    <div class="set-actions">
                        <button type="button" class="btn-sm-primary" data-save>Test &amp; save</button>
                        ${s.overridden ? '<button type="button" class="btn-sm-outline" data-reset>Reset</button>' : ""}
                    </div>
                </div>`).join("");
            return `<div class="set-group"><h3>${GROUP_TITLES[group]}</h3>${rows}</div>`;
        }).join("");

        document.getElementById("llmSettings").innerHTML = html;
    }

    async function loadLlmSettings() {
        try {
            renderLlmSettings(await apiFetch("/api/admin/llm-settings"));
        } catch (error) {
            document.getElementById("llmSettings").innerHTML = `<div class="llm-note">${escapeHtml(error.message)}</div>`;
        }
    }

    document.getElementById("llmSettings").addEventListener("click", async (event) => {

        const saveButton = event.target.closest("[data-save]");
        const resetButton = event.target.closest("[data-reset]");
        if (!saveButton && !resetButton) return;

        const row = event.target.closest(".set-row");
        const key = row.dataset.key;
        const status = row.querySelector("[data-status]");
        const button = saveButton || resetButton;

        const label = button.textContent;
        button.disabled = true;
        status.className = "status";

        try {

            if (saveButton) {
                status.textContent = "Testing the model, then saving…";
                const value = row.querySelector(".set-input").value;
                await apiFetch("/api/admin/llm-settings", {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key, value }),
                });
                status.textContent = "Saved. The next request uses it.";
            } else {
                status.textContent = "Resetting…";
                await apiFetch("/api/admin/llm-settings/reset", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key }),
                });
                status.textContent = "Back to the server default.";
            }

            status.className = "status good";
            setTimeout(loadLlmSettings, 900);

        } catch (error) {
            status.textContent = error.message;
            status.className = "status err";
            button.disabled = false;
            button.textContent = label;
        }
    });

    // ------------------------------------------------------------
    // SETTINGS — change password
    // ------------------------------------------------------------

    document.querySelectorAll(".password-toggle[data-for]").forEach((button) => {
        button.addEventListener("click", () => {
            const input = document.getElementById(button.dataset.for);
            const showing = input.type === "text";
            input.type = showing ? "password" : "text";
            button.querySelector("i").className = showing ? "bi bi-eye" : "bi bi-eye-slash";
            button.title = showing ? "Show password" : "Hide password";
        });
    });

    const changePasswordForm = document.getElementById("changePasswordForm");
    const settingsMessage = document.getElementById("settingsMessage");

    changePasswordForm.addEventListener("submit", async (event) => {

        event.preventDefault();

        const currentPassword = document.getElementById("currentPassword").value;
        const newPassword = document.getElementById("newPassword").value;
        const confirmPassword = document.getElementById("confirmPassword").value;
        const button = document.getElementById("changePasswordButton");

        settingsMessage.innerHTML = "";

        if (newPassword.length < 8) {
            settingsMessage.innerHTML = `<div class="error-box">New password must be at least 8 characters.</div>`;
            return;
        }

        if (newPassword !== confirmPassword) {
            settingsMessage.innerHTML = `<div class="error-box">New password and confirmation do not match.</div>`;
            return;
        }

        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Updating…';

        try {

            await apiFetch("/api/admin/me/change-password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
            });

            settingsMessage.innerHTML = `<div class="success-box">Password updated successfully.</div>`;
            changePasswordForm.reset();

        } catch (error) {
            settingsMessage.innerHTML = `<div class="error-box">${escapeHtml(error.message)}</div>`;
        } finally {
            button.disabled = false;
            button.innerHTML = '<i class="bi bi-key me-1"></i> Update password';
        }
    });

    // ------------------------------------------------------------
    // INIT
    // ------------------------------------------------------------

    loadCompanies();

});

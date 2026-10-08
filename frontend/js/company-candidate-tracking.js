document.addEventListener("DOMContentLoaded", () => {

    const API_BASE_URL = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_company";

    const navbarCompanyName = document.getElementById("navbarCompanyName");
    const logoutBtn = document.getElementById("logoutBtn");
    const refreshBtn = document.getElementById("refreshBtn");
    const searchInput = document.getElementById("searchInput");
    const resultCount = document.getElementById("resultCount");
    const trackingContainer = document.getElementById("trackingContainer");
    const kpiRow = document.getElementById("kpiRow");
    const minScoreSelect = document.getElementById("minScoreSelect");
    const integritySelect = document.getElementById("integritySelect");
    const sortSelect = document.getElementById("sortSelect");
    const integrityLegend = document.getElementById("integrityLegend");

    const RISK_RANK = { clean: 0, low: 1, medium: 2, high: 3 };
    const RISK_TEXT = { clean: "Clean", low: "Low risk", medium: "Medium risk", high: "High risk" };

    let company = null;
    let allCandidates = [];
    let activeFilter = "";

    function getStoredCompany() {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : null;
        } catch (error) {
            return null;
        }
    }

    function checkCompanyAccess() {
        company = getStoredCompany();
        if (!company || !company.id || company.role !== "company") {
            window.location.href = "company-login.html";
            return false;
        }
        return true;
    }

    function escapeHtml(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function formatDateTime(isoString) {
        if (!isoString) return "—";
        const date = new Date(isoString);
        return date.toLocaleString(undefined, {
            year: "numeric", month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    }

    function levelIcon(category) {
        const icons = {
            invited: "bi-envelope",
            scheduled: "bi-calendar-event",
            not_started: "bi-hourglass",
            in_progress: "bi-arrow-repeat",
            success: "bi-check-circle-fill",
            danger: "bi-x-circle-fill",
            expired: "bi-clock-history",
        };
        return icons[category] || "bi-circle";
    }

    // A small round photo (taken automatically when the candidate's camera first came on), or their initials until there is one.
    function avatarHtml(candidate) {

        const initials = String(candidate.candidate_name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0] || "").join("").toUpperCase() || "?";
        const placeholder = `<span class="cand-avatar cand-avatar-initials" title="No photo yet: the candidate has not started">${escapeHtml(initials)}</span>`;

        if (!candidate.photo_url) return placeholder;

        return `<img class="cand-avatar" src="${escapeHtml(API_BASE_URL + candidate.photo_url)}" alt="Photo of ${escapeHtml(candidate.candidate_name)}" loading="lazy" ` +
            `onerror="this.outerHTML=this.dataset.fallback" data-fallback="${escapeHtml(placeholder)}">`;
    }

    function renderKpis(candidates) {

        const counts = { invited: 0, scheduled: 0, in_progress: 0, not_started: 0, success: 0, danger: 0, expired: 0 };

        candidates.forEach(c => {
            if (counts[c.level_category] !== undefined) counts[c.level_category] += 1;
        });

        document.getElementById("kpiAll").textContent = candidates.length;
        document.getElementById("kpiInvited").textContent = counts.invited;
        document.getElementById("kpiScheduled").textContent = counts.scheduled;
        document.getElementById("kpiInProgress").textContent = counts.in_progress + counts.not_started;
        document.getElementById("kpiSuccess").textContent = counts.success;
        document.getElementById("kpiDanger").textContent = counts.danger + counts.expired;
    }

    function matchesFilter(candidate) {
        if (!activeFilter) return true;
        return activeFilter.split(",").includes(candidate.level_category);
    }

    function scoreOf(candidate) {
        const card = candidate.scorecard;
        return card && card.overall_percentage !== null && card.overall_percentage !== undefined ? Number(card.overall_percentage) : null;
    }

    function integrityOf(candidate) {
        return candidate.scorecard ? candidate.scorecard.integrity : null;
    }

    function matchesScore(candidate) {
        const minimum = Number(minScoreSelect.value || 0);
        if (!minimum) return true;
        const score = scoreOf(candidate);
        return score !== null && score >= minimum;
    }

    function matchesIntegrity(candidate) {

        const mode = integritySelect.value;
        if (mode === "any") return true;

        const integrity = integrityOf(candidate);
        if (!integrity) return false;                                  // nobody has taken a test yet: nothing to judge

        const rank = RISK_RANK[integrity.risk];

        if (mode === "clean") return rank === 0;
        if (mode === "low") return rank <= 1;
        if (mode === "medium") return rank <= 2;
        return rank === 3;
    }

    function sortCandidates(list) {

        const mode = sortSelect.value;
        if (mode === "newest") return list;

        const issues = (c) => { const i = integrityOf(c); return i ? i.strikes * 2 + i.flags : Number.MAX_SAFE_INTEGER; };
        const score = (c) => { const s = scoreOf(c); return s === null ? -1 : s; };

        const sorted = list.slice();

        if (mode === "score") sorted.sort((a, b) => score(b) - score(a));
        else if (mode === "integrity") sorted.sort((a, b) => issues(a) - issues(b) || score(b) - score(a));
        else sorted.sort((a, b) => score(b) - score(a) || issues(a) - issues(b));

        return sorted;
    }

    function scoreCellHtml(candidate) {

        const card = candidate.scorecard;
        const score = scoreOf(candidate);

        if (!card || score === null) return `<span class="score-pill score-none" title="No test has been scored yet">—</span>`;

        const tone = score >= 70 ? "score-high" : score >= 50 ? "score-mid" : "score-low";
        const parts = [];
        if (card.aptitude_percentage !== null && card.aptitude_percentage !== undefined) parts.push(`Quiz ${Math.round(card.aptitude_percentage)}%`);
        if (card.stage3_percentage !== null && card.stage3_percentage !== undefined) parts.push(`Task ${Math.round(card.stage3_percentage)}%`);
        if (card.interview_passed === true) parts.push("Interview passed");
        if (card.interview_passed === false) parts.push("Interview not passed");

        return `<span class="score-pill ${tone}" title="Average of the scored stages">${Math.round(score)}%</span>` +
            `<div class="score-sub">${escapeHtml(parts.join(" · "))}</div>`;
    }

    function integrityCellHtml(candidate) {

        const integrity = integrityOf(candidate);

        if (!integrity) return `<span class="risk-pill risk-none" title="Not started yet">—</span>`;

        const icon = integrity.risk === "clean" ? "bi-shield-check" : integrity.risk === "high" ? "bi-shield-exclamation" : "bi-shield-shaded";
        const counts = integrity.risk === "clean"
            ? "Nothing recorded"
            : `${integrity.strikes} strike${integrity.strikes === 1 ? "" : "s"} · ${integrity.flags} flag${integrity.flags === 1 ? "" : "s"}${integrity.terminated ? " · auto-terminated" : ""}`;

        const top = (integrity.what_happened || []).slice(0, 3).map((w) => `${w.label}${w.count > 1 ? " ×" + w.count : ""}`).join(", ");

        return `<span class="risk-pill risk-${integrity.risk}" title="${escapeHtml(counts)}"><i class="bi ${icon}"></i>${RISK_TEXT[integrity.risk]}</span>` +
            `<div class="risk-detail">${escapeHtml(counts)}${top ? "<br>" + escapeHtml(top) : ""}</div>`;
    }

    function matchesSearch(candidate, query) {
        if (!query) return true;
        const haystack = `${candidate.candidate_name} ${candidate.candidate_email} ${candidate.job_title || ""}`.toLowerCase();
        return haystack.includes(query.toLowerCase());
    }

    function renderTable() {

        const query = searchInput.value.trim();
        const filtered = sortCandidates(allCandidates.filter(c => matchesFilter(c) && matchesSearch(c, query) && matchesScore(c) && matchesIntegrity(c)));

        resultCount.textContent = `${filtered.length} candidate${filtered.length === 1 ? "" : "s"}`;

        if (allCandidates.length === 0) {
            trackingContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon"><i class="bi bi-signpost-split"></i></div>
                    <div class="empty-title">No candidates invited yet</div>
                    <div class="empty-text">Invite your first candidate to see their progress here.</div>
                </div>
            `;
            return;
        }

        if (filtered.length === 0) {
            trackingContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon"><i class="bi bi-search"></i></div>
                    <div class="empty-title">No matching candidates</div>
                    <div class="empty-text">Try a different search or filter.</div>
                </div>
            `;
            return;
        }

        const rows = filtered.map(c => {

            const reportHtml = c.application_id
                ? `
                    <a
                        href="company-candidate-report.html?application_id=${Number(c.application_id)}"
                        class="btn-ats-outline btn-sm"
                        style="padding:5px 10px; font-size:12px;"
                        title="View assessment report and recordings"
                    >
                        <i class="bi bi-file-earmark-bar-graph"></i>
                    </a>
                `
                : "";

            const deleteHtml = `
                <button
                    type="button"
                    class="btn-ats-outline btn-sm text-danger"
                    style="padding:5px 10px; font-size:12px; border-color:#fecdd3;"
                    onclick="deleteCandidate(${Number(c.invite_id)})"
                    title="Delete candidate permanently"
                >
                    <i class="bi bi-trash3"></i>
                </button>
            `;

            const actionHtml = `<div class="d-flex gap-2 justify-content-end">${reportHtml}${deleteHtml}</div>`;

            return `
                <tr>
                    <td>
                        <div class="d-flex align-items-center gap-2">
                            ${avatarHtml(c)}
                            <div>
                                <div class="candidate-name">${escapeHtml(c.candidate_name)}</div>
                                <div class="candidate-email">${escapeHtml(c.candidate_email)}</div>
                            </div>
                        </div>
                    </td>
                    <td>${escapeHtml(c.job_title || "—")}</td>
                    <td>
                        <span class="level-badge level-${c.level_category}">
                            <i class="bi ${levelIcon(c.level_category)}"></i>
                            ${escapeHtml(c.level)}
                        </span>
                    </td>
                    <td>${scoreCellHtml(c)}</td>
                    <td>${integrityCellHtml(c)}</td>
                    <td>
                        <div class="timestamp-line">${formatDateTime(c.created_at)}</div>
                        <div class="timestamp-sub">invited</div>
                    </td>
                    <td>
                        <div class="timestamp-line">${formatDateTime(c.expires_at)}</div>
                        <div class="timestamp-sub">deadline</div>
                    </td>
                    <td class="text-end">${actionHtml}</td>
                </tr>
            `;
        }).join("");

        trackingContainer.innerHTML = `
            <div class="tracking-table-wrap">
                <div class="tracking-table-scroll">
                    <table class="tracking-table">
                        <thead>
                            <tr>
                                <th>Candidate</th>
                                <th>Job</th>
                                <th>Current Level</th>
                                <th>Score</th>
                                <th>Integrity</th>
                                <th>Invited</th>
                                <th>Deadline</th>
                                <th class="text-end">Actions</th>
                            </tr>
                        </thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
            </div>
        `;
    }

    window.deleteCandidate = async function (inviteId) {

        const confirmed = window.confirm(
            "Permanently delete this candidate? This removes their invite and every assessment record tied to it. This cannot be undone."
        );

        if (!confirmed) return;

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/company/${company.id}/invites/${inviteId}`,
                { method: "DELETE", credentials: "include" },
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Unable to delete this candidate.");
            }

            await loadTracking();

        } catch (error) {
            window.alert(error.message || "Unable to delete this candidate.");
        }
    };

    async function loadTracking() {

        trackingContainer.innerHTML = `
            <div class="loading-state">
                <div class="spinner-border text-secondary mb-3" role="status"></div>
                <div class="text-muted small">Loading candidate tracking...</div>
            </div>
        `;

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/company-applications/${company.id}/candidate-tracking`,
                { credentials: "include" },
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Unable to load candidate tracking.");
            }

            allCandidates = data.candidates || [];

            const sample = allCandidates.find((c) => c.scorecard && c.scorecard.integrity);
            if (sample && integrityLegend) integrityLegend.title = sample.scorecard.integrity.rules;

            renderKpis(allCandidates);
            renderTable();

        } catch (error) {

            trackingContainer.innerHTML = `
                <div class="error-state">
                    <div class="empty-icon"><i class="bi bi-exclamation-triangle"></i></div>
                    <div class="empty-title">Could not load candidate tracking</div>
                    <div class="empty-text">${escapeHtml(error.message || "Please try refreshing.")}</div>
                </div>
            `;
        }
    }

    kpiRow.addEventListener("click", (event) => {
        const card = event.target.closest(".kpi-card");
        if (!card) return;

        kpiRow.querySelectorAll(".kpi-card").forEach(el => el.classList.remove("active"));
        card.classList.add("active");

        activeFilter = card.dataset.filter || "";
        renderTable();
    });

    searchInput.addEventListener("input", renderTable);
    [minScoreSelect, integritySelect, sortSelect].forEach((el) => el && el.addEventListener("change", renderTable));

    if (refreshBtn) {
        refreshBtn.addEventListener("click", loadTracking);
    }

    if (logoutBtn) {
        logoutBtn.addEventListener("click", () => {
            const confirmed = window.confirm("Are you sure you want to logout?");
            if (!confirmed) return;

            fetch(`${API_BASE_URL}/api/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {});
            localStorage.removeItem(STORAGE_KEY);
            window.location.href = "company-login.html";
        });
    }

    if (!checkCompanyAccess()) return;

    if (navbarCompanyName) {
        navbarCompanyName.textContent = company.company_name || "Company";
    }

    loadTracking();

});

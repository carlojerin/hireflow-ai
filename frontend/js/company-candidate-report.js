document.addEventListener("DOMContentLoaded", () => {

    const API_BASE_URL = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_company";

    const navbarCompanyName = document.getElementById("navbarCompanyName");
    const logoutBtn = document.getElementById("logoutBtn");
    const reportContainer = document.getElementById("reportContainer");
    const headerName = document.getElementById("candidateHeaderName");
    const headerSub = document.getElementById("candidateHeaderSub");

    let company = null;
    let applicationId = null;
    let activeTab = "report";
    let reportData = null;

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

    function getApplicationIdFromUrl() {
        const params = new URLSearchParams(window.location.search);
        const value = Number(params.get("application_id"));
        return Number.isInteger(value) && value > 0 ? value : null;
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

    function formatDuration(seconds) {
        if (seconds === null || seconds === undefined) return "—";
        const m = Math.floor(seconds / 60);
        const s = Math.round(seconds % 60);
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    }

    function stageChipClass(state) {
        if (state === "done") return "done";
        if (state === "failed") return "failed";
        if (state === "current") return "current";
        return "locked";
    }

    function stageChipIcon(state) {
        if (state === "done") return "bi-check-circle-fill";
        if (state === "failed") return "bi-x-circle-fill";
        if (state === "current") return "bi-arrow-repeat";
        return "bi-lock-fill";
    }

    // ---------------------------------------------------------
    // REPORT TAB
    // ---------------------------------------------------------

    function renderScoreCard(title, percentage, passed, sub) {
        if (percentage === null || percentage === undefined) return "";
        const pillHtml = passed === null || passed === undefined
            ? ""
            : `<span class="pass-pill ${passed ? "pass" : "fail"}">${passed ? "Passed" : "Failed"}</span>`;
        return `
            <div class="score-card">
                <div class="score-card-title">${escapeHtml(title)}${pillHtml}</div>
                <div class="score-card-value">${percentage}%</div>
                ${sub ? `<div class="score-card-sub">${escapeHtml(sub)}</div>` : ""}
            </div>
        `;
    }

    function renderEvidenceList(items) {
        if (!items || items.length === 0) return `<p class="text-muted small mb-0">None recorded.</p>`;
        return `<ul class="evidence-list">${items.map(i => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
    }

    // the name the AI planner gave this job's test in a slot (for example "Language & Grammar Assessment"), else the standard name
    function planTitle(data, slot, fallback) {
        const stage = data.assessment_plan && (data.assessment_plan.stages || []).find((s) => s.slot === slot);
        return stage && stage.title ? stage.title : fallback;
    }

    // The candidate's photo (taken before the self introduction), the face at the start of each stage, and the camera-flag pictures.
    function renderIdentityPhotos(data) {

        const photos = data.identity_photos;
        if (!photos || !photos.total_snapshots) return "";

        const src = (photo) => `${API_BASE_URL}${photo.url}`;
        const when = (iso) => iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

        const tile = (photo, caption, size) => `
            <a href="${escapeHtml(src(photo))}" target="_blank" rel="noopener" class="text-decoration-none text-reset d-block" style="width:${size}px">
                <img src="${escapeHtml(src(photo))}" alt="${escapeHtml(caption)}" loading="lazy"
                     style="width:${size}px;height:${Math.round(size * 0.75)}px;object-fit:cover;border-radius:10px;border:1px solid rgba(15,23,42,.12);background:#e5e7eb">
                <div class="small fw-semibold mt-1" style="line-height:1.25">${escapeHtml(caption)}</div>
                <div class="small text-muted">${escapeHtml(when(photo.taken_at))}</div>
            </a>`;

        const reference = photos.reference
            ? `<div class="me-4 mb-3">${tile(photos.reference, "Candidate photo: before the self introduction", 200)}</div>`
            : '<div class="text-muted small me-4 mb-3">No photo was captured before the self introduction.</div>';

        const stages = (photos.per_stage || []).filter((p) => !photos.reference || p.id !== photos.reference.id);

        const stageRow = stages.length
            ? `<div class="mb-3"><div class="fw-semibold mb-2">Face at the start of each later stage <span class="text-muted fw-normal small">(compare with the photo)</span></div>
               <div class="d-flex flex-wrap gap-3">${stages.map((p) => tile(p, p.label, 120)).join("")}</div></div>`
            : "";

        const flagged = (photos.flagged || []).length
            ? `<details class="mt-2"><summary class="fw-semibold">Pictures taken when the camera flagged something (${photos.flagged.length})</summary>
               <div class="d-flex flex-wrap gap-3 mt-3">${photos.flagged.map((p) => tile(p, `${p.label} (${p.stage_label})`, 120)).join("")}</div></details>`
            : '<div class="text-muted small">The camera flagged nothing during the assessment.</div>';

        const faceCheck = photos.reference && stages.length
            ? '<div id="faceMatchBox" class="mt-3 p-3" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px"><span class="text-muted small"><span class="spinner-border spinner-border-sm me-2"></span>Checking the photos...</span></div>'
            : "";

        return `
            <div class="card-surface">
                <div class="section-label">Candidate photo and identity</div>
                <div class="d-flex flex-wrap align-items-start">${reference}<div class="flex-grow-1" style="min-width:260px">${stageRow}${flagged}</div></div>
                ${faceCheck}
                <p class="text-muted small mt-2 mb-0"><i class="bi bi-info-circle me-1"></i>Photos are only shown to the employer. The face check below is a computer comparison to help you review; it is not an identity verification, and you make the decision.</p>
            </div>`;
    }

    // Face check: the later photos compared with the reference photo. Runs once automatically when the report opens (the answers are saved, so photos are never compared twice).
    function renderFaceMatch(view) {

        const box = document.getElementById("faceMatchBox");
        if (!box || !view) return;

        const tones = {
            consistent: ["#ecfdf5", "#047857", "bi-person-check", "Same person"],
            review: ["#fffbeb", "#b45309", "bi-person-exclamation", "Please review"],
            mismatch: ["#fef2f2", "#b91c1c", "bi-person-x", "Possible different person"],
            not_run: ["#f1f5f9", "#475569", "bi-person", "Not checked yet"],
        };
        const [bg, fg, icon, label] = tones[view.summary.status] || tones.not_run;

        const rows = (view.results || []).map((r) => {
            const colour = r.verdict === "same" ? "#047857" : r.verdict === "different" ? "#b91c1c" : "#b45309";
            const word = r.verdict === "same" ? "Same person" : r.verdict === "different" ? "Looks different" : "Could not tell";
            return `<tr><td>${escapeHtml(r.stage_label || r.stage)}</td><td style="color:${colour};font-weight:700">${word}</td><td class="text-muted small">${escapeHtml(r.reason || "")}</td></tr>`;
        }).join("");

        box.innerHTML = `
            <div class="d-flex align-items-center flex-wrap gap-2 mb-1">
                <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-weight:700;background:${bg};color:${fg}"><i class="bi ${icon}"></i>${label}</span>
                <span class="small">${escapeHtml(view.summary.headline)}</span>
                ${view.pending ? '<button type="button" id="faceMatchRun" class="btn btn-sm btn-outline-secondary ms-auto">Run the check</button>' : ""}
            </div>
            ${rows ? `<table class="table table-sm align-middle mb-0 mt-2"><tbody>${rows}</tbody></table>` : ""}`;

        const button = document.getElementById("faceMatchRun");
        if (button) button.addEventListener("click", () => runFaceMatch(true));
    }

    async function runFaceMatch(manual) {

        const box = document.getElementById("faceMatchBox");
        if (!box) return;

        const base = `${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/face-match`;

        try {
            let response = await fetch(base, { credentials: "include" });
            let view = response.ok ? await response.json() : null;

            if (view && !view.pending && !manual) {
                renderFaceMatch(view);
                return;
            }

            if (view && view.photos_available >= 2 && view.pending) {
                box.innerHTML = '<span class="text-muted small"><span class="spinner-border spinner-border-sm me-2"></span>Comparing the photos with the reference photo. This takes a few seconds...</span>';
                response = await fetch(`${base}/run`, { method: "POST", credentials: "include" });
                view = response.ok ? await response.json() : view;
            }

            renderFaceMatch(view);

        } catch (error) {
            box.innerHTML = '<span class="text-muted small">The face check is not available right now.</span>';
        }
    }

    function renderAssessmentPlan(data) {

        const plan = data.assessment_plan;
        if (!plan) return "";

        const rows = (plan.stages || []).map((s) => {
            const names = { aptitude: "Aptitude Test", programming: "Programming / Technical", technical_domain: "Technical Assessment", situational_judgment: "Situational Judgment Test", skills: "Skills Assessment" };
            const name = s.title || names[s.kind] || names[s.slot] || s.slot;
            return `<li class="mb-1"><strong>${escapeHtml(name)}</strong>${s.reason ? ` <span class="text-muted">&mdash; ${escapeHtml(s.reason)}</span>` : ""}</li>`;
        }).join("");

        return `
            <div class="card-surface">
                <div class="section-label">Assessments chosen for this role ${plan.source === "ai" ? '<span class="badge text-bg-primary ms-2">AI-planned</span>' : plan.source === "hr" ? '<span class="badge text-bg-secondary ms-2">Set by HR</span>' : '<span class="badge text-bg-light text-dark ms-2">Standard rules</span>'}</div>
                ${plan.summary ? `<p class="mb-2">${escapeHtml(plan.summary)}</p>` : ""}
                <ol class="mb-0 ps-3">${rows}<li><strong>AI Interview</strong></li></ol>
            </div>`;
    }

    function renderInterviewAnalysis(finalEval) {

        const r = finalEval && finalEval.candidate_report;
        if (!r) return "";

        const list = (items) => (items && items.length)
            ? `<ul class="mb-0 ps-3">${items.map((i) => `<li class="mb-1">${escapeHtml(i)}</li>`).join("")}</ul>`
            : '<p class="text-muted small mb-0">Nothing recorded.</p>';

        const block = (title, text) => text ? `<div class="mb-3"><div class="fw-semibold mb-1">${escapeHtml(title)}</div><div>${escapeHtml(text)}</div></div>` : "";

        const stated = r.stated_by_candidate || {};
        const said = (item) => item && item.answer ? `<div class="mt-1 text-muted small fst-italic">&ldquo;${escapeHtml(item.answer)}&rdquo;</div>` : "";

        const badge = (quality) => {
            const map = { strong: ["success", "Strong"], adequate: ["secondary", "Adequate"], weak: ["warning", "Weak"], off_topic: ["danger", "Off topic"] };
            const [color, label] = map[quality] || map.adequate;
            return `<span class="badge text-bg-${color} ms-2">${label}</span>`;
        };

        const reviews = (r.answer_reviews || []).map((a) => `
            <div class="question-review mb-3">
                <div class="question-review-text">Q${a.turn}. ${escapeHtml(a.question)} ${badge(a.quality)}</div>
                <div class="small text-muted mb-1">Candidate answer</div>
                <div class="mb-2" style="white-space:pre-wrap">${escapeHtml(a.answer)}</div>
                ${a.answer_summary ? `<div class="small"><strong>What they said:</strong> ${escapeHtml(a.answer_summary)}</div>` : ""}
                ${a.assessment ? `<div class="small"><strong>Assessment:</strong> ${escapeHtml(a.assessment)}</div>` : ""}
            </div>`).join("");

        const tools = (r.ai_tools && r.ai_tools.tools && r.ai_tools.tools.length) ? r.ai_tools.tools.map(escapeHtml).join(", ") : "";

        return `
            <div class="card-surface">
                <div class="section-label">AI Interview — Detailed Analysis ${r.generated_by === "fallback" ? '<span class="badge text-bg-warning ms-2">written analysis unavailable</span>' : ""}</div>
                ${block("Candidate overview", r.candidate_overview)}
                ${block("Overall impression", r.overall_impression)}
                <div class="row">
                    <div class="col-md-6 mb-3"><div class="fw-semibold mb-1">Strengths</div>${list(r.strengths)}</div>
                    <div class="col-md-6 mb-3"><div class="fw-semibold mb-1">Concerns</div>${list(r.concerns)}</div>
                </div>
                ${block("Communication", r.communication)}
                ${block("Fit with the resume", r.resume_consistency)}
                <div class="mb-3">
                    <div class="fw-semibold mb-1">AI tools they use${tools ? ": " + tools : ""}</div>
                    ${r.ai_tools && r.ai_tools.assessment ? `<div>${escapeHtml(r.ai_tools.assessment)}</div>` : ""}
                    ${(stated.ai_tools || []).map(said).join("")}
                </div>
                <div class="mb-3">
                    <div class="fw-semibold mb-1">Expected package</div>
                    ${said(stated.expected_package)}
                    ${r.compensation ? `<div class="mt-1">${escapeHtml(r.compensation)}</div>` : ""}
                </div>
                <div class="mb-3">
                    <div class="fw-semibold mb-1">Final answer: anything to add, and why they fit the role</div>
                    ${said(stated.work_in_company)}
                    ${r.work_style_and_fit ? `<div class="mt-1">${escapeHtml(r.work_style_and_fit)}</div>` : ""}
                </div>
                ${(r.suggested_follow_ups && r.suggested_follow_ups.length) ? `<div class="mb-3"><div class="fw-semibold mb-1">Worth asking in a later round</div>${list(r.suggested_follow_ups)}</div>` : ""}
                <div class="fw-semibold mb-2">Every answer, reviewed</div>
                ${reviews || '<p class="text-muted small mb-0">No answers recorded.</p>'}
                <p class="text-muted small mt-2 mb-0"><i class="bi bi-info-circle me-1"></i>This describes the evidence only. It never recommends hiring or rejecting: you make that decision.</p>
            </div>`;
    }

    function renderReportTab(data) {

        const progress = data.assessment.progress || [];

        const stageChips = progress.map(s => `
            <div class="stage-chip ${stageChipClass(s.state)}">
                <i class="bi ${stageChipIcon(s.state)}"></i>
                ${escapeHtml(s.label)}
            </div>
        `).join("");

        const narrative = data.narrative_summary
            ? data.narrative_summary.summary
            : "No summary available yet.";

        const aptitude = data.assessment.aptitude;
        const programming = data.assessment.programming;
        const programmingV2 = data.assessment.programming_v2;
        const sjt = data.assessment.situational_judgment_test;
        const tda = data.assessment.technical_domain_assessment;
        const interview = data.assessment.interview;
        const interviewV2 = data.assessment.interview_v2;

        let stage3Html = "";
        if (programming) {
            stage3Html = renderScoreCard("Programming", programming.percentage, programming.passed, formatDuration(programming.duration_seconds));
        } else if (programmingV2 && programmingV2.exists) {
            stage3Html = renderScoreCard("Programming", programmingV2.percentage, programmingV2.passed, formatDuration(programmingV2.duration_seconds));
        } else if (sjt && sjt.exists) {
            stage3Html = renderScoreCard(planTitle(data, "programming", "Situational Judgment Test"), sjt.percentage, sjt.passed, formatDuration(sjt.duration_seconds));
        } else if (tda && tda.exists) {
            stage3Html = renderScoreCard(`Technical Assessment (${tda.domain || ""})`, tda.percentage, tda.passed, formatDuration(tda.duration_seconds));
        }

        const interviewScore = interview ? interview.overall_score : null;
        const interviewHtml = interviewScore !== null && interviewScore !== undefined
            ? renderScoreCard("AI Interview", interviewScore, null, "Overall evidence score")
            : "";

        const scoreCards = [
            aptitude ? renderScoreCard(planTitle(data, "aptitude", "Aptitude"), aptitude.percentage, aptitude.passed, formatDuration(aptitude.duration_seconds)) : "",
            stage3Html,
            interviewHtml,
        ].filter(Boolean).join("");

        const finalEval = (interviewV2 && interviewV2.exists) ? interviewV2.final_evaluation : null;
        const v2Evidence = (programmingV2 && programmingV2.exists) ? programmingV2.evidence : null;

        const strengths = data.strengths && data.strengths.length ? data.strengths : (finalEval ? finalEval.strengths : (v2Evidence ? v2Evidence.strengths : []));
        const gaps = data.skill_gaps && data.skill_gaps.length ? data.skill_gaps : (finalEval ? finalEval.weaknesses : (v2Evidence ? v2Evidence.weaknesses : []));

        const integrityDigest = data.assessment.integrity.digest || null;
        const overall = data.overall_intelligence || {};

        reportContainer.innerHTML = `
            <div class="card-surface">
                <div class="section-label">AI Summary</div>
                <div class="narrative-box" style="white-space:pre-line">${escapeHtml(narrative)}</div>
                <p class="text-muted small mt-2 mb-0">
                    <i class="bi bi-info-circle me-1"></i>
                    This summarises the recorded results and interview. A stage that was not passed does not stop the candidate, so check it here. It never recommends a hiring decision: you make that call.
                </p>
            </div>

            ${renderIdentityPhotos(data)}

            ${renderAssessmentPlan(data)}

            ${renderInterviewAnalysis(finalEval)}

            <div class="card-surface">
                <div class="section-label">Pipeline Progress</div>
                <div class="stage-progress-row">${stageChips}</div>
            </div>

            <div class="card-surface">
                <div class="section-label">Assessment Scores</div>
                ${renderOverallScore(overall)}
                <div class="score-grid">${scoreCards || '<p class="text-muted small mb-0">No assessments completed yet.</p>'}</div>
            </div>

            ${renderPracticalVerdict(data.practical_verdict)}

            <div class="row">
                <div class="col-md-6">
                    <div class="card-surface h-100">
                        <div class="section-label">Strengths</div>
                        ${renderEvidenceList(strengths)}
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card-surface h-100">
                        <div class="section-label">Gaps / Areas to Probe</div>
                        ${renderEvidenceList(gaps)}
                    </div>
                </div>
            </div>

            ${renderIntegrity(integrityDigest, data.assessment.integrity.terminated)}

            <div id="questionReviewSection"></div>
        `;

        renderQuestionReview(data);

        runFaceMatch(false);
    }

    // One combined percentage: the plain average of the scored stages (the interview is shown as pass / fail beside it).
    function renderOverallScore(overall) {

        const value = overall.overall_percentage;

        if (value === null || value === undefined) {
            return '<p class="text-muted small mb-2">Overall score: not available yet (no stage has been scored).</p>';
        }

        const tone = value >= 70 ? "#047857" : value >= 50 ? "#b45309" : "#b91c1c";

        return `
            <div class="d-flex align-items-baseline gap-2 mb-3">
                <span style="font-size:34px;font-weight:800;color:${tone};line-height:1;">${Math.round(value)}%</span>
                <span class="text-muted small">overall score &middot; average of the scored stages, equal weight</span>
            </div>`;
    }

    // What the candidate did that raised an integrity signal: strikes (count toward the limit) and flags (for HR to review), what each was, and in which stage.
    function renderIntegrity(digest, terminated) {

        if (!digest) {
            return '<div class="card-surface"><div class="section-label">Assessment Integrity</div><p class="text-muted small mb-0">No integrity data is available.</p></div>';
        }

        const tones = {
            clean: ["#ecfdf5", "#047857", "bi-shield-check", "Clean"],
            low: ["#eff6ff", "#1d4ed8", "bi-shield-shaded", "Low risk"],
            medium: ["#fffbeb", "#b45309", "bi-shield-shaded", "Medium risk"],
            high: ["#fef2f2", "#b91c1c", "bi-shield-exclamation", "High risk"],
        };
        const [bg, fg, icon, text] = tones[digest.risk] || tones.clean;

        const badge = `<span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:999px;font-weight:700;background:${bg};color:${fg};"><i class="bi ${icon}"></i>${text}</span>`;

        const headline = digest.risk === "clean"
            ? '<span class="text-success">Nothing was recorded: no strikes and no flags.</span>'
            : `<strong>${digest.strikes}</strong> strike${digest.strikes === 1 ? "" : "s"} (count toward the limit) and <strong>${digest.flags}</strong> flag${digest.flags === 1 ? "" : "s"} (for you to review)${digest.terminated || terminated ? ' &middot; <span class="text-danger fw-bold">an assessment was auto-terminated</span>' : ""}.`;

        const what = (digest.what_happened || []).map((w) => `
            <tr>
                <td>${escapeHtml(w.label)}${w.serious ? ' <span class="badge bg-danger ms-1">serious</span>' : ""}</td>
                <td>${w.kind === "strike" ? '<span class="badge bg-warning text-dark">strike</span>' : '<span class="badge bg-secondary">flag</span>'}</td>
                <td class="text-end fw-bold">${Number(w.count)}</td>
            </tr>`).join("");

        const stages = (digest.by_stage || []).map((s) => `
            <tr>
                <td>${escapeHtml(s.stage_label)}</td>
                <td class="text-end">${Number(s.strikes)}</td>
                <td class="text-end">${Number(s.flags)}</td>
                <td>${s.terminated ? '<span class="badge bg-danger">auto-terminated</span>' : ""}</td>
            </tr>`).join("");

        return `
            <div class="card-surface">
                <div class="section-label">Assessment Integrity</div>
                <div class="d-flex align-items-center flex-wrap gap-3 mb-2">${badge}<span>${headline}</span></div>
                ${what ? `
                <div class="row g-3 mt-1">
                    <div class="col-md-7">
                        <div class="small text-muted fw-bold mb-1">WHAT HAPPENED</div>
                        <table class="table table-sm align-middle mb-0"><tbody>${what}</tbody></table>
                    </div>
                    <div class="col-md-5">
                        <div class="small text-muted fw-bold mb-1">BY STAGE</div>
                        <table class="table table-sm align-middle mb-0">
                            <thead><tr><th>Stage</th><th class="text-end">Strikes</th><th class="text-end">Flags</th><th></th></tr></thead>
                            <tbody>${stages}</tbody>
                        </table>
                    </div>
                </div>` : ""}
                <p class="text-muted small mt-3 mb-0"><i class="bi bi-info-circle me-1"></i>${escapeHtml(digest.rules || "")} These are signals recorded during the test, not a verdict: review the recordings and photos below before deciding.</p>
            </div>`;
    }

    // The practical stage as a verdict: the AI's marks and a short judgment. This is what HR reads; the candidate's own work sits one click below.
    function renderPracticalVerdict(verdict) {

        if (!verdict) return "";

        const markedBy = {
            vision: '<span class="badge bg-secondary">Marked by an AI that looked at the page, plus measurements</span>',
            ai: '<span class="badge bg-secondary">Marked by AI against a rubric</span>',
            auto: '<span class="badge bg-secondary">Marked automatically against the answer key</span>',
            rules: '<span class="badge bg-warning text-dark">AI marking was unavailable: score is provisional, please open the work below</span>',
        }[verdict.marked_by] || "";

        const bars = (verdict.marks || []).map((m) => `
            <div class="d-flex align-items-center gap-2 mb-1 small">
                <div style="flex:0 0 46%">${escapeHtml(m.label)}</div>
                <div class="flex-grow-1" style="background:#eef1ff;border-radius:999px;height:8px;overflow:hidden;">
                    <div style="width:${Math.round((m.score / (m.max || 10)) * 100)}%;height:100%;background:${m.score >= 8 ? "#12805c" : m.score >= 5 ? "#4f5bff" : "#d93a4a"};"></div>
                </div>
                <div style="flex:0 0 42px;text-align:right"><strong>${m.score}/${m.max}</strong></div>
            </div>`).join("");

        return `
            <div class="card-surface">
                <div class="section-label">Practical assessment: AI verdict</div>
                <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
                    <span style="font-size:1.6rem;font-weight:700;">${verdict.percentage === null || verdict.percentage === undefined ? "-" : verdict.percentage + "%"}</span>
                    <span class="badge ${verdict.passed ? "bg-success" : "bg-danger"}">${verdict.passed ? "Passed" : "Did not pass"}</span>
                    ${markedBy}
                </div>
                ${verdict.verdict ? `<div class="narrative-box mb-2">${escapeHtml(verdict.verdict)}</div>` : ""}
                ${bars}
            </div>`;
    }

    // A practical (work-sample) test: HR reads the candidate's OWN work next to the AI's notes (written) or the answer key (data).
    function renderPracticalReview(title, review) {

        const pct = (value) => (value === null || value === undefined ? "-" : value + "%");

        const dataBlock = review.dataset ? `
            <div class="small text-muted mb-2">${escapeHtml(review.dataset.title)}: ${escapeHtml(review.dataset.note)}
                (${review.dataset.rows.length} rows, ${escapeHtml(review.dataset.columns.join(", "))})</div>` : "";

        const items = (review.practical_items || []).map((item) => {

            if (item.type === "design") {
                const r = item.result || {};
                const m = r.metrics || {};
                const criteria = (r.criteria || []).map((c) => `
                    <li><strong>${c.score === null || c.score === undefined ? "-" : c.score + "/" + c.max}</strong> ${escapeHtml(c.criterion)}${c.comment ? ` <span class="text-muted">&middot; ${escapeHtml(c.comment)}</span>` : ""}</li>`).join("");
                const checklist = (m.checklist || []).map((c) => `<li>${c.met ? "&#10003;" : "&#10007;"} ${escapeHtml(c.label)}</li>`).join("");
                return `
                    <div class="question-review">
                        <div class="question-review-text"><strong>${escapeHtml(item.question)}</strong> &middot; ${pct(r.percent)}
                            ${r.source === "rules" ? '<span class="badge bg-warning text-dark ms-1">AI marking unavailable: please look at the design</span>' : '<span class="badge bg-secondary ms-1">' + (r.source === "vision" ? "AI looked at the page" : "AI-assisted score") + '</span>'}</div>
                        <div class="small text-muted my-1">${escapeHtml(item.scenario || "")}</div>
                        <div class="my-2" data-design-preview='${escapeHtml(JSON.stringify(item.layout || {elements: []}))}'></div>
                        ${r.summary ? `<div class="small mb-1">${escapeHtml(r.summary)}</div>` : ""}
                        <ul class="small mb-2">${criteria}</ul>
                        <div class="small fw-semibold">The brief's checklist</div>
                        <ul class="small mb-0" style="list-style:none;padding-left:0;">${checklist}</ul>
                    </div>`;
            }

            if (item.type === "roleplay") {
                const r = item.result || {};
                const lines = (item.transcript || []).map((t) => `
                    <div class="mb-1"><strong>${t.role === "agent" ? "Candidate" : "Caller"}:</strong> ${escapeHtml(t.text)}</div>`).join("");
                const criteria = (r.criteria || []).map((c) => `
                    <li><strong>${c.score === null || c.score === undefined ? "-" : c.score + "/" + c.max}</strong> ${escapeHtml(c.criterion)}${c.comment ? ` <span class="text-muted">&middot; ${escapeHtml(c.comment)}</span>` : ""}</li>`).join("");
                return `
                    <div class="question-review">
                        <div class="question-review-text"><strong>${escapeHtml(item.question)}</strong> &middot; ${pct(r.percent)}
                            ${r.source === "rules" ? '<span class="badge bg-warning text-dark ms-1">AI grading unavailable: please read the call</span>' : '<span class="badge bg-secondary ms-1">AI-assisted score</span>'}</div>
                        <div class="small text-muted my-1" style="white-space: pre-wrap;">${escapeHtml(item.scenario || "")}</div>
                        <div class="my-2 p-2 small" style="background:#f8f9ff;border-radius:8px;">${lines || "(no call took place)"}</div>
                        ${r.summary ? `<div class="small mb-1">${escapeHtml(r.summary)}</div>` : ""}
                        <ul class="small mb-0">${criteria}</ul>
                    </div>`;
            }

            if (item.type === "written") {
                const r = item.result || {};
                const criteria = (r.criteria || []).map((c) => `
                    <li><strong>${c.score === null || c.score === undefined ? "-" : c.score + "/" + c.max}</strong> ${escapeHtml(c.criterion)}${c.comment ? ` <span class="text-muted">&middot; ${escapeHtml(c.comment)}</span>` : ""}</li>`).join("");
                return `
                    <div class="question-review">
                        <div class="question-review-text"><strong>${escapeHtml(item.question)}</strong> &middot; ${pct(r.percent)}
                            ${r.source === "rules" ? '<span class="badge bg-warning text-dark ms-1">AI grading unavailable: please read the answer</span>' : '<span class="badge bg-secondary ms-1">AI-assisted score</span>'}</div>
                        <div class="small text-muted my-1" style="white-space: pre-wrap;">${escapeHtml(item.scenario || "")}</div>
                        <div class="small"><em>${escapeHtml(item.instructions || "")}</em></div>
                        <div class="my-2 p-2" style="background:#f8f9ff;border-radius:8px;white-space:pre-wrap;">${escapeHtml(item.answer || "(no answer)")}</div>
                        ${r.summary ? `<div class="small mb-1">${escapeHtml(r.summary)}</div>` : ""}
                        <ul class="small mb-0">${criteria}</ul>
                    </div>`;
            }

            return `
                <div class="question-review ${item.is_correct ? "correct" : "incorrect"}">
                    <div class="question-review-text">${escapeHtml(item.question)}</div>
                    <div class="question-review-answer">
                        <i class="bi ${item.is_correct ? "bi-check-circle-fill text-success" : "bi-x-circle-fill text-danger"} me-1"></i>
                        Candidate answered: <strong>${escapeHtml(item.answer || "(no answer)")}</strong>
                        ${!item.is_correct ? ` &middot; Correct answer: <strong>${escapeHtml(item.correct_answer)} ${escapeHtml(item.unit || "")}</strong>` : ""}
                    </div>
                </div>`;
        }).join("");

        return `
            <div class="card-surface">
                <details>
                    <summary class="section-label" style="cursor:pointer;">${escapeHtml(title)}: the candidate's actual work (${pct(review.percentage)}) &middot; optional</summary>
                    ${review.needs_review ? '<div class="alert alert-warning small mt-2">Part of this test could not be graded automatically. Please read the answers below.</div>' : ""}
                    ${dataBlock}
                    ${items}
                </details>
            </div>`;
    }

    function renderQuestionReviewBlock(title, review) {

        if (review && review.exists && review.kind === "practical") return renderPracticalReview(title, review);

        if (!review || !review.exists || !review.questions || review.questions.length === 0) return "";

        const rows = review.questions.map(q => `
            <div class="question-review ${q.is_correct ? "correct" : "incorrect"}">
                <div class="question-review-text">${escapeHtml(q.question)}</div>
                <div class="question-review-answer">
                    <i class="bi ${q.is_correct ? "bi-check-circle-fill text-success" : "bi-x-circle-fill text-danger"} me-1"></i>
                    Candidate chose: <strong>${escapeHtml(q.selected_answer || "(no answer)")}</strong>
                    ${!q.is_correct ? ` &middot; Correct answer: <strong>${escapeHtml(q.correct_answer)}</strong>` : ""}
                </div>
            </div>
        `).join("");

        return `
            <div class="card-surface">
                <div class="section-label">${escapeHtml(title)} — Question by Question</div>
                ${rows}
            </div>
        `;
    }

    async function renderQuestionReview(data) {

        const section = document.getElementById("questionReviewSection");
        if (!section) return;

        const sjtExists = data.assessment.situational_judgment_test && data.assessment.situational_judgment_test.exists;
        const tdaExists = data.assessment.technical_domain_assessment && data.assessment.technical_domain_assessment.exists;
        const aptitudeExists = data.assessment.aptitude && data.assessment.aptitude.exists;

        const fetches = [];

        if (aptitudeExists) {
            fetches.push(
                fetch(`${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/review/aptitude`, { credentials: "include" })
                    .then(r => r.json()).then(d => renderQuestionReviewBlock(planTitle(data, "aptitude", "Aptitude Test"), d)).catch(() => "")
            );
        }

        if (sjtExists) {
            fetches.push(
                fetch(`${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/review/sjt`, { credentials: "include" })
                    .then(r => r.json()).then(d => renderQuestionReviewBlock(planTitle(data, "programming", "Situational Judgment Test"), d)).catch(() => "")
            );
        }

        if (tdaExists) {
            fetches.push(
                fetch(`${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/review/technical-domain`, { credentials: "include" })
                    .then(r => r.json()).then(d => renderQuestionReviewBlock("Technical Assessment", d)).catch(() => "")
            );
        }

        const blocks = await Promise.all(fetches);
        section.innerHTML = blocks.join("");

        // a stored web design is drawn from its layout (the same renderer the candidate's editor uses)
        if (window.PracticalDesign) {
            section.querySelectorAll("[data-design-preview]").forEach((node) => {
                try { window.PracticalDesign.renderStatic(JSON.parse(node.getAttribute("data-design-preview")), node, Math.min(720, node.clientWidth || 720)); } catch (error) { /* shown without the picture */ }
            });
        }
    }

    // ---------------------------------------------------------
    // RECORDINGS TAB
    // ---------------------------------------------------------

    const STAGE_VIDEO_LABELS = {
        self_intro: "Self Introduction",
        aptitude: "Aptitude Test",
        ai_interview: "AI Interview",
    };

    // The "programming" video stage tag is shared by programming,
    // SJT, and the technical domain assessment (same storage slot --
    // see ALLOWED_STAGES in assessment_videos.py) since they occupy
    // the same pipeline step. Showing the literal word "Programming"
    // for an SJT/TDA candidate is actively misleading (e.g. a Digital
    // Marketing candidate's recording looking like a coding test), so
    // the label must reflect what this specific candidate actually
    // took, same precedence already used in renderReportTab().
    function resolveStage3Label(data) {

        const programming = data.assessment.programming;
        const programmingV2 = data.assessment.programming_v2;
        const sjt = data.assessment.situational_judgment_test;
        const tda = data.assessment.technical_domain_assessment;

        if (programming || (programmingV2 && programmingV2.exists)) return "Programming / Technical Stage";
        if (sjt && sjt.exists) return "Situational Judgment Test";
        if (tda && tda.exists) return `Technical Assessment (${tda.domain || ""})`;
        return "Programming / Technical Stage";
    }

    function renderRecordingsTab(data) {

        // A saved video has status "uploaded" (the upload route's own value): filtering on "completed" hid every recording.
        // Shown in pipeline order: self introduction, aptitude, programming / SJT / technical, AI interview.
        const STAGE_ORDER = { self_intro: 1, aptitude: 2, programming: 3, ai_interview: 4 };
        const SCREEN_STAGE = /^(self_intro|aptitude|programming|ai_interview)_screen(?:_([23]))?$/;

        // the candidate's camera video first, then the recording of their screen for that stage (and a second / third part, if any)
        const orderOf = (stage) => {
            const screen = SCREEN_STAGE.exec(stage);
            return screen ? STAGE_ORDER[screen[1]] + 0.5 + (screen[2] ? Number(screen[2]) / 100 : 0) : (STAGE_ORDER[stage] || 9);
        };

        const labelFor = (stage) => {
            const screen = SCREEN_STAGE.exec(stage);
            const base = screen ? screen[1] : stage;
            const label = base === "programming" ? stage3Label : base === "aptitude" ? planTitle(data, "aptitude", "Aptitude Test") : (STAGE_VIDEO_LABELS[base] || base);
            return screen ? `${label} — screen${screen[2] ? ` (part ${screen[2]})` : ""}` : label;
        };

        const recordings = (data.recordings || [])
            .filter(r => r.status === "uploaded" || r.status === "completed" || r.status === "partial")
            .sort((a, b) => orderOf(a.stage) - orderOf(b.stage));
        const stage3Label = resolveStage3Label(data);

        if (recordings.length === 0) {
            reportContainer.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon"><i class="bi bi-camera-video-off"></i></div>
                    <div class="empty-title">No recordings available</div>
                    <div class="empty-text">This candidate has no completed assessment recordings yet.</div>
                </div>
            `;
            return;
        }

        const stageButtons = recordings.map((r, i) => `
            <button type="button" class="video-stage-btn ${i === 0 ? "active" : ""}" data-recording-id="${r.id}" data-index="${i}">
                <i class="bi bi-play-circle"></i>
                ${escapeHtml(labelFor(r.stage))}
                ${r.status === "partial" ? '<span class="badge text-bg-warning ms-1" title="The candidate left before finishing this stage: this is what was recorded.">partial</span>' : ""}
                <span class="text-muted ms-auto" style="font-weight:400;font-size:12px;">${formatDuration(r.duration_seconds)}</span>
            </button>
        `).join("");

        reportContainer.innerHTML = `
            <div class="row">
                <div class="col-md-3">
                    <div class="card-surface">
                        <div class="section-label">Stages</div>
                        <div class="video-stage-list">${stageButtons}</div>
                    </div>
                </div>
                <div class="col-md-9">
                    <div class="fw-semibold mb-2" id="recordingHeading">${escapeHtml(labelFor(recordings[0].stage))}</div>
                    <div class="video-player-wrap">
                        <video id="recordingPlayer" controls src="${API_BASE_URL}${recordings[0].stream_url}"></video>
                    </div>
                    <div class="card-surface mt-3" id="behaviorBox"></div>
                </div>
            </div>
        `;

        // Under the video: what the recorded signals show about how the candidate conducted themselves in THIS stage.
        const behaviorCache = {};

        async function showBehavior(recording) {

            const box = document.getElementById("behaviorBox");
            if (!box) return;

            if (!behaviorCache[recording.id]) {

                box.innerHTML = '<div class="text-muted small"><span class="spinner-border spinner-border-sm me-2"></span>Summarising what was recorded in this video…</div>';

                try {
                    const response = await fetch(`${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/recordings/${recording.id}/behavior`, { credentials: "include" });
                    if (!response.ok) throw new Error("unavailable");
                    behaviorCache[recording.id] = await response.json();
                } catch (error) {
                    box.innerHTML = '<div class="text-muted small">The behaviour summary could not be loaded right now.</div>';
                    return;
                }
            }

            const data = behaviorCache[recording.id];
            const signals = (data.signals || []).map((s) => `<li>${escapeHtml(s.label)}${s.count > 1 ? ` <span class="text-muted">(${s.count}\u00d7)</span>` : ""}</li>`).join("");

            box.innerHTML = `
                <div class="fw-semibold mb-1">Observed behaviour in this video</div>
                <p class="mb-2">${escapeHtml(data.summary)}</p>
                ${signals ? `<details class="small"><summary>Signals this is based on</summary><ul class="mb-0 mt-1 ps-3">${signals}</ul></details>` : ""}
                <p class="text-muted small mt-2 mb-0"><i class="bi bi-info-circle me-1"></i>Written only from what the platform measured (face in view, looking away, tab changes, speaking pace and filler words). It does not judge emotions, confidence or personality, and it is not a hiring recommendation.</p>`;
        }

        showBehavior(recordings[0]);

        reportContainer.querySelectorAll(".video-stage-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                reportContainer.querySelectorAll(".video-stage-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                const index = Number(btn.dataset.index);
                const player = document.getElementById("recordingPlayer");
                const chosen = recordings[index];
                document.getElementById("recordingHeading").textContent = labelFor(chosen.stage);
                player.src = `${API_BASE_URL}${chosen.stream_url}`;
                player.play().catch(() => {});
                showBehavior(chosen);
            });
        });
    }

    // ---------------------------------------------------------
    // TABS + LOAD
    // ---------------------------------------------------------

    function renderTabs() {

        const existingTabRow = document.querySelector(".tab-row");
        if (existingTabRow) existingTabRow.remove();

        const tabRow = document.createElement("div");
        tabRow.className = "tab-row";
        tabRow.innerHTML = `
            <button type="button" class="tab-btn ${activeTab === "report" ? "active" : ""}" data-tab="report">
                <i class="bi bi-file-earmark-bar-graph me-1"></i> Intelligence Report
            </button>
            <button type="button" class="tab-btn ${activeTab === "recordings" ? "active" : ""}" data-tab="recordings">
                <i class="bi bi-camera-video me-1"></i> Assessment Recordings
            </button>
        `;

        reportContainer.parentElement.insertBefore(tabRow, reportContainer);

        tabRow.querySelectorAll(".tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                activeTab = btn.dataset.tab;
                renderTabs();
                if (activeTab === "report") {
                    renderReportTab(reportData);
                } else {
                    renderRecordingsTab(reportData);
                }
            });
        });
    }

    async function loadReport() {

        try {

            const response = await fetch(
                `${API_BASE_URL}/api/company-applications/${company.id}/${applicationId}/candidate-intelligence-report`,
                { credentials: "include" },
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Unable to load this candidate's report.");
            }

            reportData = data;

            headerName.textContent = data.candidate.full_name || "Candidate Report";
            headerSub.textContent = `${data.job.title || "Role"} at ${data.job.company || "Company"}`;

            renderTabs();
            renderReportTab(data);

        } catch (error) {

            reportContainer.innerHTML = `
                <div class="error-state">
                    <div class="empty-icon"><i class="bi bi-exclamation-triangle"></i></div>
                    <div class="empty-title">Could not load candidate report</div>
                    <div class="empty-text">${escapeHtml(error.message || "Please try refreshing.")}</div>
                </div>
            `;
        }
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

    applicationId = getApplicationIdFromUrl();

    if (!applicationId) {
        reportContainer.innerHTML = `
            <div class="error-state">
                <div class="empty-icon"><i class="bi bi-exclamation-triangle"></i></div>
                <div class="empty-title">No candidate specified</div>
                <div class="empty-text">Go back to Candidate Tracking and open a report from there.</div>
            </div>
        `;
        return;
    }

    loadReport();

});

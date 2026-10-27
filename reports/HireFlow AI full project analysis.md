# HireFlow AI: full project analysis

Date: 2026-10-27. Based on a read of the repository (structure, size, README, key modules) and the work done in this project. Figures marked "counted" were counted from the files; the rest is judgement. I did not run every test or load-test the system.

---

## 1. What HireFlow AI is

An AI-assisted candidate assessment platform for HR teams.

**The flow**

1. HR uploads a candidate's resume and describes the role.
2. The AI weighs the resume against the role and picks which assessments the candidate gets.
3. The candidate gets an emailed magic link (no password). Opening it builds their account and profile from the resume.
4. The candidate does a proctored sequence: self introduction, role test, hands-on task, then a live spoken AI interview (10 questions).
5. HR reads one report per candidate: scores, an AI summary, an integrity breakdown, a face check, and can send an offer letter.

**Size (counted)**

| Item | Count |
|---|---|
| Backend Python files | 276 (about 76,000 lines) |
| API route files | 30 routers (about 23,000 lines) |
| Database models | about 45 |
| Frontend pages | 27 HTML (3 are temporary previews) |
| Frontend JavaScript | 50 files (about 30,000 lines) |
| Backend test functions | about 984 in 99 files |
| Frontend (Node) test files | 22 |
| Roles in the catalogue | 96 IT roles in 16 domains |
| Code-run languages | Python, Java, JS, C, C++, C#, Go, Rust, Kotlin, PHP, Ruby, Swift and more via Docker |

---

## 2. What is good

**Product**
- A complete journey in one product: resume, invite, assessments, interview, proctoring, report, offer letter. Most competitors cover one piece.
- Candidate friction is low: a magic link, no password, and a profile pre-filled from the resume.
- HR gets one report per candidate with plain-language integrity text, not a wall of raw events.

**Engineering**
- Real test discipline: about 984 backend tests plus 22 frontend logic suites, including tests for interview script rules, proctoring rules and page wiring.
- Safe code execution: one throw-away Docker container per run, no network, memory/CPU/process limits.
- Server-owned interview script: the server arms the model with the next step, counts the question position and classifies non-answers. This is why the interview stays on structure even when the model drifts.
- Defensive habits: CSRF protection, httpOnly session cookies, hashed passwords (passlib), email verification with attempt limits, backups before risky edits, one-step undo on page changes.
- Provider flexibility: realtime / Groq pipeline / Gemini pipeline switches, fixed-image fallback if the 3D globe cannot start, stall and closing watchdogs.
- LLM usage logging exists (`llm_usage_log`), which is the base for cost control.

**Responsible design**
- Signals are split into strikes (count toward ending the test) and flags (for a human to read). The face check is labelled a recruiter aid, not identity verification.
- Candidate-facing rules give no timings or limits, which makes gaming harder.
- Integrity risk is shown as a level with reasons, not a single score that decides for HR.

---

## 3. What is unique

Compared with typical assessment tools (coding-test sites, one-way video interview tools):

1. **Per-job assessment plan.** A catalogue of 96 roles plus an AI planner for unknown titles decides the tasks. A DevOps candidate gets a log/config lab, a finance candidate gets a data task, a designer gets a drag-and-drop page builder, a sales candidate gets a voice role-play. Most tools give everyone the same test.
2. **Hands-on work for non-coding roles.** Written tasks marked against a rubric, data tasks whose answers are computed by code, and a design builder marked by code metrics plus a vision model.
3. **A live, adaptive, spoken interview built from the candidate's own resume and the job,** with six role questions, two about AI use, a job-fit question and expected salary last, and detection of manipulation attempts.
4. **Resume-to-account automation.** Opening the invite builds the resume record, analysis, name, phone, links and a filled profile.
5. **Layered proctoring in the browser** (face landmarks, phone detection, lock-down, automated-typing detection, lighting coach) with the strike/flag split.
6. **The AI interviewer as a live 3D smoke globe** that reacts to the AI's real voice level, with the candidate's own camera tile kept. This is a visible, memorable differentiator for a demo.
7. **Runs on the user's side at low cost:** the browser does the heavy proctoring work, and local speech options exist.

---

## 4. What can be done better

Ordered by how much each matters before real customers or a serious demo.

### 4.1 Security and privacy (highest priority)
- **Login protection:** I found attempt limits on the email-verification code but did not find rate limiting or lockout on password login itself. Add per-account and per-IP throttling.
- **Admin surface:** the admin panel pages were removed, but the admin router is still mounted in `main.py`. Either remove it or protect and document it.
- **Candidate data on disk:** several copies of the database (`jobpilot.db.forensic-copy-*`, `incident-preserve-copy`, `recovery-work`), the `data/` folder and `assessment recordings/` hold real candidate data. They are git-ignored, which is good, but should be deleted or moved to encrypted storage.
- **Consent and retention:** biometric-style data (face photos, voice, video, screen) needs a clear notice, a stored consent record, a retention period and a delete-on-request path. India's DPDP Act and the EU GDPR both expect this. Part of this exists (assessment consents); a retention and deletion policy does not appear to.
- **Secrets:** keys live in `.env` (ignored). Rotate any key that has ever been shown in a chat or screenshot, and move to a secret manager when deployed.

### 4.2 Reliability
- **Gemini Live is a preview model** and has stalled before. Keep the watchdogs, and add a visible "reconnecting" state plus an automatic fallback to the pipeline mode.
- **Tavus avatar is the room identity,** which couples video and voice. A failure there can break the voice connection. Decouple if possible.
- **A full end-to-end live run of the 10-question interview** has not been done on every role family. Do one per family before real use.

### 4.3 Scalability and deployment
- **SQLite** is fine for a demo and one server. It will not handle many concurrent assessments with recordings. Plan a move to PostgreSQL.
- **No CI and no app container:** there is no `.github` workflow and no Dockerfile for the app itself (only sandbox and Judge0/Piston compose files). Add a CI job that runs the tests, and a `docker compose up` for the whole app.
- **Local file storage** for recordings and snapshots should move to object storage (S3-compatible) with expiry.
- **Background work** (marking, emails, report building) should run in a queue so a slow model call never blocks a request.

### 4.4 Code health
- **Very large files:** `company_applications.py` (3,551 lines), `hr_assessment_review.py` (2,046), `assessment_sessions.py` (1,742), and several 1,600-2,000 line frontend scripts. Split by responsibility when touching them.
- **Duplication:** `hash_password` exists in both `companies.py` and `users.py`. Move shared auth helpers to one module.
- **Dead and stray files:** `hr_assessment_review.py.bak`, root clutter (`query.py`, `query_db.py`, `db_investigation.txt`, `duplicate comp.txt`, `New Text Document.txt`, the `.avif` image), the unused job-CRUD routes (`company_jobs.py`), three temporary orb preview pages, and two demo pages. Move to `_archive` or delete.
- **Naming:** `jobpilot` and `HireFlow` are mixed in code, docs and data folders. Pick one.
- **Stale documentation:** the README still says the frontend runs on port 5500 (now 5600, because 5500 is blocked on this Windows machine) and mentions whole-screen sharing in the interview (now off). The README's "about 1,300 tests" is higher than the roughly 984 test functions I counted; recheck.
- **Tests depend on environment:** some need Docker or `.env` defaults. Make them skip cleanly with a clear message.

### 4.5 Product and UX
- **Performance on weak computers:** the proctoring (MediaPipe, YOLO) plus a 3D globe is heavy. The globe now adapts its quality; add a "system check" step that measures the machine and warns before the test starts.
- **Mobile and tablet:** assessments assume a desktop browser with fullscreen. Show a clear "use a laptop" message instead of failing.
- **Accessibility:** captions exist for the interview; add keyboard access checks, screen-reader labels on all test pages, and extra-time accommodations HR can grant.
- **Language:** the interview is English. Indian customers will expect Hindi and regional languages for some roles.
- **HR report:** add PDF export and a side-by-side compare view for shortlisting.
- **Consistent design:** the home page and HR dashboard are now in the new light glass style; the other HR pages and all candidate pages still use the older look.

### 4.6 Fairness and trust
- Proctoring can produce false flags (poor lighting, shared rooms, glasses, bad cameras). Keep flags as signals for a human, add an appeal or "explain" step for candidates, and track false-positive rates.
- The AI summary and scores should show their evidence (quotes, test items) so HR can check them. Avoid any single ranking that auto-rejects.
- Run a periodic bias review on scoring (by role, language, accent) before using it for hiring decisions. Some jurisdictions (for example New York City) already require this.

---

## 5. What to add (roadmap)

**Next (small, high value)**
1. Candidate LinkedIn export upload and GitHub/portfolio link enrichment in the invite flow (consent-based; see the separate LinkedIn report).
2. Bulk invites: upload a CSV or a folder of resumes for one role, with one AI plan and a progress table.
3. Login rate limiting and lockout; remove or secure the admin router.
4. System-check step before each assessment (camera, mic, bandwidth, CPU).
5. PDF export of the HR report and a compare view.
6. Clean-up of stray files and the README.

**Soon**
7. PostgreSQL, object storage and a job queue; CI plus a one-command deployment.
8. HR team accounts with roles (a `company_recruiter` model already exists) and an audit log of who viewed or changed what.
9. Webhooks and ATS integrations (Greenhouse, Lever, Zoho Recruit, Naukri-style imports).
10. Calendar scheduling with reminders (a reminder scheduler service exists).
11. Candidate feedback report (strengths and gaps) as a value-add that also improves candidate experience.
12. Cost dashboard from `llm_usage_log`: cost per candidate, per role, per provider.

**Later**
13. Multi-language interview and tests (Hindi, Tamil, Telugu and others).
14. Question review and approval workflow for HR, and a shared question library.
15. Fairness and quality analytics: score distributions, false-flag rates, interviewer drift.
16. A mobile-friendly candidate experience for the non-proctored stages (self intro, written tasks).
17. Learning recommendations for rejected candidates (a `learning_recommendation` model already exists).

---

## 6. Demo and mentor talking points

- "It builds the right test for each role, not one test for everyone."
- "It interviews the candidate about their own resume, in a live voice, with a script the server controls."
- "Integrity is shown as evidence for a human, not a verdict."
- "We use only data the candidate gives us plus public developer profiles; we verify skills ourselves."
- Be ready to state the limits honestly: preview voice model, SQLite, English only, desktop only, one full live run per role family still to do.

## 7. Risks to watch before a live demo

| Risk | Mitigation |
|---|---|
| Gemini Live stalls or outage | Fallback pipeline mode; keep a recorded demo run |
| Low disk on C: slows the machine | Free at least 15 GB; close heavy apps |
| Docker not running (coding test) | Start Docker Desktop before the demo |
| Wrong port or CORS | Use `start_jobpilot.bat` (frontend 5600, backend 8000) |
| Candidate session left half done | Use a fresh invite for the demo |

---

## 8. Summary

HireFlow AI is already unusually complete for a solo project: a full pipeline, per-role assessments, a controlled live interview and layered integrity checks, backed by a large test suite. The biggest gaps are not features but production readiness: login protection, data retention and consent, database and storage choices, CI and deployment, and file hygiene. Fix those, add bulk invites and consent-based profile enrichment, and it is ready to pilot with a small number of real HR users.

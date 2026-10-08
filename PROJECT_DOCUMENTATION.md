# HireFlow AI — Project Documentation

**An AI-powered recruitment assessment platform.** HR uploads a candidate's resume for a role; HireFlow analyzes it, emails the candidate a one-click assessment link, and runs them through a role-appropriate pipeline — aptitude, a real coding/domain test, and a live AI voice interview — all under camera/microphone proctoring. HR gets back a single intelligence report per candidate: scores, per-question review, a narrative summary, and the full recorded video.

---

## 1. What the product actually does, end to end

1. **HR registers** (auto-verified — no manual admin approval step) and logs in.
2. **HR invites a candidate**: types a job title + free-text requirement, uploads the candidate's resume, and HireFlow's AI classifies the role (technical/non-technical, and for technical roles, which language or domain) and weighs the resume against the requirement.
3. **The candidate gets an emailed magic link.** One click creates their account and drops them into the pipeline — no separate signup.
4. **Self-introduction** (camera/mic proctored) → **Aptitude test** (mandatory for everyone) → one of:
   - **Programming test** (if the role names a language HireFlow can execute — 15 languages via Docker/SQLite/headless browser)
   - **Technical Domain Assessment** (if technical but not code-executable — 11 domain knowledge banks: Salesforce, SAP, Blockchain, RPA, Embedded, GIS, Workday, DevOps/Cloud, Networking, Cybersecurity, NoSQL)
   - **Situational Judgment Test** (if non-technical — HR, Sales, Marketing, Finance, etc.)
5. **AI Interview** — a live, voice-based conversation (Groq-driven questions, Gemini Live for realtime voice), adaptive to the candidate's actual programming/SJT/TDA results, 4–8 questions, ends with a warm, specific closing message.
6. **HR reviews the result**: a per-candidate Intelligence Report (stage-by-stage scores, per-question correctness, an AI-generated narrative summary that never issues a hire/reject verdict) and a video-recording review tab (every stage's recording, playable one by one).

Throughout, every stage is proctored: mandatory camera/mic, periodic + start + end identity photos, face/gaze tracking, phone detection, fullscreen lock, and a 3-strike violation system that auto-terminates the assessment.

---

## 2. Role classification — how "which assessment" gets decided

A job is **never** classified by its title — only by its `required_skills`/description text, via `role_classification.py`. This means the system generalizes to any new job title, not just a fixed list.

| Signal found in the job's skills/description | Routes to |
|---|---|
| A real executable language (Python, JS, TS, Java, C, C++, Go, Rust, Kotlin, PHP, Ruby, C#, Swift, SQL, HTML) | **Programming** — real code execution (Docker sandbox, or SQLite for SQL, or a headless browser for HTML/CSS) |
| Technical, but no executable language named (e.g. Salesforce, SAP, Blockchain, RPA, Embedded, GIS, Workday, DevOps/Cloud, Networking, Cybersecurity, NoSQL) | **Technical Domain Assessment** — one of 11 curated knowledge/scenario banks |
| Nothing technical (HR, Sales, Marketing, Finance, Legal, Customer Service, Project Management, etc.) | **Situational Judgment Test** |

HR can also **override** the auto-detected classification from the Invite-a-Candidate form — the AI's guess is shown live as HR types, editable with one click, never a silent decision.

Verified against a reference list of **623 real IT-industry job titles across 22 departments** — all correctly classified.

---

## 3. Candidate-facing pages

| Page | Purpose |
|---|---|
| `index.html` | Marketing/landing page, HR login + Admin login entry points |
| `company-register.html` / `company-login.html` | HR account creation and login |
| `self-intro.html` | Camera-proctored self-introduction (first real stage) |
| `aptitude-test.html` | 10-question adaptive aptitude test (20 min, 70% pass mark) |
| `programming-v3.html` | Live coding assessment — Monaco-style editor, Run Code, Live Preview tab for web questions |
| `sjt-test.html` | Situational Judgment Test (MCQ, scenario-based) |
| `tda-test.html` | Technical Domain Assessment (MCQ, domain-specific) |
| `ai-interview-v2.html` | Live voice AI interview — video-call style UI, real-time captions, pre-join device-check lobby |

All proctored pages share: `assessment-integrity.js` (camera/fullscreen/violation orchestration), `face-proctor.js` (MediaPipe face/gaze), `phone-detector.js` (YOLOv8 object detection), `audio-proctor.js` (silent-stage voice detection), `exam-lock.js` (fullscreen lock screen), `assessment-video-recorder.js` (per-stage video recording).

---

## 4. HR-facing pages

| Page | Purpose |
|---|---|
| `company-dashboard.html` | Overview + real funnel analytics (Candidates Invited / Not Started / In Progress / Completed) |
| `company-profile.html` | HR profile, company logo upload, verification status |
| `company-invite-candidate.html` | Invite a candidate — job title/requirement, resume upload, live AI role-type detection with manual override |
| `company-candidate-tracking.html` | Every invited candidate, their current pipeline stage, deadline, and a link into their report |
| `company-candidate-report.html` | Per-candidate Intelligence Report (scores, narrative summary, per-question review) + Assessment Recordings tab |
| `company-settings.html` | Account settings (email/username/password) |

---

## 5. Admin Console (platform operator tools)

New this phase — a real, live-wired admin panel, not a mockup.

| Page | Purpose |
|---|---|
| `admin-login.html` | Admin sign-in (same `User` table, `role="admin"`) |
| `admin-dashboard.html` | HR/Company Registry (suspend/reactivate/warn/delete), platform-wide Candidate Invites view, live System Connectors status, account Settings (change password) |

**Deliberately excluded**: no "approve/reject new HR account" workflow — registration already auto-verifies. Admin's role is purely platform moderation (suspend, warn, delete) and operational visibility, never a signup gatekeeper.

**System Connectors** shows real, live-checked data (not a static snapshot): which AI provider powers each assessment stage, whether API keys are actually configured, and whether the Docker sandbox daemon is actually reachable right now.

---

## 6. AI / LLM providers — what powers what

| Stage | Provider | Model |
|---|---|---|
| Aptitude question generation | Groq | `openai/gpt-oss-120b` |
| Programming question generation | Groq | `openai/gpt-oss-120b` |
| Situational Judgment Test | — | Curated static question bank, no AI call |
| Technical Domain Assessment | — | Curated static question bank (11 domains), no AI call |
| AI Interview — brain (question selection, follow-ups) | Groq | `openai/gpt-oss-120b` |
| AI Interview — realtime voice | Gemini Live | `gemini-3.8-live` |
| AI Interview — speech-to-text | Local (on-server) | `faster-whisper` |
| AI Interview — text-to-speech | Gemini TTS | rotating Gemini TTS models |
| HR narrative summary | Groq | `openai/gpt-oss-120b` |

All settings are currently `.env`-configured, read once at server startup (changing a provider/model/temperature requires an env edit + restart — no hot-reload yet).

---

## 7. Security & Proctoring

| Capability | Status |
|---|---|
| Start-of-exam identity photo | ✅ Built |
| Periodic photos during the exam (every 2 min) | ✅ Built |
| End-of-exam photo | ✅ Built |
| Multiple-person / third-party-face detection | ✅ Built (MediaPipe, 2+ faces for 2.5s) |
| Phone/gadget detection | ✅ Built (YOLOv8, ~2.4s sustained detection required) |
| Fullscreen lock (exits lock the exam behind an overlay) | ✅ Built — aptitude/programming/SJT/TDA; AI Interview intentionally excluded (candidate is expected to talk/move) |
| Gaze tracking | ✅ Built — quick glance is a soft flag only; 20+ seconds sustained look-away is a real counted violation |
| Voice detection during silent stages | ✅ Built — any sustained voice during aptitude/programming/SJT/TDA (where no talking is expected) is a real counted violation |
| Third-party voice detection during the AI interview | ✅ Built — pitch-deviation heuristic against the candidate's own calibrated voice; deliberately a flag for HR review, never an auto-violation (not verified speaker identity) |
| Camera/microphone mandatory to start any stage | ✅ Built — across all 5 assessment types, cannot be dismissed or bypassed |
| 3-strike auto-termination | ✅ Built, server-computed (never trusts client-reported counts), wired for all 5 assessment types |
| Facial expression / mood / emotion detection | ❌ Deliberately not built — avoided for accuracy/bias/privacy reasons, matching industry best practice |
| Full HR work-email domain verification | ❌ Not built — researched, recommended as a future addition (block free email providers at registration, and/or a real click-to-verify email link; the `email_verified` DB field exists but nothing sets it yet) |

---

## 8. Technology stack

- **Backend**: Python, FastAPI, SQLAlchemy, SQLite (`jobpilot.db`)
- **Frontend**: Vanilla HTML/CSS/JS (no framework/build step), Bootstrap 5, Bootstrap Icons
- **AI/LLM**: Groq (`openai/gpt-oss-120b`), Google Gemini (Live voice, TTS)
- **Speech-to-text**: `faster-whisper`, running locally on the server
- **Code execution**: Docker (13 languages), in-process SQLite sandbox (SQL), headless browser (HTML/CSS/web questions)
- **Face/gaze detection**: MediaPipe Face Landmarker (WASM, runs in-browser)
- **Phone/object detection**: YOLOv8n (ONNX Runtime Web)
- **Video calling**: LiveKit (AI Interview's realtime room)

---

## 9. Testing

- **64 backend test files**, **17 frontend (Node-based) test files**
- Full backend suite: see the latest run's pass/fail summary below
- Frontend suites cover: face-proctor engine, audio-proctor engine, voice-identity-proctor engine, exam-lock, integrity-guards, interview voice/avatar/video-room logic (LiveKit), AI interview voice page wiring
- Backend suites cover: every assessment type's integration/scoring/evidence logic, role classification, stage progression, HR visibility, the admin console, camera proctoring event classification, interview integration (including the SJT/TDA-can't-reach-the-interview bug fix), and more

<!-- TEST_RESULTS_PLACEHOLDER -->

---

## 10. Known gaps (honest, not hidden)

- **Production deployment**: not started — no hosting, no domain, no environment separation from this dev machine.
- **Security/penetration review**: not started.
- **Load testing**: not started — concurrent-candidate behavior and LLM API rate limits under real traffic are unverified.
- **AI cost/token tracking**: tokens are read per API call but never persisted (overwritten every call, lost on restart) — no usage dashboard is possible until a real logging table exists.
- **Hot-reloadable model/prompt config**: every AI setting is `.env` + restart only; no database-backed config system yet.
- **Content moderation / prompt-output audit log**: LLM prompts and outputs are used once and discarded — no system stores them for later review.
- **HR work-email verification**: researched, not implemented (see Security section above).
- **Candidate-level admin management**: the Admin Console currently manages HR/company accounts; candidate-level ban/suspend controls were not requested/built yet.

---

## 11. Summary

The core product — every assessment type, every role category, the full candidate journey, the HR review workflow, and a working admin console — is **functionally complete and verified**, much of it tested live end-to-end against the real database, not just unit tests. What remains is entirely in the "operate this at scale and in public" category: deployment, security hardening, load testing, and the AI-ops tooling (cost tracking, hot-reload config, content audit logs) that only matters once there's real, paying, production traffic.

---

*Generated as a snapshot of the project's state. Re-generate or update sections as the codebase evolves — this file is not auto-synced.*

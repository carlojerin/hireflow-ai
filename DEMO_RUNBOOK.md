# HireFlow AI: demo runbook

## Before you start (once)
1. `.env`: `ASSESSMENT_MAX_VIOLATIONS=25` for the demo (set back to `3` afterwards). Restart the backend. The test page now shows the real limit (for example `0/25`).
2. Docker Desktop running (only the Backend Developer coding test needs it).
3. Backend on 127.0.0.1:8000 and frontend on 127.0.0.1:5500, in Chrome, hard-refresh (Ctrl+Shift+R).
4. Microphone and camera allowed for 127.0.0.1. For each assessment: choose **Entire Screen** in the share dialog.

## Seven links, one per assessment type (all for the same demo candidate)
| # | Role | Stage 2 | Stage 3 | Link token |
|---|------|---------|---------|-----------|
| 1 | Backend Developer | Python skills quiz | **Coding** (Docker) | `ugZVHQwJkLNjR5yWAQQ5Y5Iyj3CXc22_` |
| 2 | DevOps Engineer | Knowledge quiz | **Technical lab** (config review) | `90Tx2JlPfxuwB3lubjA4S-hGvX1e_qJ3` |
| 3 | SAP Consultant | SAP skills quiz | **Platform question bank** | `AecOQdP9O5Ol6jNCIWGcbFiY-KYdrBXP` |
| 4 | Accountant | Accounting quiz | **Written task** (finance) | `CziPzaftFpLIoMNViyrdUi0Ew3JTdm4s` |
| 5 | Manual QA Tester | Quiz | **Written task** (test cases and bug report) | `v3Ovmj4kdiNTBkmKVQE0OegSGppkulLD` |
| 6 | Customer Support Executive | Communication quiz | **Written task** (customer email) | `-WMDVio6CtNxe170SCisUei6lRirHbrV` |
| 7 | UI/UX Designer | Design quiz | **Drag-and-drop design page** (vision-marked) | `8kE1x2G5__S9wJD6Xvklo_G0EIYoEO9Y` |
| 8 | Sales Executive | Sales quiz | **Voice role-play** (needs mic) | `geHyhLa36iS8nmDxLfXr5ZuW61noVApn` |

Link format: `http://127.0.0.1:5500/start-assessment.html?token=<token>`. Each link works for 3 days.

## What to check at each step
- Self introduction submits and unlocks the next stage.
- Stage 2 quiz: questions are about the role; the score appears; pass is 70% or higher.
- Stage 3 (per table): it opens, accepts answers, **Submit works** and shows a result.
- AI interview (do this on one role): questions 1–5 role-based, 6 how you would use AI in the role, 7 salary, 8 company-fit/anything else; no early "end of the interview"; the closing speech at the end.
- HR "intelligent report": every stage shown with its score; the AI marking is explained; the offer-letter button is there.

## For the mentor demo
- Show the invite form with a plain title ("Accountant") and a one-line requirement; the preview shows which assessment the AI chose and why.
- Show the DevOps lab (exact answers marked by code) and the design page (marked by a vision model).
- Show the HR report only; HR never reads raw answers.

## Known limits (be honest if asked)
- Platform question banks (SAP, Salesforce, Workday, RPA, GIS, embedded) have few questions.
- Vision marking can take 10–60 s; rate limits on the AI providers can slow marking, and the planner then falls back to rules.
- Not built: HR email verification, face-match, lighting coach.

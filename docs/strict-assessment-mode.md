# Task 4D-STRICT-1 — Strict Assessment Mode: Architecture & Security Notes

## 1. Overview

Strict Assessment Mode is a reusable foundation — not a single-stage feature —
for making a timed assessment behave like a controlled online examination:
fullscreen, tab/focus monitoring, a server-recorded integrity trail, a
configurable violation policy, and server-authoritative termination. This task
builds that foundation and integrates it into exactly one stage: **Aptitude**.
Programming and the AI Interview are untouched; they can adopt the same
reusable module and API later without any of this logic being duplicated.

## 2. What a browser can and cannot actually do

This is the most important limitation to state plainly, because it shapes
every design decision below:

**A website cannot forcibly prevent a candidate from switching operating-
system applications, and cannot guarantee a browser tab cannot be changed.**
There is no browser API that locks the OS-level window manager. Fullscreen
mode can be exited by the user (or the browser itself, e.g. on `Esc`), and a
candidate can always Alt-Tab to another application without any web page
being able to stop it or even always detect it (an OS-level app switch that
never touches the browser window at all is invisible to JavaScript).

What Strict Assessment Mode actually does is react to the signals a browser
**does** genuinely expose:

- `document.visibilityState` / `visibilitychange` — reliably fires when a tab
  is hidden (switched away from, minimized, or the OS focus moves to another
  app in most browsers/OSes).
- `window.blur` / `window.focus` — fires when the browser window itself loses
  or regains OS-level focus.
- `fullscreenchange` — fires when fullscreen is entered or exited, by the
  user, the browser, or a page navigation.

None of these are a security boundary. They are **signals**, reported to the
server, which is the actual source of truth for counting violations and
deciding whether an assessment continues. The UI never claims otherwise —
see the exact banner/warning copy in Section 10.

## 3. Architecture

```
frontend/js/assessment-integrity.js   (reusable, page-agnostic)
        |
        | enterStrictMode() / reportIntegrityEvent()
        v
POST /api/assessment/integrity/event  (backend/app/api/routes/assessment_integrity.py)
GET  /api/assessment/integrity/state
        |
        | authenticate, verify ownership, verify the underlying
        | test is actually active
        v
backend/app/services/assessment/integrity_service.py
        |
        | dedup/cooldown, count violations, decide warning level,
        | terminate via a per-assessment-type finalizer
        v
AssessmentIntegrityEvent rows (audit trail)  +  AptitudeTest.status="terminated"
```

The route layer (`assessment_integrity.py`) does the same three things every
other candidate-facing endpoint in this project does — authenticate
(`get_current_user`), verify ownership (`require_self` + the same
`application_id` + `user_id` combined-filter pattern used by
`aptitude_tests.py`/`programming_tests.py`/`interview_tests.py`), and check
CSRF (`require_csrf`) — then verifies the specific test is actually active
before handing off to the service layer. **All** counting/dedup/policy logic
lives in exactly one place: `integrity_service.py`. Nothing about it is
Aptitude-specific except the one finalizer function it currently has
registered.

### Why `application_id`, not `assessment_session_id`

`AssessmentSession` (Task 4A) is stage-orchestration state, not every test's
ownership anchor. A `AptitudeTest` created through the older, still-live
HR-assign flow (`POST /api/company-applications/{id}/{id}/aptitude`) never
touches `AssessmentSession` at all. Every test row — Aptitude, Programming,
Interview — is instead always anchored to `application_id`, which is what
every existing candidate-facing endpoint already uses for ownership checks.
`AssessmentIntegrityEvent.assessment_session_id` is still recorded
opportunistically (when a session row exists) for traceability, but it is
never used for ownership or authorization.

## 4. Database model — `AssessmentIntegrityEvent`

One additive table (`backend/app/models/assessment_integrity_event.py`), no
`relationship()` attributes (matching every other model in this codebase):

| Column | Purpose |
|---|---|
| `application_id` | Ownership anchor (always populated). |
| `assessment_session_id` | Populated when a session row exists; traceability only. |
| `assessment_type` | `"aptitude"` \| `"programming"` \| `"ai_interview"`. |
| `event_type` | See Section 5. |
| `severity` | `"info"` \| `"violation"` \| `"duplicate"` \| `"warning"`. |
| `counted_as_violation` | `True` only for a violation-type event that survived dedup — lets violation counting be a plain filtered `COUNT(*)`. |
| `occurred_at` | Server-recorded receipt time — never accepted from the client. |
| `metadata_json` | Small, server-generated JSON only (e.g. `{"violation_count": 2}`) — never client content. |

**Never stores**: screenshots, video, audio, camera frames, keystrokes, mouse
coordinates, or any personal data beyond the existing `application_id` link.

Registered additively in `database.py` (new `CREATE TABLE`, zero changes to
any existing table's data). `AptitudeTest` gained one new nullable column,
`exam_expires_at` (via the existing `migrate_database()` `ALTER TABLE`
pattern) — see Section 8.

## 5. Integrity event types

Client-reportable:

- `fullscreen_entered`, `window_focus`, `assessment_page_left`,
  `assessment_page_returned` — **informational only**, never count as a
  violation, always recorded for the audit trail.
- `fullscreen_exited`, `tab_hidden`, `window_blur` — **violation-eligible**,
  subject to the dedup/cooldown rule below before they count.

Server-only (never accepted in a request body — rejected as an invalid
`event_type` if a client tries):

- `violation_warning` — written by the server itself the moment a counted
  violation crosses into `"warning"` or `"final_warning"`.
- `assessment_auto_terminated` — written by the server itself when
  termination actually happens.

Deliberately **not** an event type at all: mouse movement, keystrokes,
scrolling, or clicking inside the exam. There is no code path that could ever
classify ordinary interaction as a violation — the schema itself doesn't
accept those signals.

## 6. Violation policy

```python
WARNING_LIMIT = 2
TERMINATION_LIMIT = 3
```

- 1st counted violation → `warning_level = "warning"`.
- 2nd counted violation → `warning_level = "final_warning"`.
- 3rd counted violation → `warning_level = "terminated"`, and the assessment
  is finalized server-side (Section 9).

## 7. Deduplication / cooldown

```python
VIOLATION_COOLDOWN_SECONDS = 5
```

A violation-eligible event is only counted if it did not occur within 5
seconds of the last **counted** violation for the same
`(application_id, assessment_type)`. A single real tab-switch typically fires
both `tab_hidden` and `window_blur` within the same instant — the second one
is still recorded (for the audit trail, `severity="duplicate"`,
`counted_as_violation=False`) but does not advance the violation count or
warning level. This is a single, centralized rule in `integrity_service.py`;
there is no separate copy of it anywhere in the route layer or the frontend.

## 8. Aptitude timer — 20 minutes, not 24 hours

**Root cause found during the audit (Section A of the final report):** the
pre-existing `AptitudeTest.expires_at` was a single field serving two
different jobs at once — the 24-hour *assignment* window (how long the
candidate has to click "Start") **and** the exam-taking duration once
`in_progress`, with no distinction between them. A candidate who started the
exam 30 seconds before the 24-hour assignment deadline would, in principle,
have been treated as if they had almost no time to answer — or, more subtly,
an `in_progress` test's "remaining time" was really counting down toward the
*original assignment* deadline, not a fixed, short exam window at all.

Fixed additively: `AptitudeTest.exam_expires_at` (new nullable column) is set
to `started_at + 20 minutes` the moment the candidate actually starts (status
flips `assigned` → `in_progress`, in `aptitude_tests.py`'s existing
`start_aptitude_test()`), and `expire_if_due()`/`enforce_not_expired()` check
`exam_expires_at` — never `expires_at` — once a test is `in_progress`.
`expires_at` (the 24-hour assignment window) is completely unchanged and
still governs `status == "assigned"` exactly as before. Refreshing the
browser, or triggering an integrity violation, never touches
`exam_expires_at` — only reaching the deadline itself, checked server-side,
does.

The candidate-facing timer (`#examTimer` in `aptitude-test.html`) reads
`exam_remaining_seconds` from the server on every load/resume and counts down
purely visually; on reaching zero it triggers the same `/submit` call a
manual click would (Section 20's example message: "Time is up. Submitting
your assessment..."). The **server** independently re-checks
`exam_expires_at` on every `/start` and `/submit` call regardless of what the
client's clock says — a manipulated or frozen client timer cannot grant extra
time, and a submission arriving after the real deadline is rejected with 410.

## 9. Server-authoritative termination

`_terminate_aptitude()` in `integrity_service.py` is the **only** place that
sets `AptitudeTest.status = "terminated"`. It:

1. Verifies the test is actually `in_progress` (idempotent — a second call
   after termination is a safe no-op, returns `False`).
2. Sets `status = "terminated"`, `passed = False`, `completed_at = now`.
3. Records `JobApplication.status → "aptitude_terminated"` through the
   existing `record_status_change()` / `application_status_history`
   mechanism — the same additive-only, never-destructive helper every other
   status transition in this project already uses.

No new, competing state machine was introduced: `"terminated"` is one more
value alongside the existing `assigned | in_progress | completed | expired`,
and `"aptitude_terminated"` is one more value alongside the existing
`aptitude_passed | aptitude_failed | aptitude_expired`. Every downstream
consumer that already gates on `AptitudeTest.status == "completed" and
passed is True` (e.g. the Programming-stage eligibility check) correctly
treats a terminated test as not-passed with zero code changes, because
`"terminated" != "completed"`.

The frontend never decides termination on its own. `POST
/api/assessment/integrity/event`'s response (`assessment_active`,
`violation_count`, `warning_level`, `termination_required`) is the only
source the UI trusts, and the client can never supply any of those fields as
input — see Section 11.

## 10. UI — professional, non-accusatory wording

Matches the task's own examples closely:

- Intro screen: *"Your assessment runs in strict mode. Please remain in
  fullscreen and on this screen until you submit. Switching tabs, minimizing
  the window, or exiting fullscreen may be recorded as an assessment
  integrity event."*
- Persistent banner during the exam: *"Strict Assessment Mode — Remain in
  fullscreen and stay on this assessment screen until you submit."* plus a
  live `Integrity Warnings: N/3` counter and the exam countdown.
- 1st violation: *"Assessment integrity violation detected. You left the
  assessment screen or exited fullscreen. Please remain on this screen until
  you submit. Warning 1 of 3."*
- 2nd violation: *"Assessment integrity violation detected again. One more
  violation will automatically terminate this assessment. Warning 2 of 3."*
- Termination screen: *"Assessment Terminated — The assessment was
  automatically terminated because the maximum number of assessment
  integrity violations was reached."*

Never "you are cheating," never an accusatory tone — matches the task's
explicit requirement.

## 11. Authentication, authorization, and privacy

- `GET`/`POST` both require `get_current_user` (401 with no/invalid session)
  and `require_self` (403 if the authenticated candidate's id doesn't match
  the request's `user_id`).
- Ownership of the `application_id` itself is checked with the same
  combined `id + user_id` filter (generic 404 either way) every other
  candidate-facing route in this project already uses — Candidate A can never
  read or write Candidate B's integrity events, and cannot distinguish "not
  yours" from "doesn't exist."
- `POST /event` additionally calls `require_csrf` — missing or mismatched
  double-submit CSRF token is rejected with 403, matching every other
  state-changing endpoint.
- The client can send exactly four fields (`user_id`, `application_id`,
  `assessment_type`, `event_type`). Any extra field in the request body
  (`violation_count`, `severity`, `score`, `termination_required`,
  `candidate_id`, ...) is silently ignored by the Pydantic schema — every
  authoritative value in the response is computed from what is already
  stored in the database, never from the request.
- No personal data is added to what's already stored for the application —
  no screenshots, no media, no keystroke or mouse content, nothing beyond a
  short, fixed vocabulary of event-type strings and small server-generated
  JSON.

## 12. Current Aptitude integration

`frontend/js/aptitude-test.js` wires the reusable module in exactly two
places: the moment questions are rendered (fresh start or a resumed/refreshed
in-progress test) it calls `activateStrictModeForExam()`, which calls
`IntegrityMode.enterStrictMode({...})` and then restores the candidate's real
violation state from `GET /api/assessment/integrity/state` (so a refresh
never resets the warning count to zero); and on a successful submit it stops
the exam timer and calls `IntegrityMode.cleanup()`. A `testTerminatedSection`
renders a dedicated, honest "Assessment Terminated" screen — distinct from
the normal pass/fail result screen — whenever the server reports
`assessment_active: false`.

## 13. Programming integration (Task 4E — done)

`programming-test.js` calls the exact same `frontend/js/assessment-integrity.js`
functions with `assessmentType: "programming"` (see
`activateStrictModeForProgramming()`). `INTEGRATED_ASSESSMENT_TYPES` in
`integrity_service.py` and `_ACTIVE_CHECKS` in the route module each gained
one new entry — `_verify_programming_active()` (reuses
`programming_tests.py`'s own `expire_if_due()`) and `_terminate_programming()`
(mirrors `_terminate_aptitude()` exactly: same idempotent in_progress-only
guard, same additive `ProgrammingTest.status = "terminated"`, same
`record_status_change(to_status="programming_terminated")`). No change to the
dedup/counting/policy logic itself — it was already assessment-type-agnostic.

## 14. AI Interview integration (Task 4E — done)

Same shape as Programming: `interview.js`'s `activateStrictModeForInterview()`,
`_verify_interview_active()`, `_terminate_ai_interview()`. A deliberate design
choice: termination uses a **bare** terminal status
(`InterviewTest.status = "terminated"`, `passed = False`,
`application.status = "interview_terminated"`) rather than reusing
`expire_if_due()`'s partial-finalization-via-`aggregate_interview_result()`
path. Rationale: expiry means "time simply ran out, grade whatever exists";
a policy termination for integrity violations is a punitive stop, exactly
like `_terminate_aptitude()` already treats termination as distinct from any
graded outcome — consistency with the existing precedent mattered more than
preserving partial credit for a terminated attempt.

## 15. Camera / face-presence integrity signals (Task 4E)

Adds ONE more signal category to the same pipeline described in Sections 5-7
— no second table, no second counter, no second policy.

**New event types** (`VIOLATION_EVENT_TYPES`): `camera_disconnected`,
`face_missing`, `multiple_faces_detected` — subject to the exact same
cooldown/counting/warning/termination rule as `fullscreen_exited`/
`tab_hidden`/`window_blur`.

**New event types** (`INFO_EVENT_TYPES`): `camera_connected`,
`camera_reconnected`, `camera_permission_denied`, `camera_unsupported`,
`face_present` — never counted as violations. A candidate with no working
camera, or who declines permission, is not by itself evidence of anything;
only sustained absence/multiplicity (debounced client-side, see below) is.

**Face detection**: the browser-native `FaceDetector` (Shape Detection API),
feature-detected via `'FaceDetector' in window` — no CDN/npm dependency was
added. Presence/count only: the module never performs identity recognition,
never computes embeddings, and never infers age/gender/race/emotion or any
other biometric/demographic attribute. A missing/multiple-face signal only
fires after `FACE_DEBOUNCE_FRAMES` (3) consecutive polls at `FACE_POLL_MS`
(1500ms) in the same state, so a single bad frame never fires an event.

**Privacy**: no raw video frame, image, or screenshot is ever uploaded,
stored, or sent to the server — only the same short event-type
string + timestamp every other integrity event already used. The camera
stream lives only in the candidate's own `<video>` element (with a visible
framing-guide overlay, never hidden/covert) and is torn down by
`stopCameraMonitoring()`, called from `IntegrityMode.cleanup()`.

**Pre-assessment readiness check**: `IntegrityMode.runCameraReadinessCheck()`
shows a "Camera & Assessment Check" modal before the candidate clicks the
EXISTING "Start" action (assessment.html for Programming/Interview,
aptitude-test.html's own intro screen for Aptitude) — primes camera
permission and sets expectations, but reports **no** integrity event of its
own (there is no active test row yet for the server to verify ownership/
activity against) and never blocks continuing, camera or no camera. The real,
event-reporting monitoring begins afterward via `startCameraMonitoring()`,
once the actual timed stage genuinely starts — so this gate adds zero
additional timers; the existing server-authoritative deadline (aptitude's
`exam_expires_at`, Programming/Interview's `expires_at`) still begins exactly
where it always did.

**HR-facing report**: `hr_assessment_review.py`'s `/review/integrity` now
also returns `counts` (camera disconnect/face-missing/multiple-faces/tab-
focus/fullscreen counts, `camera_active_observed`, `total_events`) and a
chronological `timeline` (every real `AssessmentIntegrityEvent` row for the
application, each tagged with a neutral `category`/`label` — e.g. "Potential
Integrity Event — no face detected", never "cheated"). `company-applicants.js`
renders this as camera/face chips on the existing Assessment Integrity tile
plus a "View Integrity Timeline" modal.

**Known limitation, verified in this task's own browser testing**: the
Claude_Browser sandboxed pane consistently denies `getUserMedia` (a genuine
`NotAllowedError`, not a fabricated result) — so the mandatory browser
walkthrough verified the honest "camera not available" signal path (panel
render, `camera_permission_denied` event, HR timeline display) across all
three stages, and verified real hardware disconnect/reconnect could not be
physically simulated in this environment, consistent with Section 16's
already-documented `visibilitychange`-simulation limitation. The combined
tab/fullscreen violation counter was independently re-confirmed working
alongside the camera panel in the same live session.

## 16. Known limitations

- As stated in Section 2, an OS-level application switch that never touches
  the browser window is invisible to this (or any) web page.
- `beforeunload` cannot reliably deliver an asynchronous request during
  unload — it is used **only** to show the browser's native "leave site?"
  confirmation, never to report an integrity event. The actual signals this
  system relies on are `visibilitychange`, `blur`/`focus`, and
  `fullscreenchange`, which fire while the page is still fully alive.
- Fullscreen is best-effort: some browsers require fullscreen to be
  initiated by a direct user gesture (a synchronous click), and the request
  can throw synchronously rather than returning a rejected promise. The
  module catches both failure modes and degrades to a visible warning —
  fullscreen is never treated as, or claimed to be, a security boundary.
- During this task's own browser testing, the automation harness did not
  faithfully simulate a background tab's `visibilityState` transition to
  `"hidden"` — only one genuine `assessment_page_returned` signal was
  observed end-to-end in that environment. The full violation → warning →
  termination pipeline (all three thresholds, dedup, server-authoritative
  termination) was still verified exhaustively — 57/57 checks — via an
  automated test suite that calls the exact same API the real frontend
  calls, and the UI itself was confirmed to render and update correctly from
  real server responses.

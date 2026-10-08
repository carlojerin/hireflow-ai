# Task 4C — Programming Assessment: Architecture & Security Notes

## 1. Overview

The programming assessment stage sits between the aptitude stage and the
(not-yet-built) AI interview stage:

```
not_started -> self_intro -> aptitude -> programming -> ai_interview -> completed
```

It is entirely candidate self-service — there is no HR "assign" step (unlike the
legacy aptitude-assignment flow). A candidate unlocks it the moment their
`AptitudeTest` is `completed` with `passed == True`. This precondition is
re-checked, server-side, on every attempt to start or interact with a
programming test — the frontend cannot unlock it by claiming a stage value.

## 2. Question generation

`backend/app/services/assessment/programming_question_generator.py` calls
Gemini (same `genai.Client`/model-string pattern as the existing aptitude
generator) with a prompt built from:

- **Candidate context**: `ResumeAnalysis.skills`, `CandidateProfile.skills`/
  `experience_level`/`about`/`headline`, `User.experience_level`/`current_role`.
- **Job context**: `Job.title`, `required_skills`, `preferred_skills`,
  `technologies`, `description`/`responsibilities`/`requirements`.
- **Assessment Blueprint**: the *existing* `build_assessment_blueprint()`
  (extended with one new, purely-cosmetic `assessment_type` parameter — no
  algorithm change) supplies `skills_to_assess` (with priority/match_status),
  `skill_gaps`, and `candidate_experience_level`. The prompt asks Gemini to
  prioritize problems around required-but-missing skills rather than generic
  puzzles.

Output is validated against a strict Pydantic schema
(`backend/app/schemas/programming_question.py`) requiring exactly 3 questions,
each with a valid difficulty/language, bounded-length text fields, 1–5
examples, and 3–15 hidden test cases. Any validation failure — or a
prompt-injection-flavored phrase in the generated text — raises `RuntimeError`,
which the bridge service catches and falls back to
`programming_question_bank.py` (a fixed, hand-written 3-problem set: Sum of
Two Numbers / Word Frequency Counter / Balanced Brackets, each with real
hidden tests). `AptitudeTest.question_source`'s sibling field,
`ProgrammingTest.question_source`, records which path was used
(`"ai"` / `"fallback"`) — never mislabeled.

## 3. Fallback behavior

Falls back on: missing `GEMINI_API_KEY`, any API exception, empty/non-JSON
response, wrong question count, any field failing Pydantic validation
(missing field, invalid difficulty/language, too few hidden tests, etc.), or
a suspicious-phrase match. The candidate always receives a complete, valid
3-question test either way — generation failure is invisible to them beyond
`question_source` and the (deliberately visible) "Standard Assessment" vs
"AI Personalized Assessment" label.

## 4. Scoring

- Each question is worth 100 points internally. `ProgrammingTest.score` is
  the **sum** of each question's hidden-test pass percentage (0–300 for 3
  questions); `total_score` is `total_questions * 100` (300). The displayed
  overall percentage is `score / total_score * 100` — equivalent to the
  **average** of the three per-question percentages, not a hidden-test-count
  weighted average. This is a deliberate, simple, documented choice.
- `PROGRAMMING_PASS_THRESHOLD = 70.0` lives in exactly one place
  (`services/assessment/programming_integration.py`) and is imported by the
  route layer — never duplicated.
- Scoring is 100% server-side: the candidate submits source code only, never
  a score. `submit_final_assessment()` re-executes every submitted answer
  against its question's hidden tests at submission time.

## 5. Timer

Server-authoritative, mirroring the existing aptitude/self-intro pattern
exactly: `assigned_at`, `started_at` are stamped at generation time (the
test is created already `in_progress` — no separate HR-assign step exists),
`expires_at = assigned_at + 45 minutes` (`PROGRAMMING_DEADLINE_MINUTES`).
Every read/action calls a lazy `expire_if_due()` (identical shape to
`aptitude_tests.py`'s own), so no background scheduler is needed. The
frontend countdown is purely visual; a page refresh cannot extend or reset
it, and closing the browser does not pause it.

## 6. Sandbox architecture — READ THIS CAREFULLY

**This is a development-grade executor, not a production sandbox.** Docker
is not installed in this environment (verified: no `docker` binary on PATH,
no `docker` Python package in the venv), and per the task's own instructions,
the honest response to that is a clearly-marked, documented fallback — never
a silently "unsafe production executor" pretending to be secure.

```
FastAPI route (programming_tests.py)
   -> services/assessment/programming_scoring.py (orchestration)
      -> services/assessment/code_sandbox.py (THIS is the isolation boundary)
         -> a separate OS subprocess running `python -I -S -B solution.py`
```

### What IS enforced

- Candidate code **never** runs via `exec()`/`eval()` inside the FastAPI
  process — always a separate `subprocess.Popen` child.
- A **static AST allowlist** (`code_sandbox.static_safety_check()`) parses
  the submission before any process starts. It permits `import` only from a
  fixed, deliberately small safe list (`math, re, collections, itertools,
  functools, heapq, bisect, string, datetime, random, statistics, decimal,
  fractions, typing, dataclasses, enum, json, copy, operator, array, cmath,
  textwrap, unicodedata`) — **not** `sys`, `os`, `subprocess`, `socket`,
  `ctypes`, `multiprocessing`, `importlib`, or anything network/file/process-
  capable. It also blocks dangerous builtins (`eval`, `exec`, `compile`,
  `__import__`, `open`, `globals`, `locals`, `vars`, `exit`, `quit`,
  `breakpoint`, `memoryview`) and known sandbox-escape dunder attributes
  (`__subclasses__`, `__globals__`, `__class__`, `__bases__`, `__mro__`,
  `__builtins__`, `__reduce__`, `__dict__`, `__code__`, ...). A rejected
  submission **never reaches a subprocess** — it returns immediately with a
  candidate-safe "restricted operation" message.
  - This is an **allowlist**, not a denylist, deliberately — deny-by-default
    is a strictly stronger posture than trying to enumerate every dangerous
    thing.
  - `sys` is excluded specifically because `sys.modules['os']` is a
    well-known way to reach a blocked module without ever writing
    `import os` — allowing `sys` would silently defeat the whole allowlist.
- The child process receives an **explicitly-constructed, minimal
  environment dict** — `os.environ` is never copied. `GEMINI_API_KEY`, the
  database path, and every other secret are simply not present in the
  child's environment, even in principle.
- `python -I -S -B`: isolated mode (ignores `PYTHON*` env vars, no user
  site-packages), no `site` module, no `.pyc` writes.
- A **hard wall-clock timeout** (`PYTHON_TIME_LIMIT_SECONDS = 10`, capped
  regardless of a question's own `time_limit_seconds`) kills the process via
  `Popen.kill()` on `subprocess.TimeoutExpired` — verified to actually
  terminate a genuine `while True: pass` submission rather than hanging the
  request.
- stdout/stderr are truncated to `MAX_OUTPUT_BYTES = 65536` — verified
  against an output-flooding submission.
- Each execution gets its own fresh `tempfile.mkdtemp()` working directory,
  removed in a `finally` block immediately after.

### What is NOT enforced (documented gaps)

A real production deployment **must** close these with Docker (network
disabled, memory/CPU/PID limits, read-only base filesystem, disposable
container, automatic removal) or an equivalent VM/gVisor/Firecracker-style
isolation layer, exactly as the task's own spec describes as the preferred
architecture:

- **No hard memory limit.** Windows has no direct stdlib equivalent of
  POSIX `resource.setrlimit`; enforcing one needs either a native Windows
  Job Object (via `pywin32`, not installed — this task does not add a new
  third-party dependency on its own authority) or a container.
- **No hard CPU-usage limit** independent of wall-clock time.
- **No OS-level network denial.** The AST allowlist blocks the obvious
  avenues (`socket`/`ctypes`/anything network-capable is simply not
  importable), but this is a code-level restriction, not a kernel-level
  one.
- **No OS-level filesystem jail** (no chroot / AppContainer). `open` itself
  is blocked by the static check, so ordinary file I/O is unreachable, but
  again this is code-level, not kernel-level.
- **No OS-level process/PID limit.** The static check blocks the imports
  needed to spawn a child process at all, but nothing at the OS level would
  stop it if a bypass existed.
- **No privilege drop.** The sandboxed subprocess runs as the same OS user
  as the FastAPI process itself.

## 7. Hidden test security

`ProgrammingTest.questions` (the stored JSON) includes `hidden_tests` for
every question. `sanitize_programming_question()` in
`api/routes/programming_tests.py` is the single choke point every
candidate-facing response passes through — it explicitly whitelists 13
fields and drops `hidden_tests` and `evaluation_notes` unconditionally.
Verified end-to-end (both by an automated string search over the full JSON
response body, and by inspecting the live page's DOM in a real browser
session) that neither `hidden_tests` nor `expected_output` ever appears in
any candidate-facing API response.

## 8. One-attempt enforcement

`ProgrammingTest.status` is the lock, exactly like `AptitudeTest.status`:
`in_progress -> completed` (via final submission) or `in_progress ->
expired` (via the deadline). `submit_final_assessment()` rejects (409) if
`status == "completed"` already. "Run Code" (`submission_kind="run"`, rate-
limited to 8 per question / 20 per test) is architecturally separate from
"Submit Final Assessment" (`submission_kind="final"`, exactly one call ever
succeeds) — the two can never be confused because they are different
endpoints writing different `submission_kind` values, and only the final
endpoint can ever change `ProgrammingTest.status`/`score`/`passed`.

## 9. Known limitation: code is not saved across a page refresh

The candidate's in-editor code lives only in the browser page's own memory
(`codeByQuestion`) until "Submit Final Assessment" is clicked. A page
refresh or navigation away before final submission loses unsaved code for
the current browser session — the server-side timer and one-attempt state
are unaffected either way (nothing is lost from the *assessment's*
perspective, only unsaved keystrokes). Adding a draft-autosave endpoint was
considered and deliberately deferred to keep the API surface — and
therefore the security-review surface — as small as possible for this task.

## 10. Supported language

Python 3 only, exactly as scoped. `expected_language`/`language` fields are
validated against a single-entry set (`{"python"}`); anything else is
rejected with a 400, both in the AI-question schema and in the `/run` and
`/submit` request bodies.

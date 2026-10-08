import functools
import re
import shutil
import subprocess
from dataclasses import dataclass

from backend.app.services.assessment.technology_capability_registry import (
    is_execution_supported as _technology_registry_execution_supported,
)


# ============================================================
# JOBPILOT AI - LANGUAGE EXECUTION CAPABILITY REGISTRY
# (Task 5C Step 4)
#
# ONE authoritative answer to "can JobPilot actually execute
# this language?" — built explicitly ON TOP of the existing
# backend/app/services/assessment/technology_capability_
# registry.py (Task 5C Step 2), never a competing/conflicting
# source of truth. That module answers the coarse question used
# by AssessmentPlanItem ("is this technology executable at all,
# yes/no"); THIS module adds the finer-grained detail this
# step's execution layer needs (name normalization, whether a
# real runtime/compiler is even installed on this machine, and
# a distinct three-state model) while always agreeing with the
# Step 2 registry's own True/False verdict — see
# LanguageCapability.execution_supported, which is never set
# True here unless technology_capability_registry.py also says
# True for the same canonical name.
#
# THREE-STATE MODEL (Step 4's own explicit requirement):
#   1. SUPPORTED + EXECUTABLE     -> known=True,  execution_supported=True
#   2. KNOWN BUT NOT EXECUTABLE   -> known=True,  execution_supported=False
#   3. UNSUPPORTED/UNKNOWN        -> known=False, execution_supported=False
#
# "execution_supported" means "JobPilot has a real, wired,
# tested LanguageRunner for this language today" — NOT merely
# "a runtime/compiler binary happens to exist on this machine".
# `runtime_detected` answers that separate, weaker question
# (e.g. Node.js IS installed on this development machine, but
# execution_supported for javascript is still False here,
# because no secure runner has been built and security-reviewed
# for it yet — see the module-level REAL_RUNNERS set below).
# Never let a detected binary alone imply execution support.
# ============================================================


@dataclass(frozen=True)
class LanguageCapability:

    canonical_name: str
    known: bool
    runtime_detected: bool
    execution_supported: bool

    # Human-readable name of the runtime/compiler this language
    # would need (for reporting only — never used to decide
    # anything).
    runtime_label: str | None = None

    # ------------------------------------------------------------
    # ADDITIVE FIELDS (Programming Engine V2 task).
    # All have defaults so every existing caller that only reads
    # canonical_name/known/runtime_detected/execution_supported/
    # runtime_label (e.g. assessment_capability_registry.py's
    # resolve_capability_state(), the Step 6 capability guard in
    # programming_integration.py) keeps working completely
    # unchanged — nothing here alters the meaning or gating logic
    # of the four original fields.
    # ------------------------------------------------------------

    # Candidate/HR-facing name for a language picker UI, e.g.
    # "C++" rather than the internal canonical key "cpp".
    display_name: str | None = None

    # The exact version string this machine's detected
    # runtime/compiler reports (e.g. "Python 3.11.9"), or None if
    # not detected / detection failed. Purely informational — see
    # `runtime_detected` (bool) for the actual gating signal.
    detected_version: str | None = None

    # One of: "implemented" (a real, registered LanguageRunner
    # exists AND is registered — this is what execution_supported
    # actually gates on), "runtime_detected_runner_missing" (the
    # compiler/interpreter IS on this machine but JobPilot has no
    # runner for it yet), "unimplemented_no_runtime" (neither a
    # runner nor a detected runtime exist). Never "implemented"
    # merely because a binary is on PATH — see module header.
    implementation_status: str = "unimplemented_no_runtime"

    # ------------------------------------------------------------
    # ADDITIVE FIELDS (Task 5C, Complete Multi-Language Execution
    # Engine). All default so no existing caller breaks.
    # ------------------------------------------------------------

    # One of "AVAILABLE" / "UNAVAILABLE" / "UNSUPPORTED" — this
    # task's own explicit three-state vocabulary, layered on top
    # of (never replacing) the existing three fields above:
    #   AVAILABLE   = a real LanguageRunner is registered AND its
    #                 runtime/compiler is actually detected right
    #                 now (== execution_supported).
    #   UNAVAILABLE = the runner ARCHITECTURE exists (a
    #                 LanguageRunner class is implemented and
    #                 registered) but the runtime/compiler is not
    #                 currently detected on this machine. This is
    #                 the state a genuinely-supported language
    #                 gets on a machine that just hasn't installed
    #                 its toolchain yet — never confused with
    #                 UNSUPPORTED.
    #   UNSUPPORTED = no runner implementation exists for this
    #                 language at all, regardless of what's
    #                 installed. Reserved for a language nothing
    #                 in this codebase has ever built a runner
    #                 for — not merely one whose compiler is
    #                 currently missing.
    state: str = "UNSUPPORTED"

    file_extension: str | None = None

    compile_required: bool = False

    # Short, human-readable pointer into execution_security_v2.py
    # — never a substitute for reading that module's own
    # SecurityBoundaryReport, only a quick label for a UI/report.
    security_policy: str | None = None


# Every alias a candidate/job/UI might use, mapped to ONE
# canonical lowercase name. Unknown input normalizes to None —
# never passed through as-is, and never guessed at (Step 4's own
# "unknown language -> reject safely" rule).
_LANGUAGE_ALIASES: dict[str, str] = {
    "python": "python", "py": "python", "python3": "python",

    "java": "java",

    "c": "c",

    "c++": "cpp", "cpp": "cpp", "cplusplus": "cpp",

    "c#": "csharp", "csharp": "csharp", "c-sharp": "csharp",
    "cs": "csharp",

    "javascript": "javascript", "js": "javascript",

    "typescript": "typescript", "ts": "typescript",

    "go": "go", "golang": "go",

    "rust": "rust", "rs": "rust",

    "php": "php",

    "kotlin": "kotlin",

    "swift": "swift",

    "ruby": "ruby", "rb": "ruby",

    "sql": "sql",

    "html": "html", "css": "html", "html5": "html", "html/css": "html", "html css": "html", "web": "html",
}

# The command JobPilot would look for on PATH to detect whether
# a real runtime/compiler for a language is even installed on
# THIS machine — detection only, never assumed. None means "no
# single binary check applies" (e.g. "sql" isn't a runnable
# interpreter/compiler in this sense).
_RUNTIME_DETECTION_COMMAND: dict[str, str | None] = {
    "python": "python",  # the venv's own interpreter is what actually runs; this is only a presence signal
    "java": "java",
    "c": "gcc",
    "cpp": "g++",
    "csharp": "dotnet",
    "javascript": "node",
    "typescript": "tsc",
    "go": "go",
    "rust": "rustc",
    "php": "php",
    "kotlin": "kotlinc",
    "swift": "swift",
    "ruby": "ruby",
    "sql": None,
    "html": None,
}

_RUNTIME_LABEL: dict[str, str] = {
    "python": "Python interpreter",
    "java": "Java Development Kit (javac/java)",
    "c": "C compiler (gcc)",
    "cpp": "C++ compiler (g++)",
    "csharp": ".NET SDK",
    "javascript": "Node.js runtime",
    "typescript": "TypeScript compiler (tsc) + Node.js runtime",
    "go": "Go toolchain",
    "rust": "Rust toolchain (rustc)",
    "php": "PHP interpreter",
    "kotlin": "Kotlin compiler",
    "swift": "Swift compiler",
    "ruby": "Ruby interpreter",
    "sql": "SQL query engine",
    "html": "Headless browser (Chrome/Edge)",
}

# The technology_capability_registry.py key each canonical
# language name maps to, for cross-checking execution_supported
# (some canonical names differ slightly, e.g. "cpp" here vs
# "c++"/"cpp" both accepted there).
_TECHNOLOGY_REGISTRY_KEY: dict[str, str] = {
    "python": "python",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "csharp": "c#",
    "javascript": "javascript",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "php": "php",
    "kotlin": "kotlin",
    "swift": "swift",
    "ruby": "ruby",
    "sql": "sql",
    "html": "html",
}

# The set of languages that ALSO have a real, wired, tested
# LanguageRunner implementation in THIS codebase today (see
# language_runner.py's RUNNER_REGISTRY) — the actual gate for
# execution_supported=True, deliberately independent of whether
# a runtime binary happens to be detected on this machine.
# Extending this set is the ONLY place a future language runner
# needs to touch to go live, alongside actually registering its
# LanguageRunner implementation.
#
# Programming Engine V2 task: javascript and csharp added ONLY
# after their runners (javascript_runner.py, csharp_runner.py)
# were implemented AND individually tested against the real
# Node.js / .NET SDK installs actually detected on this
# development machine — see that task's final report for the
# exact test results.
#
# Task 5C, Complete Multi-Language Execution Engine: typescript/
# java/c/cpp/go/rust/php/ruby/kotlin added the SAME way — each
# runner implemented AND individually tested against a real
# toolchain (installed for this task via winget/npm/an official
# JetBrains GitHub release for kotlinc — see that task's final
# report for exact versions, install sources, and test results).
# swift WAS blocked on a genuine, evidenced Windows-toolchain dead
# end (the winget Swift.Toolchain package has no Windows-target
# standard library at all — see docker_sandbox_runner.py's own
# comment for the full history). That blocker is specific to
# running swiftc directly on the Windows host; the official LINUX
# swift Docker image has no such gap, and running Swift through the
# SAME Docker sandbox every other language already uses (confirmed
# by a real compile+run test) resolved it. The old host-native
# swift_runner.py is left on disk, unused — never imported/
# registered — exactly like piston_runner.py/judge0_runner.py.
_REAL_RUNNERS_IMPLEMENTED: set[str] = {
    "python", "javascript", "csharp", "typescript", "java",
    "c", "cpp", "go", "rust", "php", "ruby", "kotlin", "swift", "sql", "html",
}

# Every language with a registered LanguageRunner CLASS and a real,
# working runtime — currently identical to _REAL_RUNNERS_IMPLEMENTED
# (kept as a separate name/union in case a future language is ever
# registered architecturally before its runtime is confirmed working,
# the same gap swift used to occupy here).
_RUNNER_ARCHITECTURE_IMPLEMENTED: set[str] = _REAL_RUNNERS_IMPLEMENTED | set()

_FILE_EXTENSION: dict[str, str] = {
    "python": ".py", "java": ".java", "c": ".c", "cpp": ".cpp",
    "csharp": ".cs", "javascript": ".js", "typescript": ".ts",
    "go": ".go", "rust": ".rs", "php": ".php", "kotlin": ".kt",
    "swift": ".swift", "ruby": ".rb", "sql": ".sql", "html": ".html",
}

_COMPILE_REQUIRED: dict[str, bool] = {
    "python": False, "java": True, "c": True, "cpp": True,
    "csharp": True, "javascript": False, "typescript": True,
    "go": True, "rust": True, "php": False, "kotlin": True,
    "swift": True, "ruby": False, "sql": False, "html": False,
}

_DISPLAY_NAME: dict[str, str] = {
    "python": "Python",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "php": "PHP",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "ruby": "Ruby",
    "sql": "SQL",
    "html": "HTML / CSS / JavaScript",
}

# Best-effort `<command> --version`-style probe used ONLY for
# reporting a detected_version string — never for any gating
# decision. A failure here (command missing, non-zero exit,
# unexpected output) always degrades to None, never raises.
_VERSION_PROBE_ARGS: dict[str, list[str]] = {
    "python": ["python", "--version"],
    "java": ["java", "-version"],
    "c": ["gcc", "--version"],
    "cpp": ["g++", "--version"],
    "csharp": ["dotnet", "--version"],
    "javascript": ["node", "--version"],
    "typescript": ["tsc", "--version"],
    "go": ["go", "version"],
    "rust": ["rustc", "--version"],
    "php": ["php", "--version"],
    "kotlin": ["kotlinc", "-version"],
    "swift": ["swift", "--version"],
    "ruby": ["ruby", "--version"],
}


@functools.lru_cache(maxsize=None)
def _detect_version(canonical_name: str) -> str | None:

    args = _VERSION_PROBE_ARGS.get(canonical_name)

    if not args or shutil.which(args[0]) is None:
        return None

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None

    output = (proc.stdout or "") + (proc.stderr or "")
    first_line = output.strip().splitlines()[0].strip() if output.strip() else None

    if not first_line:
        return None

    # Trim to a reasonable reporting length — never used for
    # parsing/decision-making, only display.
    return re.sub(r"\s+", " ", first_line)[:120]


def normalize_language_name(language: str | None) -> str | None:

    if not language:
        return None

    key = language.strip().lower()

    return _LANGUAGE_ALIASES.get(key)


def _detect_runtime(canonical_name: str) -> bool:

    command = _RUNTIME_DETECTION_COMMAND.get(canonical_name)

    if not command:
        return False

    return shutil.which(command) is not None


def get_language_capability(language: str | None) -> LanguageCapability:
    """
    The one function anything in this codebase should call to
    answer "what can JobPilot do with this language today?".
    Never raises — an unrecognized/empty language safely comes
    back as known=False, execution_supported=False rather than
    an exception, so a caller building a UI list or validating a
    submission never needs a try/except around this.
    """

    canonical_name = normalize_language_name(language)

    if canonical_name is None:

        return LanguageCapability(
            canonical_name=(language or "").strip().lower(),
            known=False,
            runtime_detected=False,
            execution_supported=False,
            runtime_label=None,
            display_name=None,
            detected_version=None,
            implementation_status="unimplemented_no_runtime",
        )

    registry_key = _TECHNOLOGY_REGISTRY_KEY.get(canonical_name, canonical_name)

    technology_registry_says_supported = _technology_registry_execution_supported(
        registry_key
    )

    runner_implemented = canonical_name in _REAL_RUNNERS_IMPLEMENTED
    runtime_detected = _detect_runtime(canonical_name)

    # Execution is only ever True if BOTH this module's own
    # "a real runner is implemented" gate AND the Step 2
    # technology registry agree — never let either source alone
    # claim execution support the other doesn't corroborate.
    execution_supported = runner_implemented and technology_registry_says_supported

    if runner_implemented and technology_registry_says_supported:
        implementation_status = "implemented"
    elif runtime_detected:
        implementation_status = "runtime_detected_runner_missing"
    else:
        implementation_status = "unimplemented_no_runtime"

    # Task 5C, Complete Multi-Language Execution Engine —
    # explicit three-state vocabulary (see LanguageCapability's
    # own field docstring for the exact definitions). Gated on
    # _RUNNER_ARCHITECTURE_IMPLEMENTED (a registered runner CLASS
    # exists), never on _REAL_RUNNERS_IMPLEMENTED alone — a
    # language whose runner exists but isn't confirmed working
    # (swift) must land on UNAVAILABLE, never UNSUPPORTED.
    if canonical_name not in _RUNNER_ARCHITECTURE_IMPLEMENTED:
        state = "UNSUPPORTED"
    elif execution_supported:
        state = "AVAILABLE"
    else:
        state = "UNAVAILABLE"

    return LanguageCapability(
        canonical_name=canonical_name,
        known=True,
        runtime_detected=runtime_detected,
        execution_supported=execution_supported,
        runtime_label=_RUNTIME_LABEL.get(canonical_name),
        display_name=_DISPLAY_NAME.get(canonical_name),
        detected_version=_detect_version(canonical_name),
        implementation_status=implementation_status,
        state=state,
        file_extension=_FILE_EXTENSION.get(canonical_name),
        compile_required=_COMPILE_REQUIRED.get(canonical_name, False),
        security_policy=(
            "see execution_security_v2.get_security_boundary_report()"
            if runner_implemented else None
        ),
    )


def list_all_known_languages() -> list[LanguageCapability]:
    """Read-only listing — useful for a future UI language picker
    or for this step's own audit/report. Never mutates anything."""

    canonical_names = sorted(set(_LANGUAGE_ALIASES.values()))

    return [get_language_capability(name) for name in canonical_names]

import hashlib
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
import os


# ============================================================
# JOBPILOT AI — SHARED RUNNER HELPERS (Task 5C, Complete
# Multi-Language Execution Engine)
#
# Extracts two pieces of logic that were duplicated identically
# across javascript_runner.py and csharp_runner.py while building
# THIS task's 9 new runners: the minimal-but-actually-sufficient
# Windows environment dict, and output truncation.
#
# WHY THESE SPECIFIC VARIABLES: discovered empirically while
# building the JavaScript and C# runners in an earlier task —
# Node's crypto subsystem crashed at startup ("Assertion failed:
# ncrypto::CSPRNG") without SystemRoot, and .NET's NuGet restore
# crashed with a bare "Value cannot be null (Parameter 'path1')"
# without HOMEDRIVE/HOMEPATH/ProgramFiles/ProgramData. Every
# runner in this task (Java/javac, gcc/g++, Go, rustc/cargo, php,
# ruby, kotlinc, tsc) is Windows OS-level plumbing that can hit
# the exact same class of failure, so every one of them gets the
# same base environment rather than rediscovering the same bugs
# one language at a time. This is still NEVER the full os.environ
# — GEMINI_API_KEY, SMTP credentials, DB connection strings,
# session secrets, etc. are never copied into a candidate
# execution process.
# ============================================================

_OS_PLUMBING_VARS = (
    "SystemRoot", "TEMP", "TMP", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH", "ProgramFiles",
    "ProgramFiles(x86)", "ProgramData", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "windir",
)


def build_minimal_execution_env(*, path: str, extra: dict | None = None) -> dict:
    """
    A minimal environment for a candidate-code subprocess: PATH
    (as given by the caller, since each runner may need its own
    toolchain directories appended — see language_capability_
    registry.py's per-language bin directories) plus the handful
    of Windows OS-level variables every toolchain investigated in
    this project so far has turned out to need at process
    startup. Never includes the parent FastAPI process's full
    os.environ.
    """

    env = {"PATH": path}

    for var in _OS_PLUMBING_VARS:
        value = os.environ.get(var)
        if value:
            env[var] = value

    if extra:
        env.update(extra)

    return env


_OUTPUT_LIMIT_BYTES = 64 * 1024


def truncate_output(text: str, limit_bytes: int = _OUTPUT_LIMIT_BYTES) -> str:

    encoded = text.encode("utf-8", errors="replace")

    if len(encoded) <= limit_bytes:
        return text

    return encoded[:limit_bytes].decode("utf-8", errors="ignore") + "\n...[output truncated]"


# Deliberately NOT tempfile.gettempdir() — on this machine that
# resolves to a path containing a space in the username
# ("C:\Users\carlo jerin\AppData\Local\Temp"), which broke gcc's
# OWN internal collect2/ld invocation with "file not recognized"
# (gcc's WinLibs build does not robustly quote every internal
# path it builds from argv[0]/its install location combined with
# whatever output path it's given — a real, reproduced bug, not
# a guess; see this task's final report for the exact evidence).
# A short, space-free cache root sidesteps this entire class of
# bug for every compiled-language toolchain, not just gcc's.
_CACHE_ROOT = Path("C:/tools/jobpilot_exec_cache")


def get_source_cache_dir(language: str, source_code: str) -> Path:
    """
    A fresh, content-addressed directory (under a fixed, space-
    free root — see _CACHE_ROOT above) for one candidate
    submission's compile artifacts, shared by every compiled-
    language runner in this task (Java/C/C++/Go/Rust/Kotlin,
    matching the pattern csharp_runner.py established first).
    Keyed by a hash of the source itself, so repeated hidden-test
    executions of the SAME submission reuse one compile rather
    than rebuilding per test case; a different submission (even
    for the same candidate/question) gets its own directory.
    """

    source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()[:24]
    cache_dir = _CACHE_ROOT / f"jobpilot_{language}_cache" / source_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@dataclass(frozen=True)
class SubprocessOutcome:

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_ms: int


def run_subprocess_capturing(
    args: list[str],
    *,
    stdin: str = "",
    timeout_seconds: float = 10,
    env: dict,
    cwd: str | None = None,
) -> SubprocessOutcome:
    """
    One shared subprocess invocation used for BOTH the compile
    step and the run step across every runner in this task —
    consistent timeout/timing/truncation behavior everywhere,
    rather than each runner re-implementing subprocess.run's
    try/except TimeoutExpired dance slightly differently.
    """

    start = time.monotonic()

    try:
        proc = subprocess.run(
            args, input=stdin, capture_output=True, text=True,
            timeout=timeout_seconds, env=env, cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return SubprocessOutcome(
            returncode=None,
            stdout=truncate_output(exc.stdout if isinstance(exc.stdout, str) else ""),
            stderr=truncate_output(exc.stderr if isinstance(exc.stderr, str) else ""),
            timed_out=True,
            elapsed_ms=elapsed_ms,
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    return SubprocessOutcome(
        returncode=proc.returncode,
        stdout=truncate_output(proc.stdout),
        stderr=truncate_output(proc.stderr),
        timed_out=False,
        elapsed_ms=elapsed_ms,
    )

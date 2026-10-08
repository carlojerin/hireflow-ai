import ast
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


# ============================================================
# JOBPILOT AI - PROGRAMMING ASSESSMENT CODE SANDBOX (Task 4C)
#
# =====================  READ THIS FIRST  =====================
# THIS IS A DEVELOPMENT-GRADE EXECUTOR, NOT A PRODUCTION SANDBOX.
#
# Docker is not available in this environment (verified: no
# `docker` binary on PATH, no `docker` Python package installed).
# The task's own spec is explicit that when Docker is
# unavailable, the honest response is a clearly-marked,
# documented development fallback — never a silently "unsafe
# production executor" pretending to be secure. This module is
# that fallback. See docs/task-4c-programming-assessment.md for
# the full, itemized list of what is and is not enforced.
#
# What this module DOES enforce:
#   - Candidate code runs in a SEPARATE OS process (never
#     exec()/eval() inside the FastAPI process itself).
#   - A static AST allowlist rejects any submission that
#     imports a module outside a small safe allowlist, calls a
#     dangerous builtin (eval/exec/compile/__import__/open/...),
#     or accesses a known sandbox-escape dunder attribute
#     (__subclasses__, __globals__, __class__, ...) — BEFORE any
#     process is even started. A rejected submission never runs.
#   - A minimal, explicitly-constructed environment dict is
#     passed to the child process — os.environ is NEVER copied,
#     so GEMINI_API_KEY / DB path / any other secret is not
#     visible to candidate code even if it somehow bypassed the
#     static check and tried to read environment variables.
#   - Python's own `-I` (isolated mode) and `-S` (no site
#     module) flags reduce the interpreter's own attack surface
#     and ignore any PYTHON*-prefixed environment variables.
#   - A hard wall-clock timeout kills the child process (and,
#     best-effort, any of its children) — protects against
#     infinite loops.
#   - stdout/stderr are truncated to a fixed byte limit —
#     protects against an output-flooding denial-of-service.
#   - Each execution gets its own fresh temporary directory,
#     deleted immediately afterward.
#
# What this module does NOT enforce (documented gaps — a real
# deployment MUST close these with Docker/gVisor/Firecracker or
# equivalent container/VM isolation before going to production):
#   - No hard memory limit. Windows has no direct equivalent of
#     POSIX `resource.setrlimit` reachable from pure-Python
#     stdlib; enforcing one would require either a native
#     Windows Job Object (via pywin32 — not installed, and this
#     task does not add a new third-party dependency on its own
#     authority) or a container. A submission that allocates
#     huge amounts of memory can degrade the host.
#   - No hard CPU-usage limit independent of wall-clock time.
#   - No OS-level network denial. The AST allowlist blocks the
#     obvious avenues (socket/http/urllib/ftplib/ctypes are not
#     importable), but this is a code-level restriction, not a
#     kernel-level one — it can in principle be defeated by a
#     sufficiently novel bypass this static check doesn't know
#     about yet.
#   - No OS-level filesystem jail (no chroot/AppContainer). The
#     `open` builtin itself is blocked by the static check, so
#     ordinary file I/O is not reachable, but this is again a
#     code-level restriction, not a kernel-level one.
#   - No process/PID limit enforced by the OS (the static check
#     blocks `subprocess`/`multiprocessing`/`os.fork`-style
#     imports, so ordinary candidate code has no way to spawn
#     children, but nothing at the OS level would stop it if a
#     bypass existed).
#   - Runs as whatever OS user the FastAPI process itself runs
#     as — no privilege drop / no dedicated low-privilege sandbox
#     account.
# ===============================================================
# ============================================================


PYTHON_TIME_LIMIT_SECONDS = 10          # hard ceiling regardless of a question's own time_limit_seconds
MAX_OUTPUT_BYTES = 65536                # 64 KB stdout/stderr cap
MAX_SOURCE_CODE_LENGTH = 20000          # characters


# ------------------------------------------------------------
# STATIC AST SAFETY CHECK (allowlist, not denylist, wherever
# possible — deny-by-default is a strictly stronger posture
# than trying to enumerate every dangerous thing).
# ------------------------------------------------------------

ALLOWED_IMPORT_MODULES = {
    "math", "re", "collections", "itertools", "functools",
    "heapq", "bisect", "string", "datetime", "random",
    "statistics", "decimal", "fractions", "typing",
    "dataclasses", "enum", "json", "copy", "operator", "array",
    "cmath", "textwrap", "unicodedata",
}

BLOCKED_BUILTIN_NAMES = {
    "eval", "exec", "compile", "__import__", "open",
    "globals", "locals", "vars", "exit", "quit", "breakpoint",
    "help", "memoryview",
}

BLOCKED_ATTRIBUTE_NAMES = {
    "__subclasses__", "__globals__", "__bases__", "__base__",
    "__mro__", "__builtins__", "__import__", "__getattribute__",
    "__reduce__", "__reduce_ex__", "__class__", "__dict__",
    "__code__", "__closure__", "__func__", "__loader__",
    "__spec__",
}


class UnsafeCodeError(Exception):
    """Raised by the static check — the caller turns this into
    a candidate-safe "restricted operation" message, and no
    subprocess is ever started for this submission."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def static_safety_check(source_code: str) -> None:
    """
    Parses source_code and raises UnsafeCodeError if it imports
    anything outside ALLOWED_IMPORT_MODULES, references a
    blocked builtin name, or accesses a blocked dunder
    attribute. Raises UnsafeCodeError on a syntax error too —
    the execution step would fail on it anyway, and this keeps
    "can't even parse" and "parses but is unsafe" on the same
    reporting path for run() to handle uniformly... actually a
    syntax error is a normal candidate mistake, not an unsafe-
    code situation, so it is NOT raised here — see run_python_
    code(), which lets a SyntaxError surface as a normal
    execution error instead.
    """

    tree = ast.parse(source_code)

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):

            for alias in node.names:

                root_module = alias.name.split(".")[0]

                if root_module not in ALLOWED_IMPORT_MODULES:

                    raise UnsafeCodeError(
                        f"import of module '{alias.name}' is not permitted"
                    )

        elif isinstance(node, ast.ImportFrom):

            root_module = (node.module or "").split(".")[0]

            if root_module not in ALLOWED_IMPORT_MODULES:

                raise UnsafeCodeError(
                    f"import from module '{node.module}' is not permitted"
                )

        elif isinstance(node, ast.Name):

            if node.id in BLOCKED_BUILTIN_NAMES:

                raise UnsafeCodeError(
                    f"use of '{node.id}' is not permitted"
                )

        elif isinstance(node, ast.Attribute):

            if node.attr in BLOCKED_ATTRIBUTE_NAMES:

                raise UnsafeCodeError(
                    f"access to '{node.attr}' is not permitted"
                )


# ------------------------------------------------------------
# RESULT
# ------------------------------------------------------------

@dataclass
class SandboxResult:

    status: str                # completed | timeout | error | rejected
    stdout: str
    stderr: str
    execution_time_ms: int
    rejected_reason: str | None = None


def _minimal_environment() -> dict:
    """
    Explicitly-constructed environment for the child process —
    os.environ is never copied, so GEMINI_API_KEY / any .env
    secret / auth cookies / DB path are never visible to
    candidate code, even in principle.
    """

    import os as _os  # local import: kept out of module scope on purpose, so nothing else in this file can accidentally read os.environ

    env = {}

    # Windows' C runtime needs SystemRoot to initialize
    # properly; it carries no secret value.
    system_root = _os.environ.get("SystemRoot")

    if system_root:
        env["SystemRoot"] = system_root

    return env


def run_python_code(
    source_code: str,
    stdin_input: str,
    time_limit_seconds: int | None = None,
) -> SandboxResult:
    """
    Execute untrusted Python 3 source code in a separate process
    with the safety measures documented at the top of this file.
    Never raises for a candidate-caused failure (syntax error,
    runtime error, timeout, unsafe code) — all of those come
    back as a SandboxResult with an appropriate status. Only a
    genuine sandbox-infrastructure failure (e.g. the Python
    interpreter itself cannot be found) raises.
    """

    if len(source_code) > MAX_SOURCE_CODE_LENGTH:

        return SandboxResult(
            status="rejected",
            stdout="",
            stderr="",
            execution_time_ms=0,
            rejected_reason=(
                f"Source code exceeds the maximum allowed length "
                f"({MAX_SOURCE_CODE_LENGTH} characters)."
            ),
        )

    try:

        static_safety_check(source_code)

    except SyntaxError:

        # Not a safety rejection — a normal candidate mistake.
        # Let it through to actual execution, which will report
        # the same SyntaxError as a normal runtime error.
        pass

    except UnsafeCodeError as exc:

        return SandboxResult(
            status="rejected",
            stdout="",
            stderr="",
            execution_time_ms=0,
            rejected_reason=(
                "Your code uses a restricted operation and cannot "
                f"be executed ({exc.reason})."
            ),
        )

    effective_timeout = min(
        time_limit_seconds or PYTHON_TIME_LIMIT_SECONDS,
        PYTHON_TIME_LIMIT_SECONDS,
    )

    temp_dir = tempfile.mkdtemp(prefix="jobpilot_sandbox_")

    try:

        script_path = Path(temp_dir) / "solution.py"
        script_path.write_text(source_code, encoding="utf-8")

        popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=temp_dir,
            env=_minimal_environment(),
        )

        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        start = time.perf_counter()

        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", str(script_path)],
            **popen_kwargs,
        )

        try:

            stdout_bytes, stderr_bytes = process.communicate(
                input=stdin_input.encode("utf-8", errors="replace"),
                timeout=effective_timeout,
            )

            elapsed_ms = int((time.perf_counter() - start) * 1000)

            stdout = stdout_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stderr = stderr_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

            status = "completed" if process.returncode == 0 else "error"

            return SandboxResult(
                status=status,
                stdout=stdout,
                stderr=stderr,
                execution_time_ms=elapsed_ms,
            )

        except subprocess.TimeoutExpired:

            process.kill()

            try:
                process.communicate(timeout=2)
            except Exception:
                pass

            elapsed_ms = int((time.perf_counter() - start) * 1000)

            return SandboxResult(
                status="timeout",
                stdout="",
                stderr="Execution exceeded the time limit.",
                execution_time_ms=elapsed_ms,
            )

    finally:

        shutil.rmtree(temp_dir, ignore_errors=True)

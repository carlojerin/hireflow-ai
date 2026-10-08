import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner


# ============================================================
# JOBPILOT AI — JAVASCRIPT LANGUAGE RUNNER (Programming Engine
# V2 task)
#
# Genuinely tested against the Node.js runtime actually detected
# on this development machine (v24.7.0 — confirmed via `node
# --version` before this file was written; see this task's final
# report). NOT registered/claimed for any language whose runtime
# is not actually present — see language_capability_registry.py's
# _REAL_RUNNERS_IMPLEMENTED, which is the only place this runner
# is switched "on".
#
# SECURITY BOUNDARY (documented honestly, mirroring
# code_sandbox.py's own header — read that module first):
#   - Runs in a SEPARATE OS process (node <tempfile>.js), never
#     inside the FastAPI process.
#   - A minimal, explicitly-constructed environment is passed —
#     the parent process's os.environ (which may contain
#     GEMINI_API_KEY, DB paths, etc.) is NEVER copied into the
#     child.
#   - A hard wall-clock timeout kills the child process.
#   - stdout/stderr are truncated to a fixed byte limit.
#   - Each execution gets its own fresh temp file, deleted
#     afterward (best-effort — a deletion failure is swallowed,
#     never allowed to turn a candidate's correct submission into
#     a platform error).
#
# UNLIKE code_sandbox.py's Python runner, this runner has NO
# static allowlist step — there is no equivalent AST-based import/
# builtin restriction for JavaScript here. Node's `fs`, `child_process`,
# `net`, etc. modules are NOT blocked by anything in this file. This
# is a STRICTLY WEAKER security boundary than the Python runner and
# MUST NOT be treated as production-safe for genuinely untrusted
# candidate code — see this task's Step 8 security note in the
# final report. It is adequate only for this task's own isolated,
# non-production testing, and for a future production wiring this
# gap must be closed (a real sandbox: Docker/gVisor/Firecracker, or
# at minimum a Node `--experimental-permission`/vm-context
# restriction) before any real candidate ever reaches this runner.
# ============================================================

RUNNER_VERSION = "javascript-node-subprocess-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15
_OUTPUT_LIMIT_BYTES = 64 * 1024


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _OUTPUT_LIMIT_BYTES:
        return text
    return encoded[:_OUTPUT_LIMIT_BYTES].decode("utf-8", errors="ignore") + "\n...[output truncated]"


class JavaScriptRunner:

    language = "javascript"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        import shutil
        return shutil.which("node") is not None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False,
                error_type="runner_unavailable",
                message="Node.js runtime is not available on this machine.",
                language=self.language,
                runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(
                _MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000))
            )

        workdir = Path(tempfile.gettempdir()) / "jobpilot_js_runs" / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)
        script_path = workdir / "submission.js"

        try:
            script_path.write_text(request.source_code, encoding="utf-8")

            # SystemRoot (and, defensively, a couple of other
            # standard OS-level Windows variables) is REQUIRED —
            # without it, Node's own crypto subsystem cannot reach
            # Windows' BCrypt RNG and the process aborts before
            # ever running the candidate's script at all (a real,
            # observed failure while testing this runner: "node:
            # Assertion failed: ncrypto::CSPRNG(...)"). This is an
            # OS plumbing requirement, not an application secret —
            # GEMINI_API_KEY, the database path, etc. are still
            # never copied into this environment.
            minimal_env = {
                "PATH": os.environ.get("PATH", ""),
                "NODE_NO_WARNINGS": "1",
                "SystemRoot": os.environ.get("SystemRoot", ""),
                "TEMP": os.environ.get("TEMP", ""),
                "TMP": os.environ.get("TMP", ""),
            }

            start = time.monotonic()

            try:
                proc = subprocess.run(
                    [
                        "node",
                        # Real, verifiable hardening (Programming
                        # Engine V2 security task, Step 13): blocks
                        # eval()/new Function()/similar string-to-
                        # code paths at the V8 engine level. This is
                        # NOT a substitute for code_sandbox.py's
                        # Python AST allowlist — there is still no
                        # import/module restriction here — but it is
                        # a genuine, tested narrowing of the attack
                        # surface, not documentation-only.
                        "--disallow-code-generation-from-strings",
                        str(script_path),
                    ],
                    input=request.stdin,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    env=minimal_env,
                    cwd=str(workdir),
                )
            except subprocess.TimeoutExpired as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return ExecutionResult(
                    success=False,
                    stdout=_truncate(exc.stdout or "" if isinstance(exc.stdout, str) else ""),
                    stderr=_truncate(exc.stderr or "" if isinstance(exc.stderr, str) else ""),
                    timed_out=True,
                    execution_time_ms=elapsed_ms,
                    error_type="timeout",
                    message=f"Execution exceeded {timeout_seconds}s.",
                    language=self.language,
                    runner_version=self.runner_version,
                )

            elapsed_ms = int((time.monotonic() - start) * 1000)

            if proc.returncode == 0:
                return ExecutionResult(
                    success=True,
                    stdout=_truncate(proc.stdout),
                    stderr=_truncate(proc.stderr),
                    exit_code=0,
                    execution_time_ms=elapsed_ms,
                    error_type="success",
                    language=self.language,
                    runner_version=self.runner_version,
                )

            return ExecutionResult(
                success=False,
                stdout=_truncate(proc.stdout),
                stderr=_truncate(proc.stderr),
                exit_code=proc.returncode,
                execution_time_ms=elapsed_ms,
                error_type="runtime_error",
                message="The submission exited with a non-zero status.",
                language=self.language,
                runner_version=self.runner_version,
            )

        except Exception:
            return ExecutionResult(
                success=False,
                error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                language=self.language,
                runner_version=self.runner_version,
            )

        finally:
            try:
                script_path.unlink(missing_ok=True)
                workdir.rmdir()
            except OSError:
                pass


register_runner(JavaScriptRunner())

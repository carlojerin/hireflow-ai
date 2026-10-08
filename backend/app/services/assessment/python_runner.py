import shutil
import sys

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.code_sandbox import (
    PYTHON_TIME_LIMIT_SECONDS,
    run_python_code,
)
from backend.app.services.assessment.language_runner import register_runner


# ============================================================
# JOBPILOT AI - PYTHON LANGUAGE RUNNER ADAPTER (Task 5C Step 4)
#
# A thin COMPATIBILITY ADAPTER over the EXISTING, UNMODIFIED
# backend/app/services/assessment/code_sandbox.py — that module
# is not touched by this step at all (Step 4's own explicit
# preference: adapt rather than risk breaking the current
# system). This class only translates between the new language-
# neutral ExecutionRequest/ExecutionResult shape (Step 4) and
# code_sandbox.py's own existing SandboxResult shape — every
# actual security control (AST allowlist, minimal environment,
# timeout, output truncation, temp-directory isolation) still
# lives entirely in code_sandbox.py, exactly as before.
#
# See code_sandbox.py's own header comment for the full,
# itemized list of what is/is not enforced — this adapter does
# not change or duplicate that list, only re-reports it through
# the new ExecutionResult.error_type vocabulary.
# ============================================================

RUNNER_VERSION = "python-adapter-1.0 (wraps code_sandbox.py)"

# The adapter's own outer ceiling — never larger than
# code_sandbox.py's own PYTHON_TIME_LIMIT_SECONDS hard cap,
# which run_python_code() already enforces internally regardless
# of what is passed to it. This exists only so
# execute()'s own bookkeeping has a concrete number to report.
_DEFAULT_TIMEOUT_SECONDS = PYTHON_TIME_LIMIT_SECONDS


def _classify_sandbox_status(status: str) -> tuple[bool, str]:
    """Maps code_sandbox.py's own status vocabulary onto the new
    ExecutionResult.error_type vocabulary."""

    if status == "completed":
        return True, "success"

    if status == "timeout":
        return False, "timeout"

    if status == "rejected":
        return False, "sandbox_error"

    # "error" - code_sandbox.py does not itself distinguish a
    # syntax error from any other non-zero-exit runtime failure
    # (both surface as Python's own traceback on stderr with a
    # non-zero exit code) - reported uniformly as runtime_error,
    # which is still an honest, useful classification for the
    # caller (a genuine platform/sandbox failure would come back
    # as "rejected" or as this adapter's own except-block below,
    # never silently mislabeled as the candidate's fault).
    return False, "runtime_error"


class PythonRunner:

    language = "python"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        """
        The Python runner is available exactly when the SAME
        interpreter this FastAPI process itself is running under
        exists — sys.executable is always populated whenever
        Python itself is running, so this is realistically always
        True; shutil.which() is used defensively only in case a
        future refactor changes how the interpreter path is
        resolved.
        """

        return bool(sys.executable) or shutil.which("python") is not None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        timeout_seconds = None

        if request.timeout_ms is not None:
            timeout_seconds = max(1, round(request.timeout_ms / 1000))

        outcome = run_python_code(
            request.source_code,
            request.stdin,
            time_limit_seconds=timeout_seconds,
        )

        success, error_type = _classify_sandbox_status(outcome.status)

        return ExecutionResult(
            success=success,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            exit_code=(0 if success else None),
            timed_out=(outcome.status == "timeout"),
            execution_time_ms=outcome.execution_time_ms,
            error_type=error_type,
            message=outcome.rejected_reason,
            language=self.language,
            runner_version=self.runner_version,
        )


register_runner(PythonRunner())

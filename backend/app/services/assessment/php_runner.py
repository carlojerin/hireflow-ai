import os
import shutil
import tempfile
import uuid
from pathlib import Path

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner
from backend.app.services.assessment.runner_common import (
    build_minimal_execution_env,
    run_subprocess_capturing,
)


# ============================================================
# JOBPILOT AI — PHP LANGUAGE RUNNER (Task 5C, Complete Multi-
# Language Execution Engine)
#
# Genuinely tested against the real PHP CLI installed for this
# task (winget PHP.PHP.8.3 — see this task's final report for
# the exact detected version/path). Interpreted — no compile
# step, mirrors javascript_runner.py's shape exactly.
#
# SECURITY BOUNDARY: same caveat as every non-Python runner in
# this codebase — no static-analysis allowlist. PHP additionally
# has no equivalent of Node's --disallow-code-generation-from-
# strings; `eval()`/`exec()`-style dynamic execution is NOT
# blocked here. See execution_security_v2.py for the honest,
# consolidated boundary report.
# ============================================================

RUNNER_VERSION = "php-cli-subprocess-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15


class PhpRunner:

    language = "php"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("php") is not None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="PHP CLI is not available on this machine.",
                language=self.language, runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000)))

        workdir = Path(tempfile.gettempdir()) / "jobpilot_php_runs" / uuid.uuid4().hex
        workdir.mkdir(parents=True, exist_ok=True)
        script_path = workdir / "submission.php"

        try:
            script_path.write_text(request.source_code, encoding="utf-8")

            env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

            outcome = run_subprocess_capturing(
                ["php", str(script_path)], stdin=request.stdin,
                timeout_seconds=timeout_seconds, env=env, cwd=str(workdir),
            )

            if outcome.timed_out:
                return ExecutionResult(
                    success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                    timed_out=True, execution_time_ms=outcome.elapsed_ms,
                    error_type="timeout", message=f"Execution exceeded {timeout_seconds}s.",
                    language=self.language, runner_version=self.runner_version,
                )

            if outcome.returncode == 0:
                return ExecutionResult(
                    success=True, stdout=outcome.stdout, stderr=outcome.stderr,
                    exit_code=0, execution_time_ms=outcome.elapsed_ms,
                    error_type="success", language=self.language, runner_version=self.runner_version,
                )

            return ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, execution_time_ms=outcome.elapsed_ms,
                error_type="runtime_error", message="The submission exited with a non-zero status.",
                language=self.language, runner_version=self.runner_version,
            )

        except Exception:
            return ExecutionResult(
                success=False, error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                language=self.language, runner_version=self.runner_version,
            )

        finally:
            try:
                script_path.unlink(missing_ok=True)
                workdir.rmdir()
            except OSError:
                pass


register_runner(PhpRunner())

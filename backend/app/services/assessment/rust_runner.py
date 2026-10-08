import os
import shutil

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner
from backend.app.services.assessment.runner_common import (
    build_minimal_execution_env,
    get_source_cache_dir,
    run_subprocess_capturing,
)


# ============================================================
# JOBPILOT AI — RUST LANGUAGE RUNNER (Task 5C, Complete Multi-
# Language Execution Engine)
#
# Genuinely tested against the real Rust toolchain installed for
# this task (winget Rustlang.Rustup -> rustc/cargo). Uses `rustc`
# directly (a single-file submission needs no Cargo project) —
# compile-then-run, hash-cached like every other compiled-
# language runner in this task.
# ============================================================

RUNNER_VERSION = "rust-rustc-compile-run-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15
_COMPILE_TIMEOUT_SECONDS = 30


class RustRunner:

    language = "rust"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("rustc") is not None

    def _build(self, source_code: str):

        cache_dir = get_source_cache_dir("rust", source_code)
        exe_path = cache_dir / "submission.exe"

        if exe_path.exists():
            return exe_path, None, None

        source_path = cache_dir / "submission.rs"
        source_path.write_text(source_code, encoding="utf-8")

        env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

        outcome = run_subprocess_capturing(
            ["rustc", str(source_path), "-o", str(exe_path), "-O"],
            timeout_seconds=_COMPILE_TIMEOUT_SECONDS, env=env, cwd=str(cache_dir),
        )

        if outcome.timed_out:
            return None, None, ExecutionResult(
                success=False, error_type="timeout", message="Compilation exceeded the build timeout.",
                language=self.language, runner_version=self.runner_version,
            )

        if outcome.returncode != 0 or not exe_path.exists():
            return None, None, ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, error_type="compile_error",
                message="The submission failed to compile.",
                language=self.language, runner_version=self.runner_version,
            )

        return exe_path, outcome.elapsed_ms, None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="Rust toolchain (rustc) is not available on this machine.",
                language=self.language, runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000)))

        try:
            exe_path, compile_time_ms, build_error = self._build(request.source_code)

            if build_error is not None:
                return build_error

            env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

            outcome = run_subprocess_capturing(
                [str(exe_path)], stdin=request.stdin,
                timeout_seconds=timeout_seconds, env=env,
            )

            if outcome.timed_out:
                return ExecutionResult(
                    success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                    timed_out=True, execution_time_ms=outcome.elapsed_ms,
                    compilation_time_ms=compile_time_ms,
                    error_type="timeout", message=f"Execution exceeded {timeout_seconds}s.",
                    language=self.language, runner_version=self.runner_version,
                )

            if outcome.returncode == 0:
                return ExecutionResult(
                    success=True, stdout=outcome.stdout, stderr=outcome.stderr,
                    exit_code=0, execution_time_ms=outcome.elapsed_ms,
                    compilation_time_ms=compile_time_ms,
                    error_type="success", language=self.language, runner_version=self.runner_version,
                )

            return ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, execution_time_ms=outcome.elapsed_ms,
                compilation_time_ms=compile_time_ms,
                error_type="runtime_error", message="The submission exited with a non-zero status.",
                language=self.language, runner_version=self.runner_version,
            )

        except Exception:
            return ExecutionResult(
                success=False, error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                language=self.language, runner_version=self.runner_version,
            )


register_runner(RustRunner())

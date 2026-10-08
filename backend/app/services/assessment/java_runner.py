import os
import re
import shutil

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner
from backend.app.services.assessment.runner_common import (
    build_minimal_execution_env,
    get_source_cache_dir,
    run_subprocess_capturing,
)


# ============================================================
# JOBPILOT AI — JAVA LANGUAGE RUNNER (Task 5C, Complete Multi-
# Language Execution Engine)
#
# Genuinely tested against the real JDK installed for this task
# (winget Microsoft.OpenJDK.21 -> javac/java). Compile-then-run,
# hash-cached like every other compiled-language runner here.
#
# JAVA'S OWN FILENAME CONSTRAINT: javac requires the source file
# to be named exactly "<PublicClassName>.java". Rather than force
# every candidate to name their class "Main" (fragile — a
# perfectly correct submission with `public class Solution`
# would otherwise fail to compile for a reason that has nothing
# to do with their actual code), this runner detects the public
# class name via a regex scan and names the file accordingly,
# defaulting to "Main" only when no `public class X` is found at
# all (e.g. a submission with a syntax error before its class
# declaration — that case still reaches javac and gets a genuine,
# accurate compile_error, never silently misclassified as
# something else).
# ============================================================

RUNNER_VERSION = "java-javac-compile-run-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15
_COMPILE_TIMEOUT_SECONDS = 30

_PUBLIC_CLASS_PATTERN = re.compile(r"public\s+(?:final\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)")


def _detect_public_class_name(source_code: str) -> str:

    match = _PUBLIC_CLASS_PATTERN.search(source_code)

    return match.group(1) if match else "Main"


class JavaRunner:

    language = "java"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("javac") is not None and shutil.which("java") is not None

    def _build(self, source_code: str):

        class_name = _detect_public_class_name(source_code)
        cache_dir = get_source_cache_dir("java", source_code)
        class_file = cache_dir / f"{class_name}.class"

        if class_file.exists():
            return class_name, cache_dir, None, None

        source_path = cache_dir / f"{class_name}.java"
        source_path.write_text(source_code, encoding="utf-8")

        env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

        outcome = run_subprocess_capturing(
            ["javac", "-d", str(cache_dir), str(source_path)],
            timeout_seconds=_COMPILE_TIMEOUT_SECONDS, env=env, cwd=str(cache_dir),
        )

        if outcome.timed_out:
            return None, None, None, ExecutionResult(
                success=False, error_type="timeout", message="Compilation exceeded the build timeout.",
                language=self.language, runner_version=self.runner_version,
            )

        if outcome.returncode != 0 or not class_file.exists():
            return None, None, None, ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, error_type="compile_error",
                message="The submission failed to compile.",
                language=self.language, runner_version=self.runner_version,
            )

        return class_name, cache_dir, outcome.elapsed_ms, None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="Java Development Kit (javac/java) is not available on this machine.",
                language=self.language, runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000)))

        try:
            class_name, cache_dir, compile_time_ms, build_error = self._build(request.source_code)

            if build_error is not None:
                return build_error

            env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

            outcome = run_subprocess_capturing(
                ["java", "-cp", str(cache_dir), class_name], stdin=request.stdin,
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


register_runner(JavaRunner())

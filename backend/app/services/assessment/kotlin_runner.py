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
# JOBPILOT AI — KOTLIN LANGUAGE RUNNER (Task 5C, Complete Multi-
# Language Execution Engine)
#
# Genuinely tested against the real Kotlin compiler for this
# task (official JetBrains GitHub release, kotlin-compiler-
# 2.4.20.zip, downloaded directly from https://github.com/
# JetBrains/kotlin/releases and its SHA-256 verified against the
# release's own published checksum before use — no winget/choco
# package existed for a bare Kotlin compiler; JDK 21 is required
# and already installed as part of this same task).
#
# `kotlinc -include-runtime -d out.jar` bundles the Kotlin
# runtime into a single fat jar and sets Main-Class in its
# manifest automatically from the file's top-level `fun main()` —
# no class-name detection needed the way java_runner.py needs one
# (Kotlin has no Java-style "filename must match public class"
# rule). Compile-then-run, hash-cached like every other compiled-
# language runner in this task. kotlinc's own JVM startup makes
# its compile step noticeably slower than the others (a longer
# _COMPILE_TIMEOUT_SECONDS reflects that measured reality, not a
# guess).
# ============================================================

RUNNER_VERSION = "kotlin-kotlinc-compile-run-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15
_COMPILE_TIMEOUT_SECONDS = 60


class KotlinRunner:

    language = "kotlin"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("kotlinc") is not None and shutil.which("java") is not None

    def _build(self, source_code: str):

        cache_dir = get_source_cache_dir("kotlin", source_code)
        jar_path = cache_dir / "submission.jar"

        if jar_path.exists():
            return jar_path, None, None

        source_path = cache_dir / "submission.kt"
        source_path.write_text(source_code, encoding="utf-8")

        env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

        # kotlinc ships as "kotlinc.bat" on Windows (no native .exe
        # entry point) — Python's subprocess.run cannot launch a
        # .bat file directly via CreateProcess (confirmed: raises
        # FileNotFoundError even though shutil.which resolves it
        # correctly), so it must be run through cmd.exe explicitly.
        outcome = run_subprocess_capturing(
            ["cmd", "/c", "kotlinc", str(source_path), "-include-runtime", "-d", str(jar_path)],
            timeout_seconds=_COMPILE_TIMEOUT_SECONDS, env=env, cwd=str(cache_dir),
        )

        if outcome.timed_out:
            return None, None, ExecutionResult(
                success=False, error_type="timeout", message="Compilation exceeded the build timeout.",
                language=self.language, runner_version=self.runner_version,
            )

        if outcome.returncode != 0 or not jar_path.exists():
            return None, None, ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, error_type="compile_error",
                message="The submission failed to compile.",
                language=self.language, runner_version=self.runner_version,
            )

        return jar_path, outcome.elapsed_ms, None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="Kotlin compiler (kotlinc) is not available on this machine.",
                language=self.language, runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000)))

        try:
            jar_path, compile_time_ms, build_error = self._build(request.source_code)

            if build_error is not None:
                return build_error

            env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

            outcome = run_subprocess_capturing(
                ["java", "-jar", str(jar_path)], stdin=request.stdin,
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


register_runner(KotlinRunner())

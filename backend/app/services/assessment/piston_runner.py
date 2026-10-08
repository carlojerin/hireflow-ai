import re
import time
from typing import Callable

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment import piston_client


# ============================================================
# JOBPILOT AI - PISTON-BACKED LANGUAGE RUNNER (replaces the bare
# OS-subprocess runners for every general-purpose language)
#
# One generic LanguageRunner implementation, parametrized per
# language, instead of a bespoke subprocess/compiler invocation
# per language (the pattern the old python_runner.py/java_runner.py/
# etc. used). Every actual security property — process isolation,
# memory ceiling, wall-clock timeout, no network access — is
# Piston's own, not re-implemented here; this class only adapts
# JobPilot's language-neutral ExecutionRequest/ExecutionResult
# shape onto Piston's request/response shape.
#
# sql_runner.py and html_runner.py are DELIBERATELY untouched —
# SQL already runs in-process against a read-only-authorized
# in-memory SQLite connection (no subprocess at all), and HTML
# runs inside a headless browser with its own CSP/network lockdown
# — neither is a general "run this arbitrary source as a program"
# case Piston applies to.
# ============================================================

RUNNER_VERSION_PREFIX = "piston"

_JAVA_PUBLIC_CLASS_PATTERN = re.compile(
    r"public\s+(?:final\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


def _java_filename(source_code: str) -> str:
    """
    Mirrors the old java_runner.py's own filename convention:
    javac (which Piston's Java executor uses internally) requires
    the source file to be named after its public class, defaulting
    to "Main" only when none is found.
    """

    match = _JAVA_PUBLIC_CLASS_PATTERN.search(source_code)
    class_name = match.group(1) if match else "Main"
    return f"{class_name}.java"


# language -> (piston_language, piston_version, filename | filename_fn)
_PISTON_LANGUAGES: dict[str, tuple[str, str, str | Callable[[str], str]]] = {
    "python": ("python", "3.10.0", "main.py"),
    "javascript": ("javascript", "18.15.0", "main.js"),
    "typescript": ("typescript", "5.0.3", "main.ts"),
    "java": ("java", "15.0.2", _java_filename),
    "c": ("c", "10.2.0", "main.c"),
    "cpp": ("c++", "10.2.0", "main.cpp"),
    "go": ("go", "1.16.2", "main.go"),
    "rust": ("rust", "1.68.2", "main.rs"),
    "php": ("php", "8.2.3", "main.php"),
    "ruby": ("ruby", "3.0.1", "main.rb"),
    "kotlin": ("kotlin", "1.8.20", "main.kt"),
    "csharp": ("csharp", "6.12.0", "main.cs"),
}

_DEFAULT_TIMEOUT_MS = 10_000
_MAX_TIMEOUT_MS = 15_000


class PistonRunner:

    def __init__(self, language: str, piston_language: str, piston_version: str,
                 filename: str | Callable[[str], str]):

        self.language = language
        self.piston_language = piston_language
        self.piston_version = piston_version
        self._filename = filename
        self.runner_version = f"{RUNNER_VERSION_PREFIX}-{piston_language}-{piston_version}"

    def _filename_for(self, source_code: str) -> str:

        if callable(self._filename):
            return self._filename(source_code)

        return self._filename

    def available(self) -> bool:
        return piston_client.is_available()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        timeout_ms = _DEFAULT_TIMEOUT_MS

        if request.timeout_ms is not None:
            timeout_ms = min(_MAX_TIMEOUT_MS, max(1000, request.timeout_ms))

        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:

            response = piston_client.execute(
                language=self.piston_language,
                version=self.piston_version,
                files=[{
                    "name": self._filename_for(request.source_code),
                    "content": request.source_code,
                }],
                stdin=request.stdin,
                run_timeout_ms=timeout_ms,
            )

        except piston_client.PistonUnavailableError:

            return ExecutionResult(
                success=False,
                error_type="runner_unavailable",
                message="The execution sandbox is not currently reachable.",
                language=self.language,
                runner_version=self.runner_version,
            )

        except piston_client.PistonExecutionError as exc:

            return ExecutionResult(
                success=False,
                error_type="sandbox_error",
                message="The execution sandbox rejected this request.",
                execution_time_ms=elapsed_ms(),
                language=self.language,
                runner_version=self.runner_version,
                stderr=str(exc),
            )

        except Exception:

            return ExecutionResult(
                success=False,
                error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                execution_time_ms=elapsed_ms(),
                language=self.language,
                runner_version=self.runner_version,
            )

        compile_step = response.get("compile") or {}
        run_step = response.get("run") or {}

        # A compile step is present (non-null) only for compiled
        # languages, and Piston reports its own failure the same
        # way run's is reported below: a non-zero code, or a
        # signal with no code at all (killed — compile timeout).
        if compile_step and (
            compile_step.get("signal") is not None
            or (compile_step.get("code") or 0) != 0
        ):

            timed_out = compile_step.get("signal") is not None and compile_step.get("code") is None

            return ExecutionResult(
                success=False,
                stdout=compile_step.get("stdout") or "",
                stderr=compile_step.get("stderr") or "",
                exit_code=compile_step.get("code"),
                timed_out=timed_out,
                execution_time_ms=elapsed_ms(),
                error_type="timeout" if timed_out else "compile_error",
                message=(
                    "Compilation exceeded the build timeout."
                    if timed_out else
                    "The submission failed to compile."
                ),
                language=self.language,
                runner_version=self.runner_version,
            )

        run_code = run_step.get("code")
        run_signal = run_step.get("signal")

        # Piston kills the process (code is null, signal set) when
        # it exceeds run_timeout — the one case this project's own
        # subprocess-based runners called "timeout".
        if run_code is None and run_signal is not None:

            return ExecutionResult(
                success=False,
                stdout=run_step.get("stdout") or "",
                stderr=run_step.get("stderr") or "",
                timed_out=True,
                execution_time_ms=elapsed_ms(),
                error_type="timeout",
                message=f"Execution exceeded {timeout_ms}ms.",
                language=self.language,
                runner_version=self.runner_version,
            )

        if run_code == 0:

            return ExecutionResult(
                success=True,
                stdout=run_step.get("stdout") or "",
                stderr=run_step.get("stderr") or "",
                exit_code=0,
                execution_time_ms=elapsed_ms(),
                error_type="success",
                language=self.language,
                runner_version=self.runner_version,
            )

        return ExecutionResult(
            success=False,
            stdout=run_step.get("stdout") or "",
            stderr=run_step.get("stderr") or "",
            exit_code=run_code,
            execution_time_ms=elapsed_ms(),
            error_type="runtime_error",
            message="The submission exited with a non-zero status.",
            language=self.language,
            runner_version=self.runner_version,
        )


def register_piston_runners() -> None:
    """
    Registers one PistonRunner per general-purpose language,
    superseding any bare-subprocess runner registered for the same
    language name earlier in language_runner.py's import order —
    RUNNER_REGISTRY is a plain dict keyed by language, so whichever
    registration runs LAST wins.

    Imports register_runner lazily (not at module load time) so
    this module can be imported on its own (e.g. by tests) without
    tripping a circular import against language_runner.py, which
    itself imports THIS module at its own bottom.
    """

    from backend.app.services.assessment.language_runner import register_runner

    for language, (piston_language, piston_version, filename) in _PISTON_LANGUAGES.items():

        register_runner(
            PistonRunner(language, piston_language, piston_version, filename)
        )

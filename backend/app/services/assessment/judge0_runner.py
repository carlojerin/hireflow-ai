import re
import time

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment import judge0_client


# ============================================================
# JOBPILOT AI - JUDGE0-BACKED LANGUAGE RUNNER
#
# Supersedes piston_runner.py (left on disk, unused) for every
# general-purpose language. One generic LanguageRunner
# implementation, parametrized per language by a Judge0 language_id
# -- every actual security property (process isolation via
# `isolate`, memory ceiling, wall-clock timeout, no network access
# by default) is Judge0's own, not re-implemented here.
#
# sql_runner.py and html_runner.py are DELIBERATELY untouched, same
# reasoning as before Piston: neither is a general "run this
# arbitrary source as a program" case.
# ============================================================

RUNNER_VERSION_PREFIX = "judge0"

_JAVA_PUBLIC_CLASS_PATTERN = re.compile(
    r"public\s+(?:final\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


def _normalize_java_source(source_code: str) -> str:
    """
    Judge0 always compiles Java submissions as "Main.java" — unlike
    the old java_runner.py/piston_runner.py, which detected whatever
    public class name the candidate (or this project's own question
    starter-code templates, which use "Solution") actually used.
    Renaming that identifier to "Main" here — at the execution
    boundary only — keeps every existing Java question/starter-code
    template working unchanged, rather than requiring every Java
    question in the system to be regenerated with "public class Main".
    A submission with no `public class X` at all (e.g. a syntax
    error before the class declaration) is left as-is and still
    reaches the compiler for a genuine, accurate compile error.
    """

    match = _JAVA_PUBLIC_CLASS_PATTERN.search(source_code)

    if not match or match.group(1) == "Main":
        return source_code

    class_name = match.group(1)

    return re.sub(rf"\b{re.escape(class_name)}\b", "Main", source_code)


# language -> (judge0_language_id, display version, source normalizer | None)
#
# IDs verified against THIS PROJECT'S self-hosted judge0/judge0:latest
# image's own GET /languages (its seed data is the older, standard
# Judge0 CE set -- meaningfully different ids/versions than the
# public ce.judge0.com demo instance, which runs an extended fork).
# Re-check GET /languages before changing any id here.
_JUDGE0_LANGUAGES: dict[str, tuple[int, str, object]] = {
    "python": (71, "3.8.1", None),
    "javascript": (63, "Node.js 12.14.0", None),
    "typescript": (74, "3.7.4", None),
    "java": (62, "OpenJDK 13.0.1", _normalize_java_source),
    "c": (50, "GCC 9.2.0", None),
    "cpp": (54, "GCC 9.2.0", None),
    "go": (60, "1.13.5", None),
    "rust": (73, "1.40.0", None),
    "php": (68, "7.4.1", None),
    "ruby": (72, "2.7.0", None),
    "kotlin": (78, "1.3.70", None),
    "csharp": (51, "Mono 6.6.0.161", None),
}

_DEFAULT_TIMEOUT_MS = 10_000
_MAX_TIMEOUT_MS = 15_000  # Judge0's own hard ceiling on cpu_time_limit is 15s


class Judge0Runner:

    def __init__(self, language: str, language_id: int, version_label: str, normalizer):

        self.language = language
        self.language_id = language_id
        self._normalizer = normalizer
        self.runner_version = f"{RUNNER_VERSION_PREFIX}-{language}-{version_label}"

    def available(self) -> bool:
        return judge0_client.is_available()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        timeout_ms = _DEFAULT_TIMEOUT_MS

        if request.timeout_ms is not None:
            timeout_ms = min(_MAX_TIMEOUT_MS, max(1000, request.timeout_ms))

        source_code = request.source_code

        if self._normalizer is not None:
            source_code = self._normalizer(source_code)

        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:

            result = judge0_client.execute(
                language_id=self.language_id,
                source_code=source_code,
                stdin=request.stdin,
                cpu_time_limit=timeout_ms / 1000.0,
            )

        except judge0_client.Judge0UnavailableError:

            return ExecutionResult(
                success=False,
                error_type="runner_unavailable",
                message="The execution sandbox is not currently reachable.",
                language=self.language,
                runner_version=self.runner_version,
            )

        except judge0_client.Judge0ExecutionError as exc:

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

        status_id = result["status_id"]

        execution_time_ms = (
            int(result["time_seconds"] * 1000)
            if result.get("time_seconds") is not None
            else elapsed_ms()
        )

        if status_id == judge0_client.STATUS_COMPILATION_ERROR:

            return ExecutionResult(
                success=False,
                stdout=result["stdout"],
                stderr=result["compile_output"] or result["stderr"],
                execution_time_ms=execution_time_ms,
                error_type="compile_error",
                message="The submission failed to compile.",
                language=self.language,
                runner_version=self.runner_version,
            )

        if status_id == judge0_client.STATUS_TIME_LIMIT_EXCEEDED:

            return ExecutionResult(
                success=False,
                stdout=result["stdout"],
                stderr=result["stderr"],
                timed_out=True,
                execution_time_ms=execution_time_ms,
                error_type="timeout",
                message=f"Execution exceeded {timeout_ms}ms.",
                language=self.language,
                runner_version=self.runner_version,
            )

        if status_id in judge0_client.STATUS_RUNTIME_ERROR_RANGE:

            return ExecutionResult(
                success=False,
                stdout=result["stdout"],
                stderr=result["stderr"] or result["message"],
                exit_code=result.get("exit_code"),
                execution_time_ms=execution_time_ms,
                error_type="runtime_error",
                message="The submission exited with a non-zero status.",
                language=self.language,
                runner_version=self.runner_version,
            )

        if status_id in (judge0_client.STATUS_ACCEPTED, judge0_client.STATUS_WRONG_ANSWER):

            # STATUS_WRONG_ANSWER only occurs when `expected_output` was
            # sent — this project never sends it (scoring happens on
            # this project's own side via programming_scoring_v2.py),
            # so ACCEPTED is the only success status actually reached;
            # both are treated as "ran successfully" here regardless.
            return ExecutionResult(
                success=True,
                stdout=result["stdout"],
                stderr=result["stderr"],
                exit_code=result.get("exit_code") or 0,
                execution_time_ms=execution_time_ms,
                error_type="success",
                language=self.language,
                runner_version=self.runner_version,
            )

        # STATUS_INTERNAL_ERROR, STATUS_EXEC_FORMAT_ERROR, or an
        # unexpected/still-queued status -- a genuine platform
        # failure, never the candidate's fault.
        return ExecutionResult(
            success=False,
            stdout=result["stdout"],
            stderr=result["stderr"],
            execution_time_ms=execution_time_ms,
            error_type="sandbox_error",
            message=result["status_description"] or "The execution sandbox encountered an internal error.",
            language=self.language,
            runner_version=self.runner_version,
        )


def register_judge0_runners() -> None:
    """
    Registers one Judge0Runner per general-purpose language.
    Imports register_runner lazily (not at module load time) so
    this module can be imported on its own (e.g. by tests) without
    tripping a circular import against language_runner.py, which
    itself imports THIS module at its own bottom -- same pattern as
    piston_runner.py's register_piston_runners().
    """

    from backend.app.services.assessment.language_runner import register_runner

    for language, (language_id, version_label, normalizer) in _JUDGE0_LANGUAGES.items():

        register_runner(
            Judge0Runner(language, language_id, version_label, normalizer)
        )

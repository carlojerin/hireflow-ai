import re
import time
from dataclasses import dataclass
from typing import Callable

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment import docker_sandbox_client as sandbox


# ============================================================
# JOBPILOT AI - DIRECT-DOCKER LANGUAGE RUNNER
#
# One generic runner, parametrized per language by a Docker image
# plus an optional compile step and a run step — see
# docker_sandbox_client.py's own header for why this replaced both
# piston_runner.py and judge0_runner.py (both left on disk, unused).
# ============================================================

RUNNER_VERSION_PREFIX = "docker-sandbox"

_JAVA_PUBLIC_CLASS_PATTERN = re.compile(
    r"public\s+(?:final\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)


def _java_filename(source_code: str) -> str:
    """
    Unlike Judge0 (which always compiles as a fixed Main.java), a
    direct `docker run` lets this project keep the SAME dynamic
    filename convention the original, pre-Piston java_runner.py
    used: name the file after whatever public class the candidate's
    (or this project's own question templates') source actually
    declares, defaulting to "Main" only when none is found.
    """

    match = _JAVA_PUBLIC_CLASS_PATTERN.search(source_code)
    class_name = match.group(1) if match else "Main"
    return class_name, f"{class_name}.java"


@dataclass(frozen=True)
class _LanguageSpec:

    image: str
    filename: str  # ignored for "java", which names its file dynamically (see _java_filename)
    compile_cmd: Callable[[str], list[str]] | None  # None means no compile step
    run_cmd: Callable[[str], list[str]]
    # Overrides sandbox.DEFAULT_MEMORY_MB for the COMPILE step only
    # (the run step always uses the tighter default — a candidate's
    # own small program never legitimately needs more). kotlinc's
    # own JVM compiler daemon gets OOM-killed under the 256MB
    # default even for a trivial "Hello World" — this was discovered
    # by hitting it directly, not assumed in advance.
    compile_memory_mb: int | None = None
    # Overrides _COMPILE_TIMEOUT_SECONDS for the COMPILE step only —
    # kotlinc's cold JVM+compiler-daemon startup alone routinely
    # exceeds the normal 30s allowance even once OOM is no longer
    # the problem; again, measured, not assumed.
    compile_timeout_seconds: float | None = None


_LANGUAGES: dict[str, _LanguageSpec] = {

    "python": _LanguageSpec(
        image="python:3.11-slim", filename="main.py",
        compile_cmd=None, run_cmd=lambda name: ["python", name],
    ),
    "javascript": _LanguageSpec(
        image="node:18-slim", filename="main.js",
        compile_cmd=None, run_cmd=lambda name: ["node", name],
    ),
    "typescript": _LanguageSpec(
        # Custom image (docker/typescript-sandbox/Dockerfile) -- no
        # official slim image bundles the TypeScript compiler.
        # --typeRoots points tsc at npm's GLOBAL @types directory --
        # without it, tsc never finds the globally-installed
        # @types/node at all (it only auto-discovers @types/* next
        # to a LOCAL node_modules), so any candidate solution using
        # Node's own globals (require, process, stdin reads, ...)
        # would otherwise fail to even compile.
        image="jobpilot-sandbox-typescript:latest", filename="main.ts",
        compile_cmd=lambda name: [
            "tsc", "--typeRoots", "/usr/local/lib/node_modules/@types", name,
        ],
        run_cmd=lambda name: ["node", "main.js"],
    ),
    "java": _LanguageSpec(
        image="eclipse-temurin:17-jdk-alpine", filename="",  # resolved dynamically
        compile_cmd=lambda name: ["javac", f"{name}.java"],
        run_cmd=lambda name: ["java", "-cp", ".", name],
    ),
    "c": _LanguageSpec(
        image="gcc:13", filename="main.c",
        compile_cmd=lambda name: ["gcc", name, "-O2", "-o", "program"],
        run_cmd=lambda name: ["./program"],
    ),
    "cpp": _LanguageSpec(
        image="gcc:13", filename="main.cpp",
        compile_cmd=lambda name: ["g++", name, "-O2", "-o", "program"],
        run_cmd=lambda name: ["./program"],
    ),
    "go": _LanguageSpec(
        image="golang:1.21-alpine", filename="main.go",
        compile_cmd=lambda name: ["go", "build", "-o", "program", name],
        run_cmd=lambda name: ["./program"],
    ),
    "rust": _LanguageSpec(
        image="rust:1.75-slim", filename="main.rs",
        compile_cmd=lambda name: ["rustc", "-O", name, "-o", "program"],
        run_cmd=lambda name: ["./program"],
    ),
    "kotlin": _LanguageSpec(
        # Pre-built image (zenika/kotlin, archived but still pullable)
        # rather than a custom build fetching the compiler from
        # GitHub releases -- the exact release-asset CDN path that
        # proved unreliable for Piston earlier in this project.
        image="zenika/kotlin:1.4.20-jdk11", filename="main.kt",
        compile_cmd=lambda name: ["kotlinc", name, "-include-runtime", "-d", "program.jar"],
        run_cmd=lambda name: ["java", "-jar", "program.jar"],
        compile_memory_mb=768,
        # kotlinc itself only takes ~3s standalone (confirmed) -- the
        # real cost here is Docker Desktop's bind-mount I/O between
        # Windows and the container, which the sheer volume of small
        # classfile reads/writes kotlinc's jar-packing does hits hard.
        # Generous on purpose until/unless this is worth a bigger fix
        # (a Docker-native named volume instead of a host bind mount).
        compile_timeout_seconds=150.0,
    ),
    "php": _LanguageSpec(
        image="php:8.2-cli", filename="main.php",
        compile_cmd=None, run_cmd=lambda name: ["php", name],
    ),
    "ruby": _LanguageSpec(
        image="ruby:3.2-slim", filename="main.rb",
        compile_cmd=None, run_cmd=lambda name: ["ruby", name],
    ),
    "csharp": _LanguageSpec(
        # Official mono image already bundles mcs (the Mono C#
        # compiler) and the mono runtime -- no custom build needed,
        # unlike typescript/kotlin.
        image="mono:6.12", filename="main.cs",
        compile_cmd=lambda name: ["mcs", name, "-out:program.exe"],
        run_cmd=lambda name: ["mono", "program.exe"],
    ),
    "swift": _LanguageSpec(
        # The earlier host-native approach (swift_runner.py) hit a
        # genuine, evidenced Windows-toolchain dead end: the winget
        # Swift.Toolchain package has no Windows-target standard
        # library at all. The official LINUX swift Docker image has
        # no such gap -- confirmed directly (compile + run both
        # succeeded on the first real test) -- so Swift runs through
        # the exact same Docker sandbox every other language does,
        # never touching the Windows toolchain.
        image="swift:5.9-jammy", filename="main.swift",
        compile_cmd=lambda name: ["swiftc", name, "-o", "program"],
        run_cmd=lambda name: ["./program"],
        compile_memory_mb=768, compile_timeout_seconds=90.0,
    ),
}

_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_TIMEOUT_SECONDS = 15.0
_COMPILE_TIMEOUT_SECONDS = 30.0


class DockerSandboxRunner:

    def __init__(self, language: str):

        self.language = language
        self.runner_version = f"{RUNNER_VERSION_PREFIX}-{language}"
        self._spec = _LANGUAGES[language]

    def available(self) -> bool:
        return sandbox.is_available()

    def _resolve_filename(self, source_code: str) -> tuple[str, str]:
        """Returns (filename, run_identifier) — for Java, run_identifier is the
        class name `java -cp . <ClassName>` needs; for everything else it's the
        same as filename."""

        if self.language == "java":
            class_name, filename = _java_filename(source_code)
            return filename, class_name

        return self._spec.filename, self._spec.filename

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS

        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1.0, request.timeout_ms / 1000))

        filename, run_identifier = self._resolve_filename(request.source_code)

        files = {filename: request.source_code}

        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:

            compile_time_ms = None
            # One shared host work directory for BOTH the compile
            # step and the run step that follows it — run_in_container()
            # itself never creates or deletes a directory, so a
            # compiled binary the compile step wrote is still there
            # for the run step (a real bug this project's first
            # version of this file had: each step got its OWN
            # throwaway directory, so the run step could never see
            # anything the compile step had just built).
            work_dir = sandbox.create_workdir(files)

            try:

                if self._spec.compile_cmd is not None:

                    compile_kwargs = {}
                    if self._spec.compile_memory_mb is not None:
                        compile_kwargs["memory_mb"] = self._spec.compile_memory_mb

                    compile_timeout = self._spec.compile_timeout_seconds or _COMPILE_TIMEOUT_SECONDS

                    compile_result = sandbox.run_in_container(
                        self._spec.image, self._spec.compile_cmd(run_identifier), work_dir,
                        timeout_seconds=compile_timeout, **compile_kwargs,
                    )
                    compile_time_ms = compile_result.elapsed_ms

                    if compile_result.timed_out:
                        return ExecutionResult(
                            success=False, error_type="timeout",
                            message="Compilation exceeded the build timeout.",
                            stderr=compile_result.stderr,
                            language=self.language, runner_version=self.runner_version,
                        )

                    if compile_result.exit_code != 0:
                        return ExecutionResult(
                            success=False, error_type="compile_error",
                            message="The submission failed to compile.",
                            stdout=compile_result.stdout, stderr=compile_result.stderr,
                            exit_code=compile_result.exit_code,
                            language=self.language, runner_version=self.runner_version,
                        )

                run_cmd = self._spec.run_cmd(run_identifier)

                run_result = sandbox.run_in_container(
                    self._spec.image, run_cmd, work_dir,
                    stdin=request.stdin, timeout_seconds=timeout_seconds,
                )

                if run_result.timed_out:
                    return ExecutionResult(
                        success=False, error_type="timeout",
                        message=f"Execution exceeded {int(timeout_seconds * 1000)}ms.",
                        stdout=run_result.stdout, stderr=run_result.stderr,
                        timed_out=True, execution_time_ms=run_result.elapsed_ms,
                        compilation_time_ms=compile_time_ms,
                        language=self.language, runner_version=self.runner_version,
                    )

                if run_result.exit_code == 0:
                    return ExecutionResult(
                        success=True, error_type="success",
                        stdout=run_result.stdout, stderr=run_result.stderr,
                        exit_code=0, execution_time_ms=run_result.elapsed_ms,
                        compilation_time_ms=compile_time_ms,
                        language=self.language, runner_version=self.runner_version,
                    )

                return ExecutionResult(
                    success=False, error_type="runtime_error",
                    message="The submission exited with a non-zero status.",
                    stdout=run_result.stdout, stderr=run_result.stderr,
                    exit_code=run_result.exit_code, execution_time_ms=run_result.elapsed_ms,
                    compilation_time_ms=compile_time_ms,
                    language=self.language, runner_version=self.runner_version,
                )

            finally:
                sandbox.cleanup_workdir(work_dir)

        except sandbox.DockerUnavailableError:

            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="The execution sandbox is not currently reachable.",
                language=self.language, runner_version=self.runner_version,
            )

        except Exception:

            return ExecutionResult(
                success=False, error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                execution_time_ms=elapsed_ms(),
                language=self.language, runner_version=self.runner_version,
            )


def register_docker_sandbox_runners() -> None:
    """
    Registers one DockerSandboxRunner per language this file defines
    a spec for — all 13 of this project's target languages.
    """

    from backend.app.services.assessment.language_runner import register_runner

    for language in _LANGUAGES:
        register_runner(DockerSandboxRunner(language))

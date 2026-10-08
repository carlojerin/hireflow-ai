from typing import Protocol

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_capability_registry import (
    get_language_capability,
    normalize_language_name,
)


# ============================================================
# JOBPILOT AI - LANGUAGE RUNNER INTERFACE + DISPATCH
# (Task 5C Step 4)
#
# A clean, consistent execution contract every language runner
# implements — today that is exactly one real implementation
# (PythonRunner, in python_runner.py, itself a thin adapter over
# the EXISTING, unmodified code_sandbox.py). Adding Java/C++/Go/
# etc. later means writing one more class implementing this same
# Protocol and registering it in RUNNER_REGISTRY below — nothing
# else in the assessment engine needs to change (Step 4's own
# "the assessment engine should not care how a language
# executes" requirement).
#
# execute_code() below is the ONE public entry point everything
# else in this codebase should call — it is responsible for
# language normalization, capability-gating (never invokes a
# runner for a language that isn't genuinely execution_supported
# — see language_capability_registry.py), and returning a
# uniform ExecutionResult even when no runner exists at all.
# ============================================================


class LanguageRunner(Protocol):
    """
    The common shape every language runner exposes. A Protocol
    (structural typing) rather than an ABC — a runner does not
    need to inherit from anything here, it only needs to match
    this shape, which keeps a future runner free to wrap whatever
    execution mechanism it needs internally.
    """

    language: str
    runner_version: str

    def available(self) -> bool:
        """
        Whether this runner can actually be used RIGHT NOW on
        this machine (e.g. the underlying interpreter/compiler is
        reachable) — distinct from whether the language is
        merely "known" to the registry.
        """
        ...

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """
        Executes request.source_code and returns an
        ExecutionResult. Must NEVER raise for a candidate-caused
        failure (syntax/compile error, runtime error, timeout) —
        those come back as a normal ExecutionResult with the
        appropriate error_type. Only a genuine infrastructure
        failure (the runner itself is broken) may raise, and even
        then execute_code() below catches it and converts it to a
        sandbox_error ExecutionResult rather than letting it
        propagate to a caller.
        """
        ...


# Populated by python_runner.py's own registration call at import
# time (see the bottom of that file) — kept here, rather than
# importing PythonRunner directly, so this module has zero
# dependency on any SPECIFIC runner's implementation details,
# only on the Protocol shape above.
RUNNER_REGISTRY: dict[str, LanguageRunner] = {}


def register_runner(runner: LanguageRunner) -> None:

    RUNNER_REGISTRY[runner.language] = runner


def execute_code(
    language: str,
    source_code: str,
    stdin: str = "",
    timeout_ms: int | None = None,
    test_case_id: str | None = None,
) -> ExecutionResult:
    """
    The one public entry point for running candidate code in any
    language. Always returns an ExecutionResult — never raises,
    never silently pretends a language is executable when it is
    not, and never falls back to running the code as a different
    language than the caller asked for (e.g. it will NEVER run
    Java source through the Python runner).
    """

    canonical_name = normalize_language_name(language)

    if canonical_name is None:

        return ExecutionResult(
            success=False,
            error_type="invalid_language",
            message=f"'{language}' is not a recognized language.",
            language=(language or "").strip(),
        )

    capability = get_language_capability(canonical_name)

    if not capability.execution_supported:

        return ExecutionResult(
            success=False,
            error_type="runner_unavailable",
            message=(
                f"JobPilot does not yet have a secure execution "
                f"runner for '{canonical_name}'."
            ),
            language=canonical_name,
        )

    runner = RUNNER_REGISTRY.get(canonical_name)

    if runner is None or not runner.available():

        return ExecutionResult(
            success=False,
            error_type="runner_unavailable",
            message=(
                f"The execution runner for '{canonical_name}' is "
                "not currently available."
            ),
            language=canonical_name,
        )

    try:

        request = ExecutionRequest(
            language=canonical_name,
            source_code=source_code,
            stdin=stdin,
            timeout_ms=timeout_ms,
            test_case_id=test_case_id,
        )

        return runner.execute(request)

    except Exception as exc:

        # A genuine runner-infrastructure failure (never a
        # candidate-code failure — runners are required to catch
        # those themselves and return a normal ExecutionResult).
        # Never leaks the exception's raw text, which could in
        # principle include a file path or other internal detail.
        _ = exc

        return ExecutionResult(
            success=False,
            error_type="sandbox_error",
            message="The execution sandbox encountered an internal error.",
            language=canonical_name,
        )


# ------------------------------------------------------------
# SANDBOXED EXECUTION (direct Docker)
#
# python/javascript/java/c/cpp/go/rust/php/ruby are dispatched
# through a direct Docker sandbox (docker_sandbox_runner.py) — each
# submission runs in its own throwaway container (--network none,
# memory/cpu/pids limits, non-root, read-only-by-default root fs).
# Docker's own container isolation is the sandbox; nothing
# third-party sits in front of it.
#
# This replaced two earlier attempts, both left on disk unused
# (this project's own "archive, don't delete" convention) rather
# than deleted:
#   - piston_runner.py/piston_client.py — Piston's post-boot
#     per-language package downloads proved unreliable on this
#     network's path to GitHub's release CDN.
#   - judge0_runner.py/judge0_client.py — Judge0's bundled `isolate`
#     sandbox needs legacy cgroup v1; Docker Desktop's internal Linux
#     environment has no systemd (its own minimal init) and always
#     mounts cgroup v2, a structural incompatibility no amount of
#     retrying or kernel-parameter tuning fixes.
#   - python_runner.py/java_runner.py/etc. (even older) — bare
#     `subprocess.run` with no OS-level isolation beyond a
#     bypassable AST denylist for Python only.
#
# typescript/kotlin/csharp are NOT YET included — they need a small
# custom-built image (a base image + one compiler installed) rather
# than an off-the-shelf official one; see docker_sandbox_runner.py's
# own register_docker_sandbox_runners() docstring. Until that
# follow-up lands, execute_code() correctly reports
# "runner_unavailable" for those three rather than failing oddly.
# ------------------------------------------------------------
from backend.app.services.assessment.docker_sandbox_runner import register_docker_sandbox_runners  # noqa: E402

register_docker_sandbox_runners()

# sql_runner.py and html_runner.py are UNCHANGED — neither is a
# bare-subprocess "run arbitrary source" case Judge0 applies to.
# SQL runs in-process against a read-only-authorized in-memory
# SQLite connection (no subprocess at all); HTML runs inside a
# headless browser with its own CSP/network lockdown.
from backend.app.services.assessment import sql_runner as _sql_runner  # noqa: E402,F401
from backend.app.services.assessment import html_runner as _html_runner  # noqa: E402,F401

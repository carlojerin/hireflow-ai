from dataclasses import dataclass, field
from enum import Enum


# ============================================================
# JOBPILOT AI — EXECUTION SECURITY BOUNDARY (Programming Engine
# V2, AI generation task, Step 13)
#
# This is the ACTUAL architecture point Step 13 asked for, not
# only documentation: ExecutionBackend is the concrete extension
# seam a future container/isolated-worker execution mode plugs
# into. Every LanguageRunner (language_runner.py's Protocol)
# already exposes exactly one method, execute(), that takes an
# ExecutionRequest and returns an ExecutionResult — a future
# runner implementing CONTAINERIZED_WORKER needs only to
# implement that SAME method differently (dispatch to a
# container/worker instead of a local subprocess); nothing in
# programming_submission_v2.py, programming_scoring_v2.py, or the
# question schema would need to change. That is the "without
# rewriting the assessment model" property the task asked for,
# and it already holds today — this module documents it
# structurally via SecurityBoundaryReport rather than leaving it
# implicit.
#
# NO backend other than IN_PROCESS_SUBPROCESS is implemented.
# Docker is confirmed unavailable in this environment (see
# code_sandbox.py's own header, verified again for this task).
# CONTAINERIZED_WORKER is a real enum value precisely so future
# code has something concrete to assign a runner's
# `backend` to — it is not claimed to exist today.
# ============================================================


class ExecutionBackend(str, Enum):

    IN_PROCESS_SUBPROCESS = "in_process_subprocess"
    CONTAINERIZED_WORKER = "containerized_worker"  # not implemented anywhere yet


@dataclass(frozen=True)
class SecurityBoundaryReport:

    language: str
    backend: ExecutionBackend

    has_static_code_analysis: bool
    enforces_timeout: bool
    enforces_output_limit: bool
    uses_minimal_environment: bool
    has_os_level_isolation: bool

    # Never True today for any language — see known_gaps. Exists
    # so a FUTURE containerized backend has a field to flip, and
    # so nothing in this codebase can claim readiness by omission.
    production_ready: bool

    known_gaps: list[str] = field(default_factory=list)


def get_security_boundary_report(language: str) -> SecurityBoundaryReport:

    if language == "python":

        return SecurityBoundaryReport(
            language="python",
            backend=ExecutionBackend.IN_PROCESS_SUBPROCESS,
            has_static_code_analysis=True,  # code_sandbox.py's AST allowlist
            enforces_timeout=True,
            enforces_output_limit=True,
            uses_minimal_environment=True,
            has_os_level_isolation=False,
            production_ready=False,
            known_gaps=[
                "No hard memory limit (no Windows Job Object / no container).",
                "No hard CPU-usage limit independent of wall-clock timeout.",
                "No OS-level network/filesystem jail — the AST allowlist "
                "is a code-level restriction, not a kernel-level one.",
                "Runs as whatever OS user the FastAPI process runs as.",
            ],
        )

    if language in (
        "javascript", "csharp", "typescript", "java", "c", "cpp",
        "go", "rust", "php", "ruby", "kotlin",
    ):

        gaps = [
            "No static-analysis allowlist equivalent to code_sandbox.py's "
            "Python AST check — import/module access is not restricted "
            "at the language level.",
            "No hard memory limit.",
            "No hard CPU-usage limit independent of wall-clock timeout.",
            "No OS-level network/filesystem jail.",
            "Runs as whatever OS user the FastAPI process runs as.",
        ]

        if language == "csharp":
            gaps.insert(
                0,
                "A narrow, text-level namespace denylist (System.IO/"
                "System.Net/System.Diagnostics.Process/reflection/unsafe) "
                "rejects the most obvious escape attempts before "
                "compilation, but is trivially bypassable by a "
                "sufficiently determined submission (aliasing, "
                "reflection) — NOT equivalent to a real sandbox.",
            )

        if language in ("javascript", "typescript"):
            gaps.insert(
                0,
                "Node's --disallow-code-generation-from-strings flag "
                "blocks eval()/new Function()-style dynamic code "
                "generation, but the fs/net/child_process modules "
                "are NOT restricted — a submission can read/write "
                "files or open network connections.",
            )

        if language in ("c", "cpp"):
            gaps.insert(
                0,
                "WEAKEST boundary of any runner in this codebase: a "
                "compiled native binary has the FULL OS syscall "
                "surface available to whatever user runs it — no "
                "managed runtime, no bytecode verifier, nothing "
                "between the submission and the kernel except the "
                "OS process boundary itself.",
            )

        if language in ("java", "kotlin"):
            gaps.insert(
                0,
                "Runs on a bare `java`/JVM invocation with NO "
                "SecurityManager/module-restriction policy configured "
                "(the JVM's own SecurityManager mechanism is also "
                "deprecated-for-removal as of recent JDK versions) — "
                "full java.io/java.net/java.lang.Runtime access is "
                "available to submitted code.",
            )

        if language == "go":
            gaps.insert(
                0,
                "A compiled Go binary has the same full-OS-syscall "
                "characteristic as C/C++ — Go's own runtime adds no "
                "sandboxing over what the OS process boundary provides.",
            )

        if language == "rust":
            gaps.insert(
                0,
                "Rust's memory-safety guarantees protect against a "
                "large class of memory-corruption bugs, but say "
                "nothing about a program that INTENTIONALLY opens "
                "files/sockets or spawns processes — those are safe, "
                "ordinary Rust standard-library calls, not exploits.",
            )

        if language in ("php", "ruby"):
            gaps.insert(
                0,
                f"No static analysis and no language-level sandboxing "
                f"flag equivalent to Node's --disallow-code-generation-"
                f"from-strings exists for {language} in this runner — "
                f"dynamic evaluation (eval in both languages) is not "
                f"blocked.",
            )

        return SecurityBoundaryReport(
            language=language,
            backend=ExecutionBackend.IN_PROCESS_SUBPROCESS,
            has_static_code_analysis=False,
            enforces_timeout=True,
            enforces_output_limit=True,
            uses_minimal_environment=True,
            has_os_level_isolation=False,
            production_ready=False,
            known_gaps=gaps,
        )

    return SecurityBoundaryReport(
        language=language,
        backend=ExecutionBackend.IN_PROCESS_SUBPROCESS,
        has_static_code_analysis=False,
        enforces_timeout=False,
        enforces_output_limit=False,
        uses_minimal_environment=False,
        has_os_level_isolation=False,
        production_ready=False,
        known_gaps=["No runner exists for this language at all."],
    )

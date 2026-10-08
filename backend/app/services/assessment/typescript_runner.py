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
# JOBPILOT AI — TYPESCRIPT LANGUAGE RUNNER (Task 5C, Complete
# Multi-Language Execution Engine)
#
# Genuinely tested against the real TypeScript compiler for this
# task (npm install -g typescript, using the Node.js install
# javascript_runner.py already depends on). .ts -> tsc -> .js ->
# node, exactly the pipeline this task specifies. Distinguishes
# a genuine COMPILE error (tsc's own diagnostics, non-zero exit)
# from a RUNTIME error in the compiled JS (node's non-zero exit)
# — two different pipeline stages, two different error_type
# values, never conflated.
# ============================================================

RUNNER_VERSION = "typescript-tsc-node-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 15
_COMPILE_TIMEOUT_SECONDS = 20

def _global_npm_type_roots() -> str:
    """
    Global @types/node (npm install -g @types/node, done for this
    task) lives under npm's own global node_modules, not under
    the per-submission cache_dir this runner compiles from — so
    tsc would never resolve it via ordinary node_modules lookup.
    --typeRoots pointed explicitly here is what makes Node
    built-ins (require, process, fs, etc.) type-check for a
    candidate's stdin-reading submission without adding a local
    package.json to every single compile. Computed from APPDATA
    (never a hardcoded username) so this works for whichever
    account actually runs the server.
    """

    appdata = os.environ.get("APPDATA", "")

    return os.path.join(appdata, "npm", "node_modules", "@types").replace("\\", "/")


class TypeScriptRunner:

    language = "typescript"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("tsc") is not None and shutil.which("node") is not None

    def _build(self, source_code: str):

        cache_dir = get_source_cache_dir("typescript", source_code)
        js_path = cache_dir / "submission.js"

        if js_path.exists():
            return js_path, None, None

        source_path = cache_dir / "submission.ts"
        source_path.write_text(source_code, encoding="utf-8")

        env = build_minimal_execution_env(path=os.environ.get("PATH", ""))

        # tsc is an npm-installed shim that resolves to "tsc.CMD" on
        # Windows (no native .exe) — same class of bug as kotlinc's
        # .bat wrapper (see kotlin_runner.py's comment): Python's
        # subprocess.run cannot launch a .cmd file directly via
        # CreateProcess, so it must go through cmd.exe explicitly.
        outcome = run_subprocess_capturing(
            [
                "cmd", "/c", "tsc", str(source_path), "--outDir", str(cache_dir),
                "--target", "es2020", "--module", "commonjs", "--skipLibCheck",
                "--typeRoots", _global_npm_type_roots(), "--types", "node",
                # REQUIRED — tsc's own default is to still emit
                # JavaScript even when it reports a type/syntax
                # error (only the CLI's exit code goes non-zero),
                # unless this flag is passed. Without it, a
                # genuinely broken submission with a "recoverable"
                # parse issue can silently produce runnable JS and
                # never be classified as a compile error — a real
                # inconsistent-result bug caught while testing this
                # runner (`let x: string[ = 5;` compiled to `let x
                # = 5;` and RAN despite tsc reporting error TS1005).
                "--noEmitOnError",
            ],
            timeout_seconds=_COMPILE_TIMEOUT_SECONDS, env=env, cwd=str(cache_dir),
        )

        if outcome.timed_out:
            return None, None, ExecutionResult(
                success=False, error_type="timeout", message="Compilation exceeded the build timeout.",
                language=self.language, runner_version=self.runner_version,
            )

        if outcome.returncode != 0 or not js_path.exists():
            return None, None, ExecutionResult(
                success=False, stdout=outcome.stdout, stderr=outcome.stderr,
                exit_code=outcome.returncode, error_type="compile_error",
                message="The submission failed to compile (TypeScript type/syntax error).",
                language=self.language, runner_version=self.runner_version,
            )

        return js_path, outcome.elapsed_ms, None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False, error_type="runner_unavailable",
                message="TypeScript compiler (tsc) or Node.js is not available on this machine.",
                language=self.language, runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(_MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000)))

        try:
            js_path, compile_time_ms, build_error = self._build(request.source_code)

            if build_error is not None:
                return build_error

            env = build_minimal_execution_env(
                path=os.environ.get("PATH", ""), extra={"NODE_NO_WARNINGS": "1"},
            )

            outcome = run_subprocess_capturing(
                ["node", "--disallow-code-generation-from-strings", str(js_path)],
                stdin=request.stdin, timeout_seconds=timeout_seconds, env=env,
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


register_runner(TypeScriptRunner())

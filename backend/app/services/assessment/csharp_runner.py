import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner


# ============================================================
# JOBPILOT AI — C# LANGUAGE RUNNER (Programming Engine V2 task)
#
# Genuinely tested against the .NET SDK actually detected on this
# development machine (`dotnet --version` -> 10.0.101, confirmed
# before this file was written).
#
# IMPORTANT DISCLOSED SIDE EFFECT FROM BUILDING THIS RUNNER: the
# FIRST-EVER `dotnet run` invoked anywhere on this machine (while
# probing feasibility for this task, before this runner file
# existed) triggered .NET's one-time "first run experience" and
# installed a local ASP.NET Core HTTPS development certificate
# (a standard, machine-local, untrusted-by-default localhost dev
# cert — not a secret, not network-facing, not silently trusted).
# This runner sets DOTNET_NOLOGO / DOTNET_CLI_TELEMETRY_OPTOUT /
# DOTNET_SKIP_FIRST_TIME_EXPERIENCE / DOTNET_GENERATE_ASPNET_
# CERTIFICATE=0 on every subprocess invocation specifically so
# this can never happen again — but the earlier, one-time
# side effect is disclosed here and in this task's final report
# rather than silently left unmentioned.
#
# COMPILE STEP: `dotnet build` against a minimal generated
# console-app project, cached by a hash of the submitted source so
# repeat test cases against the SAME submission do not re-pay the
# ~2s build cost. Compile failures are reported as error_type=
# "compile_error" (a genuine use of that ExecutionResult value,
# which the Python runner never needed since code_sandbox.py's
# `run_python_code` does not separate compile from runtime
# failures for an interpreted language).
#
# SECURITY BOUNDARY: identical caveat as javascript_runner.py —
# there is NO static-analysis allowlist step here at all (unlike
# code_sandbox.py's Python AST check). A compiled C# submission
# can call File I/O, networking, process APIs, etc. with nothing
# in this runner stopping it beyond OS-process boundaries, a
# timeout, and a minimal environment. This is NOT production-safe
# for untrusted candidate code — real deployment needs Docker/
# gVisor/Firecracker-style isolation before this runner (or the
# JavaScript one) ever sees a real candidate submission.
# ============================================================

RUNNER_VERSION = "csharp-dotnet-build-run-1.0"

_DEFAULT_TIMEOUT_SECONDS = 10
_MAX_TIMEOUT_SECONDS = 20
_BUILD_TIMEOUT_SECONDS = 60
_OUTPUT_LIMIT_BYTES = 64 * 1024

_DOTNET_ENV_BASE = {
    "DOTNET_NOLOGO": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    "DOTNET_GENERATE_ASPNET_CERTIFICATE": "0",
    # Required OS plumbing, not an application secret — see
    # javascript_runner.py's identical note. Without SystemRoot,
    # Windows-hosted .NET/Node processes cannot reach the OS
    # crypto/RNG subsystem during startup.
    "SystemRoot": os.environ.get("SystemRoot", ""),
    "TEMP": os.environ.get("TEMP", ""),
    "TMP": os.environ.get("TMP", ""),
    "USERPROFILE": os.environ.get("USERPROFILE", ""),
    "APPDATA": os.environ.get("APPDATA", ""),
    "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
    # Also required — the .NET SDK's own NuGet.targets resolves
    # its restore/cache paths via these and raised a bare
    # "Value cannot be null (Parameter 'path1')" from
    # Path.Combine() without them (observed while testing this
    # runner). Still OS profile plumbing, not an application
    # secret.
    "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
    "HOMEPATH": os.environ.get("HOMEPATH", ""),
    "ProgramFiles": os.environ.get("ProgramFiles", ""),
    "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
    "ProgramData": os.environ.get("ProgramData", ""),
}

# Real, narrow, TEXT-LEVEL denylist (Programming Engine V2
# security task, Step 13) — rejects a submission that references
# any of these namespaces/APIs BEFORE it is ever compiled. This is
# NOT equivalent to code_sandbox.py's Python AST allowlist (a
# determined submission could still reach file/network/process
# APIs via reflection, aliasing, or a fully-qualified name this
# regex doesn't happen to match) — it is a genuine but weak
# mitigation, honestly documented as such, not a claim of real
# sandboxing. A console algorithmic assessment never legitimately
# needs any of these.
_DENYLISTED_PATTERNS = re.compile(
    r"System\.IO|System\.Net|System\.Diagnostics\.Process|"
    r"System\.Runtime\.InteropServices|Marshal\.|"
    r"\bunsafe\b|System\.Reflection\.Assembly",
)


def _contains_denylisted_api(source_code: str) -> str | None:

    match = _DENYLISTED_PATTERNS.search(source_code)

    return match.group(0) if match else None


_CSPROJ_TEMPLATE = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <InvariantGlobalization>true</InvariantGlobalization>
  </PropertyGroup>
</Project>
"""


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _OUTPUT_LIMIT_BYTES:
        return text
    return encoded[:_OUTPUT_LIMIT_BYTES].decode("utf-8", errors="ignore") + "\n...[output truncated]"


class CSharpRunner:

    language = "csharp"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return shutil.which("dotnet") is not None

    def _build(self, source_code: str) -> tuple[Path | None, ExecutionResult | None]:

        source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()[:24]
        project_dir = Path(tempfile.gettempdir()) / "jobpilot_csharp_cache" / source_hash
        output_dir = project_dir / "bin"
        dll_path = output_dir / "submission.dll"

        if dll_path.exists():
            return dll_path, None

        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "submission.csproj").write_text(_CSPROJ_TEMPLATE, encoding="utf-8")
        (project_dir / "Program.cs").write_text(source_code, encoding="utf-8")

        env = {"PATH": os.environ.get("PATH", ""), **_DOTNET_ENV_BASE}

        try:
            proc = subprocess.run(
                [
                    "dotnet", "build", str(project_dir / "submission.csproj"),
                    "-c", "Release", "-o", str(output_dir), "--nologo",
                ],
                capture_output=True,
                text=True,
                timeout=_BUILD_TIMEOUT_SECONDS,
                env=env,
                cwd=str(project_dir),
            )
        except subprocess.TimeoutExpired:
            return None, ExecutionResult(
                success=False,
                error_type="timeout",
                message="Compilation exceeded the build timeout.",
                language=self.language,
                runner_version=self.runner_version,
            )

        if proc.returncode != 0 or not dll_path.exists():
            return None, ExecutionResult(
                success=False,
                stdout=_truncate(proc.stdout),
                stderr=_truncate(proc.stderr),
                exit_code=proc.returncode,
                error_type="compile_error",
                message="The submission failed to compile.",
                language=self.language,
                runner_version=self.runner_version,
            )

        return dll_path, None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        if not self.available():
            return ExecutionResult(
                success=False,
                error_type="runner_unavailable",
                message=".NET SDK is not available on this machine.",
                language=self.language,
                runner_version=self.runner_version,
            )

        timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        if request.timeout_ms is not None:
            timeout_seconds = min(
                _MAX_TIMEOUT_SECONDS, max(1, round(request.timeout_ms / 1000))
            )

        denylisted_match = _contains_denylisted_api(request.source_code)

        if denylisted_match:
            return ExecutionResult(
                success=False,
                error_type="security_violation",
                message=(
                    f"The submission references a restricted API "
                    f"('{denylisted_match}') that is not permitted "
                    f"in this assessment."
                ),
                language=self.language,
                runner_version=self.runner_version,
            )

        try:
            dll_path, build_error = self._build(request.source_code)

            if build_error is not None:
                return build_error

            env = {"PATH": os.environ.get("PATH", ""), **_DOTNET_ENV_BASE}

            start = time.monotonic()

            try:
                proc = subprocess.run(
                    ["dotnet", str(dll_path)],
                    input=request.stdin,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                return ExecutionResult(
                    success=False,
                    stdout=_truncate(exc.stdout if isinstance(exc.stdout, str) else ""),
                    stderr=_truncate(exc.stderr if isinstance(exc.stderr, str) else ""),
                    timed_out=True,
                    execution_time_ms=elapsed_ms,
                    error_type="timeout",
                    message=f"Execution exceeded {timeout_seconds}s.",
                    language=self.language,
                    runner_version=self.runner_version,
                )

            elapsed_ms = int((time.monotonic() - start) * 1000)

            if proc.returncode == 0:
                return ExecutionResult(
                    success=True,
                    stdout=_truncate(proc.stdout),
                    stderr=_truncate(proc.stderr),
                    exit_code=0,
                    execution_time_ms=elapsed_ms,
                    error_type="success",
                    language=self.language,
                    runner_version=self.runner_version,
                )

            return ExecutionResult(
                success=False,
                stdout=_truncate(proc.stdout),
                stderr=_truncate(proc.stderr),
                exit_code=proc.returncode,
                execution_time_ms=elapsed_ms,
                error_type="runtime_error",
                message="The submission exited with a non-zero status.",
                language=self.language,
                runner_version=self.runner_version,
            )

        except Exception:
            return ExecutionResult(
                success=False,
                error_type="sandbox_error",
                message="The execution sandbox encountered an internal error.",
                language=self.language,
                runner_version=self.runner_version,
            )


register_runner(CSharpRunner())

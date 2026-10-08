import shutil
import subprocess
from dataclasses import dataclass


# ============================================================
# JOBPILOT AI — LOCAL ENVIRONMENT DISCOVERY (Programming Engine
# V2 task, Step 6)
#
# READ-ONLY. Detects what is actually installed on THIS
# development machine. Never installs anything, never modifies
# PATH, never modifies system configuration. Used only to produce
# an honest report and to decide which LanguageRunner
# implementations are worth building (Step 7) — a tool this
# function reports as absent never gets a runner registered for
# it (see language_capability_registry.py's _REAL_RUNNERS_
# IMPLEMENTED, which is populated independently and by hand, not
# derived from this module, so a future re-scan on a different
# machine can never silently flip execution_supported on/off).
# ============================================================


@dataclass(frozen=True)
class ToolDetectionResult:

    tool: str
    found: bool
    path: str | None
    version: str | None


# command -> (display tool name, version probe args)
_TOOLS: dict[str, list[str]] = {
    "python": ["python", "--version"],
    "java": ["java", "-version"],
    "javac": ["javac", "-version"],
    "gcc": ["gcc", "--version"],
    "g++": ["g++", "--version"],
    "dotnet": ["dotnet", "--version"],
    "node": ["node", "--version"],
    "npm": ["npm", "--version"],
    "tsc": ["tsc", "--version"],
    "go": ["go", "version"],
    "rustc": ["rustc", "--version"],
    "cargo": ["cargo", "--version"],
    "php": ["php", "--version"],
    "ruby": ["ruby", "--version"],
    "swift": ["swift", "--version"],
    "kotlinc": ["kotlinc", "-version"],
    "kotlin": ["kotlin", "-version"],
}


def _probe_version(args: list[str]) -> str | None:

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except Exception:
        return None

    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]

    return lines[0][:120] if lines else None


def discover_tool(command: str) -> ToolDetectionResult:

    path = shutil.which(command)

    if path is None:
        return ToolDetectionResult(tool=command, found=False, path=None, version=None)

    args = _TOOLS.get(command, [command, "--version"])
    version = _probe_version(args)

    return ToolDetectionResult(tool=command, found=True, path=path, version=version)


def discover_environment() -> list[ToolDetectionResult]:
    """
    Detection ONLY — never installs, never modifies PATH or any
    system/user configuration. Safe to call any number of times.
    """

    return [discover_tool(command) for command in _TOOLS]

# ============================================================
# JOBPILOT AI - TECHNOLOGY EXECUTION CAPABILITY REGISTRY
# (Task 5C Step 2)
#
# ONE centralized source of truth for whether JobPilot's
# assessment engine can actually EXECUTE candidate code in a
# given technology today — as distinct from whether that
# technology is merely relevant to a job. "required by the job"
# and "currently executable" are deliberately kept as two
# separate concepts everywhere in this codebase (see
# AssessmentPlanItem.execution_supported in
# backend/app/schemas/assessment_plan.py) — this registry only
# ever answers the second question, never the first.
#
# Listing a technology here does NOT imply a runner has been
# built for it. Only "python" is backed by a real, wired
# sandbox today (see backend/app/services/assessment/
# code_sandbox.py, and programming_tests.py's hard rejection of
# any non-Python language). Every other entry is False until a
# real, secure runner exists for it — this project never fakes
# execution support (see code_sandbox.py's own docstring on why
# claiming support that doesn't exist is unacceptable here).
#
# Extending this registry is the ONLY place a future language
# runner needs to flip an entry to True once it is genuinely
# implemented and tested — nothing else in the assessment-
# planning code should hardcode a language's executability.
# ============================================================

# Normalized (lowercase, trimmed) technology name -> whether
# JobPilot can currently execute candidate code in it.
TECHNOLOGY_EXECUTION_SUPPORT: dict[str, bool] = {

    # Programming languages
    "python": True,

    # Flipped True by the Programming Engine V2 / Complete Multi-
    # Language Execution Engine tasks, ONLY after each language's
    # runner (java_runner.py / c_runner.py / cpp_runner.py /
    # go_runner.py / rust_runner.py / php_runner.py / ruby_runner.py
    # / kotlin_runner.py / typescript_runner.py / javascript_runner.py
    # / csharp_runner.py) was actually built AND individually
    # tested against a real, installed toolchain on this
    # development machine — see that task's final report for the
    # exact versions and test results. This is the module's own
    # documented threshold for flipping an entry, not a special
    # exception to it.
    "java": True,
    "c": True,
    "c++": True,
    "cpp": True,
    "c#": True,
    "csharp": True,
    "javascript": True,
    "typescript": True,
    "go": True,
    "golang": True,
    "rust": True,
    "php": True,
    "kotlin": True,
    "swift": False,
    "ruby": True,

    # Database / query languages
    "sql": True,
    "postgresql": False,
    "postgres": False,
    "mysql": False,

    # Frontend technologies
    "react": False,
    "angular": False,
    "vue": False,
    "html": True,
    "css": True,

    # Backend frameworks
    "fastapi": False,
    "django": False,
    "flask": False,
    "spring boot": False,
    "spring": False,
    "node.js": False,
    "nodejs": False,
    ".net": False,
    "dotnet": False,
}


def normalize_technology_name(technology: str | None) -> str | None:

    if not technology:
        return None

    normalized = technology.strip().lower()

    return normalized or None


def is_execution_supported(technology: str | None) -> bool:
    """
    Returns True only for a technology this project can
    genuinely execute candidate code in today. An unknown/
    unlisted technology name is treated as UNSUPPORTED — this
    function fails closed and must never be changed to fail
    open, since a false positive here would mean telling HR a
    candidate's code actually ran when it never did.
    """

    normalized = normalize_technology_name(technology)

    if normalized is None:
        return False

    return TECHNOLOGY_EXECUTION_SUPPORT.get(normalized, False)

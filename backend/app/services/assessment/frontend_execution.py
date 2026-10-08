from dataclasses import dataclass


# ============================================================
# JOBPILOT AI — FRONTEND CODING EXECUTION MODE (Programming
# Engine V2 task, Step 9)
#
# `frontend_coding` (see programming_question_v2.ExecutionMode)
# is a RECOGNIZED value, not a WORKING one. This module documents
# the architecture a real implementation would need and
# deliberately implements NONE of it — there is no isolated
# browser environment, no headless-browser harness, no DOM
# assertion runner anywhere in this codebase, and this module
# creates none. Calling anything in this module (there is nothing
# to call besides the capability check below) must never be
# mistaken for "frontend coding is supported".
#
# What a real `frontend_coding` execution engine would need
# (documented, not built):
#   - An isolated browser context per submission (e.g. a
#     headless Chromium/Playwright instance, or an iframe
#     sandboxed with a strict CSP), never the FastAPI process
#     itself rendering candidate HTML/CSS/JS.
#   - A DOM-assertion test format distinct from
#     ExecutableTestCase's stdin/expected_stdout shape — a
#     frontend test case asserts on rendered DOM state, computed
#     styles, or emitted events, not on a single stdout string.
#   - Framework-specific bundling/transpilation (JSX/TSX for
#     React, .vue single-file components for Vue, Angular's
#     compiler) BEFORE anything can even render — none of this
#     exists in JobPilot today.
#   - The same untrusted-code security concerns as any other
#     runner, PLUS browser-specific ones (candidate script
#     running in a real DOM has access to a much larger API
#     surface than a console script).
#
# `execution_supported_for_frontend_coding()` below is the ONLY
# function this module exposes, and it always returns False. Any
# future caller MUST check this rather than assuming
# "frontend_coding" being a valid ExecutionMode literal means it
# works — exactly the same pattern IMPLEMENTED_EXECUTION_MODES
# already encodes in programming_question_v2.py.
# ============================================================


@dataclass(frozen=True)
class FrontendExecutionCapability:

    execution_supported: bool = False
    reason: str = (
        "No frontend execution engine exists in this codebase yet "
        "(no headless-browser harness, no DOM-assertion test "
        "format, no framework bundler integration). See this "
        "module's docstring for what a real implementation would "
        "require."
    )


def get_frontend_execution_capability() -> FrontendExecutionCapability:

    return FrontendExecutionCapability()

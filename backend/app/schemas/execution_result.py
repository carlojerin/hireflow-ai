from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI - EXECUTION REQUEST / RESULT SCHEMA
# (Task 5C Step 4)
#
# Language-neutral contract between the assessment engine and
# any LanguageRunner (see language_runner.py). This schema is
# deliberately STRICT about what a caller may specify — no shell
# commands, no executable paths, no environment variables, no
# working directories, no network settings. Every runner decides
# those internally (see python_runner.py for the concrete
# example: a runner never lets the request dictate anything
# beyond source/stdin/timeout).
#
# IMPORTANT SEPARATION OF RESPONSIBILITY (Step 4's own rule):
# ExecutionResult only ever reports WHAT HAPPENED when code ran.
# It never decides whether a candidate "passed" anything — that
# is the behavioral test evaluator's and personalized_scoring's
# job, layered strictly on top of this, never merged into it.
# ============================================================


# Fixed error-classification vocabulary (Step 4's own list) —
# lets a caller distinguish "the candidate's code failed" from
# "the platform failed to execute it at all".
#
# "security_violation" added (Task 5C, Complete Multi-Language
# Execution Engine) — additive, non-breaking (every existing
# caller/switch on this Literal still compiles; nothing removed
# or renamed). Distinguishes a submission REJECTED before
# execution for using a denylisted API (e.g. csharp_runner.py's
# namespace denylist) from "sandbox_error" (a genuine platform/
# infrastructure failure) — these are different situations for
# HR to see in a report, and were previously conflated.
ExecutionErrorType = Literal[
    "success",
    "compile_error",
    "runtime_error",
    "timeout",
    "output_limit_exceeded",
    "invalid_language",
    "runner_unavailable",
    "sandbox_error",
    "security_violation",
]


class ExecutionRequest(BaseModel):

    # Raw candidate-supplied language string — normalized by
    # language_capability_registry.py before any runner ever
    # sees it; a runner itself never has to normalize.
    language: str = Field(min_length=1, max_length=50)

    source_code: str = Field(min_length=0)

    stdin: str = Field(default="")

    # A CEILING the caller may request — every runner clamps
    # this to its own hard maximum internally (see
    # python_runner.py) and never simply trusts a caller-supplied
    # value as-is.
    timeout_ms: Optional[int] = Field(default=None, ge=1)

    # Free-form identifier for logging/debugging correlation only
    # (e.g. "question_3/hidden_test_2") — never interpreted as a
    # path, command, or credential of any kind.
    test_case_id: Optional[str] = Field(default=None, max_length=100)


class ExecutionResult(BaseModel):

    success: bool

    stdout: str = ""
    stderr: str = ""

    exit_code: Optional[int] = None

    timed_out: bool = False

    execution_time_ms: int = 0

    # Additive (Task 5C, Complete Multi-Language Execution
    # Engine). None for interpreted languages (Python/JavaScript/
    # PHP/Ruby) and for a cached/already-built compiled-language
    # execution — only set when this specific call actually
    # performed a compile step.
    compilation_time_ms: Optional[int] = None

    error_type: ExecutionErrorType

    # A short, candidate-safe message for the "sandbox_error" /
    # "invalid_language" / "runner_unavailable" cases — e.g. "your
    # code uses a restricted operation". Never a raw stack trace
    # from JobPilot's own infrastructure, and never the candidate's
    # own source code echoed back (see module header — never log
    # raw source unnecessarily).
    message: Optional[str] = None

    language: str

    runner_version: Optional[str] = None

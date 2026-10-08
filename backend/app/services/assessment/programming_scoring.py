from backend.app.services.assessment.code_sandbox import (
    run_python_code,
)


# ============================================================
# JOBPILOT AI - PROGRAMMING ASSESSMENT SCORING (Task 4C)
#
# Pure-orchestration, backend-only scoring logic — mirrors
# services/aptitude/scoring.py's role exactly (the frontend
# never supplies a score, only source code; this module grades
# it). The one difference from aptitude scoring is that grading
# here requires actually *running* the candidate's code, so this
# module calls into code_sandbox.py rather than doing a pure
# in-memory comparison.
# ============================================================


def _normalize_output(text: str) -> str:
    """
    Trims trailing/leading whitespace on the whole output and
    on each line, so a trailing newline or trailing spaces
    (which a correct solution may or may not emit) never cause
    a false mismatch. This is the same forgiving-but-exact
    comparison strategy real competitive-programming judges use.
    """

    lines = text.strip("\n").split("\n")

    return "\n".join(line.rstrip() for line in lines).strip()


def run_against_examples(
    source_code: str,
    examples: list[dict],
    time_limit_seconds: int,
) -> dict:
    """
    Runs source_code against a question's VISIBLE examples only
    — for "Run Code" feedback. Never touches hidden tests, never
    contributes to score.

    Returns:
        {
            "execution_status": "passed" | "failed" | "error" | "timeout" | "rejected",
            "execution_time_ms": int,
            "results": [
                {"passed": bool, "stdout": str, "stderr": str, "expected": str}
                ...
            ],
            "rejected_reason": str | None,
        }
    """

    results = []
    max_time_ms = 0
    overall_status = "passed"

    for example in examples:

        outcome = run_python_code(
            source_code,
            example.get("input", ""),
            time_limit_seconds=time_limit_seconds,
        )

        max_time_ms = max(max_time_ms, outcome.execution_time_ms)

        if outcome.status == "rejected":

            return {
                "execution_status": "rejected",
                "execution_time_ms": 0,
                "results": [],
                "rejected_reason": outcome.rejected_reason,
            }

        if outcome.status != "completed":

            overall_status = outcome.status
            results.append({
                "passed": False,
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "expected": example.get("output", ""),
            })
            continue

        example_passed = (
            _normalize_output(outcome.stdout)
            == _normalize_output(example.get("output", ""))
        )

        if not example_passed and overall_status == "passed":
            overall_status = "failed"

        results.append({
            "passed": example_passed,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "expected": example.get("output", ""),
        })

    return {
        "execution_status": overall_status,
        "execution_time_ms": max_time_ms,
        "results": results,
        "rejected_reason": None,
    }


def score_against_hidden_tests(
    source_code: str,
    hidden_tests: list[dict],
    time_limit_seconds: int,
) -> dict:
    """
    Runs source_code against ALL hidden tests for one question —
    used only at final assessment submission. Returns a
    percentage score (0-100) for this one question, never the
    hidden test inputs/outputs themselves.

    Returns:
        {
            "execution_status": "passed" | "failed" | "error" | "timeout" | "rejected",
            "score_percentage": int,       # 0-100
            "passed_count": int,
            "total_count": int,
            "execution_time_ms": int,
            "rejected_reason": str | None,
        }
    """

    total = len(hidden_tests)
    passed_count = 0
    max_time_ms = 0
    saw_non_pass_status = None

    for test_case in hidden_tests:

        outcome = run_python_code(
            source_code,
            test_case.get("input", ""),
            time_limit_seconds=time_limit_seconds,
        )

        max_time_ms = max(max_time_ms, outcome.execution_time_ms)

        if outcome.status == "rejected":

            return {
                "execution_status": "rejected",
                "score_percentage": 0,
                "passed_count": 0,
                "total_count": total,
                "execution_time_ms": 0,
                "rejected_reason": outcome.rejected_reason,
            }

        if outcome.status != "completed":

            saw_non_pass_status = saw_non_pass_status or outcome.status
            continue

        expected = test_case.get("expected_output", "")

        if _normalize_output(outcome.stdout) == _normalize_output(expected):
            passed_count += 1

    score_percentage = (
        round((passed_count / total) * 100)
        if total > 0
        else 0
    )

    if passed_count == total:
        execution_status = "passed"
    elif saw_non_pass_status:
        execution_status = saw_non_pass_status
    else:
        execution_status = "failed"

    return {
        "execution_status": execution_status,
        "score_percentage": score_percentage,
        "passed_count": passed_count,
        "total_count": total,
        "execution_time_ms": max_time_ms,
        "rejected_reason": None,
    }

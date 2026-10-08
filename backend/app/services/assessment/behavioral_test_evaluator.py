from backend.app.schemas.execution_result import ExecutionResult


# ============================================================
# JOBPILOT AI - BEHAVIORAL TEST EVALUATOR (Task 5C Step 4)
#
# Sits strictly BETWEEN the language runner and scoring (Step
# 4's own required architecture: Runner -> ExecutionResult ->
# Behavioral Test Evaluator -> Personalized Scoring). This
# module only ever decides "did the program behave as
# specified?" for ONE test case — it never decides a candidate's
# overall pass/fail or awards any marks itself; that remains
# personalized_scoring.py's job (Task 5C Step 3), layered on top
# of this module's boolean verdict.
#
# Deliberately language-neutral: an expected behavior is
# specified purely as "given this stdin, stdout should look like
# this" — the exact same specification format works whether the
# ExecutionResult came from the Python runner today or a Java/
# C++/Go runner added later, so a behavioral test bank never
# needs to be rewritten when a new language runner is added.
# ============================================================


def _normalize_output(text: str) -> str:
    """
    Same forgiving-but-exact comparison strategy already used by
    programming_scoring.py (Task 4C) — trims trailing/leading
    whitespace on the whole output and on each line, so a
    trailing newline a correct solution may or may not emit
    never causes a false mismatch. Intentionally reimplemented
    here (not imported from programming_scoring.py) so this
    module has zero dependency on the live, unmodified
    programming pipeline — Step 4 stays fully isolated.
    """

    lines = text.strip("\n").split("\n")

    return "\n".join(line.rstrip() for line in lines).strip()


def evaluate_behavioral_test(
    execution_result: ExecutionResult,
    expected_stdout: str,
) -> dict:
    """
    Returns a small, explicit verdict dict — never raises, never
    itself decides marks:

        {
            "passed": bool,
            "reason": "matched" | "output_mismatch" | "execution_failed",
        }

    A test case can only ever be "passed" if the execution itself
    genuinely succeeded (execution_result.success is True) AND
    the normalized stdout matches — an execution failure (timeout,
    runtime error, sandbox rejection, unavailable runner) is
    always "execution_failed", distinct from "the program ran
    fine but produced the wrong output" ("output_mismatch"). This
    distinction is exactly what lets a future assessment engine
    tell "candidate solution failed" apart from "platform
    execution failed" (Step 4's own explicit goal).
    """

    if not execution_result.success:

        return {"passed": False, "reason": "execution_failed"}

    matched = (
        _normalize_output(execution_result.stdout)
        == _normalize_output(expected_stdout)
    )

    return {
        "passed": matched,
        "reason": "matched" if matched else "output_mismatch",
    }


def evaluate_behavioral_test_suite(
    execution_results_with_expected: list[tuple[ExecutionResult, str]],
) -> dict:
    """
    Convenience batch wrapper over evaluate_behavioral_test() for
    a list of (execution_result, expected_stdout) pairs — e.g.
    one sub-part's several test cases. Returns:

        {
            "results": [ {"passed": bool, "reason": str}, ... ],
            "passed_count": int,
            "total_count": int,
        }

    Still never awards marks — that weighting/aggregation is
    personalized_scoring.py's responsibility, given this
    function's own `results` list as its input.
    """

    verdicts = [
        evaluate_behavioral_test(execution_result, expected_stdout)
        for execution_result, expected_stdout in execution_results_with_expected
    ]

    return {
        "results": verdicts,
        "passed_count": sum(1 for verdict in verdicts if verdict["passed"]),
        "total_count": len(verdicts),
    }

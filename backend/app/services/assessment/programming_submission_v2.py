from dataclasses import dataclass

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.schemas.programming_question_v2 import (
    ProgrammingMainQuestionV2,
    ProgrammingQuestionSetV2,
)
from backend.app.services.assessment.language_runner import RUNNER_REGISTRY
from backend.app.services.assessment.language_selection import (
    validate_language_selection,
)
from backend.app.services.assessment.programming_scoring_v2 import (
    AssessmentScoreResult,
    MainQuestionScoreResult,
    score_assessment,
    score_main_question,
    score_subpart,
    score_test_case,
)


# ============================================================
# JOBPILOT AI — PROGRAMMING V2 SUBMISSION ORCHESTRATION
# (Programming Engine V2, AI generation task, Step 9)
#
# STANDALONE. The exact flow the task specifies:
#   CandidateSubmission -> validate_language_selection() ->
#   RUNNER_REGISTRY lookup -> execute() per test case ->
#   ExecutionResult -> programming_scoring_v2 -> final score.
#
# Reuses the EXISTING RUNNER_REGISTRY (language_runner.py) —
# no second runner abstraction is created here. Never trusts a
# candidate-declared language: validate_language_selection() is
# the one and only gate, called before any runner is ever looked
# up, for both run_public_examples() and submit_question() below.
#
# RUN vs SUBMIT, exactly as Step 14 requires: run_public_examples()
# only ever executes test cases marked is_hidden=False (the
# question's own worked examples) and returns their raw
# ExecutionResult (stdout/stderr) so a candidate can see what
# their code actually printed while iterating. submit_question()
# executes EVERY test case (hidden and visible) but returns only
# the candidate-safe TestCaseScoreResult/SubpartScoreResult/
# MainQuestionScoreResult dataclasses (programming_scoring_v2.py),
# which by construction never carry a hidden test's stdin,
# expected_stdout, or the candidate's own stdout for that hidden
# case — only whether it passed, its weight, and execution
# metadata. Nothing here is written to any database — persistence
# is a separate, later wiring concern.
# ============================================================


@dataclass(frozen=True)
class CandidateSubmission:

    question_id: int
    language: str
    source_code: str


@dataclass(frozen=True)
class RunExampleResult:

    test_case_id: str
    stdin: str
    expected_stdout: str
    execution: ExecutionResult


@dataclass(frozen=True)
class LanguageRejectedResult:

    accepted: bool
    reason: str


def _find_question(
    question_set: ProgrammingQuestionSetV2, question_id: int,
) -> ProgrammingMainQuestionV2:

    for question in question_set.main_questions:
        if question.question_id == question_id:
            return question

    raise ValueError(f"No question with question_id={question_id} in this assessment.")


def _execute_test_case(language: str, source_code: str, stdin: str) -> ExecutionResult:

    runner = RUNNER_REGISTRY[language]

    return runner.execute(
        ExecutionRequest(language=language, source_code=source_code, stdin=stdin)
    )


def run_public_examples(
    question_set: ProgrammingQuestionSetV2,
    submission: CandidateSubmission,
) -> list[RunExampleResult] | LanguageRejectedResult:
    """
    "Run Code" — executes only this question's PUBLIC (is_hidden=
    False) test cases, and returns their real stdout/stderr so the
    candidate can see what their code actually did. Never touches
    a hidden test case. Never scores anything — running is purely
    diagnostic for the candidate.
    """

    question = _find_question(question_set, submission.question_id)

    selection = validate_language_selection(submission.language, question)

    if not selection.accepted:
        return LanguageRejectedResult(accepted=False, reason=selection.reason or "")

    results: list[RunExampleResult] = []

    for subpart in question.subparts:
        for test_case in subpart.test_cases:
            if test_case.is_hidden:
                continue

            execution = _execute_test_case(
                selection.canonical_language, submission.source_code, test_case.stdin,
            )

            results.append(
                RunExampleResult(
                    test_case_id=test_case.test_case_id,
                    stdin=test_case.stdin,
                    expected_stdout=test_case.expected_stdout,
                    execution=execution,
                )
            )

    return results


def submit_question(
    question_set: ProgrammingQuestionSetV2,
    submission: CandidateSubmission,
) -> MainQuestionScoreResult | LanguageRejectedResult:
    """
    "Submit" — executes EVERY test case (visible and hidden) for
    this one question and returns the candidate-safe scored
    result. The candidate never receives a hidden test's stdin/
    expected_stdout/actual stdout from this function — only
    pass/fail, weight, and execution metadata (see
    programming_scoring_v2.TestCaseScoreResult).
    """

    question = _find_question(question_set, submission.question_id)

    selection = validate_language_selection(submission.language, question)

    if not selection.accepted:
        return LanguageRejectedResult(accepted=False, reason=selection.reason or "")

    subpart_results = []

    for subpart in question.subparts:

        test_case_results = []

        for test_case in subpart.test_cases:

            execution = _execute_test_case(
                selection.canonical_language, submission.source_code, test_case.stdin,
            )

            test_case_results.append(score_test_case(test_case, execution))

        subpart_results.append(score_subpart(subpart, test_case_results))

    return score_main_question(question, subpart_results, selection.canonical_language)


def submit_assessment(
    question_set: ProgrammingQuestionSetV2,
    submissions: list[CandidateSubmission],
) -> AssessmentScoreResult:
    """
    Submits every question a candidate answered and aggregates the
    final assessment score. A question the candidate never
    submitted simply contributes 0 earned points — it is the
    caller's job to decide whether an incomplete assessment is
    even allowed to reach this function.
    """

    question_results: list[MainQuestionScoreResult] = []

    for submission in submissions:

        result = submit_question(question_set, submission)

        if isinstance(result, LanguageRejectedResult):
            raise ValueError(
                f"Submission for question {submission.question_id} used a "
                f"rejected language: {result.reason}"
            )

        question_results.append(result)

    return score_assessment(question_set, question_results)

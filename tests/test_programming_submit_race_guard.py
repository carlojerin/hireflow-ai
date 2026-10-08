"""
Programming assessment (V2) submit race guard: two near-simultaneous
submits (double-click, or auto-submit-on-timeout racing a manual
click) must never both succeed -- mirrors the identical fix already
applied to the aptitude stage.

Reuses test_programming_v2_live_integration.py's own DB/fixture
helpers, but monkeypatches the language runner so this test never
needs a reachable Piston instance -- it's testing the atomic status
transition, not real code execution.
"""

from backend.app.models.application_status_history import ApplicationStatusHistory
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.schemas.execution_result import ExecutionResult
from backend.app.services.assessment.language_runner import RUNNER_REGISTRY
from backend.app.services.assessment.programming_integration_v2 import (
    ProgrammingIntegrationV2Error,
    start_programming_assessment_v2,
    submit_assessment_v2,
)

from test_programming_v2_live_integration import _make_db, _questions_of, _seed


class _FixedOutputRunner:
    """Always returns the given stdout, regardless of source_code -- lets
    this test control pass/fail scoring deterministically with zero
    real execution (no Piston, no network)."""

    language = "python"
    runner_version = "test-stub"

    def __init__(self, stdout: str):
        self.stdout = stdout

    def available(self) -> bool:
        return True

    def execute(self, request) -> ExecutionResult:
        return ExecutionResult(
            success=True, stdout=self.stdout, exit_code=0,
            error_type="success", language="python", runner_version=self.runner_version,
        )


def _answers_for_every_question(assessment, stdout: str):
    return [
        {"question_id": q["question_id"], "language": "python", "source_code": "print('anything')"}
        for q in _questions_of(assessment)
    ]


def test_two_near_simultaneous_submits_only_one_succeeds(monkeypatch):

    db = _make_db()
    user, job, application, session_record = _seed(db)

    assessment, created = start_programming_assessment_v2(
        db, session_record, application, job, user, None, None, use_gemini=False,
    )
    assert created is True

    # Every hidden test case's expected_stdout is real data generated per
    # candidate -- stub the runner to return something that will NOT match
    # any of them, so this test stays isolated from advance_pipeline()
    # (only called on a pass), matching the same isolation choice made for
    # aptitude's equivalent race test.
    monkeypatch.setitem(RUNNER_REGISTRY, "python", _FixedOutputRunner("definitely-wrong-output"))

    answers = _answers_for_every_question(assessment, "definitely-wrong-output")

    first = submit_assessment_v2(db, assessment, application, answers)
    assert first["status"] == "completed"

    try:
        submit_assessment_v2(db, assessment, application, answers)
        raised = False
    except ProgrammingIntegrationV2Error as exc:
        raised = True
        assert exc.status_code == 409

    assert raised, "second near-simultaneous submit must be rejected, not silently re-scored"

    db.refresh(assessment)
    assert assessment.status == "completed"

    # 2, not 1: start_programming_assessment_v2() itself already
    # records one "programming_pending" transition at creation time
    # -- the atomic guard's job is only to stop a THIRD row (a
    # duplicate "programming_failed"/"programming_passed" from the
    # second, rejected submit) from ever being added.
    history_count = (
        db.query(ApplicationStatusHistory)
        .filter(ApplicationStatusHistory.application_id == application.id)
        .count()
    )
    assert history_count == 2

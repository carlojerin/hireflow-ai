"""
piston_runner.py's response-classification logic (compile error /
timeout / success / runtime error / unavailable), tested against a
stubbed piston_client -- no network, no real Piston instance
required. Complements tests/test_language_runners_v2.py and
tests/test_multi_language_execution.py, which exercise the same
runners for real but are skipped unless a live Piston is reachable.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.app.schemas.execution_result import ExecutionRequest
from backend.app.services.assessment import piston_client
from backend.app.services.assessment.piston_runner import PistonRunner, _java_filename


def _runner(monkeypatch, response=None, raise_error=None):

    def fake_execute(**kwargs):
        if raise_error is not None:
            raise raise_error
        fake_execute.last_call = kwargs
        return response

    monkeypatch.setattr(piston_client, "execute", fake_execute)
    monkeypatch.setattr(piston_client, "is_available", lambda force=False: True)

    runner = PistonRunner("python", "python", "3.10.0", "main.py")
    return runner, fake_execute


def test_successful_run(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "run": {"stdout": "hello\n", "stderr": "", "code": 0, "signal": None},
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="print('hello')"))
    assert result.success is True
    assert result.error_type == "success"
    assert result.stdout == "hello\n"


def test_nonzero_exit_is_runtime_error(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "run": {"stdout": "", "stderr": "Traceback...", "code": 1, "signal": None},
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="raise ValueError()"))
    assert result.success is False
    assert result.error_type == "runtime_error"
    assert result.exit_code == 1


def test_killed_run_with_no_exit_code_is_timeout(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "run": {"stdout": "", "stderr": "", "code": None, "signal": "SIGKILL"},
    })
    result = runner.execute(
        ExecutionRequest(language="python", source_code="while True: pass", timeout_ms=1000)
    )
    assert result.success is False
    assert result.error_type == "timeout"
    assert result.timed_out is True


def test_compile_failure_is_compile_error(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "compile": {"stdout": "", "stderr": "syntax error", "code": 1, "signal": None},
        "run": {"stdout": "", "stderr": "", "code": None, "signal": None},
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="def bad(:"))
    assert result.success is False
    assert result.error_type == "compile_error"
    assert result.stderr == "syntax error"


def test_killed_compile_is_reported_as_timeout(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "compile": {"stdout": "", "stderr": "", "code": None, "signal": "SIGKILL"},
        "run": {"stdout": "", "stderr": "", "code": None, "signal": None},
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="whatever"))
    assert result.success is False
    assert result.error_type == "timeout"
    assert result.timed_out is True


def test_zero_exit_compile_step_is_not_treated_as_a_failure(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "compile": {"stdout": "", "stderr": "", "code": 0, "signal": None},
        "run": {"stdout": "ok\n", "stderr": "", "code": 0, "signal": None},
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="print('ok')"))
    assert result.success is True
    assert result.error_type == "success"


def test_unreachable_piston_reports_runner_unavailable(monkeypatch):
    runner, _ = _runner(
        monkeypatch, raise_error=piston_client.PistonUnavailableError("connection refused")
    )
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))
    assert result.success is False
    assert result.error_type == "runner_unavailable"


def test_rejected_request_reports_sandbox_error(monkeypatch):
    runner, _ = _runner(
        monkeypatch, raise_error=piston_client.PistonExecutionError("unknown language version")
    )
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))
    assert result.success is False
    assert result.error_type == "sandbox_error"


def test_timeout_ms_is_clamped_between_1_second_and_the_ceiling(monkeypatch):
    runner, fake_execute = _runner(monkeypatch, response={
        "run": {"stdout": "", "stderr": "", "code": 0, "signal": None},
    })

    runner.execute(ExecutionRequest(language="python", source_code="pass", timeout_ms=50))
    assert fake_execute.last_call["run_timeout_ms"] == 1000  # clamped up

    runner.execute(ExecutionRequest(language="python", source_code="pass", timeout_ms=999_999))
    assert fake_execute.last_call["run_timeout_ms"] == 15_000  # clamped down


# ------------------------------------------------------------
# available() delegates to piston_client.is_available()
# ------------------------------------------------------------

def test_available_reflects_piston_reachability(monkeypatch):
    monkeypatch.setattr(piston_client, "is_available", lambda force=False: False)
    runner = PistonRunner("python", "python", "3.10.0", "main.py")
    assert runner.available() is False

    monkeypatch.setattr(piston_client, "is_available", lambda force=False: True)
    assert runner.available() is True


# ------------------------------------------------------------
# Java's public-class filename detection (Piston's Java executor
# uses javac internally, which has the exact same constraint the
# old java_runner.py worked around).
# ------------------------------------------------------------

def test_java_filename_matches_the_public_class_name():
    source = "public class Solution { public static void main(String[] a) {} }"
    assert _java_filename(source) == "Solution.java"


def test_java_filename_defaults_to_main_when_no_public_class_found():
    assert _java_filename("class NotPublic {}") == "Main.java"

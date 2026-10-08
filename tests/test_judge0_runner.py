"""
judge0_runner.py's response-classification logic (compile error /
timeout / success / runtime error / unavailable), tested against a
stubbed judge0_client -- no network, no real Judge0 instance
required. Mirrors test_piston_runner.py's structure and coverage.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.schemas.execution_result import ExecutionRequest
from backend.app.services.assessment import judge0_client
from backend.app.services.assessment.judge0_runner import Judge0Runner, _normalize_java_source


def _runner(monkeypatch, response=None, raise_error=None):

    def fake_execute(**kwargs):
        if raise_error is not None:
            raise raise_error
        fake_execute.last_call = kwargs
        return response

    monkeypatch.setattr(judge0_client, "execute", fake_execute)
    monkeypatch.setattr(judge0_client, "is_available", lambda force=False: True)

    runner = Judge0Runner("python", 71, "3.8.1", None)
    return runner, fake_execute


def test_successful_run(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_ACCEPTED, "status_description": "Accepted",
        "stdout": "hello\n", "stderr": "", "compile_output": "", "message": "",
        "exit_code": 0, "time_seconds": 0.02,
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="print('hello')"))
    assert result.success is True
    assert result.error_type == "success"
    assert result.stdout == "hello\n"
    assert result.execution_time_ms == 20


def test_nonzero_exit_is_runtime_error(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_RUNTIME_ERROR_RANGE.start,  # 7, SIGSEGV
        "status_description": "Runtime Error (SIGSEGV)",
        "stdout": "", "stderr": "Traceback...", "compile_output": "", "message": "",
        "exit_code": 139, "time_seconds": 0.01,
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="raise ValueError()"))
    assert result.success is False
    assert result.error_type == "runtime_error"
    assert result.exit_code == 139


def test_time_limit_exceeded_is_timeout(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_TIME_LIMIT_EXCEEDED, "status_description": "Time Limit Exceeded",
        "stdout": "", "stderr": "", "compile_output": "", "message": "",
        "exit_code": None, "time_seconds": None,
    })
    result = runner.execute(
        ExecutionRequest(language="python", source_code="while True: pass", timeout_ms=1000)
    )
    assert result.success is False
    assert result.error_type == "timeout"
    assert result.timed_out is True


def test_compile_error(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_COMPILATION_ERROR, "status_description": "Compilation Error",
        "stdout": "", "stderr": "", "compile_output": "syntax error", "message": "",
        "exit_code": None, "time_seconds": None,
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="def bad(:"))
    assert result.success is False
    assert result.error_type == "compile_error"
    assert result.stderr == "syntax error"


def test_internal_error_is_sandbox_error(monkeypatch):
    runner, _ = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_INTERNAL_ERROR, "status_description": "Internal Error",
        "stdout": "", "stderr": "", "compile_output": "", "message": "box crashed",
        "exit_code": None, "time_seconds": None,
    })
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))
    assert result.success is False
    assert result.error_type == "sandbox_error"


def test_unreachable_judge0_reports_runner_unavailable(monkeypatch):
    runner, _ = _runner(
        monkeypatch, raise_error=judge0_client.Judge0UnavailableError("connection refused")
    )
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))
    assert result.success is False
    assert result.error_type == "runner_unavailable"


def test_rejected_request_reports_sandbox_error(monkeypatch):
    runner, _ = _runner(
        monkeypatch, raise_error=judge0_client.Judge0ExecutionError("invalid language_id")
    )
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))
    assert result.success is False
    assert result.error_type == "sandbox_error"


def test_timeout_ms_is_clamped_and_converted_to_cpu_time_limit_seconds(monkeypatch):
    runner, fake_execute = _runner(monkeypatch, response={
        "status_id": judge0_client.STATUS_ACCEPTED, "status_description": "Accepted",
        "stdout": "", "stderr": "", "compile_output": "", "message": "",
        "exit_code": 0, "time_seconds": 0.0,
    })

    runner.execute(ExecutionRequest(language="python", source_code="pass", timeout_ms=50))
    assert fake_execute.last_call["cpu_time_limit"] == 1.0  # clamped up to 1000ms -> 1.0s

    runner.execute(ExecutionRequest(language="python", source_code="pass", timeout_ms=999_999))
    assert fake_execute.last_call["cpu_time_limit"] == 15.0  # clamped down to 15000ms -> 15.0s


def test_available_reflects_judge0_reachability(monkeypatch):
    monkeypatch.setattr(judge0_client, "is_available", lambda force=False: False)
    runner = Judge0Runner("python", 71, "3.8.1", None)
    assert runner.available() is False

    monkeypatch.setattr(judge0_client, "is_available", lambda force=False: True)
    assert runner.available() is True


# ------------------------------------------------------------
# Java source normalization (Judge0 always compiles as Main.java,
# unlike Piston/the old runners, which detected the candidate's
# actual public class name).
# ------------------------------------------------------------

def test_java_source_is_renamed_to_main():
    source = "public class Solution { public static void main(String[] a) {} }"
    normalized = _normalize_java_source(source)
    assert "public class Main" in normalized
    assert "Solution" not in normalized


def test_java_source_already_named_main_is_untouched():
    source = "public class Main { public static void main(String[] a) {} }"
    assert _normalize_java_source(source) == source


def test_java_source_with_no_public_class_is_untouched():
    source = "class NotPublic {}"
    assert _normalize_java_source(source) == source


def test_java_runner_applies_normalization_before_sending(monkeypatch):
    def fake_execute(**kwargs):
        fake_execute.last_call = kwargs
        return {
            "status_id": judge0_client.STATUS_ACCEPTED, "status_description": "Accepted",
            "stdout": "", "stderr": "", "compile_output": "", "message": "",
            "exit_code": 0, "time_seconds": 0.0,
        }

    monkeypatch.setattr(judge0_client, "execute", fake_execute)
    monkeypatch.setattr(judge0_client, "is_available", lambda force=False: True)

    runner = Judge0Runner("java", 91, "JDK 17.0.6", _normalize_java_source)
    runner.execute(ExecutionRequest(
        language="java",
        source_code="public class Solution { public static void main(String[] a) {} }",
    ))

    assert "public class Main" in fake_execute.last_call["source_code"]

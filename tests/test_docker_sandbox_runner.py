"""
docker_sandbox_runner.py's response-classification logic (compile
error / timeout / success / runtime error / unavailable), tested
against a stubbed docker_sandbox_client -- no real Docker daemon
required. Mirrors test_piston_runner.py / test_judge0_runner.py's
structure and coverage.

create_workdir()/cleanup_workdir() are left real (not mocked) in
every test below -- they're plain local filesystem operations with
no Docker dependency, and letting them run for real is what catches
the exact bug this file's first version had: a compiled binary from
the compile step vanishing before the run step could see it, because
each step got its OWN throwaway directory instead of sharing one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.schemas.execution_result import ExecutionRequest
from backend.app.services.assessment import docker_sandbox_client as sandbox
from backend.app.services.assessment.docker_sandbox_runner import (
    DockerSandboxRunner,
    _java_filename,
)


def _run_result(exit_code=0, stdout="", stderr="", timed_out=False, elapsed_ms=10):
    return sandbox.ContainerRunResult(
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        timed_out=timed_out, elapsed_ms=elapsed_ms,
    )


def test_successful_run_no_compile_step(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)
    monkeypatch.setattr(
        sandbox, "run_in_container",
        lambda *a, **k: _run_result(exit_code=0, stdout="hello\n"),
    )

    runner = DockerSandboxRunner("python")
    result = runner.execute(ExecutionRequest(language="python", source_code="print('hello')"))

    assert result.success is True
    assert result.error_type == "success"
    assert result.stdout == "hello\n"


def test_nonzero_exit_is_runtime_error(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)
    monkeypatch.setattr(
        sandbox, "run_in_container",
        lambda *a, **k: _run_result(exit_code=1, stderr="Traceback..."),
    )

    runner = DockerSandboxRunner("python")
    result = runner.execute(ExecutionRequest(language="python", source_code="raise ValueError()"))

    assert result.success is False
    assert result.error_type == "runtime_error"
    assert result.exit_code == 1


def test_timeout(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)
    monkeypatch.setattr(
        sandbox, "run_in_container",
        lambda *a, **k: _run_result(timed_out=True),
    )

    runner = DockerSandboxRunner("python")
    result = runner.execute(
        ExecutionRequest(language="python", source_code="while True: pass", timeout_ms=1000)
    )

    assert result.success is False
    assert result.error_type == "timeout"
    assert result.timed_out is True


def test_compile_step_failure_is_compile_error(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)

    calls = []

    def fake_run_in_container(image, command, work_dir, stdin="", timeout_seconds=10, **kwargs):
        calls.append(command)
        if command[0] in ("gcc", "g++"):
            return _run_result(exit_code=1, stderr="syntax error")
        return _run_result(exit_code=0, stdout="should not run")

    monkeypatch.setattr(sandbox, "run_in_container", fake_run_in_container)

    runner = DockerSandboxRunner("c")
    result = runner.execute(ExecutionRequest(language="c", source_code="int main() { return \"oops\" }"))

    assert result.success is False
    assert result.error_type == "compile_error"
    assert result.stderr == "syntax error"
    assert len(calls) == 1  # run step never attempted after a failed compile


def test_compile_step_success_then_runs_in_the_same_workdir(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)

    calls = []
    work_dirs_seen = []

    def fake_run_in_container(image, command, work_dir, stdin="", timeout_seconds=10, **kwargs):
        calls.append(command)
        work_dirs_seen.append(work_dir)
        if command[0] in ("gcc", "g++"):
            return _run_result(exit_code=0)
        return _run_result(exit_code=0, stdout="42\n")

    monkeypatch.setattr(sandbox, "run_in_container", fake_run_in_container)

    runner = DockerSandboxRunner("cpp")
    result = runner.execute(ExecutionRequest(language="cpp", source_code="int main() {}"))

    assert result.success is True
    assert result.stdout == "42\n"
    assert len(calls) == 2  # compile, then run
    # the exact bug this file's first version had: compile and run
    # must share ONE work_dir, never two different throwaway ones.
    assert work_dirs_seen[0] == work_dirs_seen[1]


def test_unavailable_docker_reports_runner_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)

    def fake_run_in_container(*a, **k):
        raise sandbox.DockerUnavailableError("daemon not running")

    monkeypatch.setattr(sandbox, "run_in_container", fake_run_in_container)

    runner = DockerSandboxRunner("python")
    result = runner.execute(ExecutionRequest(language="python", source_code="print(1)"))

    assert result.success is False
    assert result.error_type == "runner_unavailable"


def test_available_reflects_docker_reachability(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: False)
    runner = DockerSandboxRunner("python")
    assert runner.available() is False

    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)
    assert runner.available() is True


# ------------------------------------------------------------
# Java: dynamic public-class filename detection (kept from the
# original pre-Piston java_runner.py, since a direct `docker run`
# doesn't force a fixed filename the way Judge0 did).
# ------------------------------------------------------------

def test_java_filename_matches_the_public_class_name():
    source = "public class Solution { public static void main(String[] a) {} }"
    class_name, filename = _java_filename(source)
    assert class_name == "Solution"
    assert filename == "Solution.java"


def test_java_filename_defaults_to_main_when_no_public_class_found():
    class_name, filename = _java_filename("class NotPublic {}")
    assert class_name == "Main"
    assert filename == "Main.java"


def test_java_runner_compiles_with_the_detected_class_name(monkeypatch):
    monkeypatch.setattr(sandbox, "is_available", lambda force=False: True)

    calls = []

    def fake_run_in_container(image, command, work_dir, stdin="", timeout_seconds=10, **kwargs):
        calls.append(command)
        assert _read_file_from_volume(work_dir, "Solution.java") == (
            "public class Solution { public static void main(String[] a) {} }"
        )
        if command[0] == "javac":
            return _run_result(exit_code=0)
        return _run_result(exit_code=0, stdout="ok\n")

    monkeypatch.setattr(sandbox, "run_in_container", fake_run_in_container)

    runner = DockerSandboxRunner("java")
    result = runner.execute(ExecutionRequest(
        language="java",
        source_code="public class Solution { public static void main(String[] a) {} }",
    ))

    assert result.success is True
    assert calls[0] == ["javac", "Solution.java"]
    assert calls[1] == ["java", "-cp", ".", "Solution"]


# ------------------------------------------------------------
# create_workdir()/cleanup_workdir() themselves -- the exact pieces
# whose interaction caused the original bug. Both are backed by a
# real Docker named volume (not a host directory -- a bind-mounted
# host directory measured 25-30x slower for I/O-heavy compilers on
# this Docker Desktop/WSL2 setup, see create_workdir()'s own
# docstring), so these specific tests need a reachable Docker daemon.
# ------------------------------------------------------------

_needs_docker_for_volume_tests = __import__("pytest").mark.skipif(
    not sandbox.is_available(force=True),
    reason="Docker is not reachable -- create_workdir()/cleanup_workdir() need it for real (named volumes)",
)


def _docker_exe():
    return sandbox._docker_exe()


def _read_file_from_volume(volume_name: str, path: str) -> str | None:
    import subprocess

    proc = subprocess.run(
        [_docker_exe(), "run", "--rm", "--network", "none",
         "-v", f"{volume_name}:/sandbox", "-w", "/sandbox",
         sandbox._SEED_IMAGE, "cat", path],
        capture_output=True, text=True, timeout=15,
    )
    return proc.stdout if proc.returncode == 0 else None


@_needs_docker_for_volume_tests
def test_create_workdir_writes_files_and_cleanup_removes_the_volume():
    work_dir = sandbox.create_workdir({"main.py": "print(1)"})
    try:
        assert _read_file_from_volume(work_dir, "main.py") == "print(1)"
    finally:
        sandbox.cleanup_workdir(work_dir)

    import subprocess
    listing = subprocess.run(
        [_docker_exe(), "volume", "ls", "-q"], capture_output=True, text=True, timeout=15,
    ).stdout
    assert work_dir not in listing.splitlines()


@_needs_docker_for_volume_tests
def test_a_file_written_after_create_workdir_is_still_there_for_a_later_call():
    """Simulates exactly what a compile step does: writes a NEW file
    (the compiled binary) into the SAME work_dir create_workdir()
    handed back, and confirms it's still readable afterward -- this
    is the guarantee the run step depends on."""

    import subprocess

    work_dir = sandbox.create_workdir({"main.c": "int main(){return 0;}"})
    try:
        subprocess.run(
            [_docker_exe(), "run", "--rm", "--network", "none",
             "-v", f"{work_dir}:/sandbox", "-w", "/sandbox",
             sandbox._SEED_IMAGE, "sh", "-c", "echo pretend-compiled-binary > program"],
            capture_output=True, text=True, timeout=15,
        )

        assert _read_file_from_volume(work_dir, "main.c") == "int main(){return 0;}"
        assert _read_file_from_volume(work_dir, "program") == "pretend-compiled-binary\n"
    finally:
        sandbox.cleanup_workdir(work_dir)

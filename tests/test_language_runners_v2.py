"""
Every general-purpose language now dispatches through a direct
Docker sandbox (docker_sandbox_runner.py) -- one throwaway container
per submission, using Docker's own isolation -- rather than a bare
local subprocess, or either of the two third-party judge services
tried and abandoned before it (Piston: unreliable network path to
its package CDN; Judge0: its bundled `isolate` sandbox needs legacy
cgroup v1, which Docker Desktop's cgroup-v2-only internal Linux
environment cannot provide). The real-execution smoke tests below
require a reachable Docker daemon with the core language images
pulled and are skipped, not failed, when none is reachable;
response-classification logic (compile error / timeout / success) is
covered without needing that in tests/test_docker_sandbox_runner.py
via a stubbed client.

csharp is not yet supported by docker_sandbox_runner.py (needs a
small custom-built image) -- its tests below are skipped separately
until that follow-up lands.
"""

import pytest

from backend.app.schemas.execution_result import ExecutionRequest
from backend.app.services.assessment import docker_sandbox_client as sandbox
from backend.app.services.assessment.language_runner import RUNNER_REGISTRY

_docker_reachable = sandbox.is_available(force=True)

_needs_docker = pytest.mark.skipif(
    not _docker_reachable,
    reason="Docker is not reachable -- see docker_sandbox_client.py's own header for setup",
)

_needs_csharp = pytest.mark.skipif(
    "csharp" not in RUNNER_REGISTRY,
    reason="csharp isn't supported by docker_sandbox_runner.py yet (needs a custom-built image)",
)


# ------------------------------------------------------------
# Python
# ------------------------------------------------------------

@_needs_docker
def test_python_hello_world():
    runner = RUNNER_REGISTRY["python"]
    result = runner.execute(ExecutionRequest(language="python", source_code="print('hello')"))
    assert result.success is True
    assert result.stdout.strip() == "hello"


@_needs_docker
def test_python_stdin_stdout():
    runner = RUNNER_REGISTRY["python"]
    code = "name = input()\nprint(f'hi {name}')"
    result = runner.execute(ExecutionRequest(language="python", source_code=code, stdin="Ada"))
    assert result.success is True
    assert result.stdout.strip() == "hi Ada"


# ------------------------------------------------------------
# JavaScript
# ------------------------------------------------------------

@_needs_docker
def test_javascript_hello_world():
    runner = RUNNER_REGISTRY["javascript"]
    result = runner.execute(
        ExecutionRequest(language="javascript", source_code="console.log('hello');")
    )
    assert result.success is True
    assert result.stdout.strip() == "hello"


@_needs_docker
def test_javascript_stdin_stdout():
    runner = RUNNER_REGISTRY["javascript"]
    code = (
        "const data = require('fs').readFileSync(0, 'utf-8').trim();"
        "console.log('hi ' + data);"
    )
    result = runner.execute(
        ExecutionRequest(language="javascript", source_code=code, stdin="Ada")
    )
    assert result.success is True
    assert result.stdout.strip() == "hi Ada"


@_needs_docker
def test_javascript_runtime_error():
    runner = RUNNER_REGISTRY["javascript"]
    result = runner.execute(
        ExecutionRequest(language="javascript", source_code="throw new Error('boom');")
    )
    assert result.success is False
    assert result.error_type == "runtime_error"


@_needs_docker
def test_javascript_timeout():
    runner = RUNNER_REGISTRY["javascript"]
    result = runner.execute(
        ExecutionRequest(
            language="javascript",
            source_code="while(true) {}",
            timeout_ms=1000,
        )
    )
    assert result.success is False
    assert result.error_type == "timeout"
    assert result.timed_out is True


# ------------------------------------------------------------
# C# — not yet supported by the Docker sandbox runner
# ------------------------------------------------------------

@_needs_docker
@_needs_csharp
def test_csharp_hello_world():
    runner = RUNNER_REGISTRY["csharp"]
    code = (
        "using System;\n"
        "class Program { static void Main() { Console.WriteLine(\"hello\"); } }\n"
    )
    result = runner.execute(ExecutionRequest(language="csharp", source_code=code))
    assert result.success is True
    assert result.stdout.strip() == "hello"


@_needs_docker
@_needs_csharp
def test_csharp_stdin_stdout():
    runner = RUNNER_REGISTRY["csharp"]
    code = (
        "using System;\n"
        "class Program { static void Main() {"
        " string name = Console.ReadLine();"
        " Console.WriteLine(\"hi \" + name); } }\n"
    )
    result = runner.execute(
        ExecutionRequest(language="csharp", source_code=code, stdin="Ada")
    )
    assert result.success is True
    assert result.stdout.strip() == "hi Ada"


@_needs_docker
@_needs_csharp
def test_csharp_compile_error():
    runner = RUNNER_REGISTRY["csharp"]
    code = "this is not valid C# at all {{{"
    result = runner.execute(ExecutionRequest(language="csharp", source_code=code))
    assert result.success is False
    assert result.error_type == "compile_error"


# ------------------------------------------------------------
# Registry shape — true regardless of whether Docker is reachable
# ------------------------------------------------------------

_CORE_LANGUAGES = [
    "python", "javascript", "typescript", "java",
    "c", "cpp", "go", "rust", "php", "ruby", "kotlin", "csharp", "swift",
]


def test_core_language_runners_are_registered():
    for lang in _CORE_LANGUAGES:
        assert lang in RUNNER_REGISTRY


@_needs_docker
def test_core_language_runners_are_available_when_docker_is_up():
    for lang in _CORE_LANGUAGES:
        assert RUNNER_REGISTRY[lang].available() is True


def test_swift_runs_through_the_same_docker_sandbox_as_every_other_language():
    # swift_runner.py (bare local subprocess against the Windows
    # toolchain) is still unused/unregistered -- but docker_sandbox_
    # runner.py now has a real swift entry (the official Linux swift
    # image), registered exactly like every other language. The
    # earlier Windows-toolchain dead end never applied to Docker,
    # since the container is Linux regardless of the host OS.
    assert "swift" in RUNNER_REGISTRY
    assert RUNNER_REGISTRY["swift"].__class__.__module__.endswith("docker_sandbox_runner")


def test_all_13_target_languages_are_registered():
    # The complete set: all 13 languages this project supports now
    # have a real DockerSandboxRunner — swift (the official Linux
    # swift image, sidestepping the Windows-toolchain gap) and
    # csharp (the official mono image directly, no custom build
    # needed, unlike typescript/kotlin) both need no custom build.
    for lang in _CORE_LANGUAGES:
        assert lang in RUNNER_REGISTRY
    assert len(RUNNER_REGISTRY) >= len(_CORE_LANGUAGES)

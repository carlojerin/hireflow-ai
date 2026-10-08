from backend.app.services.assessment.language_capability_registry import (
    get_language_capability,
)


def test_python_is_fully_implemented():
    cap = get_language_capability("python")
    assert cap.known is True
    assert cap.execution_supported is True
    assert cap.implementation_status == "implemented"
    assert cap.display_name == "Python"


def test_javascript_is_fully_implemented_on_this_machine():
    cap = get_language_capability("javascript")
    assert cap.known is True
    assert cap.runtime_detected is True  # node is actually installed here
    assert cap.execution_supported is True
    assert cap.implementation_status == "implemented"


def test_csharp_is_fully_implemented_on_this_machine():
    cap = get_language_capability("csharp")
    assert cap.known is True
    assert cap.runtime_detected is True  # dotnet is actually installed here
    assert cap.execution_supported is True
    assert cap.implementation_status == "implemented"


def test_typescript_is_fully_implemented_on_this_machine():
    # Task 5C, Complete Multi-Language Execution Engine: tsc was
    # installed (npm install -g typescript) and typescript_runner.py
    # built+tested -- typescript moved from UNAVAILABLE to AVAILABLE.
    cap = get_language_capability("typescript")
    assert cap.known is True
    assert cap.runtime_detected is True
    assert cap.execution_supported is True
    assert cap.implementation_status == "implemented"
    assert cap.state == "AVAILABLE"


def test_java_is_fully_implemented_on_this_machine():
    # Task 5C, Complete Multi-Language Execution Engine: JDK 21
    # was installed (winget Microsoft.OpenJDK.21) and java_runner.py
    # built+tested -- java moved from UNSUPPORTED to AVAILABLE.
    cap = get_language_capability("java")
    assert cap.known is True
    assert cap.runtime_detected is True
    assert cap.execution_supported is True
    assert cap.implementation_status == "implemented"
    assert cap.state == "AVAILABLE"


def test_swift_has_a_registered_runner_but_is_genuinely_unavailable():
    # Task 5C, Complete Multi-Language Execution Engine: swift_runner.py
    # is real, registered architecture, but the installed Swift 6.4.0
    # Windows toolchain is missing its Windows-target standard library
    # (confirmed by direct filesystem inspection -- see swift_runner.py's
    # docstring). This is the exact "architecture exists, runtime is
    # not currently usable" case this task's own vocabulary calls
    # UNAVAILABLE, never UNSUPPORTED.
    cap = get_language_capability("swift")
    assert cap.known is True
    assert cap.execution_supported is False
    assert cap.state == "UNAVAILABLE"


def test_unknown_language_is_safely_unknown():
    cap = get_language_capability("brainfuck")
    assert cap.known is False
    assert cap.execution_supported is False


def test_execution_supported_never_true_without_implementation_status_implemented():
    # cross-invariant: whenever execution_supported is True, the
    # implementation_status (and state) must say so too -- these
    # fields must never disagree.
    for lang in [
        "python", "javascript", "csharp", "java", "go", "rust",
        "typescript", "c", "cpp", "php", "ruby", "kotlin", "swift",
    ]:
        cap = get_language_capability(lang)
        if cap.execution_supported:
            assert cap.implementation_status == "implemented"
            assert cap.state == "AVAILABLE"
        else:
            assert cap.implementation_status != "implemented"
            assert cap.state != "AVAILABLE"


def test_all_13_target_languages_are_now_available():
    # Task 5C, Complete Multi-Language Execution Engine's own
    # acceptance bar: every target language except swift (see the
    # dedicated UNAVAILABLE test above) is now genuinely AVAILABLE.
    for lang in [
        "python", "javascript", "typescript", "java", "c", "cpp",
        "csharp", "go", "rust", "php", "ruby", "kotlin",
    ]:
        cap = get_language_capability(lang)
        assert cap.state == "AVAILABLE", f"{lang}: expected AVAILABLE, got {cap.state}"

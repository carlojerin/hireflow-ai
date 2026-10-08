from backend.app.services.assessment.execution_security_v2 import (
    ExecutionBackend,
    get_security_boundary_report,
)


def test_python_has_static_analysis_but_is_not_production_ready():
    report = get_security_boundary_report("python")
    assert report.backend == ExecutionBackend.IN_PROCESS_SUBPROCESS
    assert report.has_static_code_analysis is True
    assert report.production_ready is False
    assert len(report.known_gaps) > 0


def test_javascript_and_csharp_have_no_static_analysis():
    for lang in ["javascript", "csharp"]:
        report = get_security_boundary_report(lang)
        assert report.has_static_code_analysis is False
        assert report.production_ready is False
        assert report.enforces_timeout is True


def test_no_language_is_ever_claimed_production_ready():
    for lang in ["python", "javascript", "csharp", "java", "go"]:
        report = get_security_boundary_report(lang)
        assert report.production_ready is False

from backend.app.schemas.assessment_plan import AssessmentPlan, AssessmentPlanItem
from backend.app.services.assessment.frontend_generator_v2 import (
    generate_frontend_assessment_v2,
)


def _frontend_plan():
    return AssessmentPlan(
        role_type="React Developer",
        assessment_types=[
            AssessmentPlanItem(
                assessment_type="frontend", technology="React", execution_supported=False,
                reason="Job requires React.", priority="high", difficulty="intermediate",
                question_count=5,
            )
        ],
        generated_by="deterministic_fallback",
    )


def _non_frontend_plan():
    return AssessmentPlan(
        role_type="Data Analyst",
        assessment_types=[
            AssessmentPlanItem(
                assessment_type="sql", technology="SQL", execution_supported=False,
                reason="Job requires SQL.", priority="high", difficulty="intermediate",
                question_count=5,
            )
        ],
        generated_by="deterministic_fallback",
    )


# W: frontend assessment contract -- honestly reports capability_unavailable,
# never fakes execution
def test_frontend_plan_reports_capability_unavailable_not_fake_success():
    result = generate_frontend_assessment_v2(_frontend_plan())
    assert result.status == "capability_unavailable"
    assert result.question is None
    assert "cannot yet execute" in result.message


def test_non_frontend_plan_is_not_applicable():
    result = generate_frontend_assessment_v2(_non_frontend_plan())
    assert result.status == "not_applicable"
    assert result.question is None

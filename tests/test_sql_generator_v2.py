from backend.app.schemas.assessment_blueprint import AssessmentBlueprint, DifficultyDistribution
from backend.app.schemas.assessment_plan import AssessmentPlan, AssessmentPlanItem
from backend.app.services.assessment.sql_execution import SQLFixture, run_sql_submission
from backend.app.services.assessment.sql_generator_v2 import generate_sql_assessment_v2


class _FakeJob:
    title = "Data Analyst"
    description = "Analyze transaction data."
    required_skills = "SQL"


def _blueprint():
    return AssessmentBlueprint(
        candidate_id=1, job_id=1, job_title="Data Analyst",
        total_questions=10, candidate_experience_level="intermediate",
        difficulty_distribution=DifficultyDistribution(basic=2, intermediate=6, advanced=2),
    )


def _sql_plan():
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


def _non_sql_plan():
    return AssessmentPlan(
        role_type="HR Executive",
        assessment_types=[
            AssessmentPlanItem(
                assessment_type="hr_scenario", technology=None, execution_supported=False,
                reason="Role is HR-focused.", priority="high", difficulty="intermediate",
                question_count=5,
            )
        ],
        generated_by="deterministic_fallback",
    )


# U: SQL assessment execution -- real, isolated fixture, not routed through Python
def test_sql_fallback_generates_valid_executable_question_set():
    result = generate_sql_assessment_v2(_FakeJob(), _blueprint(), _sql_plan(), use_gemini=False)
    assert result.status == "generated"
    assert result.generated_by == "fallback"
    qs = result.question_set
    assert len(qs.questions) >= 2

    solutions = {
        1: "SELECT c.name, SUM(t.amount) FROM customers c JOIN transactions t ON t.customer_id=c.id GROUP BY c.id ORDER BY c.name",
        2: "SELECT c.name FROM customers c JOIN transactions t ON t.customer_id=c.id GROUP BY c.id ORDER BY SUM(t.amount) DESC LIMIT 1",
    }

    for q in qs.questions:
        fixture = SQLFixture(setup_sql=q.setup_sql)
        expected = [tuple(row) for row in q.expected_rows]
        result2 = run_sql_submission(fixture, solutions[q.question_id], expected, order_sensitive=q.order_sensitive)
        assert result2.success is True


def test_sql_not_requested_is_not_applicable():
    result = generate_sql_assessment_v2(_FakeJob(), _blueprint(), _non_sql_plan(), use_gemini=False)
    assert result.status == "not_applicable"
    assert result.question_set is None

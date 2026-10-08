"""
role_classification.py's shared technical/non-technical heuristic —
used by both the AI interview blueprint's role_shape and the
pipeline's programming-stage skip gate, so a job is never classified
differently in one place than the other.
"""

from backend.app.services.assessment.role_classification import (
    job_role_shape,
    required_skills_are_technical,
)


class _FakeJob:
    def __init__(self, title="", required_skills="", description="", technologies=""):
        self.title = title
        self.required_skills = required_skills
        self.description = description
        self.technologies = technologies


def test_a_coding_language_in_required_skills_is_technical():
    assert required_skills_are_technical("Java, SQL") is True


def test_a_framework_or_cloud_keyword_is_technical_even_without_a_named_language():
    assert required_skills_are_technical("React, AWS, Docker, Git") is True


def test_business_skills_with_no_technical_term_are_non_technical():
    assert required_skills_are_technical("Excel, Financial Modeling, Risk Analysis, Communication") is False


def test_empty_required_skills_is_non_technical_not_a_crash():
    assert required_skills_are_technical("") is False


def test_extra_text_can_supply_the_technical_signal_the_skills_list_lacks():
    assert required_skills_are_technical(
        "", extra_text="Build and maintain our REST API backend services.",
    ) is True


# --- the fintech scenario that prompted this fix ------------------------

def test_fintech_backend_engineer_is_technical():
    job = _FakeJob(
        title="Backend Engineer",
        required_skills="Python, PostgreSQL, AWS",
        description="Build payment processing APIs for our fintech platform.",
    )
    assert job_role_shape(job) == "technical"


def test_fintech_financial_analyst_is_non_technical():
    # Same INDUSTRY (fintech) as the job above -- a different ROLE,
    # and the classifier must follow the role, not the industry.
    job = _FakeJob(
        title="Financial Analyst",
        required_skills="Excel, Financial Modeling, Risk Analysis",
        description="Analyze transaction risk and produce quarterly reports for our fintech platform.",
    )
    assert job_role_shape(job) == "non_technical"


def test_digital_marketing_role_is_non_technical():
    job = _FakeJob(
        title="Digital Marketing Executive",
        required_skills="SEO, Content Writing, Social Media Management",
        description="Plan and run marketing campaigns across social channels.",
    )
    assert job_role_shape(job) == "non_technical"

"""
HR's applicant dashboard previously had NO visibility into SJT/TDA
results -- invisible even though the candidate completed a real,
scored assessment (same severity bug class as the earlier stage-3
URL-routing bug). Confirms application_summary_response() now
surfaces both.
"""

from datetime import datetime, timedelta

from backend.app.api.routes.company_applications import (
    application_summary_response,
    sjt_summary_response,
    tda_summary_response,
)
from backend.app.models.situational_judgment_test import SituationalJudgmentTest
from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment


class _FakeApplication:
    id = 1
    status = "sjt_passed"
    recruiter_shortlisted = False
    recruiter_notes = None
    applied_date = None
    updated_date = None
    is_archived = False
    archived_at = None
    ai_match_score = None
    ai_shortlist = False
    ai_recommendation = None
    ai_skill_score = None
    ai_experience_score = None
    ai_role_score = None
    ai_location_score = None
    ai_technology_score = None
    ai_education_score = None
    ai_matched_skills = None
    ai_missing_skills = None
    ai_analyzed_at = None


class _FakeJob:
    id = 1
    title = "Digital Marketing Executive"
    company = "Co"
    location = None
    work_mode = None
    employment_type = None


class _FakeUser:
    id = 1
    full_name = "T"
    username = "t"
    email = "t@example.com"
    mobile = None
    location = None
    current_role = None
    industry = None
    experience_level = None


def _sjt(passed=True, status="completed"):
    now = datetime.utcnow()
    return SituationalJudgmentTest(
        id=1, application_id=1, questions="[]", total_questions=10, passed=passed, status=status,
        score=9, assigned_at=now, expires_at=now + timedelta(hours=1),
        exam_expires_at=now + timedelta(minutes=20), started_at=now, completed_at=now,
    )


def _tda(passed=True, status="completed"):
    now = datetime.utcnow()
    return TechnicalDomainAssessment(
        id=1, application_id=1, domain="salesforce", questions="[]", total_questions=10, passed=passed, status=status,
        score=9, assigned_at=now, expires_at=now + timedelta(hours=1),
        exam_expires_at=now + timedelta(minutes=20), started_at=now, completed_at=now,
    )


def test_sjt_summary_response_is_never_none_for_a_completed_test():
    summary = sjt_summary_response(_sjt())
    assert summary["status"] == "completed"
    assert summary["passed"] is True
    assert summary["percentage"] == 90.0


def test_tda_summary_response_includes_the_domain():
    summary = tda_summary_response(_tda())
    assert summary["domain"] == "salesforce"
    assert summary["passed"] is True
    assert summary["percentage"] == 90.0


def test_summary_response_is_none_when_no_assessment_exists():
    assert sjt_summary_response(None) is None
    assert tda_summary_response(None) is None


def test_application_summary_response_surfaces_sjt_and_tda_to_hr():
    # The actual bug: HR's candidate list/detail view previously had
    # no field at all for these two assessment types.
    response = application_summary_response(
        _FakeApplication(), _FakeJob(), _FakeUser(), None,
        sjt_test=_sjt(),
        tda_assessment=_tda(),
    )

    assert response["situational_judgment_test"] is not None
    assert response["situational_judgment_test"]["passed"] is True

    assert response["technical_domain_assessment"] is not None
    assert response["technical_domain_assessment"]["domain"] == "salesforce"


def test_application_summary_response_defaults_are_none_not_missing_key():
    response = application_summary_response(
        _FakeApplication(), _FakeJob(), _FakeUser(), None,
    )

    assert "situational_judgment_test" in response
    assert response["situational_judgment_test"] is None
    assert "technical_domain_assessment" in response
    assert response["technical_domain_assessment"] is None

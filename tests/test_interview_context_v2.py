"""
Deterministic unit tests for the Task 5H interview context/
blueprint builders (interview_context_builder_v2.py). No database,
no Gemini -- built from lightweight fake objects mirroring the real
SQLAlchemy models' attribute shape, matching this project's
existing "pure function, pure test" style.
"""

import json

from backend.app.services.assessment.interview_context_builder_v2 import (
    _aptitude_category_results,
    build_interview_blueprint_v2,
    build_interview_context_v2,
)
from backend.app.services.assessment.programming_assessment_evidence_v2 import (
    ProgrammingAssessmentEvidenceV2,
)


class _FakeJob:
    def __init__(self, title="Backend Developer", company="Acme", description="Build APIs.", required_skills="Java, SQL"):
        self.title = title
        self.company = company
        self.description = description
        self.required_skills = required_skills


class _FakeUser:
    def __init__(self, full_name="Jane Doe", experience_level=None, current_role=None):
        self.full_name = full_name
        self.experience_level = experience_level
        self.current_role = current_role


class _FakeProfile:
    def __init__(self, skills=None, education=None, experience_level=None):
        self.skills = json.dumps(skills) if skills else None
        self.education = education
        self.experience_level = experience_level


class _FakeResumeAnalysis:
    def __init__(self, skills=None, experience=None, education=None, experience_level=None, summary=None):
        self.skills = json.dumps(skills) if skills else None
        self.experience = json.dumps(experience) if experience else None
        self.education = education
        self.experience_level = experience_level
        self.summary = summary


class _FakeAptitudeTest:
    def __init__(self, status="completed", score=8, total_questions=10, passed=True, questions=None, answers=None):
        self.status = status
        self.score = score
        self.total_questions = total_questions
        self.passed = passed
        self.questions = json.dumps(questions) if questions is not None else "[]"
        self.answers = json.dumps(answers) if answers is not None else None


# --- context building -------------------------------------------------

def test_context_uses_real_resume_data_never_invents_it():

    job = _FakeJob()
    user = _FakeUser(experience_level="Mid Level")
    resume_analysis = _FakeResumeAnalysis(
        skills=["Java", "SQL"], experience=["Backend Developer (2 years)"], summary="A backend engineer.",
    )

    context = build_interview_context_v2(
        job=job, user=user, profile=None, resume_analysis=resume_analysis,
        aptitude_test=None, programming_evidence=None,
    )

    assert context.candidate.skills == ["Java", "SQL"]
    assert context.candidate.resume_summary == "A backend engineer."
    assert context.aptitude.available is False
    assert context.programming.available is False


def test_context_marks_missing_information_unavailable_not_fabricated():

    job = _FakeJob()
    user = _FakeUser()

    context = build_interview_context_v2(
        job=job, user=user, profile=None, resume_analysis=None,
        aptitude_test=None, programming_evidence=None,
    )

    assert context.candidate.skills == []
    assert context.candidate.resume_summary is None
    assert context.aptitude.available is False
    assert context.programming.available is False


def test_context_carries_programming_evidence_v2_unchanged():

    evidence = ProgrammingAssessmentEvidenceV2(
        strengths=["Java"], weaknesses=["Debugging"], insufficient_evidence=["System Design"],
        technologies_demonstrated=["Java"], languages_used=["java"], highest_difficulty_passed="hard",
    )

    context = build_interview_context_v2(
        job=_FakeJob(), user=_FakeUser(), profile=None, resume_analysis=None,
        aptitude_test=None, programming_evidence=evidence,
    )

    assert context.programming.available is True
    assert context.programming.strengths == ["Java"]
    assert context.programming.weaknesses == ["Debugging"]
    assert context.programming.insufficient_evidence == ["System Design"]
    assert context.programming.highest_difficulty_passed == "hard"


# --- aptitude category derivation --------------------------------------

def test_aptitude_category_results_derived_from_real_stored_answers():

    questions = [
        {"id": 1, "category": "Logical Reasoning", "correct_answer": "30"},
        {"id": 2, "category": "Logical Reasoning", "correct_answer": "40"},
        {"id": 3, "category": "Numerical", "correct_answer": "$100"},
    ]
    answers = [
        {"question_id": 1, "selected_answer": "30"},
        {"question_id": 2, "selected_answer": "wrong"},
        {"question_id": 3, "selected_answer": "$100"},
    ]

    test = _FakeAptitudeTest(questions=questions, answers=answers)

    results = _aptitude_category_results(test)

    assert results["Logical Reasoning"] == "mixed"
    assert results["Numerical"] == "strong"


def test_aptitude_category_results_empty_when_no_test():
    assert _aptitude_category_results(None) == {}


# --- blueprint derivation -----------------------------------------------

def test_blueprint_competencies_come_from_job_required_skills():

    context = build_interview_context_v2(
        job=_FakeJob(required_skills="Java, Spring Boot"),
        user=_FakeUser(), profile=None, resume_analysis=None,
        aptitude_test=None, programming_evidence=None,
    )

    blueprint = build_interview_blueprint_v2(context)

    names = {c.name for c in blueprint.competencies}
    assert "Java" in names
    assert "Spring Boot" in names
    # universal baseline competencies always present
    assert "Communication" in names
    assert "Problem Solving" in names


def test_blueprint_marks_known_strength_from_programming_evidence():

    evidence = ProgrammingAssessmentEvidenceV2(strengths=["Java"], weaknesses=[])

    context = build_interview_context_v2(
        job=_FakeJob(required_skills="Java"), user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=evidence,
    )

    blueprint = build_interview_blueprint_v2(context)
    java = next(c for c in blueprint.competencies if c.name == "Java")

    assert java.known_strength is True
    assert java.evidence_status == "KNOWN"
    assert java.source == "programming"


def test_blueprint_marks_needs_verification_from_programming_weakness():

    evidence = ProgrammingAssessmentEvidenceV2(strengths=[], weaknesses=["Debugging"])

    context = build_interview_context_v2(
        job=_FakeJob(required_skills="Java"), user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=evidence,
    )

    blueprint = build_interview_blueprint_v2(context)
    names = {c.name: c for c in blueprint.competencies}

    assert "Debugging" in names
    assert names["Debugging"].known_weakness is True
    assert names["Debugging"].evidence_status == "NEEDS_VERIFICATION"


def test_blueprint_never_fabricates_strength_when_no_evidence():

    context = build_interview_context_v2(
        job=_FakeJob(required_skills="Kotlin"), user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=None,
    )

    blueprint = build_interview_blueprint_v2(context)
    kotlin = next(c for c in blueprint.competencies if c.name == "Kotlin")

    assert kotlin.known_strength is False
    assert kotlin.known_weakness is False
    assert kotlin.evidence_status == "UNKNOWN"


def test_blueprint_role_shape_reflects_job_signal():

    technical_context = build_interview_context_v2(
        job=_FakeJob(required_skills="Java, SQL"), user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=None,
    )

    assert build_interview_blueprint_v2(technical_context).role_shape == "technical"


def test_every_candidate_gets_asked_about_ai_tool_usage_regardless_of_role_shape():

    technical_context = build_interview_context_v2(
        job=_FakeJob(title="Backend Developer", required_skills="Java, SQL"),
        user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=None,
    )
    technical_blueprint = build_interview_blueprint_v2(technical_context)

    non_technical_context = build_interview_context_v2(
        job=_FakeJob(
            title="Digital Marketing Executive", required_skills="",
            description="Plan and run social media campaigns, SEO and content strategy.",
        ),
        user=_FakeUser(), profile=None,
        resume_analysis=None, aptitude_test=None, programming_evidence=None,
    )
    non_technical_blueprint = build_interview_blueprint_v2(non_technical_context)

    assert non_technical_blueprint.role_shape == "non_technical"

    # The AI-tool / package / work-style questions are a fixed closing sequence asked by
    # interview_integration_v2._enforce_closing_sequence(), not competencies the model picks.
    for blueprint in (technical_blueprint, non_technical_blueprint):
        names = {c.name for c in blueprint.competencies}
        assert not names & {"AI Tool Fluency", "Salary Expectations", "Closing Thoughts & Fit"}

    from backend.app.services.assessment.interview_integration_v2 import AI_TOOL_QUESTIONS

    # technical candidates are asked about AI coding tools, non-technical ones about work tools like Canva
    assert "Cursor" in AI_TOOL_QUESTIONS["technical"] and "Cursor" not in AI_TOOL_QUESTIONS["non_technical"]
    assert "Canva" in AI_TOOL_QUESTIONS["non_technical"]

import json
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.application_answer import ApplicationAnswer
from backend.app.models.job_question import JobQuestion
from backend.app.models.user import User
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.interview_test import InterviewTest
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.application_status_history import (
    ApplicationStatusHistory,
)

from backend.app.services.ai_candidate_matching import (
    calculate_candidate_match,
)

from backend.app.services.application_status_history import (
    record_status_change,
    TERMINAL_APPLICATION_STATUSES,
)

from backend.app.services.email.notifications import (
    notify_status_change,
)

from backend.app.services.resume.active_resume import get_active_resume
from backend.app.services.resume.resolve import resolve_application_resume_path
from backend.app.services.aptitude.question_bank import (
    get_aptitude_questions,
)
from backend.app.services.aptitude.aptitude_question_service import build_aptitude_questions
from backend.app.api.routes.aptitude_tests import (
    APTITUDE_START_STATUS,
    APTITUDE_DEADLINE_HOURS,
)
from backend.app.services.assessment.programming_integration import (
    PROGRAMMING_START_STATUS,
)
from backend.app.services.assessment.interview_integration import (
    INTERVIEW_START_STATUS,
)
from backend.app.services.assessment.assignment_service import (
    get_assignment,
    create_assignment,
    batch_get_assignments,
)
from backend.app.models.assessment_assignment import AssessmentAssignment
from backend.app.models.assessment_consent import AssessmentConsent
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.assessment_video_recording import AssessmentVideoRecording
from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
from backend.app.api.deps import get_current_company, require_company_self, require_csrf


logger = logging.getLogger("jobpilot.company_applications")


router = APIRouter(
    prefix="/api/company-applications",
    tags=["Company Applications"],
)


# ============================================================
# CONSTANTS
# ============================================================

RECRUITMENT_STATUSES = {
    "new",
    "reviewing",
    "shortlisted",
    "test_pending",
    "test_passed",
    "aptitude_pending",
    "aptitude_passed",
    "aptitude_failed",
    "aptitude_expired",
    "aptitude_terminated",
    "interview",
    "hired",
    "rejected",
}


# ============================================================
# SCHEMAS
# ============================================================

class ApplicationStatusUpdate(BaseModel):
    status: str = Field(
        ...,
        min_length=1,
        max_length=30,
    )


class RecruiterNotesUpdate(BaseModel):
    recruiter_notes: str | None = Field(
        default=None,
        max_length=2000,
    )


class RecruiterShortlistUpdate(BaseModel):
    shortlisted: bool


# ============================================================
# COMPANY CHECK
# ============================================================

def get_verified_company(
    company_id: int,
    db: Session,
) -> Company:

    company = (
        db.query(Company)
        .filter(
            Company.id == company_id
        )
        .first()
    )

    if not company:
        raise HTTPException(
            status_code=404,
            detail="Company not found",
        )

    if not company.account_active:
        raise HTTPException(
            status_code=403,
            detail="Company account is not active",
        )

    if company.verification_status != "verified":
        raise HTTPException(
            status_code=403,
            detail="Company is not approved",
        )

    if not company.admin_verified:
        raise HTTPException(
            status_code=403,
            detail="Company is not admin verified",
        )

    return company


# ============================================================
# PROFILE RESPONSE
# ============================================================

def profile_response(
    profile: CandidateProfile | None,
):

    if not profile:
        return None

    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "full_name": profile.full_name,
        "profile_photo": profile.profile_photo,
        "headline": profile.headline,
        "about": profile.about,
        "education": profile.education,
        "experience": profile.experience,
        "preferred_roles": profile.preferred_roles,
        "preferred_locations": profile.preferred_locations,
        "work_mode": profile.work_mode,
        "minimum_salary": profile.minimum_salary,
        "experience_level": profile.experience_level,
        "preferred_technologies": profile.preferred_technologies,
        "job_type": profile.job_type,
        "skills": profile.skills,
        "projects": profile.projects,
        "certifications": profile.certifications,
        "achievements": profile.achievements,
        "github_url": profile.github_url,
        "linkedin_url": profile.linkedin_url,
        "portfolio_url": profile.portfolio_url,
        "profile_slug": profile.profile_slug,
    }


# ============================================================
# AI RESULT RESPONSE
# ============================================================

def ai_result_response(
    application: JobApplication,
):

    return {
        "ai_match_score": application.ai_match_score,
        "ai_shortlist": application.ai_shortlist,
        "ai_recommendation": application.ai_recommendation,
        "ai_skill_score": application.ai_skill_score,
        "ai_experience_score": application.ai_experience_score,
        "ai_role_score": application.ai_role_score,
        "ai_location_score": application.ai_location_score,
        "ai_technology_score": application.ai_technology_score,
        "ai_education_score": application.ai_education_score,
        "ai_matched_skills": application.ai_matched_skills,
        "ai_missing_skills": application.ai_missing_skills,
        "ai_analyzed_at": (
            application.ai_analyzed_at.isoformat()
            if application.ai_analyzed_at
            else None
        ),
    }


# ============================================================
# APTITUDE ASSIGNMENT RESPONSE
#
# Lightweight summary only — never includes questions or
# answers. Reuses the same AptitudeTest row the candidate-side
# aptitude endpoints already read/write.
# ============================================================

def aptitude_summary_response(
    test: AptitudeTest | None,
):

    if not test:
        return None

    percentage = None

    if (
        test.score is not None
        and test.total_questions
    ):

        percentage = round(
            (
                test.score
                / test.total_questions
            ) * 100,
            2,
        )

    remaining_seconds = None

    if (
        test.expires_at
        and test.status in ("assigned", "in_progress")
    ):

        remaining_seconds = max(
            0,
            int(
                (
                    test.expires_at
                    - datetime.utcnow()
                ).total_seconds()
            ),
        )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "assigned_at": (
            test.assigned_at.isoformat()
            if test.assigned_at
            else None
        ),
        "expires_at": (
            test.expires_at.isoformat()
            if test.expires_at
            else None
        ),
        "remaining_seconds": remaining_seconds,
        "started_at": (
            test.started_at.isoformat()
            if test.started_at
            else None
        ),
        "completed_at": (
            test.completed_at.isoformat()
            if test.completed_at
            else None
        ),
        "score": test.score,
        "total_questions": test.total_questions,
        "percentage": percentage,
        "passed": test.passed,
    }


# ============================================================
# PROGRAMMING TEST RESPONSE (Task 4C)
#
# Mirrors aptitude_summary_response() exactly — lightweight
# result summary only. Never includes questions, hidden tests,
# or submitted source code.
# ============================================================

def programming_summary_response(
    test: ProgrammingTest | None,
):

    if not test:
        return None

    percentage = None

    if (
        test.score is not None
        and test.total_score
    ):

        percentage = round(
            (
                test.score
                / test.total_score
            ) * 100,
            2,
        )

    remaining_seconds = None

    if (
        test.expires_at
        and test.status == "in_progress"
    ):

        remaining_seconds = max(
            0,
            int(
                (
                    test.expires_at
                    - datetime.utcnow()
                ).total_seconds()
            ),
        )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "assigned_at": (
            test.assigned_at.isoformat()
            if test.assigned_at
            else None
        ),
        "expires_at": (
            test.expires_at.isoformat()
            if test.expires_at
            else None
        ),
        "remaining_seconds": remaining_seconds,
        "started_at": (
            test.started_at.isoformat()
            if test.started_at
            else None
        ),
        "submitted_at": (
            test.submitted_at.isoformat()
            if test.submitted_at
            else None
        ),
        "score": test.score,
        "total_score": test.total_score,
        "percentage": percentage,
        "passed": test.passed,
    }


# ============================================================
# PROGRAMMING V2 (MULTI-LANGUAGE) SUMMARY (Task 5E)
#
# Lightweight, list-safe summary mirroring programming_summary_
# response() above exactly — same fields, same shape — so the
# existing applicant list/detail UI can show a V2 candidate's
# status/score tile the same way it already shows a legacy one.
# Never includes source code or per-question detail; that stays
# behind the dedicated /review/programming-v2 endpoint (hr_
# assessment_review.py), opened only when HR expands it.
# ============================================================

def programming_v2_summary_response(assessment):

    if not assessment:
        return None

    percentage = None

    if assessment.score is not None and assessment.total_score:

        percentage = round(
            (assessment.score / assessment.total_score) * 100, 2,
        )

    remaining_seconds = None

    if assessment.expires_at and assessment.status == "in_progress":

        remaining_seconds = max(
            0,
            int((assessment.expires_at - datetime.utcnow()).total_seconds()),
        )

    return {
        "id": assessment.id,
        "status": assessment.status,
        "assigned_at": (
            assessment.assigned_at.isoformat() if assessment.assigned_at else None
        ),
        "expires_at": (
            assessment.expires_at.isoformat() if assessment.expires_at else None
        ),
        "remaining_seconds": remaining_seconds,
        "started_at": (
            assessment.started_at.isoformat() if assessment.started_at else None
        ),
        "submitted_at": (
            assessment.submitted_at.isoformat() if assessment.submitted_at else None
        ),
        "score": assessment.score,
        "total_score": assessment.total_score,
        "percentage": percentage,
        "passed": assessment.passed,
    }


# ============================================================
# SITUATIONAL JUDGMENT TEST / TECHNICAL DOMAIN ASSESSMENT RESPONSE
#
# HR's applicant list exposed programming_v2's result but had
# NOTHING for the two stage-3 assessments a non-technical or
# no-executable-language candidate actually takes (SituationalJudgment
# Test / TechnicalDomainAssessment) — invisible to HR even though the
# candidate completed a real, scored assessment. Mirrors
# programming_v2_summary_response() exactly.
# ============================================================

def sjt_summary_response(assessment):

    if not assessment:
        return None

    percentage = (
        round((assessment.score / assessment.total_questions) * 100, 2)
        if assessment.score is not None and assessment.total_questions
        else None
    )

    remaining_seconds = None

    if assessment.exam_expires_at and assessment.status == "in_progress":
        remaining_seconds = max(0, int((assessment.exam_expires_at - datetime.utcnow()).total_seconds()))

    return {
        "id": assessment.id,
        "status": assessment.status,
        "assigned_at": assessment.assigned_at.isoformat() if assessment.assigned_at else None,
        "expires_at": assessment.expires_at.isoformat() if assessment.expires_at else None,
        "remaining_seconds": remaining_seconds,
        "started_at": assessment.started_at.isoformat() if assessment.started_at else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "score": assessment.score,
        "total_questions": assessment.total_questions,
        "percentage": percentage,
        "passed": assessment.passed,
    }


def tda_summary_response(assessment):

    if not assessment:
        return None

    percentage = (
        round((assessment.score / assessment.total_questions) * 100, 2)
        if assessment.score is not None and assessment.total_questions
        else None
    )

    remaining_seconds = None

    if assessment.exam_expires_at and assessment.status == "in_progress":
        remaining_seconds = max(0, int((assessment.exam_expires_at - datetime.utcnow()).total_seconds()))

    return {
        "id": assessment.id,
        "domain": assessment.domain,
        "status": assessment.status,
        "assigned_at": assessment.assigned_at.isoformat() if assessment.assigned_at else None,
        "expires_at": assessment.expires_at.isoformat() if assessment.expires_at else None,
        "remaining_seconds": remaining_seconds,
        "started_at": assessment.started_at.isoformat() if assessment.started_at else None,
        "completed_at": assessment.completed_at.isoformat() if assessment.completed_at else None,
        "score": assessment.score,
        "total_questions": assessment.total_questions,
        "percentage": percentage,
        "passed": assessment.passed,
    }


# ============================================================
# AI INTERVIEW RESPONSE (Task 4D)
#
# Mirrors programming_summary_response() exactly — aggregate
# result only. Never includes question text or raw candidate
# answer text; evaluation_summary carries only the averaged
# dimension scores plus a merged, capped strengths/gaps list —
# the same granularity aptitude/programming already expose to
# HR, not per-response detail.
# ============================================================

def interview_summary_response(
    test: InterviewTest | None,
):

    if not test:
        return None

    remaining_seconds = None

    if (
        test.expires_at
        and test.status == "in_progress"
    ):

        remaining_seconds = max(
            0,
            int(
                (
                    test.expires_at
                    - datetime.utcnow()
                ).total_seconds()
            ),
        )

    evaluation_summary = (
        json.loads(test.evaluation_summary)
        if test.evaluation_summary
        else None
    )

    return {
        "id": test.id,
        "status": test.status,
        "question_source": test.question_source,
        "assigned_at": (
            test.assigned_at.isoformat()
            if test.assigned_at
            else None
        ),
        "expires_at": (
            test.expires_at.isoformat()
            if test.expires_at
            else None
        ),
        "remaining_seconds": remaining_seconds,
        "started_at": (
            test.started_at.isoformat()
            if test.started_at
            else None
        ),
        "submitted_at": (
            test.submitted_at.isoformat()
            if test.submitted_at
            else None
        ),
        "current_question_index": test.current_question_index,
        "total_questions": test.total_questions,
        "overall_score": test.overall_score,
        "passed": test.passed,
        "recommendation": test.recommendation,
        "evaluation_summary": evaluation_summary,
    }


# ============================================================
# AI INTERVIEW V2 SUMMARY (Task 5H)
#
# Lightweight, list-safe summary mirroring programming_v2_summary_
# response() exactly (Task 5E precedent) — same shape convention
# as interview_summary_response() above, so the existing applicant
# list/detail UI can show a V2 candidate's interview status tile
# the same way it already shows a legacy one. Never includes the
# per-turn conversation or evidence; that stays behind the
# dedicated /review/interview-v2 endpoint.
# ============================================================

def interview_v2_summary_response(session):

    if not session:
        return None

    remaining_seconds = None

    if session.expires_at and session.status == "in_progress":

        remaining_seconds = max(
            0, int((session.expires_at - datetime.utcnow()).total_seconds()),
        )

    final_evaluation = (
        json.loads(session.final_evaluation_json) if session.final_evaluation_json else None
    )

    return {
        "id": session.id,
        "status": session.status,
        "assigned_at": session.assigned_at.isoformat() if session.assigned_at else None,
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
        "remaining_seconds": remaining_seconds,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "current_turn": session.current_turn,
        "passed": final_evaluation.get("passed") if final_evaluation else None,
    }


# ============================================================
# ASSESSMENT SESSION SUMMARY RESPONSE (Task 4A)
#
# Minimal HR-facing view of the candidate's assessment
# progress — stage + self-intro status/timestamps/duration only.
# No text content, no score, no pass/fail: there is no AI
# evaluation of this stage, so none is claimed here.
# ============================================================

def _aptitude_status_label(
    aptitude_test: AptitudeTest | None,
) -> str:
    """
    Collapses AptitudeTest.status into the simple HR-facing
    label named by Task 4B Part 16 — "Not Started / In Progress
    / Completed" (plus "Expired", a real existing status). Never
    invents a score or pass/fail here; the full legitimate
    result is already exposed separately via the existing
    "aptitude" key (aptitude_summary_response above).
    """

    if not aptitude_test or aptitude_test.status == "assigned":
        return "not_started"

    if aptitude_test.status == "in_progress":
        return "in_progress"

    if aptitude_test.status == "completed":
        return "completed"

    if aptitude_test.status == "expired":
        return "expired"

    if aptitude_test.status == "terminated":
        return "terminated"

    return "not_started"


def _programming_status_label(
    programming_test: ProgrammingTest | None,
) -> str:
    """
    Same collapsing rule as _aptitude_status_label(), applied to
    ProgrammingTest.status. ProgrammingTest itself still has no
    "assigned" status (question generation only happens once the
    candidate actually starts) — HR's assignment decision lives
    separately on AssessmentAssignment (see "programming_assigned"
    in application_summary_response()), so a missing test here
    simply means "not_started" regardless of assignment state.
    """

    if not programming_test:
        return "not_started"

    if programming_test.status == "in_progress":
        return "in_progress"

    if programming_test.status == "completed":
        return "completed"

    if programming_test.status == "expired":
        return "expired"

    return "not_started"


def _interview_status_label(
    interview_test: InterviewTest | None,
) -> str:
    """Same collapsing rule, applied to InterviewTest.status."""

    if not interview_test:
        return "not_started"

    if interview_test.status == "in_progress":
        return "in_progress"

    if interview_test.status == "completed":
        return "completed"

    if interview_test.status == "expired":
        return "expired"

    return "not_started"


def assessment_summary_response(
    session_record: AssessmentSession | None,
    aptitude_test: AptitudeTest | None = None,
    programming_test: ProgrammingTest | None = None,
    interview_test: InterviewTest | None = None,
):

    if not session_record:
        return None

    return {
        "current_stage": session_record.current_stage,
        "self_intro_status": session_record.self_intro_status,
        "self_intro_started_at": (
            session_record.self_intro_started_at.isoformat()
            if session_record.self_intro_started_at
            else None
        ),
        "self_intro_completed_at": (
            session_record.self_intro_completed_at.isoformat()
            if session_record.self_intro_completed_at
            else None
        ),
        "self_intro_duration_seconds": (
            session_record.self_intro_duration_seconds
        ),
        "aptitude_status": _aptitude_status_label(aptitude_test),
        "programming_status": _programming_status_label(programming_test),
        "interview_status": _interview_status_label(interview_test),
    }


# ============================================================
# BATCH-FETCH APTITUDE TESTS (avoids N+1 queries per listing)
# ============================================================

def _fetch_aptitude_tests_by_application(
    db: Session,
    application_ids: list[int],
) -> dict[int, AptitudeTest]:

    if not application_ids:
        return {}

    tests = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        test.application_id: test
        for test in tests
    }


# ============================================================
# BATCH-FETCH ASSESSMENT SESSIONS (avoids N+1 queries)
# ============================================================

def _fetch_assessment_sessions_by_application(
    db: Session,
    application_ids: list[int],
) -> dict[int, AssessmentSession]:

    if not application_ids:
        return {}

    sessions = (
        db.query(AssessmentSession)
        .filter(
            AssessmentSession.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        session_record.application_id: session_record
        for session_record in sessions
    }


# ============================================================
# BATCH-FETCH PROGRAMMING TESTS (avoids N+1 queries) (Task 4C)
# ============================================================

def _fetch_programming_tests_by_application(
    db: Session,
    application_ids: list[int],
) -> dict[int, ProgrammingTest]:

    if not application_ids:
        return {}

    tests = (
        db.query(ProgrammingTest)
        .filter(
            ProgrammingTest.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        test.application_id: test
        for test in tests
    }


# ============================================================
# BATCH-FETCH PROGRAMMING V2 ASSESSMENTS (avoids N+1) (Task 5E)
# ============================================================

def _fetch_programming_v2_assessments_by_application(
    db: Session,
    application_ids: list[int],
):

    if not application_ids:
        return {}

    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2

    assessments = (
        db.query(ProgrammingAssessmentV2)
        .filter(
            ProgrammingAssessmentV2.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        assessment.application_id: assessment
        for assessment in assessments
    }


# ============================================================
# BATCH-FETCH SJT / TDA ASSESSMENTS (avoids N+1)
# ============================================================

def _fetch_sjt_tests_by_application(
    db: Session,
    application_ids: list[int],
):

    if not application_ids:
        return {}

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest

    tests = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id.in_(application_ids))
        .all()
    )

    return {test.application_id: test for test in tests}


def _fetch_tda_assessments_by_application(
    db: Session,
    application_ids: list[int],
):

    if not application_ids:
        return {}

    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    assessments = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id.in_(application_ids))
        .all()
    )

    return {assessment.application_id: assessment for assessment in assessments}


# ============================================================
# BATCH-FETCH AI INTERVIEW V2 SESSIONS (avoids N+1) (Task 5H)
# ============================================================

def _fetch_interview_v2_sessions_by_application(
    db: Session,
    application_ids: list[int],
):

    if not application_ids:
        return {}

    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2

    sessions = (
        db.query(AIInterviewSessionV2)
        .filter(
            AIInterviewSessionV2.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        session.application_id: session
        for session in sessions
    }


# ============================================================
# BATCH-FETCH INTERVIEW TESTS (avoids N+1 queries) (Task 4D)
# ============================================================

def _fetch_interview_tests_by_application(
    db: Session,
    application_ids: list[int],
) -> dict[int, InterviewTest]:

    if not application_ids:
        return {}

    tests = (
        db.query(InterviewTest)
        .filter(
            InterviewTest.application_id.in_(
                application_ids
            )
        )
        .all()
    )

    return {
        test.application_id: test
        for test in tests
    }


def _fetch_resume_availability_by_application(
    db: Session,
    applications: list[JobApplication],
) -> dict[int, bool]:
    """Whether a resume file exists for each application -- one query per
    source (invite / self-upload) instead of one query per application."""

    if not applications:
        return {}

    job_user_pairs = {
        (application.job_id, application.user_id)
        for application in applications
    }

    invited_pairs = set(
        db.query(
            CandidateInvite.job_id,
            CandidateInvite.claimed_user_id,
        )
        .filter(
            CandidateInvite.resume_file_path.isnot(None),
            CandidateInvite.claimed_user_id.isnot(None),
        )
        .all()
    ) & job_user_pairs

    user_ids = {application.user_id for application in applications}

    resumed_user_ids = {
        row[0]
        for row in db.query(Resume.user_id)
        .filter(Resume.user_id.in_(user_ids))
        .distinct()
        .all()
    }

    return {
        application.id: (
            (application.job_id, application.user_id) in invited_pairs
            or application.user_id in resumed_user_ids
        )
        for application in applications
    }


# ============================================================
# APPLICATION RESPONSE
# ============================================================

def application_summary_response(
    application: JobApplication,
    job: Job,
    user: User,
    profile: CandidateProfile | None,
    aptitude_test: AptitudeTest | None = None,
    assessment_session: AssessmentSession | None = None,
    programming_test: ProgrammingTest | None = None,
    interview_test: InterviewTest | None = None,
    programming_assignment: AssessmentAssignment | None = None,
    interview_assignment: AssessmentAssignment | None = None,
    programming_v2_assessment=None,
    interview_v2_session=None,
    sjt_test=None,
    tda_assessment=None,
    resume_available: bool = False,
):

    response = {
        "id": application.id,

        "resume_available": resume_available,

        "candidate": {
            "id": user.id,
            "full_name": user.full_name,
            "username": user.username,
            "email": user.email,
            "mobile": user.mobile,
            "location": user.location,
            "current_role": user.current_role,
            "industry": user.industry,
            "experience_level": user.experience_level,
        },

        "profile": profile_response(profile),

        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "work_mode": job.work_mode,
            "employment_type": job.employment_type,
        },

        "status": application.status,

        "recruiter_shortlisted": (
            application.recruiter_shortlisted
        ),

        "recruiter_notes": (
            application.recruiter_notes
        ),

        "applied_date": (
            application.applied_date.isoformat()
            if application.applied_date
            else None
        ),

        "updated_date": (
            application.updated_date.isoformat()
            if application.updated_date
            else None
        ),

        "is_archived": application.is_archived,

        "archived_at": (
            application.archived_at.isoformat()
            if application.archived_at
            else None
        ),

        "aptitude": aptitude_summary_response(
            aptitude_test
        ),

        "programming": programming_summary_response(
            programming_test
        ),

        "programming_v2": programming_v2_summary_response(
            programming_v2_assessment
        ),

        "situational_judgment_test": sjt_summary_response(
            sjt_test
        ),

        "technical_domain_assessment": tda_summary_response(
            tda_assessment
        ),

        "programming_assigned": (
            programming_assignment is not None
            and programming_assignment.status == "assigned"
        ),

        "interview": interview_summary_response(
            interview_test
        ),

        "interview_v2": interview_v2_summary_response(
            interview_v2_session
        ),

        "interview_assigned": (
            interview_assignment is not None
            and interview_assignment.status == "assigned"
        ),

        "assessment": assessment_summary_response(
            assessment_session,
            aptitude_test,
            programming_test,
            interview_test,
        ),
    }

    response.update(
        ai_result_response(application)
    )

    return response


# ============================================================
# ANSWER RESPONSE
# ============================================================

def answer_response(
    answer: ApplicationAnswer,
    question: JobQuestion | None,
):

    return {
        "id": answer.id,
        "question_id": answer.question_id,
        "question": (
            question.question
            if question
            else None
        ),
        "question_type": (
            question.question_type
            if question
            else None
        ),
        "options": (
            question.options
            if question
            else None
        ),
        "required": (
            question.required
            if question
            else None
        ),
        "answer": answer.answer,
    }


# ============================================================
# GET COMPANY APPLICATION
# ============================================================

def get_company_application(
    company_id: int,
    application_id: int,
    db: Session,
):

    get_verified_company(
        company_id,
        db,
    )

    result = (
        db.query(
            JobApplication,
            Job,
            User,
            CandidateProfile,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .join(
            User,
            User.id == JobApplication.user_id,
        )
        .outerjoin(
            CandidateProfile,
            CandidateProfile.user_id == User.id,
        )
        .filter(
            JobApplication.id == application_id,
            Job.company_id == company_id,
        )
        .first()
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    return result


# ============================================================
# JOB FILTER OPTIONS
# IMPORTANT:
# Keep this BEFORE the dynamic application route.
# ============================================================

@router.get("/{company_id}/filters/jobs")
def get_company_application_job_filters(
    company_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    get_verified_company(
        company_id,
        db,
    )

    jobs = (
        db.query(Job)
        .filter(
            Job.company_id == company_id
        )
        .order_by(
            Job.title.asc()
        )
        .all()
    )

    return {
        "company_id": company_id,
        "total": len(jobs),
        "jobs": [
            {
                "id": job.id,
                "title": job.title,
                "status": job.status,
                "location": job.location,
                "work_mode": job.work_mode,
            }
            for job in jobs
        ],
    }


# ============================================================
# AI SHORTLIST
# ============================================================

@router.post("/{company_id}/ai-shortlist")
def ai_shortlist_company_applications(
    company_id: int,
    request: Request,
    force: bool = Query(
        default=False
    ),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    get_verified_company(
        company_id,
        db,
    )

    applications = (
        db.query(
            JobApplication,
            Job,
            User,
            CandidateProfile,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .join(
            User,
            User.id == JobApplication.user_id,
        )
        .outerjoin(
            CandidateProfile,
            CandidateProfile.user_id == User.id,
        )
        .filter(
            Job.company_id == company_id
        )
        .all()
    )

    analyzed_count = 0
    reused_count = 0
    error_count = 0

    results = []

    for (
        application,
        job,
        user,
        profile,
    ) in applications:

        # ----------------------------------------------------
        # REUSE EXISTING MATCH
        # ----------------------------------------------------

        if (
            not force
            and application.ai_analyzed_at
            is not None
        ):

            reused_count += 1

            results.append({
                "application_id": application.id,
                "candidate": user.full_name,
                "job": job.title,
                "score": application.ai_match_score,
                "ai_shortlist": application.ai_shortlist,
                "recommendation": application.ai_recommendation,
                "reused": True,
            })

            continue

        try:

            # ------------------------------------------------
            # FIND LATEST RESUME
            # ------------------------------------------------

            resume = (
                db.query(Resume)
                .filter(
                    Resume.user_id == user.id
                )
                .order_by(
                    Resume.id.desc()
                )
                .first()
            )

            analysis = None

            if resume:

                analysis = (
                    db.query(ResumeAnalysis)
                    .filter(
                        ResumeAnalysis.resume_id == resume.id
                    )
                    .first()
                )

            # ------------------------------------------------
            # NO GEMINI ANALYSIS AVAILABLE
            # ------------------------------------------------

            if not analysis:

                application.ai_match_score = 0.0
                application.ai_shortlist = False

                application.ai_recommendation = (
                    "Resume analysis required"
                )

                application.ai_skill_score = 0.0
                application.ai_experience_score = 0.0
                application.ai_role_score = 0.0
                application.ai_location_score = 0.0
                application.ai_technology_score = 0.0
                application.ai_education_score = 0.0

                application.ai_matched_skills = (
                    json.dumps([])
                )

                application.ai_missing_skills = (
                    json.dumps([])
                )

                application.ai_analyzed_at = (
                    datetime.utcnow()
                )

                error_count += 1

                results.append({
                    "application_id": application.id,
                    "candidate": user.full_name,
                    "job": job.title,
                    "score": 0.0,
                    "ai_shortlist": False,
                    "recommendation": (
                        "Resume analysis required"
                    ),
                    "reused": False,
                })

                continue

            # ------------------------------------------------
            # MATCH GEMINI ANALYSIS WITH JOB
            # ------------------------------------------------

            match = calculate_candidate_match(
                candidate_analysis=analysis,
                candidate_profile=profile,
                candidate_user=user,
                job=job,
            )

            # ------------------------------------------------
            # SAVE SCORE
            # ------------------------------------------------

            application.ai_match_score = (
                match["score"]
            )

            application.ai_shortlist = (
                match["ai_shortlist"]
            )

            application.ai_recommendation = (
                match["recommendation"]
            )

            application.ai_skill_score = (
                match["skill_score"]
            )

            application.ai_experience_score = (
                match["experience_score"]
            )

            application.ai_role_score = (
                match["role_score"]
            )

            application.ai_location_score = (
                match["location_score"]
            )

            application.ai_technology_score = (
                match["technology_score"]
            )

            application.ai_education_score = (
                match["education_score"]
            )

            application.ai_matched_skills = (
                json.dumps(
                    match["matched_skills"]
                )
            )

            application.ai_missing_skills = (
                json.dumps(
                    match["missing_skills"]
                )
            )

            application.ai_analyzed_at = (
                datetime.utcnow()
            )

            analyzed_count += 1

            results.append({
                "application_id": application.id,
                "candidate": user.full_name,
                "job": job.title,
                "score": match["score"],
                "ai_shortlist": match["ai_shortlist"],
                "recommendation": match["recommendation"],
                "matched_skills": match["matched_skills"],
                "missing_skills": match["missing_skills"],
                "reused": False,
            })

        except Exception as exc:

            error_count += 1

            results.append({
                "application_id": application.id,
                "candidate": user.full_name,
                "job": job.title,
                "score": 0.0,
                "ai_shortlist": False,
                "recommendation": "AI matching failed",
                "error": str(exc),
                "reused": False,
            })

    db.commit()

    return {
        "message": (
            "AI candidate matching completed"
        ),
        "company_id": company_id,
        "total_applications": len(applications),
        "analyzed": analyzed_count,
        "reused": reused_count,
        "errors": error_count,
        "results": results,
    }


# ============================================================
# AI SHORTLISTED APPLICATIONS
# ============================================================

@router.get("/{company_id}/ai-shortlisted")
def get_ai_shortlisted_applications(
    company_id: int,
    job_id: int | None = Query(
        default=None
    ),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    get_verified_company(
        company_id,
        db,
    )

    query = (
        db.query(
            JobApplication,
            Job,
            User,
            CandidateProfile,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .join(
            User,
            User.id == JobApplication.user_id,
        )
        .outerjoin(
            CandidateProfile,
            CandidateProfile.user_id == User.id,
        )
        .filter(
            Job.company_id == company_id,
            JobApplication.ai_shortlist == True,
            JobApplication.is_archived == False,
        )
    )

    if job_id is not None:

        query = query.filter(
            JobApplication.job_id == job_id
        )

    results = (
        query
        .order_by(
            JobApplication.ai_match_score.desc()
        )
        .all()
    )

    aptitude_tests_by_application = (
        _fetch_aptitude_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    assessment_sessions_by_application = (
        _fetch_assessment_sessions_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    programming_tests_by_application = (
        _fetch_programming_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    interview_tests_by_application = (
        _fetch_interview_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    # Parity with the main GET /{company_id} listing below -- this endpoint
    # previously stopped at the legacy programming/interview tables, so an
    # AI-shortlisted V2 programming/interview candidate, or ANY SJT/TDA
    # candidate (every non-technical or domain-assessment role), showed none
    # of their actual stage-3/stage-4 results here even though they were
    # fully visible on the main Applicants list.
    programming_v2_assessments_by_application = (
        _fetch_programming_v2_assessments_by_application(
            db,
            [application.id for application, _, _, _ in results],
        )
    )

    interview_v2_sessions_by_application = (
        _fetch_interview_v2_sessions_by_application(
            db,
            [application.id for application, _, _, _ in results],
        )
    )

    sjt_tests_by_application = (
        _fetch_sjt_tests_by_application(
            db,
            [application.id for application, _, _, _ in results],
        )
    )

    tda_assessments_by_application = (
        _fetch_tda_assessments_by_application(
            db,
            [application.id for application, _, _, _ in results],
        )
    )

    resume_availability_by_application = _fetch_resume_availability_by_application(
        db,
        [application for application, _, _, _ in results],
    )

    programming_assignments_by_application = (
        batch_get_assignments(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
            "programming",
        )
    )

    interview_assignments_by_application = (
        batch_get_assignments(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
            "ai_interview",
        )
    )

    applications = []

    for (
        application,
        job,
        user,
        profile,
    ) in results:

        applications.append(
            application_summary_response(
                application,
                job,
                user,
                profile,
                aptitude_tests_by_application.get(
                    application.id
                ),
                assessment_sessions_by_application.get(
                    application.id
                ),
                programming_tests_by_application.get(
                    application.id
                ),
                interview_tests_by_application.get(
                    application.id
                ),
                programming_assignment=programming_assignments_by_application.get(
                    application.id
                ),
                interview_assignment=interview_assignments_by_application.get(
                    application.id
                ),
                programming_v2_assessment=programming_v2_assessments_by_application.get(
                    application.id
                ),
                interview_v2_session=interview_v2_sessions_by_application.get(
                    application.id
                ),
                sjt_test=sjt_tests_by_application.get(
                    application.id
                ),
                tda_assessment=tda_assessments_by_application.get(
                    application.id
                ),
                resume_available=resume_availability_by_application.get(
                    application.id, False
                ),
            )
        )

    return {
        "company_id": company_id,
        "total": len(applications),
        "applications": applications,
    }


# ============================================================
# LIST COMPANY APPLICATIONS
# ============================================================

@router.get("/{company_id}")
def get_company_applications(
    company_id: int,

    job_id: int | None = Query(
        default=None
    ),

    status: str | None = Query(
        default=None
    ),

    shortlisted: bool | None = Query(
        default=None
    ),

    ai_shortlisted: bool | None = Query(
        default=None
    ),

    search: str | None = Query(
        default=None
    ),

    archived: bool = Query(
        default=False,
        description=(
            "False (default) returns the active applicant "
            "list only. True returns archived applications "
            "only — the two views are mutually exclusive, "
            "matching how archiving is meant to be used."
        ),
    ),

    view: str | None = Query(
        default=None,
        description=(
            "Omitted (default): no status filter at all — the "
            "original, unchanged behavior, so existing callers "
            "(company-dashboard.js's own counts, etc.) are never "
            "affected by this. 'active': applications still being "
            "processed or assessed. 'history': applications that "
            "have already reached a final outcome (rejected, hired, "
            "or any existing assessment-failure/expiry/termination "
            "status) — see TERMINAL_APPLICATION_STATUSES. "
            "Independent of, and composes with, the pre-existing "
            "`archived` filter above — archiving is a separate, "
            "HR-only manual visibility toggle this never redefines."
        ),
    ),

    current_company: Company = Depends(get_current_company),

    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    get_verified_company(
        company_id,
        db,
    )

    if view is not None and view not in ("active", "history"):

        raise HTTPException(
            status_code=400,
            detail="Invalid view. Allowed values: active, history.",
        )

    query = (
        db.query(
            JobApplication,
            Job,
            User,
            CandidateProfile,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .join(
            User,
            User.id == JobApplication.user_id,
        )
        .outerjoin(
            CandidateProfile,
            CandidateProfile.user_id == User.id,
        )
        .filter(
            Job.company_id == company_id,
            JobApplication.is_archived == archived,
        )
    )

    if view == "history":

        query = query.filter(
            JobApplication.status.in_(
                TERMINAL_APPLICATION_STATUSES
            )
        )

    elif view == "active":

        query = query.filter(
            JobApplication.status.notin_(
                TERMINAL_APPLICATION_STATUSES
            )
        )

    # view is None: no status filter at all — identical to this
    # endpoint's original, unfiltered behavior.

    # --------------------------------------------------------
    # JOB FILTER
    # --------------------------------------------------------

    if job_id is not None:

        query = query.filter(
            JobApplication.job_id == job_id
        )

    # --------------------------------------------------------
    # STATUS FILTER
    # --------------------------------------------------------

    if status:

        status_value = (
            status.strip().lower()
        )

        if status_value not in RECRUITMENT_STATUSES:

            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid status. Allowed values: "
                    + ", ".join(
                        sorted(
                            RECRUITMENT_STATUSES
                        )
                    )
                ),
            )

        query = query.filter(
            JobApplication.status == status_value
        )

    # --------------------------------------------------------
    # RECRUITER SHORTLIST FILTER
    # --------------------------------------------------------

    if shortlisted is not None:

        query = query.filter(
            JobApplication.recruiter_shortlisted
            == shortlisted
        )

    # --------------------------------------------------------
    # AI SHORTLIST FILTER
    # --------------------------------------------------------

    if ai_shortlisted is not None:

        query = query.filter(
            JobApplication.ai_shortlist
            == ai_shortlisted
        )

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if search:

        search_value = (
            f"%{search.strip()}%"
        )

        query = query.filter(
            (
                User.full_name.ilike(
                    search_value
                )
            )
            |
            (
                User.username.ilike(
                    search_value
                )
            )
            |
            (
                Job.title.ilike(
                    search_value
                )
            )
        )

    # --------------------------------------------------------
    # ORDER
    # --------------------------------------------------------

    results = (
        query
        .order_by(
            JobApplication.applied_date.desc()
        )
        .all()
    )

    aptitude_tests_by_application = (
        _fetch_aptitude_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    assessment_sessions_by_application = (
        _fetch_assessment_sessions_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    programming_tests_by_application = (
        _fetch_programming_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    programming_v2_assessments_by_application = (
        _fetch_programming_v2_assessments_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    interview_tests_by_application = (
        _fetch_interview_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    interview_v2_sessions_by_application = (
        _fetch_interview_v2_sessions_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    sjt_tests_by_application = (
        _fetch_sjt_tests_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    tda_assessments_by_application = (
        _fetch_tda_assessments_by_application(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
        )
    )

    programming_assignments_by_application = (
        batch_get_assignments(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
            "programming",
        )
    )

    interview_assignments_by_application = (
        batch_get_assignments(
            db,
            [
                application.id
                for application, _, _, _ in results
            ],
            "ai_interview",
        )
    )

    resume_availability_by_application = _fetch_resume_availability_by_application(
        db,
        [application for application, _, _, _ in results],
    )

    applications = [
        application_summary_response(
            application,
            job,
            user,
            profile,
            aptitude_tests_by_application.get(
                application.id
            ),
            assessment_sessions_by_application.get(
                application.id
            ),
            programming_tests_by_application.get(
                application.id
            ),
            interview_tests_by_application.get(
                application.id
            ),
            programming_assignment=programming_assignments_by_application.get(
                application.id
            ),
            interview_assignment=interview_assignments_by_application.get(
                application.id
            ),
            programming_v2_assessment=programming_v2_assessments_by_application.get(
                application.id
            ),
            interview_v2_session=interview_v2_sessions_by_application.get(
                application.id
            ),
            sjt_test=sjt_tests_by_application.get(
                application.id
            ),
            tda_assessment=tda_assessments_by_application.get(
                application.id
            ),
            resume_available=resume_availability_by_application.get(
                application.id, False
            ),
        )
        for (
            application,
            job,
            user,
            profile,
        ) in results
    ]

    return {
        "company_id": company_id,
        "view": view,
        "archived": archived,
        "total": len(applications),
        "applications": applications,
    }


# ============================================================
# CANDIDATE TRACKING (every level, from "invited" through the
# whole assessment pipeline)
#
# Unlike the applicants list above (which only ever shows
# candidates who already have a JobApplication row), this covers
# every CandidateInvite for the company -- including ones nobody
# has clicked yet, ones scheduled for later, and ones whose
# deadline passed with no response -- so HR has one place that
# answers "where is every candidate I've invited, right now".
#
# Registered here, BEFORE the generic /{company_id}/{application_id}
# detail route below, so "/candidate-tracking" isn't swallowed by
# that route matching it as a literal application_id path segment
# (FastAPI/Starlette matches routes in registration order).
# ============================================================

APPLICATION_STATUS_LEVEL_LABELS = {
    "shortlisted": "Shortlisted — Assessment Not Started",
    "aptitude_pending": "Aptitude Test — In Progress",
    "aptitude_passed": "Aptitude Test — Passed",
    "aptitude_failed": "Aptitude Test — Failed",
    "aptitude_expired": "Aptitude Test — Expired",
    "aptitude_terminated": "Aptitude Test — Terminated (Violation)",
    "programming_pending": "Programming Test — In Progress",
    "programming_passed": "Programming Test — Passed",
    "programming_failed": "Programming Test — Failed",
    "programming_expired": "Programming Test — Expired",
    "programming_terminated": "Programming Test — Terminated (Violation)",
    "sjt_pending": "Situational Judgment Test — In Progress",
    "sjt_passed": "Situational Judgment Test — Passed",
    "sjt_failed": "Situational Judgment Test — Failed",
    "sjt_expired": "Situational Judgment Test — Expired",
    "sjt_terminated": "Situational Judgment Test — Terminated (Violation)",
    "tda_pending": "Technical Assessment — In Progress",
    "tda_passed": "Technical Assessment — Passed",
    "tda_failed": "Technical Assessment — Failed",
    "tda_expired": "Technical Assessment — Expired",
    "tda_terminated": "Technical Assessment — Terminated (Violation)",
    "interview_pending": "AI Interview — In Progress",
    "interview_passed": "AI Interview — Passed",
    "interview_failed": "AI Interview — Failed",
    "interview_expired": "AI Interview — Expired",
    "interview_terminated": "AI Interview — Terminated (Violation)",
    "interview": "AI Interview",
    "hired": "Hired",
    "rejected": "Rejected",
}


def _candidate_level_for_application(application: JobApplication, session_record) -> tuple[str, str]:
    """Returns (level_label, level_category) for a claimed candidate who
    has a JobApplication. level_category is a coarse bucket the frontend
    uses for badge coloring."""

    status = application.status

    if status != "shortlisted":

        label = APPLICATION_STATUS_LEVEL_LABELS.get(status, status.replace("_", " ").title())

        if status in ("hired",):
            category = "success"
        elif status in ("rejected",) or status.endswith(("_failed", "_expired", "_terminated")):
            category = "danger"
        elif status.endswith("_passed"):
            category = "success"
        else:
            category = "in_progress"

        return label, category

    # status == "shortlisted": the candidate hasn't reached the aptitude
    # stage yet -- AssessmentSession.current_stage says whether they're
    # mid self-introduction or haven't opened the assessment at all.
    if session_record and session_record.current_stage == "self_intro":

        if session_record.self_intro_status == "completed":
            return "Self-Introduction Done — Starting Aptitude", "in_progress"

        return "Self-Introduction — In Progress", "in_progress"

    return "Shortlisted — Not Started", "not_started"


def _reference_photo_urls(db: Session, application_ids) -> dict[int, str]:
    """application_id -> URL of the candidate's photo: the picture taken when the camera first came on, before the self introduction
    (else the earliest stage-start picture). One query for the whole list. The URL needs the company session cookie."""

    if not application_ids:
        return {}

    rows = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id.in_(list(application_ids)), AssessmentSnapshot.reason == "identity_reference")
        .order_by(AssessmentSnapshot.created_at.asc(), AssessmentSnapshot.id.asc())
        .all()
    )

    chosen: dict[int, AssessmentSnapshot] = {}

    for row in rows:
        current = chosen.get(row.application_id)
        if current is None or (row.assessment_type == "self_intro" and current.assessment_type != "self_intro"):
            chosen[row.application_id] = row

    return {application_id: f"/api/assessment/snapshots/file/{row.id}" for application_id, row in chosen.items()}


def _scorecards_by_application(db: Session, application_ids) -> dict[int, dict]:
    """application_id -> {overall_percentage, per-stage percentages, interview result, integrity digest}. A handful of queries for the whole list."""

    from backend.app.services.assessment.integrity_digest import digest, overall_percentage

    application_ids = list(application_ids)

    if not application_ids:
        return {}

    aptitude = _fetch_aptitude_tests_by_application(db, application_ids)
    programming_v2 = _fetch_programming_v2_assessments_by_application(db, application_ids)
    programming_v1 = _fetch_programming_tests_by_application(db, application_ids)
    sjt = _fetch_sjt_tests_by_application(db, application_ids)
    tda = _fetch_tda_assessments_by_application(db, application_ids)
    interviews = _fetch_interview_v2_sessions_by_application(db, application_ids)

    events_by_application: dict[int, list] = {}
    for event in db.query(AssessmentIntegrityEvent).filter(AssessmentIntegrityEvent.application_id.in_(application_ids)).all():
        events_by_application.setdefault(event.application_id, []).append(event)

    def percent(summary):
        return summary["percentage"] if summary and summary.get("percentage") is not None else None

    cards = {}

    for application_id in application_ids:
        aptitude_percentage = percent(aptitude_summary_response(aptitude.get(application_id)))

        stage3_percentage = None
        for summary in (
            programming_v2_summary_response(programming_v2.get(application_id)),
            programming_summary_response(programming_v1.get(application_id)),
            sjt_summary_response(sjt.get(application_id)),
            tda_summary_response(tda.get(application_id)),
        ):
            if percent(summary) is not None:
                stage3_percentage = percent(summary)
                break

        interview = interview_v2_summary_response(interviews.get(application_id))

        cards[application_id] = {
            "overall_percentage": overall_percentage([aptitude_percentage, stage3_percentage]),
            "aptitude_percentage": aptitude_percentage,
            "stage3_percentage": stage3_percentage,
            "interview_passed": interview["passed"] if interview else None,
            "integrity": digest(events_by_application.get(application_id, [])),
        }

    return cards


@router.get("/{company_id}/candidate-tracking")
def get_candidate_tracking(
    company_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    get_verified_company(company_id, db)

    invites = (
        db.query(CandidateInvite)
        .filter(CandidateInvite.company_id == company_id)
        .order_by(CandidateInvite.created_at.desc())
        .all()
    )

    now = datetime.utcnow()

    job_ids = {invite.job_id for invite in invites}
    jobs_by_id = {
        job.id: job
        for job in db.query(Job).filter(Job.id.in_(job_ids)).all()
    } if job_ids else {}

    application_keys = [
        (invite.job_id, invite.claimed_user_id)
        for invite in invites
        if invite.claimed_user_id
    ]

    applications_by_key = {}

    if application_keys:

        claimed_job_ids = {job_id for job_id, _ in application_keys}
        claimed_user_ids = {user_id for _, user_id in application_keys}

        candidate_applications = (
            db.query(JobApplication)
            .filter(
                JobApplication.job_id.in_(claimed_job_ids),
                JobApplication.user_id.in_(claimed_user_ids),
            )
            .all()
        )

        applications_by_key = {
            (application.job_id, application.user_id): application
            for application in candidate_applications
        }

    application_ids = [application.id for application in applications_by_key.values()]

    sessions_by_application = (
        _fetch_assessment_sessions_by_application(db, application_ids)
        if application_ids
        else {}
    )

    photo_urls = _reference_photo_urls(db, application_ids)
    scorecards = _scorecards_by_application(db, application_ids)

    results = []

    for invite in invites:

        job = jobs_by_id.get(invite.job_id)
        application = applications_by_key.get((invite.job_id, invite.claimed_user_id))

        is_deadline_passed = now > invite.expires_at

        if application:

            session_record = sessions_by_application.get(application.id)
            level, level_category = _candidate_level_for_application(application, session_record)
            application_id = application.id

        elif invite.status == "expired" or is_deadline_passed:

            level = "Expired — Never Started"
            level_category = "expired"
            application_id = None

        elif invite.scheduled_at and invite.scheduled_at > now:

            level = f"Scheduled for {invite.scheduled_at.strftime('%b %d, %Y %I:%M %p UTC')}"
            level_category = "scheduled"
            application_id = None

        else:

            level = "Invited — Not Started"
            level_category = "invited"
            application_id = None

        results.append({
            "invite_id": invite.id,
            "candidate_name": invite.candidate_name,
            "candidate_email": invite.candidate_email,
            "job_title": job.title if job else None,
            "invite_status": invite.status,
            "scheduled_at": invite.scheduled_at.isoformat() if invite.scheduled_at else None,
            "expires_at": invite.expires_at.isoformat(),
            "created_at": invite.created_at.isoformat(),
            "level": level,
            "level_category": level_category,
            "application_id": application_id,
            "photo_url": photo_urls.get(application_id) if application_id else None,
            "scorecard": scorecards.get(application_id) if application_id else None,
        })

    return {"candidates": results}


# ============================================================
# DELETE APPLICANT (permanent -- distinct from the existing
# archive/restore toggle, which only hides an application from
# the active list without ever destroying data)
#
# Removes the JobApplication and every row keyed off it across
# the assessment pipeline (aptitude/programming/interview v1+v2,
# integrity events, video/snapshot records, consent, status
# history, answers). Does NOT delete the candidate's User
# account or Resume rows -- those are the candidate's own data,
# independent of any one job/company, and may be attached to
# other applications elsewhere on the platform.
# ============================================================

def _delete_job_application_cascade(db: Session, application: JobApplication) -> None:

    application_id = application.id

    # Grandchildren first: rows keyed off an intermediate v2 session/
    # assessment id rather than application_id directly.
    programming_assessment_ids = [
        row[0]
        for row in db.query(ProgrammingAssessmentV2.id)
        .filter(ProgrammingAssessmentV2.application_id == application_id)
        .all()
    ]

    if programming_assessment_ids:
        db.query(ProgrammingSubmissionV2).filter(
            ProgrammingSubmissionV2.assessment_id.in_(programming_assessment_ids)
        ).delete(synchronize_session=False)

    interview_session_ids = [
        row[0]
        for row in db.query(AIInterviewSessionV2.id)
        .filter(AIInterviewSessionV2.application_id == application_id)
        .all()
    ]

    if interview_session_ids:
        db.query(AIInterviewTurnV2).filter(
            AIInterviewTurnV2.session_id.in_(interview_session_ids)
        ).delete(synchronize_session=False)

    # Situational Judgment / Technical Domain results were missing here (this function predates
    # them), so deleting an applicant or a whole company left orphaned rows behind -- which a
    # later application reusing that id could even inherit.
    from backend.app.models.situational_judgment_test import SituationalJudgmentTest
    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    # Direct children (all keyed by application_id).
    for model in (
        SituationalJudgmentTest,
        TechnicalDomainAssessment,
        ApplicationStatusHistory,
        ApplicationAnswer,
        AssessmentConsent,
        AssessmentSnapshot,
        AssessmentVideoRecording,
        AssessmentIntegrityEvent,
        AssessmentAssignment,
        AssessmentSession,
        AptitudeTest,
        ProgrammingTest,
        InterviewTest,
        ProgrammingAssessmentV2,
        AIInterviewSessionV2,
    ):
        db.query(model).filter(model.application_id == application_id).delete(
            synchronize_session=False
        )

    db.delete(application)


@router.delete("/{company_id}/{application_id}")
def delete_applicant(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    application, _, _, _ = get_company_application(company_id, application_id, db)

    _delete_job_application_cascade(db, application)

    db.commit()

    return {"message": "Applicant deleted successfully."}


# ============================================================
# APPLICATION DETAIL
# ============================================================

@router.get(
    "/{company_id}/{application_id}"
)
def get_application_detail(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    answer_rows = (
        db.query(
            ApplicationAnswer,
            JobQuestion,
        )
        .outerjoin(
            JobQuestion,
            JobQuestion.id == ApplicationAnswer.question_id,
        )
        .filter(
            ApplicationAnswer.application_id
            == application_id
        )
        .order_by(
            JobQuestion.display_order.asc()
        )
        .all()
    )

    answers = [
        answer_response(
            answer,
            question,
        )
        for answer, question
        in answer_rows
    ]

    aptitude_test = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.application_id
            == application.id
        )
        .first()
    )

    assessment_session = (
        db.query(AssessmentSession)
        .filter(
            AssessmentSession.application_id
            == application.id
        )
        .first()
    )

    programming_test = (
        db.query(ProgrammingTest)
        .filter(
            ProgrammingTest.application_id
            == application.id
        )
        .first()
    )

    interview_test = (
        db.query(InterviewTest)
        .filter(
            InterviewTest.application_id
            == application.id
        )
        .first()
    )

    programming_assignment = get_assignment(
        db, application.id, "programming"
    )

    interview_assignment = get_assignment(
        db, application.id, "ai_interview"
    )

    programming_v2_assessment = (
        _fetch_programming_v2_assessments_by_application(
            db, [application.id],
        ).get(application.id)
    )

    interview_v2_session = (
        _fetch_interview_v2_sessions_by_application(
            db, [application.id],
        ).get(application.id)
    )

    sjt_test = (
        _fetch_sjt_tests_by_application(
            db, [application.id],
        ).get(application.id)
    )

    tda_assessment = (
        _fetch_tda_assessments_by_application(
            db, [application.id],
        ).get(application.id)
    )

    history_rows = (
        db.query(ApplicationStatusHistory)
        .filter(
            ApplicationStatusHistory.application_id
            == application.id
        )
        .order_by(
            ApplicationStatusHistory.created_at.asc()
        )
        .all()
    )

    status_history = [
        {
            "from_status": row.from_status,
            "to_status": row.to_status,
            "changed_by_type": row.changed_by_type,
            "note": row.note,
            "created_at": (
                row.created_at.isoformat()
                if row.created_at
                else None
            ),
        }
        for row in history_rows
    ]

    response = application_summary_response(
        application,
        job,
        user,
        profile,
        aptitude_test,
        assessment_session,
        programming_test,
        interview_test,
        programming_assignment=programming_assignment,
        interview_assignment=interview_assignment,
        programming_v2_assessment=programming_v2_assessment,
        interview_v2_session=interview_v2_session,
        sjt_test=sjt_test,
        tda_assessment=tda_assessment,
        resume_available=resolve_application_resume_path(db, application) is not None,
    )

    response["application_answers"] = answers
    response["status_history"] = status_history

    return response


# ============================================================
# APPLICANT RESUME FILE (inline PDF viewer / DOCX download)
# ============================================================

@router.get("/{company_id}/{application_id}/resume-file")
def get_application_resume_file(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    application, _, user, _ = get_company_application(
        company_id, application_id, db,
    )

    file_path = resolve_application_resume_path(db, application)

    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="No resume found for this applicant.")

    extension = file_path.rsplit(".", 1)[-1].lower()
    media_type = "application/pdf" if extension == "pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    download_name = f"{user.full_name or 'candidate'}-resume.{extension}"

    return FileResponse(
        file_path,
        media_type=media_type,
        filename=download_name,
        content_disposition_type="inline" if extension == "pdf" else "attachment",
    )


# ============================================================
# PROGRAMMING V2 (MULTI-LANGUAGE) — HR RESULT VIEW (Programming
# Engine V2, Live Integration task)
#
# ADDITIVE, read-only, separate from get_application_detail()
# above — never modifies that endpoint's existing response
# contract. Same company-ownership check (get_company_application)
# as every other HR-facing endpoint in this file, so HR can never
# view another company's applications. Reconstructs the HR
# report from the persisted ProgrammingAssessmentV2 snapshot +
# ProgrammingSubmissionV2 rows — hidden test stdin/expected_stdout
# is never present on either (see programming_scoring_v2.py's own
# candidate-safe-by-construction guarantee), so this endpoint
# cannot leak it even by omission.
# ============================================================

@router.get("/{company_id}/{application_id}/programming/v2")
def get_application_programming_v2_result(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)

    application, job, user, profile = get_company_application(
        company_id, application_id, db,
    )

    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
    from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
    from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment:

        raise HTTPException(
            status_code=404,
            detail="No V2 programming assessment exists for this application.",
        )

    question_set = ProgrammingQuestionSetV2.model_validate_json(assessment.question_set_json)

    submission_rows = (
        db.query(ProgrammingSubmissionV2)
        .filter(
            ProgrammingSubmissionV2.assessment_id == assessment.id,
            ProgrammingSubmissionV2.submission_kind == "submit",
        )
        .all()
    )

    submissions_by_question = {
        row.question_id: {
            "language": row.language,
            "source_code": row.source_code,
            "earned_points": row.earned_points,
            "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        }
        for row in submission_rows
    }

    questions_by_id = {q.question_id: q for q in question_set.main_questions}

    question_summaries = [
        {
            "question_id": qid,
            "title": questions_by_id[qid].title if qid in questions_by_id else None,
            "technology": questions_by_id[qid].technology if qid in questions_by_id else None,
            **submission_data,
        }
        for qid, submission_data in submissions_by_question.items()
    ]

    return {
        "application_id": application.id,
        "candidate_name": user.full_name if hasattr(user, "full_name") else None,
        "job_title": job.title,
        "status": assessment.status,
        "score": assessment.score,
        "total_score": assessment.total_score,
        "passed": assessment.passed,
        "percentage": (
            round(100.0 * assessment.score / assessment.total_score, 2)
            if assessment.score is not None and assessment.total_score
            else None
        ),
        "generated_by": assessment.generated_by,
        "allowed_languages": json.loads(assessment.allowed_languages_json),
        "questions": question_summaries,
    }


# ============================================================
# STATUS UPDATE
# ============================================================

@router.patch(
    "/{company_id}/{application_id}/status"
)
def update_application_status(
    company_id: int,
    application_id: int,
    payload: ApplicationStatusUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    status_value = (
        payload.status.strip().lower()
    )

    if status_value not in RECRUITMENT_STATUSES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid status. Allowed values: "
                + ", ".join(
                    sorted(
                        RECRUITMENT_STATUSES
                    )
                )
            ),
        )

    # --------------------------------------------------------
    # RECORD TRANSITION
    #
    # No-op (same status) calls are handled safely inside
    # record_status_change — no duplicate history row is
    # created, but updated_date is still refreshed below to
    # match the existing behavior of this endpoint.
    # --------------------------------------------------------

    history_record = record_status_change(
        db,
        application,
        to_status=status_value,
        changed_by_type="hr",
        changed_by_id=company_id,
    )

    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(application)

    # --------------------------------------------------------
    # CANDIDATE EMAIL NOTIFICATION
    #
    # Attempted only AFTER the status change above has already
    # been committed. notify_status_change never raises and
    # only sends for an actual "rejected" transition (it is a
    # no-op when history_record is None, e.g. the status was
    # already "rejected"), so this can never affect the
    # response below.
    # --------------------------------------------------------

    notify_status_change(
        db,
        application,
        user,
        job,
        company_id,
        history_record,
    )

    return {
        "message": (
            "Application status updated successfully"
        ),
        "application_id": application.id,
        "status": application.status,
    }


# ============================================================
# RECRUITER SHORTLIST
# ============================================================

@router.patch(
    "/{company_id}/{application_id}/shortlist"
)
def update_recruiter_shortlist(
    company_id: int,
    application_id: int,
    payload: RecruiterShortlistUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    application.recruiter_shortlisted = (
        payload.shortlisted
    )

    history_record = None

    if payload.shortlisted:

        if application.status in {
            "new",
            "reviewing",
        }:

            history_record = record_status_change(
                db,
                application,
                to_status="shortlisted",
                changed_by_type="hr",
                changed_by_id=company_id,
            )

    else:

        if application.status == "shortlisted":

            history_record = record_status_change(
                db,
                application,
                to_status="reviewing",
                changed_by_type="hr",
                changed_by_id=company_id,
            )

    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(application)

    # --------------------------------------------------------
    # CANDIDATE EMAIL NOTIFICATION
    #
    # Attempted only AFTER the status change above has already
    # been committed. notify_status_change only sends for a
    # "shortlisted" to_status, so toggling shortlist OFF (which
    # transitions to "reviewing") never sends an email, and it
    # never raises, so this can never affect the response below.
    # --------------------------------------------------------

    notify_status_change(
        db,
        application,
        user,
        job,
        company_id,
        history_record,
    )

    return {
        "message": (
            "Candidate shortlisted"
            if payload.shortlisted
            else "Candidate removed from shortlist"
        ),
        "application_id": application.id,
        "recruiter_shortlisted": (
            application.recruiter_shortlisted
        ),
        "status": application.status,
    }


# ============================================================
# RECRUITER NOTES
# ============================================================

@router.patch(
    "/{company_id}/{application_id}/notes"
)
def update_recruiter_notes(
    company_id: int,
    application_id: int,
    payload: RecruiterNotesUpdate,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    application.recruiter_notes = (
        payload.recruiter_notes
    )

    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(application)

    return {
        "message": (
            "Recruiter notes updated successfully"
        ),
        "application_id": application.id,
        "recruiter_notes": application.recruiter_notes,
    }


# ============================================================
# ASSIGN APTITUDE TEST (HR SENDS THE EXISTING TEST)
#
# Reuses the existing aptitude test model, Gemini generator,
# deterministic fallback bank and answer-sanitization exactly
# as implemented for the candidate-owned aptitude endpoints —
# this only adds the assignment/24-hour-deadline layer on top.
# The candidate later calls the existing
# POST /api/aptitude-tests/application/{id}/start endpoint to
# begin the test that is created here.
# ============================================================

@router.post(
    "/{company_id}/{application_id}/aptitude"
)
def assign_aptitude_test(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    # --------------------------------------------------------
    # NO DUPLICATE ACTIVE ASSIGNMENTS
    # --------------------------------------------------------

    existing_test = (
        db.query(AptitudeTest)
        .filter(
            AptitudeTest.application_id == application.id
        )
        .first()
    )

    if existing_test:

        raise HTTPException(
            status_code=409,
            detail=(
                "An aptitude test has already been assigned "
                "to this candidate for this application."
            ),
            headers={
                "X-Aptitude-Status": existing_test.status
            },
        )

    # --------------------------------------------------------
    # ELIGIBILITY
    #
    # Same eligibility rule the candidate-owned start endpoint
    # used to enforce itself — reused here as the single point
    # where a NEW aptitude assignment can be created.
    # --------------------------------------------------------

    if application.status != APTITUDE_START_STATUS:

        raise HTTPException(
            status_code=400,
            detail=(
                "The candidate must be shortlisted before an "
                "aptitude test can be sent. Current status: "
                f"{application.status}."
            ),
        )

    # --------------------------------------------------------
    # GENERATE QUESTIONS NOW (Gemini-first, deterministic
    # fallback on ANY failure) — identical logic to the
    # pre-existing candidate-owned start endpoint, just moved
    # to assignment time so the 24-hour clock can start
    # immediately.
    # --------------------------------------------------------

    latest_resume = get_active_resume(db, application.user_id)

    resume_analysis = None

    if latest_resume:

        resume_analysis = (
            db.query(ResumeAnalysis)
            .filter(
                ResumeAnalysis.resume_id == latest_resume.id
            )
            .first()
        )

    # Groq first, then the pool of earlier Groq questions, then parametric questions:
    # a different set for every candidate at every step (never the old identical bank).
    questions, question_source = build_aptitude_questions(
        job,
        user,
        profile,
        resume_analysis,
    )

    # --------------------------------------------------------
    # CREATE ASSIGNMENT
    #
    # expires_at is always assigned_at + 24 hours, computed
    # here on the backend — the frontend never supplies or
    # controls this deadline.
    # --------------------------------------------------------

    now = datetime.utcnow()
    expires_at = now + timedelta(hours=APTITUDE_DEADLINE_HOURS)

    test = AptitudeTest(
        application_id=application.id,
        questions=json.dumps(questions),
        answers=None,
        score=None,
        total_questions=len(questions),
        passed=None,
        status="assigned",
        question_source=question_source,
        assigned_at=now,
        expires_at=expires_at,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(test)
    db.flush()

    record_status_change(
        db,
        application,
        to_status="aptitude_pending",
        changed_by_type="hr",
        changed_by_id=company_id,
        note=(
            "Aptitude test assigned by recruiter. "
            f"Deadline: {expires_at.isoformat()}."
        ),
    )

    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(test)
    db.refresh(application)

    remaining_seconds = max(
        0,
        int((test.expires_at - datetime.utcnow()).total_seconds()),
    )

    return {
        "message": "Aptitude test assigned successfully.",
        "application_id": application.id,
        "aptitude_test_id": test.id,
        "candidate_id": application.user_id,
        "job_id": application.job_id,
        "status": test.status,
        "question_source": test.question_source,
        "assigned_at": test.assigned_at.isoformat(),
        "expires_at": test.expires_at.isoformat(),
        "remaining_seconds": remaining_seconds,
        "application_status": application.status,
    }


# ============================================================
# ASSIGN PROGRAMMING ASSESSMENT (Task 4D-F1)
#
# HR-controlled gate: this endpoint does NOT generate any
# questions or create a ProgrammingTest row — it only records
# that HR has enabled the stage for this application. The
# candidate's own existing "start" flow (assessment_sessions.py
# -> start_programming_assessment()) still performs the actual
# generation, and now additionally checks this assignment
# exists before it will do so. This mirrors assign_aptitude_test
# above in spirit (an explicit HR-facing "send" action, same
# ownership/CSRF checks, same duplicate-assignment 409), but is
# much lighter since it owns no test content.
# ============================================================

@router.post(
    "/{company_id}/{application_id}/programming/assign"
)
def assign_programming_assessment(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    existing_assignment = get_assignment(
        db, application.id, "programming"
    )

    if existing_assignment:

        raise HTTPException(
            status_code=409,
            detail=(
                "The programming assessment has already been "
                "assigned to this candidate for this application."
            ),
        )

    if application.status != PROGRAMMING_START_STATUS:

        raise HTTPException(
            status_code=400,
            detail=(
                "The candidate must have passed the aptitude "
                "assessment before the programming assessment can "
                f"be assigned. Current status: {application.status}."
            ),
        )

    # Never assign a programming assessment the blueprint cannot produce (same gate as the automatic pipeline)
    from backend.app.services.assessment.programming_integration_v2 import programming_assessment_feasible

    feasible, reason = programming_assessment_feasible(db, application)

    if not feasible:

        raise HTTPException(
            status_code=409,
            detail=f"A programming assessment cannot be assigned: {reason}.",
        )

    assignment = create_assignment(
        db,
        application.id,
        "programming",
        assigned_by_id=company_id,
    )

    return {
        "message": "Programming assessment assigned successfully.",
        "application_id": application.id,
        "assessment_type": "programming",
        "assigned_at": assignment.assigned_at.isoformat(),
    }


# ============================================================
# ASSIGN AI INTERVIEW (Task 4D-F1)
#
# Mirrors assign_programming_assessment() exactly, one stage
# later: records HR's decision to enable the AI interview for
# this application. The candidate's existing "start" flow
# (start_interview_assessment()) still owns generation and now
# additionally checks this assignment exists first.
# ============================================================

@router.post(
    "/{company_id}/{application_id}/interview/assign"
)
def assign_interview_assessment(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    existing_assignment = get_assignment(
        db, application.id, "ai_interview"
    )

    if existing_assignment:

        raise HTTPException(
            status_code=409,
            detail=(
                "The AI interview has already been assigned to "
                "this candidate for this application."
            ),
        )

    if application.status != INTERVIEW_START_STATUS:

        raise HTTPException(
            status_code=400,
            detail=(
                "The candidate must have passed the programming "
                "assessment before the AI interview can be "
                f"assigned. Current status: {application.status}."
            ),
        )

    assignment = create_assignment(
        db,
        application.id,
        "ai_interview",
        assigned_by_id=company_id,
    )

    return {
        "message": "AI interview assigned successfully.",
        "application_id": application.id,
        "assessment_type": "ai_interview",
        "assigned_at": assignment.assigned_at.isoformat(),
    }


# ============================================================
# ARCHIVE APPLICATION (HR visibility only — never a deletion)
#
# Archiving never touches JobApplication.status, never writes
# to application_status_history, and never removes any related
# row (aptitude test, answers, candidate profile, resume). It
# only flips is_archived so the application drops out of the
# default active list while remaining fully queryable via
# ?archived=true and fully intact for reporting/history.
# ============================================================

@router.put(
    "/{company_id}/{application_id}/archive"
)
def archive_application(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    if application.is_archived:

        raise HTTPException(
            status_code=409,
            detail="This application is already archived.",
        )

    application.is_archived = True
    application.archived_at = datetime.utcnow()
    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(application)

    return {
        "message": "Application archived successfully.",
        "application_id": application.id,
        "is_archived": application.is_archived,
        "archived_at": application.archived_at.isoformat(),
    }


# ============================================================
# RESTORE APPLICATION
# ============================================================

@router.put(
    "/{company_id}/{application_id}/restore"
)
def restore_application(
    company_id: int,
    application_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    (
        application,
        job,
        user,
        profile,
    ) = get_company_application(
        company_id,
        application_id,
        db,
    )

    if not application.is_archived:

        raise HTTPException(
            status_code=409,
            detail="This application is not archived.",
        )

    application.is_archived = False
    application.archived_at = None
    application.updated_date = datetime.utcnow()

    db.commit()
    db.refresh(application)

    return {
        "message": "Application restored successfully.",
        "application_id": application.id,
        "is_archived": application.is_archived,
    }
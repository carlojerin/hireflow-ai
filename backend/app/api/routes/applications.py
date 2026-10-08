import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.services.resume.active_resume import get_active_resume
from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.job import Job
from backend.app.models.job_question import JobQuestion
from backend.app.models.job_application import JobApplication
from backend.app.models.application_answer import ApplicationAnswer
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.application_status_history import (
    ApplicationStatusHistory,
)
from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.interview_test import InterviewTest
from backend.app.models.assessment_session import AssessmentSession

from backend.app.services.assessment.assignment_service import (
    batch_get_assignments,
)
from backend.app.services.application_status_history import (
    TERMINAL_APPLICATION_STATUSES,
)

from backend.app.services.ai_candidate_matching import (
    calculate_candidate_match,
)

from backend.app.api.deps import get_current_user, require_csrf, require_self


router = APIRouter(
    prefix="/api/applications",
    tags=["Applications"],
)


# ============================================================
# AI MATCH ELIGIBILITY
#
# A candidate may only apply to a job if their AI match score
# for that job is at least this threshold. This is separate
# from AI_SHORTLIST_THRESHOLD in ai_candidate_matching.py,
# which controls whether the company sees a candidate as
# AI-shortlisted after they have already applied. The two
# thresholds are intentionally independent business rules.
# ============================================================

MINIMUM_AI_MATCH_TO_APPLY = 70.0


def get_candidate_job_match_score(
    user: User,
    job: Job,
    db: Session,
) -> float:
    """
    Compute the candidate's AI match score for a job using the
    existing, unmodified AI matching engine
    (calculate_candidate_match). This mirrors the same lookup
    already used by the candidate job detail endpoint and the
    company AI shortlist endpoint, so the score used here is
    always consistent with what the candidate is shown and
    what recruiters see.

    The score is always computed here, on the backend, from
    the candidate's stored resume analysis and profile. It is
    never accepted from the frontend.
    """

    resume = (
        get_active_resume(db, user.id)
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

    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user.id
        )
        .first()
    )

    match = calculate_candidate_match(
        candidate_analysis=analysis,
        candidate_profile=profile,
        candidate_user=user,
        job=job,
    )

    return match["score"]


# ============================================================
# SCHEMAS
# ============================================================

class ApplicationAnswerCreate(BaseModel):
    question_id: int
    answer: str | None = None


class ApplicationCreate(BaseModel):
    user_id: int
    job_id: int
    answers: list[ApplicationAnswerCreate] = Field(
        default_factory=list
    )


# ============================================================
# CONSTANTS
# ============================================================

SUPPORTED_QUESTION_TYPES = {
    "text",
    "long_text",
    "number",
    "yes_no",
    "single_choice",
    "multiple_choice",
}

# Your existing normal candidate accounts use role="user".
# Older candidate records may use role="candidate".
ALLOWED_CANDIDATE_ROLES = {
    "user",
    "candidate",
}


# ============================================================
# HELPERS
# ============================================================

def safe_json_load(value):
    if not value:
        return []

    try:
        data = json.loads(value)

        if isinstance(data, list):
            return data

        return []

    except Exception:
        return []


def normalize_answer(value):
    if value is None:
        return ""

    return str(value).strip()


def validate_question_answer(
    question: JobQuestion,
    answer: str | None,
):
    """
    Validate one candidate answer according to
    the job question type.
    """

    answer_value = normalize_answer(answer)

    # --------------------------------------------------------
    # QUESTION TYPE
    # --------------------------------------------------------

    question_type = (
        question.question_type
        or "text"
    ).strip().lower()

    if question_type not in SUPPORTED_QUESTION_TYPES:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported question type: "
                f"{question_type}"
            ),
        )

    # --------------------------------------------------------
    # REQUIRED
    # --------------------------------------------------------

    if question.required and not answer_value:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Answer required for question: "
                f"{question.question}"
            ),
        )

    # Optional empty answers are valid.
    if not answer_value:
        return

    # --------------------------------------------------------
    # NUMBER
    # --------------------------------------------------------

    if question_type == "number":

        try:
            float(answer_value)

        except ValueError:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Please enter a valid number for: "
                    f"{question.question}"
                ),
            )

        return

    # --------------------------------------------------------
    # YES / NO
    # --------------------------------------------------------

    if question_type == "yes_no":

        normalized = answer_value.lower()

        valid_values = {
            "yes",
            "no",
            "true",
            "false",
        }

        if normalized not in valid_values:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Answer must be Yes or No for: "
                    f"{question.question}"
                ),
            )

        return

    # --------------------------------------------------------
    # SINGLE CHOICE
    # --------------------------------------------------------

    if question_type == "single_choice":

        options = safe_json_load(
            question.options
        )

        if not options:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"No options configured for: "
                    f"{question.question}"
                ),
            )

        normalized_answer = (
            answer_value.strip()
        )

        normalized_options = {
            str(option).strip()
            for option in options
        }

        if normalized_answer not in normalized_options:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid choice for: "
                    f"{question.question}"
                ),
            )

        return

    # --------------------------------------------------------
    # MULTIPLE CHOICE
    # --------------------------------------------------------

    if question_type == "multiple_choice":

        options = safe_json_load(
            question.options
        )

        if not options:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"No options configured for: "
                    f"{question.question}"
                ),
            )

        try:

            selected = json.loads(
                answer_value
            )

        except Exception:

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Multiple-choice answer must "
                    f"be a JSON array for: "
                    f"{question.question}"
                ),
            )

        if not isinstance(selected, list):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Multiple-choice answer must "
                    f"be an array for: "
                    f"{question.question}"
                ),
            )

        normalized_options = {
            str(option).strip()
            for option in options
        }

        for item in selected:

            if (
                str(item).strip()
                not in normalized_options
            ):

                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Invalid selection for: "
                        f"{question.question}"
                    ),
                )

        return


# ============================================================
# GET JOB QUESTIONS
# ============================================================

@router.get("/jobs/{job_id}/questions")
def get_application_questions(
    job_id: int,
    db: Session = Depends(get_db),
):
    """
    Return the custom application questions
    configured by the company for a job.
    """

    job = (
        db.query(Job)
        .filter(
            Job.id == job_id
        )
        .first()
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if job.status != "active":

        raise HTTPException(
            status_code=400,
            detail="Applications are not open for this job",
        )

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == job_id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc(),
        )
        .all()
    )

    return {

        "job": {

            "id":
                job.id,

            "title":
                job.title,

            "company":
                job.company,

            "location":
                job.location,

            "work_mode":
                job.work_mode,

            "employment_type":
                job.employment_type,
        },

        "questions": [

            {
                "id":
                    question.id,

                "question":
                    question.question,

                "question_type":
                    question.question_type,

                "options":
                    safe_json_load(
                        question.options
                    ),

                "required":
                    question.required,

                "display_order":
                    question.display_order,
            }

            for question in questions
        ],
    }


# ============================================================
# APPLY TO JOB
# ============================================================

@router.post("/")
def apply_to_job(
    payload: ApplicationCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a candidate application and save all
    custom application answers.

    IMPORTANT:
    This endpoint is called only when the candidate
    clicks "Submit Application".

    Opening "View & Apply" does NOT create an application.
    """

    require_self(current_user, payload.user_id)
    require_csrf(request)

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

    user = (
        db.query(User)
        .filter(
            User.id == payload.user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="Candidate not found",
        )

    # Existing candidate accounts use role="user".
    # Also allow role="candidate" for compatibility.
    user_role = (
        getattr(user, "role", None)
        or "user"
    ).strip().lower()

    if user_role not in ALLOWED_CANDIDATE_ROLES:

        raise HTTPException(
            status_code=403,
            detail="Only candidate accounts can apply",
        )

    # --------------------------------------------------------
    # JOB
    # --------------------------------------------------------

    job = (
        db.query(Job)
        .filter(
            Job.id == payload.job_id
        )
        .first()
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found",
        )

    if job.status != "active":

        raise HTTPException(
            status_code=400,
            detail="Applications are closed for this job",
        )

    # --------------------------------------------------------
    # APPLICATION DEADLINE
    # --------------------------------------------------------

    if job.application_deadline:

        try:

            deadline = datetime.strptime(
                job.application_deadline,
                "%Y-%m-%d",
            )

            deadline = deadline.replace(
                hour=23,
                minute=59,
                second=59,
            )

            if datetime.utcnow() > deadline:

                raise HTTPException(
                    status_code=400,
                    detail="Application deadline has passed",
                )

        except ValueError:

            # Existing jobs with a different date format
            # remain usable.
            pass

    # --------------------------------------------------------
    # OPENINGS
    # --------------------------------------------------------

    if job.openings is not None:

        try:

            if int(job.openings) <= 0:

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Applications are closed because "
                        "there are no openings"
                    ),
                )

        except (
            ValueError,
            TypeError
        ):

            pass

    # --------------------------------------------------------
    # DUPLICATE APPLICATION
    # --------------------------------------------------------

    existing = (
        db.query(JobApplication)
        .filter(
            JobApplication.job_id == payload.job_id,
            JobApplication.user_id == payload.user_id,
        )
        .first()
    )

    if existing:

        raise HTTPException(
            status_code=409,
            detail="You have already applied to this job",
        )

    # --------------------------------------------------------
    # AI MATCH ELIGIBILITY
    #
    # Candidates may only apply if their AI match score for
    # this job is at least MINIMUM_AI_MATCH_TO_APPLY. The score
    # is computed here using the existing AI matching engine —
    # never trusted from the frontend.
    # --------------------------------------------------------

    match_score = get_candidate_job_match_score(
        user,
        job,
        db,
    )

    if match_score < MINIMUM_AI_MATCH_TO_APPLY:

        raise HTTPException(
            status_code=403,
            detail=(
                "You do not meet the minimum AI match score "
                "required to apply for this job. "
                f"Minimum AI match required to apply: "
                f"{MINIMUM_AI_MATCH_TO_APPLY:.0f}%. "
                f"Your current AI match score is "
                f"{match_score}%."
            ),
        )

    # --------------------------------------------------------
    # LOAD QUESTIONS
    # --------------------------------------------------------

    questions = (
        db.query(JobQuestion)
        .filter(
            JobQuestion.job_id == payload.job_id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc(),
        )
        .all()
    )

    questions_by_id = {
        question.id: question
        for question in questions
    }

    # --------------------------------------------------------
    # CHECK UNKNOWN QUESTIONS
    # --------------------------------------------------------

    submitted_question_ids = {
        answer.question_id
        for answer in payload.answers
    }

    unknown_question_ids = (
        submitted_question_ids
        - set(questions_by_id.keys())
    )

    if unknown_question_ids:

        raise HTTPException(
            status_code=400,
            detail=(
                "One or more submitted questions "
                "do not belong to this job"
            ),
        )

    # --------------------------------------------------------
    # BUILD ANSWER MAP
    # --------------------------------------------------------

    answers_by_question_id = {}

    for answer in payload.answers:

        if (
            answer.question_id
            in answers_by_question_id
        ):

            raise HTTPException(
                status_code=400,
                detail=(
                    f"Duplicate answer submitted "
                    f"for question "
                    f"{answer.question_id}"
                ),
            )

        answers_by_question_id[
            answer.question_id
        ] = normalize_answer(
            answer.answer
        )

    # --------------------------------------------------------
    # VALIDATE QUESTIONS
    # --------------------------------------------------------

    for question in questions:

        answer_value = (
            answers_by_question_id.get(
                question.id
            )
        )

        validate_question_answer(
            question,
            answer_value,
        )

    # --------------------------------------------------------
    # CREATE APPLICATION
    # --------------------------------------------------------

    application = JobApplication(

        job_id=
            job.id,

        user_id=
            user.id,

        status=
            "new",

        ai_match_score=
            None,

        ai_shortlist=
            False,

        recruiter_shortlisted=
            False,

        recruiter_notes=
            None,

        applied_date=
            datetime.utcnow(),

        updated_date=
            datetime.utcnow(),
    )

    db.add(application)

    try:

        db.flush()

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="You have already applied to this job",
        )

    # --------------------------------------------------------
    # SAVE ANSWERS
    # --------------------------------------------------------

    for question in questions:

        if question.id not in answers_by_question_id:
            continue

        answer_value = (
            answers_by_question_id[
                question.id
            ]
        )

        answer = ApplicationAnswer(

            application_id=
                application.id,

            question_id=
                question.id,

            answer=
                answer_value,
        )

        db.add(answer)

    # --------------------------------------------------------
    # COMMIT
    # --------------------------------------------------------

    try:

        db.commit()

    except IntegrityError:

        db.rollback()

        raise HTTPException(
            status_code=409,
            detail="Unable to create application",
        )

    db.refresh(application)

    return {

        "success":
            True,

        "message":
            "Application submitted successfully",

        "application": {

            "id":
                application.id,

            "job_id":
                application.job_id,

            "user_id":
                application.user_id,

            "status":
                application.status,

            "applied_date": (

                application.applied_date.isoformat()

                if application.applied_date

                else None
            ),
        },

        "job": {

            "id":
                job.id,

            "title":
                job.title,

            "company":
                job.company,
        },

        "answers_submitted":
            len(
                answers_by_question_id
            ),

        "ai_status":
            "Pending AI matching",
    }


# ============================================================
# GET MY APPLICATIONS
# ============================================================

@router.get("/user/{user_id}")
def get_user_applications(
    user_id: int,
    view: str | None = Query(
        default=None,
        description=(
            "Omitted (default): returns every application, "
            "unfiltered — the original, unchanged behavior, so "
            "existing callers (e.g. the dashboard's own totals) "
            "are never affected by this. 'active': applications "
            "still being processed or assessed. 'history': "
            "applications that have already reached a final "
            "outcome (rejected, hired, or any existing assessment-"
            "failure/expiry/termination status) — see "
            "TERMINAL_APPLICATION_STATUSES. Purely a status-based "
            "filter; no application is ever moved, archived, or "
            "deleted by this."
        ),
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return applications submitted by a candidate.
    """

    require_self(current_user, user_id)

    if view is not None and view not in ("active", "history"):

        raise HTTPException(
            status_code=400,
            detail="Invalid view. Allowed values: active, history.",
        )

    user = (
        db.query(User)
        .filter(
            User.id == user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="Candidate not found",
        )

    query = (
        db.query(
            JobApplication,
            Job,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .filter(
            JobApplication.user_id == user_id
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

    applications = (
        query
        .order_by(
            JobApplication.applied_date.desc()
        )
        .all()
    )

    # --------------------------------------------------------
    # STATUS HISTORY
    #
    # Fetched in one batch query and grouped in Python so the
    # candidate timeline can be rendered without an extra
    # request per application. Only the fields a candidate
    # should see (to_status, created_at) are exposed — no
    # recruiter/company/system identifiers.
    # --------------------------------------------------------

    application_ids = [
        application.id
        for application, _job in applications
    ]

    history_by_application_id = {}

    if application_ids:

        history_rows = (
            db.query(ApplicationStatusHistory)
            .filter(
                ApplicationStatusHistory.application_id.in_(
                    application_ids
                )
            )
            .order_by(
                ApplicationStatusHistory.created_at.asc()
            )
            .all()
        )

        for history_row in history_rows:

            history_by_application_id.setdefault(
                history_row.application_id,
                [],
            ).append({

                "to_status":
                    history_row.to_status,

                "created_at": (

                    history_row.created_at.isoformat()

                    if history_row.created_at

                    else None
                ),
            })

    # --------------------------------------------------------
    # APTITUDE TEST SUMMARY
    #
    # Only a lightweight summary (no questions/answers) so the
    # candidate timeline can show score/percentage/passed
    # without a separate request per application.
    # --------------------------------------------------------

    aptitude_test_by_application_id = {}

    if application_ids:

        aptitude_test_rows = (
            db.query(AptitudeTest)
            .filter(
                AptitudeTest.application_id.in_(
                    application_ids
                )
            )
            .all()
        )

        for test_row in aptitude_test_rows:

            percentage = None

            if (
                test_row.score is not None
                and test_row.total_questions
            ):

                percentage = round(
                    (
                        test_row.score
                        / test_row.total_questions
                    ) * 100,
                    2,
                )

            remaining_seconds = None

            if (
                test_row.expires_at
                and test_row.status in (
                    "assigned",
                    "in_progress",
                )
            ):

                remaining_seconds = max(
                    0,
                    int(
                        (
                            test_row.expires_at
                            - datetime.utcnow()
                        ).total_seconds()
                    ),
                )

            aptitude_test_by_application_id[
                test_row.application_id
            ] = {

                "status":
                    test_row.status,

                "score":
                    test_row.score,

                "total_questions":
                    test_row.total_questions,

                "percentage":
                    percentage,

                "passed":
                    test_row.passed,

                "assigned_at": (
                    test_row.assigned_at.isoformat()
                    if test_row.assigned_at
                    else None
                ),

                "expires_at": (
                    test_row.expires_at.isoformat()
                    if test_row.expires_at
                    else None
                ),

                "remaining_seconds":
                    remaining_seconds,

                "completed_at": (

                    test_row.completed_at.isoformat()

                    if test_row.completed_at

                    else None
                ),
            }

    # --------------------------------------------------------
    # ASSESSMENT SESSION SUMMARY (Task 4A)
    #
    # Minimal stage/self-intro status so the candidate timeline
    # can show an assessment step. No text content, no score —
    # there is no AI evaluation of this stage yet.
    # --------------------------------------------------------

    assessment_session_by_application_id = {}

    if application_ids:

        assessment_session_rows = (
            db.query(AssessmentSession)
            .filter(
                AssessmentSession.application_id.in_(
                    application_ids
                )
            )
            .all()
        )

        for session_row in assessment_session_rows:

            assessment_session_by_application_id[
                session_row.application_id
            ] = {

                "current_stage":
                    session_row.current_stage,

                "self_intro_status":
                    session_row.self_intro_status,

                "self_intro_started_at": (
                    session_row.self_intro_started_at.isoformat()
                    if session_row.self_intro_started_at
                    else None
                ),

                "self_intro_completed_at": (
                    session_row.self_intro_completed_at.isoformat()
                    if session_row.self_intro_completed_at
                    else None
                ),

                "self_intro_duration_seconds":
                    session_row.self_intro_duration_seconds,
            }

    # --------------------------------------------------------
    # PROGRAMMING TEST SUMMARY (Task 4C)
    #
    # Minimal result summary only — no source code, no hidden
    # tests, mirroring the aptitude_test embedding above exactly.
    # --------------------------------------------------------

    programming_test_by_application_id = {}

    if application_ids:

        programming_test_rows = (
            db.query(ProgrammingTest)
            .filter(
                ProgrammingTest.application_id.in_(
                    application_ids
                )
            )
            .all()
        )

        for prog_row in programming_test_rows:

            prog_percentage = None

            if (
                prog_row.score is not None
                and prog_row.total_score
            ):

                prog_percentage = round(
                    (
                        prog_row.score
                        / prog_row.total_score
                    ) * 100,
                    2,
                )

            prog_remaining_seconds = None

            if (
                prog_row.expires_at
                and prog_row.status == "in_progress"
            ):

                prog_remaining_seconds = max(
                    0,
                    int(
                        (
                            prog_row.expires_at
                            - datetime.utcnow()
                        ).total_seconds()
                    ),
                )

            programming_test_by_application_id[
                prog_row.application_id
            ] = {

                "status":
                    prog_row.status,

                "question_source":
                    prog_row.question_source,

                "score":
                    prog_row.score,

                "total_score":
                    prog_row.total_score,

                "percentage":
                    prog_percentage,

                "passed":
                    prog_row.passed,

                "expires_at": (
                    prog_row.expires_at.isoformat()
                    if prog_row.expires_at
                    else None
                ),

                "remaining_seconds":
                    prog_remaining_seconds,

                "submitted_at": (
                    prog_row.submitted_at.isoformat()
                    if prog_row.submitted_at
                    else None
                ),
            }

    # --------------------------------------------------------
    # AI INTERVIEW SUMMARY (Task 4D)
    #
    # Minimal result summary only — no question text, no answer
    # text, mirroring the programming_test embedding above
    # exactly. evaluation_summary carries only averaged
    # dimension scores + merged strengths/gaps, never raw
    # per-answer content.
    # --------------------------------------------------------

    interview_test_by_application_id = {}

    if application_ids:

        interview_test_rows = (
            db.query(InterviewTest)
            .filter(
                InterviewTest.application_id.in_(
                    application_ids
                )
            )
            .all()
        )

        for interview_row in interview_test_rows:

            interview_remaining_seconds = None

            if (
                interview_row.expires_at
                and interview_row.status == "in_progress"
            ):

                interview_remaining_seconds = max(
                    0,
                    int(
                        (
                            interview_row.expires_at
                            - datetime.utcnow()
                        ).total_seconds()
                    ),
                )

            interview_test_by_application_id[
                interview_row.application_id
            ] = {

                "status":
                    interview_row.status,

                "question_source":
                    interview_row.question_source,

                "total_questions":
                    interview_row.total_questions,

                "current_question_index":
                    interview_row.current_question_index,

                "overall_score":
                    interview_row.overall_score,

                "passed":
                    interview_row.passed,

                "recommendation":
                    interview_row.recommendation,

                "evaluation_summary": (
                    json.loads(interview_row.evaluation_summary)
                    if interview_row.evaluation_summary
                    else None
                ),

                "expires_at": (
                    interview_row.expires_at.isoformat()
                    if interview_row.expires_at
                    else None
                ),

                "remaining_seconds":
                    interview_remaining_seconds,

                "submitted_at": (
                    interview_row.submitted_at.isoformat()
                    if interview_row.submitted_at
                    else None
                ),
            }

    # --------------------------------------------------------
    # HR ASSIGNMENT STATE (Task 4D-F1)
    #
    # Whether HR has enabled the Programming / AI Interview
    # stages for each application — computed fresh from
    # AssessmentAssignment on every call, never cached, so a
    # normal page refresh reflects HR's latest decision.
    # --------------------------------------------------------

    programming_assignment_by_application_id = (
        batch_get_assignments(db, application_ids, "programming")
        if application_ids
        else {}
    )

    interview_assignment_by_application_id = (
        batch_get_assignments(db, application_ids, "ai_interview")
        if application_ids
        else {}
    )

    result = []

    for application, job in applications:

        result.append({

            "id":
                application.id,

            "job": {

                "id":
                    job.id,

                "title":
                    job.title,

                "company":
                    job.company,

                "location":
                    job.location,

                "work_mode":
                    job.work_mode,

                "employment_type":
                    job.employment_type,
            },

            "status":
                application.status,

            "ai_match_score":
                application.ai_match_score,

            "ai_shortlist":
                application.ai_shortlist,

            "ai_recommendation":
                application.ai_recommendation,

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

            "status_history":
                history_by_application_id.get(
                    application.id,
                    [],
                ),

            "aptitude_test":
                aptitude_test_by_application_id.get(
                    application.id
                ),

            "assessment_session":
                assessment_session_by_application_id.get(
                    application.id
                ),

            "programming_test":
                programming_test_by_application_id.get(
                    application.id
                ),

            "interview_test":
                interview_test_by_application_id.get(
                    application.id
                ),

            "programming_assigned": (
                application.id
                in programming_assignment_by_application_id
            ),

            "interview_assigned": (
                application.id
                in interview_assignment_by_application_id
            ),
        })

    return {

        "success":
            True,

        "user_id":
            user_id,

        "view":
            view,

        "total":
            len(result),

        "applications":
            result,
    }


# ============================================================
# GET SINGLE USER APPLICATION
# ============================================================

@router.get("/user/{user_id}/{application_id}")
def get_user_application(
    user_id: int,
    application_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_self(current_user, user_id)

    result = (
        db.query(
            JobApplication,
            Job,
        )
        .join(
            Job,
            Job.id == JobApplication.job_id,
        )
        .filter(
            JobApplication.id == application_id,
            JobApplication.user_id == user_id,
        )
        .first()
    )

    if not result:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    application, job = result

    answer_rows = (
        db.query(
            ApplicationAnswer,
            JobQuestion,
        )
        .join(
            JobQuestion,
            JobQuestion.id ==
            ApplicationAnswer.question_id,
        )
        .filter(
            ApplicationAnswer.application_id ==
            application.id
        )
        .order_by(
            JobQuestion.display_order.asc(),
            JobQuestion.id.asc(),
        )
        .all()
    )

    answers = [

        {

            "question_id":
                question.id,

            "question":
                question.question,

            "question_type":
                question.question_type,

            "answer":
                answer.answer,
        }

        for answer, question
        in answer_rows
    ]

    return {

        "success":
            True,

        "id":
            application.id,

        "job": {

            "id":
                job.id,

            "title":
                job.title,

            "company":
                job.company,

            "location":
                job.location,

            "work_mode":
                job.work_mode,

            "employment_type":
                job.employment_type,
        },

        "status":
            application.status,

        "ai_match_score":
            application.ai_match_score,

        "ai_shortlist":
            application.ai_shortlist,

        "ai_recommendation":
            application.ai_recommendation,

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

        "answers":
            answers,
    }
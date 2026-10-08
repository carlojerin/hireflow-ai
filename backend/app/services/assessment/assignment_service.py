from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.models.assessment_assignment import AssessmentAssignment


# ============================================================
# JOBPILOT AI - ASSESSMENT ASSIGNMENT SERVICE (Task 4D-F1)
#
# Thin read/write helpers around AssessmentAssignment, shared by
# the HR-facing assign endpoints (company_applications.py) and
# the candidate-facing eligibility gates (programming_integration
# .py / interview_integration.py). Deliberately has no opinion on
# WHEN a stage is eligible to be assigned or started — that
# business logic stays in each stage's own integration module,
# exactly like AptitudeTest/ProgrammingTest/InterviewTest never
# leaked their own eligibility rules into a shared helper.
# ============================================================

VALID_ASSESSMENT_TYPES = ("programming", "ai_interview", "situational_judgment", "technical_domain")


def get_assignment(
    db: Session,
    application_id: int,
    assessment_type: str,
) -> AssessmentAssignment | None:

    return (
        db.query(AssessmentAssignment)
        .filter(
            AssessmentAssignment.application_id == application_id,
            AssessmentAssignment.assessment_type == assessment_type,
        )
        .first()
    )


def is_assigned(
    db: Session,
    application_id: int,
    assessment_type: str,
) -> bool:

    assignment = get_assignment(db, application_id, assessment_type)

    return assignment is not None and assignment.status == "assigned"


def create_assignment(
    db: Session,
    application_id: int,
    assessment_type: str,
    assigned_by_id: int,
    assigned_by_type: str = "hr",
) -> AssessmentAssignment:
    """
    Raises ValueError if an assignment for this (application,
    assessment_type) pair already exists — the caller (an HTTP
    route) is expected to translate this into a 409, mirroring
    assign_aptitude_test()'s own "no duplicate active
    assignments" check.

    assigned_by_type defaults to "hr" for the existing HR-facing
    call sites; the automatic stage-progression pipeline (see
    aptitude_tests.py / programming_tests.py) passes "system" so
    an assignment created because the candidate passed the prior
    stage is never confused with one an HR user actually clicked.
    """

    existing = get_assignment(db, application_id, assessment_type)

    if existing:

        raise ValueError(
            f"An assignment for '{assessment_type}' already "
            f"exists for application {application_id}."
        )

    now = datetime.utcnow()

    assignment = AssessmentAssignment(
        application_id=application_id,
        assessment_type=assessment_type,
        status="assigned",
        assigned_by_type=assigned_by_type,
        assigned_by_id=assigned_by_id,
        assigned_at=now,
        created_at=now,
    )

    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    return assignment


def batch_get_assignments(
    db: Session,
    application_ids: list[int],
    assessment_type: str,
) -> dict[int, AssessmentAssignment]:
    """
    Avoids N+1 queries for listing endpoints — mirrors the
    existing _fetch_aptitude_tests_by_application() /
    _fetch_programming_tests_by_application() pattern in
    company_applications.py.
    """

    if not application_ids:
        return {}

    assignments = (
        db.query(AssessmentAssignment)
        .filter(
            AssessmentAssignment.application_id.in_(application_ids),
            AssessmentAssignment.assessment_type == assessment_type,
        )
        .all()
    )

    return {
        assignment.application_id: assignment
        for assignment in assignments
    }

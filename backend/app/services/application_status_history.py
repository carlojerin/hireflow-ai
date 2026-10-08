from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.models.job_application import JobApplication
from backend.app.models.application_status_history import (
    ApplicationStatusHistory,
)


# ============================================================
# JOBPILOT AI - APPLICATION STATUS HISTORY
#
# Reusable helper for recording every JobApplication.status
# transition as an additive application_status_history row,
# without ever replacing or bypassing JobApplication.status
# itself.
# ============================================================

VALID_CHANGED_BY_TYPES = {
    "hr",
    "system",
    "candidate",
}


# ============================================================
# ACTIVE vs HISTORY (Task 3)
#
# One centralized definition of which existing JobApplication.
# status values represent a FINAL outcome — the application will
# never progress further on its own. Everything else (new,
# reviewing, shortlisted, aptitude_pending/passed, programming_
# pending/passed, interview_pending/passed, the generic "interview"
# manual-override value, etc.) is ACTIVE by omission: it is either
# still awaiting a step, or (interview_passed) awaiting HR's own
# final hire/reject decision per Task 2 — never assumed terminal
# just because a candidate finished a stage.
#
# This is a purely logical/display distinction. It never deletes,
# archives, or moves anything — see is_archived on JobApplication
# for the separate, pre-existing, HR-only manual visibility toggle,
# which this deliberately does not touch or redefine.
#
# Every value below is a real status this codebase already writes
# via record_status_change() — see aptitude_tests.py,
# programming_tests.py, interview_tests.py, integrity_service.py,
# and company_applications.py's own manual status-update/reject
# endpoints. No new status value was invented for this task.
# ============================================================

TERMINAL_APPLICATION_STATUSES = {
    "rejected",
    "hired",
    "aptitude_failed",
    "aptitude_expired",
    "aptitude_terminated",
    "programming_failed",
    "programming_expired",
    "programming_terminated",
    "interview_failed",
    "interview_expired",
    "interview_terminated",
}


def is_terminal_status(status: str | None) -> bool:
    """
    True if this status represents a final outcome (Application
    History) rather than an application still being processed or
    assessed (Active Applications).
    """

    return status in TERMINAL_APPLICATION_STATUSES


def record_status_change(
    db: Session,
    application: JobApplication,
    to_status: str,
    changed_by_type: str,
    changed_by_id: int | None = None,
    note: str | None = None,
) -> ApplicationStatusHistory | None:
    """
    Update a JobApplication's status and append a matching
    application_status_history record in the same transaction.

    - Does nothing (returns None) if to_status is the same as
      the application's current status, so no-op calls never
      create redundant history rows.
    - Does NOT call db.commit(). The caller controls the
      transaction, matching the existing pattern already used
      throughout company_applications.py.
    """

    if changed_by_type not in VALID_CHANGED_BY_TYPES:

        raise ValueError(
            "Invalid changed_by_type: "
            f"{changed_by_type}"
        )

    from_status = application.status

    if from_status == to_status:
        return None

    application.status = to_status

    history = ApplicationStatusHistory(
        application_id=application.id,
        from_status=from_status,
        to_status=to_status,
        changed_by_type=changed_by_type,
        changed_by_id=changed_by_id,
        note=note,
        created_at=datetime.utcnow(),
    )

    db.add(history)

    return history

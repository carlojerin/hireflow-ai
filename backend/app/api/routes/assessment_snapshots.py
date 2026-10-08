import os
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, require_csrf
from backend.app.api.routes.assessment_videos import (
    _authorize_viewer,
    _get_application_and_job,
    get_owned_application,
)
from backend.app.database.database import get_db
from backend.app.models.assessment_snapshot import AssessmentSnapshot
from backend.app.models.user import User


# ============================================================
# JOBPILOT AI - CAMERA SNAPSHOTS (proctoring evidence)
#
# The candidate's browser uploads a small JPEG at the start of a stage, every couple of minutes, when the stage
# completes, and whenever a camera flag fires. The owning company / admin / candidate can list and view them. Same
# ownership, CSRF and server-generated file name rules as the video recordings (assessment_videos.py). Nothing is
# analysed server-side.
# ============================================================

router = APIRouter(prefix="/api/assessment/snapshots", tags=["Assessment Snapshots"])

SNAPSHOT_STORAGE_ROOT = os.path.join("data", "assessment_snapshots")
os.makedirs(SNAPSHOT_STORAGE_ROOT, exist_ok=True)

ALLOWED_STAGES = {"aptitude", "programming", "ai_interview", "self_intro", "situational_judgment", "technical_domain"}
ALLOWED_REASONS = {
    "identity_reference", "periodic", "completion", "face_missing", "multiple_faces_detected", "looking_away",
    "gaze_away", "looking_away_repeated", "camera_covered", "mobile_phone_detected",
}
MAX_SNAPSHOT_BYTES = 200 * 1024
MAX_SNAPSHOTS_PER_STAGE = 80            # bounded: a stage is at most ~1 hour
MIN_SECONDS_BETWEEN_SAME_REASON = 8     # a flag storm cannot flood the disk
JPEG_MAGIC = b"\xff\xd8\xff"


def _snapshot_response(row: AssessmentSnapshot) -> dict:

    return {
        "id": row.id,
        "application_id": row.application_id,
        "assessment_type": row.assessment_type,
        "reason": row.reason,
        "file_size": row.file_size,
        "created_at": row.created_at.isoformat(),
        "url": f"/api/assessment/snapshots/file/{row.id}",
    }


@router.post("/{application_id}/{stage}")
async def upload_snapshot(
    application_id: int,
    stage: str,
    request: Request,
    reason: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    if stage not in ALLOWED_STAGES:
        raise HTTPException(status_code=400, detail="Invalid stage.")

    if reason not in ALLOWED_REASONS:
        raise HTTPException(status_code=400, detail="Invalid reason.")

    application = get_owned_application(db, application_id, current_user.id)

    data = await file.read()

    if not data or len(data) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(status_code=400, detail="Snapshot is empty or too large.")

    if not data.startswith(JPEG_MAGIC):
        raise HTTPException(status_code=400, detail="Snapshots must be JPEG images.")

    now = datetime.utcnow()

    stage_rows = db.query(AssessmentSnapshot).filter(
        AssessmentSnapshot.application_id == application.id,
        AssessmentSnapshot.assessment_type == stage,
    )

    if stage_rows.count() >= MAX_SNAPSHOTS_PER_STAGE:
        raise HTTPException(status_code=429, detail="Snapshot limit for this stage reached.")

    if reason == "identity_reference" and stage_rows.filter(AssessmentSnapshot.reason == "identity_reference").first():
        raise HTTPException(status_code=409, detail="This stage already has an identity reference snapshot.")

    if reason == "completion" and stage_rows.filter(AssessmentSnapshot.reason == "completion").first():
        raise HTTPException(status_code=409, detail="This stage already has a completion snapshot.")

    recent = stage_rows.filter(
        AssessmentSnapshot.reason == reason,
        AssessmentSnapshot.created_at >= now - timedelta(seconds=MIN_SECONDS_BETWEEN_SAME_REASON),
    ).first()

    if recent:
        raise HTTPException(status_code=429, detail="Snapshots are being sent too quickly.")

    directory = os.path.join(SNAPSHOT_STORAGE_ROOT, str(application.id), stage)
    os.makedirs(directory, exist_ok=True)

    name = f"{uuid.uuid4().hex}.jpg"

    with open(os.path.join(directory, name), "wb") as handle:
        handle.write(data)

    row = AssessmentSnapshot(
        application_id=application.id,
        assessment_type=stage,
        reason=reason,
        file_path="/".join((str(application.id), stage, name)),
        file_size=len(data),
        created_at=now,
    )

    db.add(row)
    db.commit()
    db.refresh(row)

    return _snapshot_response(row)


@router.get("/list/{application_id}")
def list_snapshots(application_id: int, request: Request, db: Session = Depends(get_db)):

    result = _get_application_and_job(db, application_id)

    if not result:
        raise HTTPException(status_code=404, detail="Application not found")

    application, job = result

    if not _authorize_viewer(request, db, application, job):
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")

    rows = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id == application_id)
        .order_by(AssessmentSnapshot.created_at.asc())
        .all()
    )

    return {"application_id": application_id, "snapshots": [_snapshot_response(r) for r in rows]}


@router.get("/file/{snapshot_id}")
def get_snapshot_file(snapshot_id: int, request: Request, db: Session = Depends(get_db)):

    row = db.query(AssessmentSnapshot).filter(AssessmentSnapshot.id == snapshot_id).first()

    if not row:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    result = _get_application_and_job(db, row.application_id)

    if not result:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    application, job = result

    if not _authorize_viewer(request, db, application, job):
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource.")

    root = os.path.realpath(SNAPSHOT_STORAGE_ROOT)
    path = os.path.realpath(os.path.join(SNAPSHOT_STORAGE_ROOT, row.file_path))

    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(path, media_type="image/jpeg", content_disposition_type="inline", filename=os.path.basename(path))

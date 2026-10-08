import os
import re
import shutil
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.assessment_session import AssessmentSession
from backend.app.models.assessment_video_recording import (
    AssessmentVideoRecording,
)

from backend.app.api.deps import (
    get_current_user,
    get_current_company,
    get_current_admin,
    require_csrf,
)


# ============================================================
# JOBPILOT AI - ASSESSMENT VIDEO RECORDING (Task 4)
#
# Foundation only: receive ONE completed MediaRecorder Blob per
# (application, stage), store it on the filesystem, persist
# metadata, and let an authorized viewer (the owning candidate,
# the owning company, or an admin) stream it back. No AI
# analysis of any kind happens here or anywhere else in this
# module — see the model's own docstring for the full privacy
# scope.
#
# Mirrors this project's own established conventions exactly:
# - data/{something}/ storage root + os.makedirs(exist_ok=True),
#   same as resumes.py's UPLOAD_FOLDER.
# - server-generated uuid4 filename, never anything the browser
#   supplied, same as resumes.py.
# - the get_current_user / get_current_company / get_current_admin
#   try-each-in-order authorization pattern, same as
#   assessment_sessions.py's get_assessment_session().
# ============================================================

router = APIRouter(
    prefix="/api/assessment/videos",
    tags=["Assessment Videos"],
)


VIDEO_STORAGE_ROOT = os.path.join("data", "assessment_videos")

os.makedirs(
    VIDEO_STORAGE_ROOT,
    exist_ok=True,
)


# self_intro | aptitude | programming | ai_interview — the exact
# same four stage values AssessmentSession.current_stage already
# uses. No new stage concept invented.
# SCREEN recordings (the candidate's whole screen, shared during a test) are stored as their own recording per stage:
#   self_intro_screen | aptitude_screen | programming_screen | ai_interview_screen        and, when the candidate had to share again after stopping,
#   <stage>_screen_2 | <stage>_screen_3 (a stage has at most three screen segments)
SCREEN_STAGE_RE = re.compile(r"^(self_intro|aptitude|programming|ai_interview)_screen(_[23])?$")


def is_allowed_stage(stage: str) -> bool:
    return stage in ALLOWED_STAGES or bool(SCREEN_STAGE_RE.fullmatch(stage or ""))


ALLOWED_STAGES = {
    "self_intro",
    "aptitude",
    "programming",
    "ai_interview",
}

# Base MIME type only (before any ";codecs=..." parameter) —
# MediaRecorder.isTypeSupported() on the frontend already picks
# from exactly this set (see js/assessment-video-recorder.js).
ALLOWED_MIME_TYPES = {
    "video/webm",
    "video/mp4",
    "video/x-matroska",
}

EXTENSION_BY_MIME_TYPE = {
    "video/webm": "webm",
    "video/mp4": "mp4",
    "video/x-matroska": "mkv",
}

# A few minutes of low-resolution (320x240-ish) webcam+mic webm
# per stage is realistically a few MB to a few tens of MB — 200MB
# leaves generous headroom without being unbounded.
MAX_VIDEO_BYTES = 200 * 1024 * 1024


def _base_mime_type(mime_type: str) -> str:

    return mime_type.split(";")[0].strip().lower()


def get_owned_application(
    db: Session,
    application_id: int,
    user_id: int,
) -> JobApplication:
    """Identical combined id+owner filter used everywhere else in
    this project — a generic 404 either way, so an application_id
    belonging to a different candidate can never be distinguished
    from one that doesn't exist."""

    application = (
        db.query(JobApplication)
        .filter(
            JobApplication.id == application_id,
            JobApplication.user_id == user_id,
        )
        .first()
    )

    if not application:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    return application


def _get_application_and_job(
    db: Session,
    application_id: int,
) -> tuple[JobApplication, Job] | None:

    return (
        db.query(JobApplication, Job)
        .join(Job, Job.id == JobApplication.job_id)
        .filter(JobApplication.id == application_id)
        .first()
    )


def _authorize_viewer(
    request: Request,
    db: Session,
    application: JobApplication,
    job: Job,
) -> bool:
    """
    Same try-each-in-order pattern as assessment_sessions.py's
    get_assessment_session(): the owning candidate, the owning
    company, or an admin — in that order, each checked against
    actual ownership of THIS application/job.
    """

    try:

        current_user = get_current_user(request, db)

        if current_user.id == application.user_id:
            return True

    except HTTPException:
        pass

    try:

        current_company = get_current_company(request, db)

        if current_company.id == job.company_id:
            return True

    except HTTPException:
        pass

    try:

        get_current_admin(request, db)
        return True

    except HTTPException:
        pass

    return False


def _recording_response(recording: AssessmentVideoRecording) -> dict:

    return {
        "id": recording.id,
        "application_id": recording.application_id,
        "stage": recording.stage,
        "mime_type": recording.mime_type,
        "file_size": recording.file_size,
        "duration_seconds": recording.duration_seconds,
        "started_at": (
            recording.started_at.isoformat()
            if recording.started_at
            else None
        ),
        "ended_at": (
            recording.ended_at.isoformat()
            if recording.ended_at
            else None
        ),
        "status": recording.status,
        "created_at": recording.created_at.isoformat(),
    }


def _parse_client_timestamp(value: str | None) -> datetime | None:
    """
    Best-effort parse of a browser-supplied ISO timestamp
    (Date.prototype.toISOString()) — purely advisory/display
    data, never used for any security or scoring decision, so a
    malformed value is simply dropped rather than rejected.
    """

    if not value:
        return None

    try:

        cleaned = value.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).replace(tzinfo=None)

    except ValueError:

        return None


# ============================================================
# UPLOAD STAGE RECORDING
# ============================================================

# ============================================================
# HUMAN-READABLE COPY: "assessment recordings/<candidate - job (app N)>/<n> - <heading>.webm"
# One folder per candidate, one headed video per finished stage, in pipeline order. Best-effort: the
# database row and data/assessment_videos stay the source of truth, a failed copy never fails an upload.
# ============================================================

RECORDINGS_FOLDER = "assessment recordings"


def _ffmpeg_path() -> str | None:

    import shutil as _shutil

    for candidate in (os.getenv("FFMPEG_PATH"), _shutil.which("ffmpeg"), r"D:\ffmpeg\bin\ffmpeg.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def remux_for_playback(path: str) -> bool:
    """
    A browser's MediaRecorder writes a WebM with NO duration and no seek index, so the HR player shows 0:00, cannot be
    scrubbed and may not play to the end. Copying the streams into a fresh container (no re-encoding, same quality, a few
    seconds) writes the real duration and seek index. Best-effort: on any failure the original file is left untouched.
    """

    import subprocess

    ffmpeg = _ffmpeg_path()
    if not ffmpeg or not os.path.isfile(path):
        return False

    fixed = path + ".fixed.webm"

    try:
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", path, "-c", "copy", fixed],
            check=True, timeout=300, stdin=subprocess.DEVNULL, capture_output=True,
        )
        if os.path.getsize(fixed) < 1024:
            raise RuntimeError("remuxed file is empty")
        os.replace(fixed, path)
        return True
    except Exception as exc:  # noqa: BLE001 - keep the original recording
        print(f"[assessment-videos] could not remux '{path}' for playback: {exc}")
        try:
            os.remove(fixed)
        except OSError:
            pass
        return False

STAGE_HEADINGS = {
    "self_intro": "1 - Self Introduction",
    "aptitude": "2 - Aptitude Test",
    "programming": "3 - Programming Test",
    "ai_interview": "4 - AI Interview",
}


def _safe_name(text: str, fallback: str) -> str:

    cleaned = re.sub(r"[<>:/|?*]", "", (text or "").replace(chr(92), "").replace(chr(34), "")).strip(" .")
    return cleaned[:60] or fallback


def mirror_recording_to_folder(db: Session, application: JobApplication, stage: str, source_path: str) -> str | None:

    try:

        from backend.app.models.situational_judgment_test import SituationalJudgmentTest
        from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

        user = db.query(User).filter(User.id == application.user_id).first()
        job = db.query(Job).filter(Job.id == application.job_id).first()

        screen = SCREEN_STAGE_RE.fullmatch(stage)
        base_stage = screen.group(1) if screen else stage
        heading = STAGE_HEADINGS[base_stage]

        if base_stage == "programming":

            if db.query(SituationalJudgmentTest).filter(SituationalJudgmentTest.application_id == application.id).first():
                heading = "3 - Situational Judgment Test"
            elif db.query(TechnicalDomainAssessment).filter(TechnicalDomainAssessment.application_id == application.id).first():
                heading = "3 - Technical Domain Test"

        if screen:
            heading += " - screen recording" + (f" (part {screen.group(2)[1:]})" if screen.group(2) else "")

        candidate = _safe_name(getattr(user, "full_name", "") or getattr(user, "username", ""), "candidate")
        job_title = _safe_name(getattr(job, "title", ""), "job")

        folder = os.path.join(RECORDINGS_FOLDER, f"{candidate} - {job_title} (app {application.id})")
        os.makedirs(folder, exist_ok=True)

        extension = os.path.splitext(source_path)[1] or ".webm"
        target = os.path.join(folder, heading + extension)

        shutil.copyfile(source_path, target)

        return target

    except Exception as exc:  # noqa: BLE001 - a nicety, never a reason to reject the upload
        print(f"[assessment-videos] could not copy recording to '{RECORDINGS_FOLDER}': {exc}")
        return None


@router.post("/{application_id}/{stage}")
async def upload_stage_recording(
    application_id: int,
    stage: str,
    request: Request,
    file: UploadFile = File(...),
    started_at: str | None = Form(default=None),
    ended_at: str | None = Form(default=None),
    duration_seconds: float | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    if not is_allowed_stage(stage):

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid stage. Allowed values: "
                + ", ".join(sorted(ALLOWED_STAGES))
            ),
        )

    # Ownership verified BEFORE anything else — a candidate can
    # never upload against another candidate's application_id.
    application = get_owned_application(
        db, application_id, current_user.id
    )

    # One recording per (application, stage), enforced first at
    # the application level (cheap, friendly error message) and
    # again by the DB's own unique constraint below (authoritative
    # against a race between two near-simultaneous requests).
    existing = (
        db.query(AssessmentVideoRecording)
        .filter(
            AssessmentVideoRecording.application_id == application.id,
            AssessmentVideoRecording.stage == stage,
        )
        .first()
    )

    if existing:

        raise HTTPException(
            status_code=409,
            detail=(
                "A recording for this stage has already been "
                "uploaded."
            ),
        )

    mime_type = (file.content_type or "").strip()
    base_mime = _base_mime_type(mime_type)

    if base_mime not in ALLOWED_MIME_TYPES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported video type: "
                f"{mime_type or 'unknown'}."
            ),
        )

    file_data = await file.read()

    if not file_data:

        raise HTTPException(
            status_code=400,
            detail="Empty recording upload.",
        )

    if len(file_data) > MAX_VIDEO_BYTES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Recording exceeds the maximum allowed size "
                f"({MAX_VIDEO_BYTES // (1024 * 1024)} MB)."
            ),
        )

    session_record = (
        db.query(AssessmentSession)
        .filter(AssessmentSession.application_id == application.id)
        .first()
    )

    # ----------------------------------------------------
    # SERVER-GENERATED FILENAME/PATH — never anything the
    # browser sent. application_id and stage are already
    # verified-safe integers/enum values above, never raw
    # client strings, so the resulting path can never escape
    # VIDEO_STORAGE_ROOT.
    # ----------------------------------------------------

    extension = EXTENSION_BY_MIME_TYPE.get(base_mime, "webm")
    unique_filename = f"{uuid.uuid4().hex}.{extension}"

    stage_dir = os.path.join(
        VIDEO_STORAGE_ROOT, str(application.id), stage
    )

    os.makedirs(stage_dir, exist_ok=True)

    absolute_path = os.path.join(stage_dir, unique_filename)

    relative_path = "/".join(
        (str(application.id), stage, unique_filename)
    )

    try:

        with open(absolute_path, "wb") as buffer:
            buffer.write(file_data)

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Could not save recording: {exc}",
        )

    # write the real duration + seek index into the file (see remux_for_playback); runs off the event loop
    from fastapi.concurrency import run_in_threadpool

    if base_mime == "video/webm":
        await run_in_threadpool(remux_for_playback, absolute_path)

    saved_size = os.path.getsize(absolute_path)

    recording = AssessmentVideoRecording(
        application_id=application.id,
        assessment_session_id=(
            session_record.id if session_record else None
        ),
        stage=stage,
        file_path=relative_path,
        original_filename=(file.filename or None),
        mime_type=mime_type or base_mime,
        file_size=saved_size,
        duration_seconds=duration_seconds,
        started_at=_parse_client_timestamp(started_at),
        ended_at=_parse_client_timestamp(ended_at),
        status="uploaded",
        created_at=datetime.utcnow(),
    )

    db.add(recording)

    try:

        db.commit()

    except IntegrityError:

        db.rollback()

        # The unique constraint caught a race we didn't see in
        # the pre-check above — the file we just wrote is now
        # orphaned, so remove it rather than leaving a dangling
        # video no database row ever points to.
        try:
            os.remove(absolute_path)
        except OSError:
            pass

        raise HTTPException(
            status_code=409,
            detail=(
                "A recording for this stage has already been "
                "uploaded."
            ),
        )

    db.refresh(recording)

    mirror_recording_to_folder(db, application, stage, absolute_path)
    _clear_partial(application.id, stage)

    return _recording_response(recording)


# ============================================================
# SPOOLED RECORDING (a candidate who leaves mid-stage must not lose the video)
#
# While a stage is recorded the browser sends the video to the server in chunks (every ~10 s). At the end of the stage it asks the
# server to assemble them ("finalize") instead of uploading the whole video a second time. If the candidate closes the tab, loses
# connection or crashes, the chunks stay on the server and are assembled into a recording marked "partial" a few minutes later
# (assemble_stale_partials, run at startup and whenever HR opens the report). A recording is never created twice for a stage.
# ============================================================

PARTIAL_ROOT = os.path.join(VIDEO_STORAGE_ROOT, "_partial")
MAX_CHUNK_BYTES = 30 * 1024 * 1024
MAX_CHUNK_SEQ = 100000
STALE_PARTIAL_SECONDS = 300


def _partial_dir(application_id: int, stage: str) -> str:
    return os.path.join(PARTIAL_ROOT, str(int(application_id)), stage)


def _clear_partial(application_id: int, stage: str) -> None:
    shutil.rmtree(_partial_dir(application_id, stage), ignore_errors=True)


def _contiguous_parts(directory: str) -> list[str]:
    """The chunk files 0, 1, 2 ... in order, stopping at the first gap (a later chunk without its predecessors cannot be played)."""

    if not os.path.isdir(directory):
        return []

    seqs = sorted(int(n[:-5]) for n in os.listdir(directory) if re.fullmatch(r"\d{6}\.part", n))
    parts = []

    for expected, seq in enumerate(seqs):
        if seq != expected:
            break
        parts.append(os.path.join(directory, f"{seq:06d}.part"))

    return parts


def _probe_duration(path: str) -> float | None:

    import subprocess

    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None

    probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + os.path.splitext(ffmpeg)[1])
    if not os.path.isfile(probe):
        return None

    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        ).stdout.strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return None


def assemble_partial_recording(
    db: Session, application: JobApplication, stage: str, *, status: str,
    started_at: datetime | None = None, ended_at: datetime | None = None, duration_seconds: float | None = None,
    min_chunks: int = 1,
) -> AssessmentVideoRecording | None:
    """Builds the stage's recording from its spooled chunks. Returns None when there is nothing usable (or one already exists)."""

    if db.query(AssessmentVideoRecording).filter(
        AssessmentVideoRecording.application_id == application.id, AssessmentVideoRecording.stage == stage,
    ).first():
        _clear_partial(application.id, stage)
        return None

    parts = _contiguous_parts(_partial_dir(application.id, stage))

    if len(parts) < max(1, min_chunks):
        return None

    stage_dir = os.path.join(VIDEO_STORAGE_ROOT, str(application.id), stage)
    os.makedirs(stage_dir, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.webm"
    absolute_path = os.path.join(stage_dir, filename)

    with open(absolute_path, "wb") as target:
        for part in parts:
            with open(part, "rb") as source:
                shutil.copyfileobj(source, target)

    remux_for_playback(absolute_path)

    size = os.path.getsize(absolute_path)
    if size == 0:
        os.remove(absolute_path)
        return None

    session_record = db.query(AssessmentSession).filter(AssessmentSession.application_id == application.id).first()
    now = datetime.utcnow()

    recording = AssessmentVideoRecording(
        application_id=application.id,
        assessment_session_id=session_record.id if session_record else None,
        stage=stage,
        file_path="/".join((str(application.id), stage, filename)),
        original_filename=f"{stage}.webm",
        mime_type="video/webm",
        file_size=size,
        duration_seconds=duration_seconds if duration_seconds is not None else _probe_duration(absolute_path),
        started_at=started_at,
        ended_at=ended_at,
        status=status,
        created_at=now,
    )
    db.add(recording)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        try:
            os.remove(absolute_path)
        except OSError:
            pass
        return None

    db.refresh(recording)
    mirror_recording_to_folder(db, application, stage, absolute_path)
    _clear_partial(application.id, stage)

    return recording


def assemble_stale_partials(db: Session, application_id: int | None = None) -> int:
    """Turns chunks left behind by a candidate who never finished the stage into a "partial" recording. Never raises."""

    import time

    made = 0

    try:

        if not os.path.isdir(PARTIAL_ROOT):
            return 0

        applications = [str(application_id)] if application_id is not None else os.listdir(PARTIAL_ROOT)

        for app_name in applications:

            app_dir = os.path.join(PARTIAL_ROOT, app_name)
            if not app_name.isdigit() or not os.path.isdir(app_dir):
                continue

            application = db.query(JobApplication).filter(JobApplication.id == int(app_name)).first()

            for stage in os.listdir(app_dir):

                directory = os.path.join(app_dir, stage)
                if not is_allowed_stage(stage) or not os.path.isdir(directory):
                    continue

                files = [os.path.join(directory, n) for n in os.listdir(directory)]
                newest = max((os.path.getmtime(f) for f in files), default=0)

                if not files or time.time() - newest < STALE_PARTIAL_SECONDS:
                    continue                                              # still being recorded (or just ended): leave it

                if application is None:
                    shutil.rmtree(directory, ignore_errors=True)         # the application was deleted: nothing to attach it to
                    continue

                if assemble_partial_recording(db, application, stage, status="partial"):
                    made += 1

    except Exception as exc:  # noqa: BLE001 - housekeeping must never break a request or the startup
        print(f"[assessment-videos] could not assemble partial recordings: {exc}")

    return made


def screen_share_required() -> bool:
    """Whole-screen sharing during tests is ON unless the admin switched it off (Admin console -> LLM Usage -> Security)."""

    return (os.getenv("ASSESSMENT_REQUIRE_SCREEN_SHARE", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


@router.get("/config/screen-share")
def get_screen_share_config():
    return {"require_screen_share": screen_share_required()}


@router.post("/{application_id}/{stage}/chunk")
async def upload_recording_chunk(
    application_id: int,
    stage: str,
    request: Request,
    seq: int = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    if not is_allowed_stage(stage):
        raise HTTPException(status_code=400, detail="Invalid stage.")

    if not 0 <= seq < MAX_CHUNK_SEQ:
        raise HTTPException(status_code=400, detail="Invalid chunk number.")

    application = get_owned_application(db, application_id, current_user.id)

    if db.query(AssessmentVideoRecording).filter(
        AssessmentVideoRecording.application_id == application.id, AssessmentVideoRecording.stage == stage,
    ).first():
        raise HTTPException(status_code=409, detail="A recording for this stage has already been uploaded.")

    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="Empty chunk.")

    if len(data) > MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="Chunk is too large.")

    directory = _partial_dir(application.id, stage)
    os.makedirs(directory, exist_ok=True)

    already = sum(os.path.getsize(os.path.join(directory, n)) for n in os.listdir(directory))
    if already + len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=413, detail="Recording exceeds the maximum allowed size.")

    with open(os.path.join(directory, f"{seq:06d}.part"), "wb") as buffer:
        buffer.write(data)

    return {"ok": True, "seq": seq}


@router.post("/{application_id}/{stage}/finalize")
async def finalize_recording_from_chunks(
    application_id: int,
    stage: str,
    request: Request,
    expected_chunks: int = Form(...),
    started_at: str | None = Form(default=None),
    ended_at: str | None = Form(default=None),
    duration_seconds: float | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The stage ended: build the recording from the chunks already on the server (one upload in total, not two). 409 when chunks are
    missing, so the browser falls back to uploading the whole video the normal way."""

    from fastapi.concurrency import run_in_threadpool

    require_csrf(request)

    if not is_allowed_stage(stage):
        raise HTTPException(status_code=400, detail="Invalid stage.")

    application = get_owned_application(db, application_id, current_user.id)

    if len(_contiguous_parts(_partial_dir(application.id, stage))) < expected_chunks:
        raise HTTPException(status_code=409, detail="Some recording chunks are missing: upload the whole video instead.")

    recording = await run_in_threadpool(
        lambda: assemble_partial_recording(
            db, application, stage, status="uploaded", min_chunks=expected_chunks,
            started_at=_parse_client_timestamp(started_at), ended_at=_parse_client_timestamp(ended_at), duration_seconds=duration_seconds,
        )
    )

    if recording is None:
        raise HTTPException(status_code=409, detail="A recording for this stage has already been uploaded.")

    return _recording_response(recording)



# ============================================================
# LIST RECORDINGS FOR AN APPLICATION
# ============================================================

@router.get("/{application_id}")
def list_application_recordings(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
):

    result = _get_application_and_job(db, application_id)

    if not result:

        raise HTTPException(
            status_code=404,
            detail="Application not found",
        )

    application, job = result

    if not _authorize_viewer(request, db, application, job):

        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource.",
        )

    recordings = (
        db.query(AssessmentVideoRecording)
        .filter(AssessmentVideoRecording.application_id == application_id)
        .order_by(AssessmentVideoRecording.created_at.asc())
        .all()
    )

    return {
        "application_id": application_id,
        "recordings": [
            _recording_response(recording)
            for recording in recordings
        ],
    }


# ============================================================
# STREAM ONE RECORDING
# ============================================================

@router.get("/{recording_id}/stream")
def stream_recording(
    recording_id: int,
    request: Request,
    db: Session = Depends(get_db),
):

    recording = (
        db.query(AssessmentVideoRecording)
        .filter(AssessmentVideoRecording.id == recording_id)
        .first()
    )

    if not recording:

        raise HTTPException(
            status_code=404,
            detail="Recording not found",
        )

    result = _get_application_and_job(db, recording.application_id)

    if not result:

        raise HTTPException(
            status_code=404,
            detail="Recording not found",
        )

    application, job = result

    if not _authorize_viewer(request, db, application, job):

        raise HTTPException(
            status_code=403,
            detail="You do not have permission to access this resource.",
        )

    # Defense in depth: file_path is always server-generated (see
    # the upload route) and never contains ".." — but re-verify
    # the resolved path is still genuinely inside
    # VIDEO_STORAGE_ROOT before ever handing a path to
    # FileResponse, in case that invariant is ever violated by a
    # future change.
    storage_root = os.path.realpath(VIDEO_STORAGE_ROOT)
    absolute_path = os.path.realpath(
        os.path.join(VIDEO_STORAGE_ROOT, recording.file_path)
    )

    if (
        absolute_path != storage_root
        and not absolute_path.startswith(storage_root + os.sep)
    ):

        raise HTTPException(
            status_code=404,
            detail="Recording not found",
        )

    if not os.path.isfile(absolute_path):

        raise HTTPException(
            status_code=404,
            detail="Recording file not found",
        )

    # Starlette's FileResponse already handles HTTP Range
    # requests (seeking/scrubbing) and conditional requests
    # correctly on its own — no need to hand-roll that here.
    # filename + content_disposition_type="inline" together are
    # what actually make Starlette emit a Content-Disposition
    # header at all (it is a no-op without a filename) — so <video>
    # plays this directly instead of the browser offering to
    # download it.
    return FileResponse(
        absolute_path,
        media_type=recording.mime_type,
        filename=os.path.basename(absolute_path),
        content_disposition_type="inline",
    )

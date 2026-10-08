import json
import os
import uuid
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User

from backend.app.services.resume.extractor import (
    extract_resume_text
)

from backend.app.services.resume.analyzer import (
    analyze_resume,
    TransientAnalysisError
)

from backend.app.services.resume.active_resume import (
    get_active_resume,
    get_unanalyzed_newer_upload,
)

from backend.app.api.deps import get_current_user, require_csrf, require_self


router = APIRouter(
    prefix="/api/resumes",
    tags=["Resumes"]
)


UPLOAD_FOLDER = "data/resumes"

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


ALLOWED_EXTENSIONS = {
    "pdf",
    "docx"
}


# ==================================================
# HELPER - SAFE JSON LOAD
# ==================================================

def safe_json_load(value):

    if not value:
        return []

    try:

        return json.loads(value)

    except Exception:

        return []


# ==================================================
# HELPER - FORMAT ANALYSIS
# ==================================================

def format_analysis(
    analysis: ResumeAnalysis
):

    return {

        "summary":
            analysis.summary,

        "skills":
            safe_json_load(
                analysis.skills
            ),

        "job_roles":
            safe_json_load(
                analysis.job_roles
            ),

        "education":
            safe_json_load(
                analysis.education
            ),

        "experience":
            safe_json_load(
                analysis.experience
            ),

        "experience_level":
            analysis.experience_level,

        "analyzed_at":
            analysis.analyzed_at

    }


# ==================================================
# HELPER - ANALYZE AND SAVE
# ==================================================

def analyze_and_save_resume(
    resume: Resume,
    db: Session
):

    # ----------------------------------------------
    # CHECK FILE
    # ----------------------------------------------

    if not os.path.exists(
        resume.file_path
    ):

        raise HTTPException(
            status_code=404,
            detail=(
                "Resume file does not exist "
                "on the server."
            )
        )


    # ----------------------------------------------
    # CHECK EXISTING ANALYSIS
    # ----------------------------------------------

    existing_analysis = (

        db.query(ResumeAnalysis)

        .filter(
            ResumeAnalysis.resume_id
            == resume.id
        )

        .first()

    )


    if existing_analysis:

        return existing_analysis


    # ----------------------------------------------
    # EXTRACT TEXT
    # ----------------------------------------------

    try:

        resume_text = extract_resume_text(
            resume.file_path,
            resume.file_type
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Resume text extraction failed: "
                f"{exc}"
            )
        )


    if not resume_text:

        raise HTTPException(
            status_code=400,
            detail=(
                "No readable text was found "
                "inside the resume."
            )
        )


    # ----------------------------------------------
    # AI ANALYSIS
    # ----------------------------------------------

    try:

        analysis_data = analyze_resume(
            resume_text
        )

    except TransientAnalysisError as exc:

        raise HTTPException(
            status_code=503,
            detail=str(exc)
        )

    except Exception as exc:

        # analyze_resume already logged the provider detail; the candidate
        # gets a clean message only.
        raise HTTPException(
            status_code=500,
            detail=(
                str(exc)
                if isinstance(exc, RuntimeError)
                else "Resume analysis could not be completed."
            )
        )


    # ----------------------------------------------
    # SAVE ANALYSIS
    # ----------------------------------------------

    resume_analysis = ResumeAnalysis(

        resume_id=resume.id,

        user_id=resume.user_id,

        summary=analysis_data.get(
            "summary",
            ""
        ),

        skills=json.dumps(
            analysis_data.get(
                "skills",
                []
            )
        ),

        job_roles=json.dumps(
            analysis_data.get(
                "job_roles",
                []
            )
        ),

        education=json.dumps(
            analysis_data.get(
                "education",
                []
            )
        ),

        experience=json.dumps(
            analysis_data.get(
                "experience",
                []
            )
        ),

        experience_level=
            analysis_data.get(
                "experience_level",
                ""
            ),

        raw_text=resume_text,

        analyzed_at=
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

    )


    db.add(
        resume_analysis
    )

    db.commit()

    db.refresh(
        resume_analysis
    )


    return resume_analysis


def _previous_active_exists(db, user_id, new_resume_id):

    active = get_active_resume(db, user_id)

    return bool(active and active.id != new_resume_id)


# ==================================================
# UPLOAD + ANALYZE RESUME
# ==================================================

@router.post("/upload")
async def upload_resume(

    request: Request,

    user_id: int = Form(...),

    file: UploadFile = File(...),

    current_user: User = Depends(get_current_user),

    db: Session = Depends(get_db)

):

    require_self(current_user, user_id)
    require_csrf(request)

    # ----------------------------------------------
    # VALIDATE FILE
    # ----------------------------------------------

    if not file.filename:

        raise HTTPException(
            status_code=400,
            detail="Please select a resume."
        )


    if "." not in file.filename:

        raise HTTPException(
            status_code=400,
            detail=(
                "Resume must be a PDF "
                "or DOCX file."
            )
        )


    extension = (

        file.filename
        .rsplit(".", 1)[-1]
        .lower()

    )


    if extension not in ALLOWED_EXTENSIONS:

        raise HTTPException(
            status_code=400,
            detail=(
                "Only PDF and DOCX "
                "files are supported."
            )
        )


    # ----------------------------------------------
    # READ FILE
    # ----------------------------------------------

    file_data = await file.read()


    if not file_data:

        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty."
        )


    # ----------------------------------------------
    # 10 MB
    # ----------------------------------------------

    max_size = (
        10 *
        1024 *
        1024
    )


    if len(file_data) > max_size:

        raise HTTPException(
            status_code=400,
            detail=(
                "Resume must be smaller "
                "than 10 MB."
            )
        )


    # ----------------------------------------------
    # UNIQUE SERVER FILENAME
    # ----------------------------------------------

    unique_filename = (

        f"user_{user_id}_"
        f"{uuid.uuid4().hex}."
        f"{extension}"

    )


    file_path = os.path.join(
        UPLOAD_FOLDER,
        unique_filename
    )


    # ----------------------------------------------
    # SAVE FILE
    # ----------------------------------------------

    try:

        with open(
            file_path,
            "wb"
        ) as buffer:

            buffer.write(
                file_data
            )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not save resume: "
                f"{exc}"
            )
        )


    # ----------------------------------------------
    # CREATE RESUME RECORD
    # ----------------------------------------------

    resume = Resume(

        user_id=user_id,

        file_name=file.filename,

        file_path=file_path,

        file_type=extension,

        uploaded_at=
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

    )


    db.add(
        resume
    )

    db.commit()

    db.refresh(
        resume
    )


    # ----------------------------------------------
    # ANALYZE
    # ----------------------------------------------

    try:

        analysis_record = (

            analyze_and_save_resume(
                resume,
                db
            )

        )

    except HTTPException as exc:

        return {

            "success": True,

            "id":
                resume.id,

            "user_id":
                resume.user_id,

            "file_name":
                resume.file_name,

            "file_type":
                resume.file_type,

            "analysis_status":
                "failed",

            "message":
                (
                    "Resume uploaded, "
                    "but analysis failed."
                    + (
                        " Your previous analyzed resume remains active."
                        if _previous_active_exists(db, user_id, resume.id)
                        else ""
                    )
                ),

            "error":
                exc.detail,

            "active_resume_retained":
                _previous_active_exists(db, user_id, resume.id)

        }


    # ----------------------------------------------
    # SUCCESS
    # ----------------------------------------------

    return {

        "success": True,

        "id":
            resume.id,

        "user_id":
            resume.user_id,

        "file_name":
            resume.file_name,

        "file_type":
            resume.file_type,

        "analysis_status":
            "completed",

        "message":
            (
                "Resume uploaded and "
                "analyzed successfully."
            ),

        "analysis":
            format_analysis(
                analysis_record
            )

    }


# ==================================================
# ANALYZE EXISTING RESUME
# ==================================================

@router.post(
    "/{user_id}/analyze"
)
def analyze_existing_resume(

    user_id: int,

    request: Request,

    current_user: User = Depends(get_current_user),

    db: Session = Depends(get_db)

):

    require_self(current_user, user_id)
    require_csrf(request)

    # ----------------------------------------------
    # FIND LATEST RESUME
    # ----------------------------------------------

    resume = (

        db.query(Resume)

        .filter(
            Resume.user_id
            == user_id
        )

        .order_by(
            Resume.id.desc()
        )

        .first()

    )


    if not resume:

        raise HTTPException(
            status_code=404,
            detail=(
                "No resume found "
                "for this user."
            )
        )


    # ----------------------------------------------
    # ANALYZE
    # ----------------------------------------------

    analysis = (

        analyze_and_save_resume(
            resume,
            db
        )

    )


    return {

        "success": True,

        "resume_id":
            resume.id,

        "user_id":
            resume.user_id,

        "file_name":
            resume.file_name,

        "analysis_status":
            "completed",

        "message":
            (
                "Resume analyzed "
                "successfully."
            ),

        "analysis":
            format_analysis(
                analysis
            )

    }


# ==================================================
# GET LATEST RESUME FOR USER
# ==================================================

@router.get(
    "/{user_id}"
)
def get_user_resume(

    user_id: int,

    current_user: User = Depends(get_current_user),

    db: Session = Depends(get_db)

):

    require_self(current_user, user_id)

    # ----------------------------------------------
    # FIND LATEST RESUME FOR USER
    # ----------------------------------------------

    resume = (

        get_active_resume(
            db,
            user_id
        )

    )


    # ----------------------------------------------
    # NO RESUME
    # ----------------------------------------------

    if not resume:

        return {

            "success": True,

            "resume": None

        }


    # ----------------------------------------------
    # FIND ANALYSIS
    # ----------------------------------------------

    analysis = (

        db.query(ResumeAnalysis)

        .filter(
            ResumeAnalysis.resume_id
            == resume.id
        )

        .first()

    )


    # ----------------------------------------------
    # RESPONSE
    # ----------------------------------------------

    result = {

        "id":
            resume.id,

        "user_id":
            resume.user_id,

        "file_name":
            resume.file_name,

        "file_type":
            resume.file_type,

        "uploaded_at":
            resume.uploaded_at,

        "analysis_status":
            (
                "completed"
                if analysis
                else "pending"
            )

    }


    if analysis:

        result["analysis"] = (

            format_analysis(
                analysis
            )

        )


    # A newer upload whose analysis failed never replaces the active analyzed resume.
    newer_upload = get_unanalyzed_newer_upload(
        db,
        user_id,
        resume
    )

    if newer_upload:

        result["newer_upload_not_analyzed"] = {
            "id": newer_upload.id,
            "file_name": newer_upload.file_name,
            "uploaded_at": newer_upload.uploaded_at,
            "message": (
                "Your new resume could not be analyzed right now. "
                "Your previous analyzed resume remains active."
            ),
        }


    return {

        "success": True,

        "resume": result

    }


# ==================================================
# DELETE / REMOVE LATEST RESUME
# ==================================================

@router.delete(
    "/{user_id}"
)
def delete_user_resume(

    user_id: int,

    request: Request,

    current_user: User = Depends(get_current_user),

    db: Session = Depends(get_db)

):

    require_self(current_user, user_id)
    require_csrf(request)

    # ----------------------------------------------
    # FIND LATEST RESUME
    # ----------------------------------------------

    resume = (

        db.query(Resume)

        .filter(
            Resume.user_id
            == user_id
        )

        .order_by(
            Resume.id.desc()
        )

        .first()

    )


    # ----------------------------------------------
    # NO RESUME
    # ----------------------------------------------

    if not resume:

        raise HTTPException(
            status_code=404,
            detail=(
                "No resume found "
                "for this user."
            )
        )


    # ----------------------------------------------
    # SAVE FILE PATH
    # ----------------------------------------------

    resume_file_path = (
        resume.file_path
    )


    # ----------------------------------------------
    # DELETE AI ANALYSIS
    # ----------------------------------------------

    db.query(
        ResumeAnalysis
    ).filter(

        ResumeAnalysis.resume_id
        == resume.id

    ).delete(
        synchronize_session=False
    )


    # ----------------------------------------------
    # DELETE RESUME DATABASE RECORD
    # ----------------------------------------------

    db.delete(
        resume
    )


    try:

        db.commit()

    except Exception as exc:

        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not remove resume: "
                f"{exc}"
            )
        )


    # ----------------------------------------------
    # DELETE PHYSICAL FILE
    # ----------------------------------------------

    file_deleted = False


    if resume_file_path:

        try:

            if os.path.exists(
                resume_file_path
            ):

                os.remove(
                    resume_file_path
                )

                file_deleted = True

        except Exception as exc:

            # Database deletion already succeeded.
            # Do not restore the database record.
            # Report the file cleanup issue.

            return {

                "success": True,

                "message":
                    (
                        "Resume and AI analysis "
                        "were removed from the "
                        "account, but the server "
                        "file could not be deleted."
                    ),

                "file_deleted":
                    False,

                "error":
                    str(exc)

            }


    # ----------------------------------------------
    # SUCCESS
    # ----------------------------------------------

    return {

        "success": True,

        "message":
            (
                "Resume and AI analysis "
                "removed successfully."
            ),

        "resume_id":
            resume.id,

        "file_deleted":
            file_deleted

    }
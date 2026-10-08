from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session

from pathlib import Path
from uuid import uuid4

from backend.app.database.database import get_db
from backend.app.models.user import User
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.schemas.candidate_profile import CandidateProfileCreate
from backend.app.api.deps import get_current_user, require_csrf, require_self


router = APIRouter(
    prefix="/api/candidate-profiles",
    tags=["Candidate Profiles"]
)


# ==================================================
# PROFILE PHOTO STORAGE
# ==================================================

PROFILE_PHOTO_DIRECTORY = (
    Path(__file__).resolve().parents[3]
    / "uploads"
    / "profile_photos"
)


PROFILE_PHOTO_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True
)


ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp"
}


MAX_PROFILE_PHOTO_SIZE = 5 * 1024 * 1024


# ==================================================
# PROFILE RESPONSE
# ==================================================

def profile_response(profile):

    return {
        "id": profile.id,
        "user_id": profile.user_id,

        "full_name": profile.full_name,
        "profile_photo": profile.profile_photo,
        "headline": profile.headline,
        "about": profile.about,

        "education": profile.education,
        "experience": profile.experience,
        "skills": profile.skills,
        "projects": profile.projects,
        "certifications": profile.certifications,
        "achievements": profile.achievements,

        "preferred_roles": profile.preferred_roles,
        "preferred_locations": profile.preferred_locations,
        "work_mode": profile.work_mode,
        "minimum_salary": profile.minimum_salary,
        "experience_level": profile.experience_level,
        "preferred_technologies": profile.preferred_technologies,
        "job_type": profile.job_type,

        "github_url": profile.github_url,
        "linkedin_url": profile.linkedin_url,
        "portfolio_url": profile.portfolio_url,

        "profile_slug": profile.profile_slug
    }


# ==================================================
# CREATE PROFILE
# ==================================================

@router.post("/")
def create_candidate_profile(
    profile_data: CandidateProfileCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, profile_data.user_id)
    require_csrf(request)

    user = (
        db.query(User)
        .filter(
            User.id == profile_data.user_id
        )
        .first()
    )

    if not user:

        raise HTTPException(
            status_code=404,
            detail="User not found."
        )


    existing_profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user.id
        )
        .first()
    )


    if existing_profile:

        raise HTTPException(
            status_code=400,
            detail="Profile already exists for this user."
        )


    profile_slug = profile_data.profile_slug


    if profile_slug:

        profile_slug = profile_slug.strip()


        existing_slug = (
            db.query(CandidateProfile)
            .filter(
                CandidateProfile.profile_slug == profile_slug
            )
            .first()
        )


        if existing_slug:

            raise HTTPException(
                status_code=400,
                detail="Profile URL is already in use."
            )


    profile = CandidateProfile(

        user_id=user.id,

        full_name=(
            profile_data.full_name
            or user.full_name
        ),

        profile_photo=profile_data.profile_photo,

        headline=profile_data.headline,

        about=profile_data.about,

        education=(
            profile_data.education
            or user.education
            or ""
        ),

        experience=profile_data.experience,

        skills=profile_data.skills,

        projects=profile_data.projects,

        certifications=profile_data.certifications,

        achievements=profile_data.achievements,

        preferred_roles=(
            profile_data.preferred_roles
            or user.preferred_role
            or ""
        ),

        preferred_locations=(
            profile_data.preferred_locations
            or user.location
            or ""
        ),

        work_mode=(
            profile_data.work_mode
            or user.preferred_work_mode
            or ""
        ),

        minimum_salary=profile_data.minimum_salary,

        experience_level=(
            profile_data.experience_level
            or user.experience_level
            or ""
        ),

        preferred_technologies=(
            profile_data.preferred_technologies
        ),

        job_type=profile_data.job_type,

        github_url=profile_data.github_url,

        linkedin_url=profile_data.linkedin_url,

        portfolio_url=profile_data.portfolio_url,

        profile_slug=profile_slug
    )


    db.add(profile)

    db.commit()

    db.refresh(profile)


    return {
        "message":
            "Candidate profile created successfully",

        "profile":
            profile_response(profile)
    }


# ==================================================
# GET PROFILE
# ==================================================

@router.get("/{user_id}")
def get_candidate_profile(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)

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
            detail="User not found."
        )


    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user_id
        )
        .first()
    )


    if not profile:

        profile = CandidateProfile(

            user_id=user.id,

            full_name=user.full_name,

            education=user.education or "",

            preferred_roles=(
                user.preferred_role or ""
            ),

            preferred_locations=(
                user.location or ""
            ),

            work_mode=(
                user.preferred_work_mode or ""
            ),

            experience_level=(
                user.experience_level or ""
            ),

            profile_photo=None,

            headline=None,

            about=None,

            experience=None,

            skills=None,

            projects=None,

            certifications=None,

            achievements=None,

            minimum_salary=None,

            preferred_technologies=None,

            job_type=None,

            github_url=None,

            linkedin_url=None,

            portfolio_url=None,

            profile_slug=None
        )


        db.add(profile)

        db.commit()

        db.refresh(profile)


    return {
        "profile":
            profile_response(profile)
    }


# ==================================================
# UPLOAD PROFILE PHOTO
# ==================================================

@router.post("/{user_id}/photo")
async def upload_profile_photo(
    user_id: int,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)
    require_csrf(request)

    # ------------------------------------------
    # FIND USER
    # ------------------------------------------

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
            detail="User not found."
        )


    # ------------------------------------------
    # CHECK CONTENT TYPE
    # ------------------------------------------

    content_type = (
        file.content_type or ""
    ).lower()


    if content_type not in ALLOWED_IMAGE_TYPES:

        raise HTTPException(
            status_code=400,
            detail=(
                "Only JPG, PNG and WebP images "
                "are supported."
            )
        )


    # ------------------------------------------
    # READ FILE
    # ------------------------------------------

    file_data = await file.read()


    # ------------------------------------------
    # CHECK SIZE
    # ------------------------------------------

    if len(file_data) > MAX_PROFILE_PHOTO_SIZE:

        raise HTTPException(
            status_code=400,
            detail=(
                "Profile photo must be "
                "smaller than 5 MB."
            )
        )


    # ------------------------------------------
    # EMPTY FILE
    # ------------------------------------------

    if not file_data:

        raise HTTPException(
            status_code=400,
            detail="The uploaded image is empty."
        )


    # ------------------------------------------
    # FIND PROFILE
    # ------------------------------------------

    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user_id
        )
        .first()
    )


    # ------------------------------------------
    # CREATE PROFILE IF MISSING
    # ------------------------------------------

    if not profile:

        profile = CandidateProfile(

            user_id=user.id,

            full_name=user.full_name,

            education=user.education or "",

            preferred_roles=(
                user.preferred_role or ""
            ),

            preferred_locations=(
                user.location or ""
            ),

            work_mode=(
                user.preferred_work_mode or ""
            ),

            experience_level=(
                user.experience_level or ""
            )
        )

        db.add(profile)

        db.flush()


    # ------------------------------------------
    # DELETE OLD PHOTO
    # ------------------------------------------

    old_photo = profile.profile_photo


    if old_photo:

        old_filename = Path(
            old_photo
        ).name


        old_file_path = (
            PROFILE_PHOTO_DIRECTORY
            / old_filename
        )


        try:

            if old_file_path.exists():

                old_file_path.unlink()

        except OSError:

            pass


    # ------------------------------------------
    # GENERATE FILENAME
    # ------------------------------------------

    extension = ALLOWED_IMAGE_TYPES[
        content_type
    ]


    filename = (
        f"user_{user_id}_"
        f"{uuid4().hex}"
        f"{extension}"
    )


    destination = (
        PROFILE_PHOTO_DIRECTORY
        / filename
    )


    # ------------------------------------------
    # SAVE FILE
    # ------------------------------------------

    try:

        destination.write_bytes(
            file_data
        )

    except OSError as error:

        raise HTTPException(
            status_code=500,
            detail="Unable to save profile photo."
        ) from error


    # ------------------------------------------
    # STORE URL
    # ------------------------------------------

    photo_url = (
        f"/uploads/profile_photos/{filename}"
    )


    profile.profile_photo = photo_url


    # ------------------------------------------
    # DATABASE SAVE
    # ------------------------------------------

    try:

        db.commit()

        db.refresh(profile)

    except Exception as error:

        db.rollback()


        try:

            if destination.exists():

                destination.unlink()

        except OSError:

            pass


        raise HTTPException(
            status_code=500,
            detail="Unable to save profile photo."
        ) from error


    # ------------------------------------------
    # RESPONSE
    # ------------------------------------------

    return {

        "message":
            "Profile photo uploaded successfully.",

        "profile_photo":
            photo_url,

        "profile":
            profile_response(profile)
    }


# ==================================================
# REMOVE PROFILE PHOTO
# ==================================================

@router.delete("/{user_id}/photo")
def remove_profile_photo(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)
    require_csrf(request)

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
            detail="User not found."
        )


    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user_id
        )
        .first()
    )


    if not profile:

        raise HTTPException(
            status_code=404,
            detail="Profile not found."
        )


    old_photo = profile.profile_photo


    if old_photo:

        old_filename = Path(
            old_photo
        ).name


        old_file_path = (
            PROFILE_PHOTO_DIRECTORY
            / old_filename
        )


        try:

            if old_file_path.exists():

                old_file_path.unlink()

        except OSError:

            pass


    profile.profile_photo = None


    db.commit()

    db.refresh(profile)


    return {

        "message":
            "Profile photo removed successfully.",

        "profile":
            profile_response(profile)
    }


# ==================================================
# UPDATE PROFILE
# ==================================================

@router.put("/{user_id}")
def update_candidate_profile(
    user_id: int,

    profile_data: CandidateProfileCreate,

    request: Request,

    current_user: User = Depends(get_current_user),

    db: Session = Depends(get_db)
):

    require_self(current_user, user_id)
    require_csrf(request)

    # ------------------------------------------
    # FIND USER
    # ------------------------------------------

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
            detail="User not found."
        )


    # ------------------------------------------
    # FIND PROFILE
    # ------------------------------------------

    profile = (
        db.query(CandidateProfile)
        .filter(
            CandidateProfile.user_id == user_id
        )
        .first()
    )


    # ------------------------------------------
    # CREATE IF MISSING
    # ------------------------------------------

    if not profile:

        profile = CandidateProfile(

            user_id=user.id,

            full_name=user.full_name,

            education=user.education or "",

            preferred_roles=(
                user.preferred_role or ""
            ),

            preferred_locations=(
                user.location or ""
            ),

            work_mode=(
                user.preferred_work_mode or ""
            ),

            experience_level=(
                user.experience_level or ""
            )
        )

        db.add(profile)

        db.flush()


    # ------------------------------------------
    # PROFILE SLUG CHECK
    # ------------------------------------------

    new_slug = (
        profile_data.profile_slug
    )


    if new_slug:

        new_slug = new_slug.strip()


        existing_slug = (
            db.query(CandidateProfile)
            .filter(
                CandidateProfile.profile_slug == new_slug,
                CandidateProfile.id != profile.id
            )
            .first()
        )


        if existing_slug:

            raise HTTPException(
                status_code=400,
                detail="Profile URL is already in use."
            )


    # ------------------------------------------
    # BASIC PROFESSIONAL IDENTITY
    # ------------------------------------------

    profile.full_name = (

        profile_data.full_name

        if profile_data.full_name is not None

        else user.full_name
    )


    # Keep the existing uploaded photo.
    #
    # The dedicated photo endpoint is responsible
    # for changing/removing it.

    if (
        profile_data.profile_photo
        and profile_data.profile_photo
        != profile.profile_photo
    ):

        profile.profile_photo = (
            profile_data.profile_photo
        )


    profile.headline = (
        profile_data.headline
    )


    profile.about = (
        profile_data.about
    )


    # ------------------------------------------
    # PROFESSIONAL INFORMATION
    # ------------------------------------------

    profile.education = (

        profile_data.education

        if profile_data.education is not None

        else user.education
    )


    profile.experience = (
        profile_data.experience
    )


    profile.skills = (
        profile_data.skills
    )


    profile.projects = (
        profile_data.projects
    )


    profile.certifications = (
        profile_data.certifications
    )


    profile.achievements = (
        profile_data.achievements
    )


    # ------------------------------------------
    # CAREER PREFERENCES
    # ------------------------------------------

    profile.preferred_roles = (
        profile_data.preferred_roles
    )


    profile.preferred_locations = (
        profile_data.preferred_locations
    )


    profile.work_mode = (
        profile_data.work_mode
    )


    profile.minimum_salary = (
        profile_data.minimum_salary
    )


    profile.experience_level = (
        profile_data.experience_level
    )


    profile.preferred_technologies = (
        profile_data.preferred_technologies
    )


    profile.job_type = (
        profile_data.job_type
    )


    # ------------------------------------------
    # ONLINE PRESENCE
    # ------------------------------------------

    profile.github_url = (
        profile_data.github_url
    )


    profile.linkedin_url = (
        profile_data.linkedin_url
    )


    profile.portfolio_url = (
        profile_data.portfolio_url
    )


    # ------------------------------------------
    # PUBLIC PROFILE
    # ------------------------------------------

    profile.profile_slug = new_slug


    # ------------------------------------------
    # KEEP USER DATA IN SYNC
    # ------------------------------------------

    user.full_name = (
        profile.full_name
        or user.full_name
    )


    user.education = (
        profile.education
    )


    user.experience_level = (
        profile.experience_level
    )


    user.preferred_role = (
        profile.preferred_roles
    )


    user.preferred_work_mode = (
        profile.work_mode
    )


    user.location = (
        profile.preferred_locations
    )


    # ------------------------------------------
    # SAVE
    # ------------------------------------------

    db.commit()

    db.refresh(profile)

    db.refresh(user)


    return {

        "message":
            "Profile updated successfully",

        "profile":
            profile_response(profile)
    }
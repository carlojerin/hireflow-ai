import json

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.services.resume.active_resume import get_active_resume
from backend.app.api.deps import get_current_user, require_self

from backend.app.services.job_search.jooble import search_jooble_jobs
from backend.app.services.job_search.filters import filter_recent_jobs
from backend.app.services.job_search.deduplicator import deduplicate_jobs
from backend.app.services.job_search.verifier import filter_valid_job_urls
from backend.app.services.job_search.normalizer import normalize_jobs
from backend.app.services.job_search.job_analyzer import analyze_jobs
from backend.app.services.candidate_matcher import rank_candidate_jobs

from backend.app.database.database import SessionLocal

from backend.app.models.resume import Resume
from backend.app.models.resume_analysis import ResumeAnalysis
from backend.app.models.user import User


router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"]
)


# ==================================================
# SAFE JSON LOAD
# ==================================================

def safe_json_load(value):

    if not value:
        return []

    try:
        result = json.loads(value)

        if isinstance(result, list):
            return result

        return []

    except Exception:
        return []


# ==================================================
# GET USER PROFILE + RESUME
# ==================================================

def get_user_job_profile(user_id: int):

    db = SessionLocal()

    try:

        # ------------------------------------------
        # Get user
        # ------------------------------------------

        user = (
            db.query(User)
            .filter(
                User.id == user_id
            )
            .first()
        )

        if not user:
            return None


        # ------------------------------------------
        # Get latest resume
        # ------------------------------------------

        resume = (
            get_active_resume(db, user_id)
        )

        if not resume:
            return None


        # ------------------------------------------
        # Get resume analysis
        # ------------------------------------------

        analysis = (
            db.query(ResumeAnalysis)
            .filter(
                ResumeAnalysis.resume_id == resume.id
            )
            .first()
        )

        if not analysis:
            return None


        # ------------------------------------------
        # Decode JSON
        # ------------------------------------------

        skills = safe_json_load(
            analysis.skills
        )

        job_roles = safe_json_load(
            analysis.job_roles
        )

        education = safe_json_load(
            analysis.education
        )

        experience = safe_json_load(
            analysis.experience
        )


        # ------------------------------------------
        # Build profile
        # ------------------------------------------

        return {

            # User information
            "user_id": user.id,

            "name": user.full_name or "",

            "username": user.username or "",

            "location": user.location or "",

            "registered_education": (
                user.education or ""
            ),

            "graduation_year": (
                user.graduation_year
            ),

            "registered_experience_level": (
                user.experience_level or ""
            ),

            "preferred_role": (
                user.preferred_role or ""
            ),

            "preferred_work_mode": (
                user.preferred_work_mode or ""
            ),

            # Resume information
            "resume_id": resume.id,

            "resume_name": resume.file_name,

            "skills": skills,

            "job_roles": job_roles,

            "education": education,

            "experience": experience,

            "experience_level": (
                analysis.experience_level or ""
            ),

            "summary": (
                analysis.summary or ""
            )
        }

    finally:

        db.close()


# ==================================================
# BUILD SEARCH KEYWORD
# ==================================================

def build_search_keyword(
    keyword: str,
    profile: dict
):

    keyword = (
        keyword or ""
    ).strip()


    # User selected a role
    if keyword:
        return keyword


    # Registration preferred role
    preferred_role = (
        profile.get(
            "preferred_role",
            ""
        )
        or ""
    ).strip()


    if preferred_role:
        return preferred_role


    # First AI resume role
    roles = profile.get(
        "job_roles",
        []
    )


    if roles:
        return roles[0]


    return "Software Developer"


# ==================================================
# SEARCH JOBS
# ==================================================

@router.get("/search")
def search_jobs(

    keyword: str = Query(
        default=""
    ),

    location: str = Query(
        default=""
    ),

    max_age_days: int = Query(
        default=30,
        ge=1,
        le=90
    ),

    user_id: int | None = Query(
        default=None
    ),

    current_user: User = Depends(get_current_user),

):

    try:

        # ==========================================
        # USER ID
        #
        # SECURITY: this endpoint builds its search using the
        # named user's own resume/profile data (skills, roles,
        # location) and returns that data in the response — so
        # user_id must be proven to be the caller's own id via
        # the live session, never trusted as a bare query param.
        # ==========================================

        if not user_id:

            raise HTTPException(
                status_code=400,
                detail=(
                    "User ID is required. "
                    "Please login again."
                )
            )

        require_self(current_user, user_id)


        # ==========================================
        # LOAD PROFILE
        # ==========================================

        profile = get_user_job_profile(
            user_id
        )


        if not profile:

            raise HTTPException(
                status_code=400,
                detail=(
                    "No analyzed resume found "
                    "for this user. "
                    "Please upload and analyze "
                    "your resume first."
                )
            )


        # ==========================================
        # SEARCH KEYWORD
        # ==========================================

        search_keyword = build_search_keyword(
            keyword,
            profile
        )


        # ==========================================
        # LOCATION
        # ==========================================

        search_location = (
            location
            or profile.get(
                "location",
                ""
            )
            or "Chennai"
        ).strip()


        # ==========================================
        # JOOBLE
        # ==========================================

        jobs = search_jooble_jobs(

            keywords=search_keyword,

            location=search_location,

            page=1,

            results_per_page=20

        )


        # ==========================================
        # RECENT JOBS
        # ==========================================

        recent_jobs = filter_recent_jobs(

            jobs,

            max_age_days=max_age_days

        )


        # ==========================================
        # DEDUPLICATE
        # ==========================================

        unique_jobs = deduplicate_jobs(
            recent_jobs
        )


        # ==========================================
        # VERIFY URLS
        # ==========================================

        valid_jobs = filter_valid_job_urls(
            unique_jobs
        )


        # ==========================================
        # NORMALIZE
        # ==========================================

        normalized_jobs = normalize_jobs(
            valid_jobs
        )


        # ==========================================
        # ANALYZE JOBS
        # ==========================================

        analyzed_jobs = analyze_jobs(
            normalized_jobs
        )


        # ==========================================
        # MATCH WITH USER RESUME
        # ==========================================

        ranked_jobs = rank_candidate_jobs(

            profile,

            analyzed_jobs

        )


        # ==========================================
        # RESPONSE
        # ==========================================

        return {

            "success": True,

            "user_id": user_id,

            "keyword": search_keyword,

            "location": search_location,

            "resume_used": {

                "resume_id": (
                    profile["resume_id"]
                ),

                "name": (
                    profile["resume_name"]
                ),

                "skills": (
                    profile["skills"]
                ),

                "job_roles": (
                    profile["job_roles"]
                ),

                "experience_level": (
                    profile["experience_level"]
                )

            },

            "registration_preferences": {

                "preferred_role": (
                    profile["preferred_role"]
                ),

                "preferred_work_mode": (
                    profile["preferred_work_mode"]
                ),

                "location": (
                    profile["location"]
                ),

                "education": (
                    profile["registered_education"]
                ),

                "graduation_year": (
                    profile["graduation_year"]
                )

            },

            "total": len(
                ranked_jobs
            ),

            "jobs": ranked_jobs

        }


    except HTTPException:

        raise


    except Exception as e:

        # SECURITY: never echo the raw exception message back to
        # the client (it can contain internal details) — log it
        # server-side and return a generic message instead.
        print("JOB SEARCH ERROR:", e)

        raise HTTPException(

            status_code=500,

            detail="Unable to search jobs right now. Please try again."

        )
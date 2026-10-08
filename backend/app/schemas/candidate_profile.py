from pydantic import BaseModel, Field


class CandidateProfileCreate(BaseModel):

    # ==========================================
    # USER
    # ==========================================

    user_id: int


    # ==========================================
    # BASIC PROFESSIONAL IDENTITY
    # ==========================================

    full_name: str | None = Field(
        default=None,
        max_length=150
    )

    profile_photo: str | None = None

    headline: str | None = Field(
        default=None,
        max_length=250
    )

    about: str | None = None


    # ==========================================
    # PROFESSIONAL INFORMATION
    # ==========================================

    education: str | None = None

    experience: str | None = None

    skills: str | None = None

    projects: str | None = None

    certifications: str | None = None

    achievements: str | None = None


    # ==========================================
    # CAREER PREFERENCES
    # ==========================================

    preferred_roles: str | None = None

    preferred_locations: str | None = None

    work_mode: str | None = None

    minimum_salary: str | None = None

    experience_level: str | None = None

    preferred_technologies: str | None = None

    job_type: str | None = None


    # ==========================================
    # ONLINE PRESENCE
    # ==========================================

    github_url: str | None = None

    linkedin_url: str | None = None

    portfolio_url: str | None = None


    # ==========================================
    # PUBLIC PROFILE
    # ==========================================

    profile_slug: str | None = Field(
        default=None,
        max_length=150
    )
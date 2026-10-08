from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        unique=True
    )

    # ==========================================
    # BASIC PROFILE
    # ==========================================

    full_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    profile_photo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    headline: Mapped[str | None] = mapped_column(
        String(250),
        nullable=True
    )

    about: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # ==========================================
    # PROFESSIONAL INFORMATION
    # ==========================================

    education: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    experience: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    preferred_roles: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    preferred_locations: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    work_mode: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    minimum_salary: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    experience_level: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    preferred_technologies: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    job_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    # ==========================================
    # PROFESSIONAL PROFILE
    # ==========================================

    skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    projects: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    certifications: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    achievements: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # ==========================================
    # ONLINE PRESENCE
    # ==========================================

    github_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    linkedin_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    portfolio_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    # ==========================================
    # PUBLIC PROFILE
    # ==========================================

    profile_slug: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
        unique=True,
        index=True
    )
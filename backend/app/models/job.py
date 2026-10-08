from sqlalchemy import (
    String,
    Text,
    Integer,
    ForeignKey
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class Job(Base):

    __tablename__ = "jobs"


    # ==========================================
    # JOB ID
    # ==========================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )


    # ==========================================
    # COMPANY REFERENCE
    # ==========================================

    # For company-created jobs this points to
    # the registered company.
    #
    # For JobPilot system/demo jobs this can
    # remain NULL.

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"),
        nullable=True,
        index=True
    )


    # ==========================================
    # BASIC JOB INFORMATION
    # ==========================================

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True
    )

    company: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True
    )

    location: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    work_mode: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )


    # ==========================================
    # JOB TYPE
    # ==========================================

    employment_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )


    # ==========================================
    # SALARY
    # ==========================================

    # Existing salary field is kept for compatibility.

    salary: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    salary_min: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    salary_max: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    salary_currency: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        default="INR"
    )


    # ==========================================
    # EXPERIENCE / EDUCATION
    # ==========================================

    experience: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    education: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )


    # ==========================================
    # SKILLS
    # ==========================================

    required_skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    preferred_skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    technologies: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    # ==========================================
    # ASSESSMENT ROLE-TYPE OVERRIDE
    # ==========================================

    # null (default) = fully automatic: role_classification.job_role_shape()
    # decides technical/non_technical from required_skills/description/
    # technologies, as it always has. HR can see that auto-detection on the
    # Invite-a-Candidate form and override it with "technical" or
    # "non_technical" if the AI guessed wrong -- stored here, and
    # job_role_shape() checks this FIRST before falling back to its own
    # keyword detection.

    role_shape_override: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True
    )


    # ==========================================
    # JOB DESCRIPTION
    # ==========================================

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    responsibilities: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    requirements: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    benefits: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    # ==========================================
    # OPENINGS
    # ==========================================

    openings: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )


    # ==========================================
    # JOB STATUS
    # ==========================================

    # Examples:
    # draft
    # active
    # paused
    # closed
    # expired

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="active",
        index=True
    )


    # ==========================================
    # SOURCE
    # ==========================================

    # Examples:
    # internal
    # company
    # jooble

    source: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="internal",
        index=True
    )


    # ==========================================
    # DATES
    # ==========================================

    posted_date: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    application_deadline: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )


    # ==========================================
    # ASSESSMENT PLAN (decided per job by the AI planner,
    # reviewable/overridable by HR -- see
    # services/assessment/assessment_planner.py)
    # ==========================================

    assessment_plan_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    # ==========================================
    # JOB URL
    # ==========================================

    # Kept because your existing job-search
    # system already uses these fields.

    job_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=""
    )

    application_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
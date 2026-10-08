from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class User(Base):

    __tablename__ = "users"


    # ==========================================
    # USER ID
    # ==========================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )


    # ==========================================
    # BASIC ACCOUNT INFORMATION
    # ==========================================

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    username: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    mobile: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
        index=True
    )


    # ==========================================
    # PASSWORD
    # ==========================================

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )


    # ==========================================
    # BASIC CAREER / ONBOARDING INFORMATION
    # ==========================================

    location: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    employment_status: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    current_role: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    industry: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    education: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True
    )

    graduation_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    experience_level: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default=""
    )


    # ==========================================
    # JOB PREFERENCES
    # ==========================================

    preferred_role: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    preferred_work_mode: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )


    # ==========================================
    # ACCOUNT ROLE
    # ==========================================

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="user"
    )


    # ==========================================
    # ACCOUNT STATUS
    #
    # Deactivated by an admin. Never deletes the
    # account or any related data — it only blocks
    # login.
    # ==========================================

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )
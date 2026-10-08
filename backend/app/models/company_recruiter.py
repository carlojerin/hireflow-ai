from sqlalchemy import String, Integer, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class CompanyRecruiter(Base):

    __tablename__ = "company_recruiters"


    # ==========================================
    # RECRUITER ID
    # ==========================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )


    # ==========================================
    # COMPANY REFERENCE
    # ==========================================

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True
    )


    # ==========================================
    # RECRUITER INFORMATION
    # ==========================================

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    job_title: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    mobile: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True
    )


    # ==========================================
    # ONLINE PRESENCE
    # ==========================================

    linkedin_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )


    # ==========================================
    # PROFILE PHOTO
    # ==========================================

    profile_photo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )


    # ==========================================
    # VISIBILITY
    # ==========================================

    profile_visible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


    # ==========================================
    # STATUS
    # ==========================================

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True
    )
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class Company(Base):

    __tablename__ = "companies"


    # ==========================================
    # COMPANY ID
    # ==========================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )


    # ==========================================
    # COMPANY ACCOUNT INFORMATION
    # ==========================================

    company_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        index=True
    )

    legal_name: Mapped[str] = mapped_column(
        String(250),
        nullable=False
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    mobile: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False,
        index=True
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )


    # ==========================================
    # HR PERSON IDENTITY
    # ==========================================

    # The person logging in on behalf of the company -- distinct
    # from the company's own legal/business identity above.

    hr_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    username: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        index=True
    )


    # ==========================================
    # COMPANY INFORMATION
    # ==========================================

    company_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    industry: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True
    )

    website: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    location: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    # ==========================================
    # BUSINESS IDENTIFIERS
    # ==========================================

    cin: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True
    )

    llpin: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True
    )

    gstin: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True
    )


    # ==========================================
    # COMPANY SIZE / BASIC DETAILS
    # ==========================================

    company_size: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    founded_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )


    # ==========================================
    # VERIFICATION
    # ==========================================

    verification_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        index=True
    )

    account_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True
    )

    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    mobile_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    # HR email verification: a 6-digit code emailed at registration. Only its hash is stored.
    email_verification_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_verification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    admin_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )


    # ==========================================
    # LOGO
    # ==========================================

    company_logo: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )


    # ==========================================
    # ROLE
    # ==========================================

    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="company"
    )


    # ==========================================
    # REGISTRATION DATE
    # ==========================================

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=datetime.utcnow
    )
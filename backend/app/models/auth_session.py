from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - SERVER-SIDE SESSION
#
# One row per logged-in session. The browser only ever holds
# an opaque, high-entropy token in an HttpOnly cookie — never
# this row's data. Only token_hash (SHA-256 of that token) is
# stored here, mirroring the existing PasswordResetOTP.otp_hash
# pattern: the secret value itself is never persisted, only a
# one-way hash of it.
#
# subject_type/subject_id together identify who this session
# belongs to, since candidates/admins (users table) and
# companies (companies table) are two different tables:
#   subject_type="user"    -> subject_id is users.id
#                              (covers both candidate and admin
#                              logins — admin is just a
#                              role="admin" row in users)
#   subject_type="company" -> subject_id is companies.id
# ============================================================

class AuthSession(Base):

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    subject_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    subject_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    # Snapshot only, for audit/log readability. Never trusted
    # for an authorization decision — the current-identity
    # dependencies always re-read the live User/Company row.
    role_at_login: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    revoked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

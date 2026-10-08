from datetime import datetime

from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class PasswordResetOTP(Base):
    __tablename__ = "password_reset_otps"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        nullable=False,
        index=True
    )

    otp_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False
    )

    used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )
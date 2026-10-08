from sqlalchemy import ForeignKey, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from backend.app.database.database import Base


class UserActivity(Base):

    __tablename__ = "user_activities"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    session_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True
    )

    login_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    last_seen: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    logout_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    active_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )

    page: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="online"
    )
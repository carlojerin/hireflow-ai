from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="saved"
    )

    date_saved: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    date_applied: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    interview_date: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    follow_up_date: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )
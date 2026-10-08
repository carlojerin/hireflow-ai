from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class Interview(Base):
    __tablename__ = "interviews"

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

    interview_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    interview_date: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="scheduled"
    )

    questions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    feedback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    score: Mapped[float | None] = mapped_column(
        nullable=True
    )
from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class ResumeAnalysis(Base):

    __tablename__ = "resume_analyses"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id"),
        nullable=False,
        unique=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    skills: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    job_roles: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    education: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    experience: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    experience_level: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    raw_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    analyzed_at: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
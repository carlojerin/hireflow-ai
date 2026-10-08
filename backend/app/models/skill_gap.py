from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class SkillGap(Base):
    __tablename__ = "skill_gaps"

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

    skill_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    priority: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    learning_difficulty: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    recommendation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
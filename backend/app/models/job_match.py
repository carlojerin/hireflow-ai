from sqlalchemy import Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class JobMatch(Base):
    __tablename__ = "job_matches"

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

    match_score: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    skill_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    experience_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    role_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    location_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    technology_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    preference_match: Mapped[float] = mapped_column(
        Float,
        nullable=False
    )

    explanation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
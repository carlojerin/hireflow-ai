from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class LearningRecommendation(Base):
    __tablename__ = "learning_recommendations"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False
    )

    skill_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    priority: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    learning_order: Mapped[int | None] = mapped_column(
        nullable=True
    )

    suggested_projects: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    resources: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class ApplicationAnswer(Base):
    __tablename__ = "application_answers"

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "question_id",
            name="uq_application_answer_application_question",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # Application this answer belongs to
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        index=True,
    )

    # Job question that was answered
    question_id: Mapped[int] = mapped_column(
        ForeignKey("job_questions.id"),
        nullable=False,
        index=True,
    )

    # Candidate's submitted answer
    answer: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
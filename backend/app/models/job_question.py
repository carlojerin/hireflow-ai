from sqlalchemy import (
    String,
    Text,
    Integer,
    Boolean,
    ForeignKey
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class JobQuestion(Base):

    __tablename__ = "job_questions"


    # ==========================================
    # QUESTION ID
    # ==========================================

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )


    # ==========================================
    # JOB REFERENCE
    # ==========================================

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=False,
        index=True
    )


    # ==========================================
    # QUESTION
    # ==========================================

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )


    # ==========================================
    # QUESTION TYPE
    # ==========================================

    # Supported:
    #
    # text
    # long_text
    # number
    # yes_no
    # single_choice
    # multiple_choice

    question_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="text"
    )


    # ==========================================
    # OPTIONS
    # ==========================================

    # Used for:
    #
    # single_choice
    # multiple_choice
    #
    # Stored as JSON text.

    options: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )


    # ==========================================
    # REQUIRED
    # ==========================================

    required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )


    # ==========================================
    # DISPLAY ORDER
    # ==========================================

    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        index=True
    )
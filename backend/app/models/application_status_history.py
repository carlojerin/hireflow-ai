from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class ApplicationStatusHistory(Base):
    __tablename__ = "application_status_history"

    # =========================================================
    # IDENTITY
    # =========================================================

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    # =========================================================
    # APPLICATION
    # =========================================================

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        index=True,
    )

    # =========================================================
    # TRANSITION
    # =========================================================

    from_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    to_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # =========================================================
    # ACTOR
    #
    # changed_by_type examples:
    # hr
    # system
    # candidate
    # =========================================================

    changed_by_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    changed_by_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # =========================================================
    # NOTE
    # =========================================================

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # =========================================================
    # TIMESTAMP
    # =========================================================

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

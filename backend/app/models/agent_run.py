from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    agent_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    input_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    output_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    started_at: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    completed_at: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )
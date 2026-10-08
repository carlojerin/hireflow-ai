from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class PracticalRoleplay(Base):
    """
    The voice role-play of a practical assessment: the AI plays a customer / prospect / client on a call and the candidate is the agent.

    scenario_json holds EVERYTHING the AI character knows (including facts it reveals only if asked and the grading rubric): it is never sent
    to the candidate. The candidate only sees payload_json of their SituationalJudgmentTest row (a short brief of their own role).
    transcript_json is the call itself, turn by turn, kept for grading and for HR to read.
    """

    __tablename__ = "practical_roleplays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"), nullable=False, unique=True, index=True)

    scenario_json: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # created | in_call | done
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

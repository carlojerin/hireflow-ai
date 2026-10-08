from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# The "observed behaviour" paragraph shown under one recorded video in the HR report. Written once from recorded signals (see
# services/assessment/recording_behavior_summary.py) and cached here, so opening the report again costs no AI call.

class RecordingBehaviorSummary(Base):

    __tablename__ = "recording_behavior_summaries"

    recording_id: Mapped[int] = mapped_column(ForeignKey("assessment_video_recordings.id"), primary_key=True)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="facts")          # "ai" (written by the model) | "facts" (plain template)
    signals_json: Mapped[str | None] = mapped_column(Text, nullable=True)                      # the labelled facts it was written from
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

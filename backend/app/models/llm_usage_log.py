from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# One row per LLM request the platform made (Groq chat, Gemini text/STT/TTS). Powers the admin console's
# "LLM usage" tab. Token counts are what the provider itself reported for that request.

class LlmUsageLog(Base):

    __tablename__ = "llm_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)       # groq | gemini
    model: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)

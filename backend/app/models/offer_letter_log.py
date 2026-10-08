from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# One row per offer letter HR sent from the candidate report: who, what subject, which
# attachments, and whether the mail server accepted it. The body is kept so HR can see
# exactly what the candidate was told.
class OfferLetterLog(Base):

    __tablename__ = "offer_letter_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"), index=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True, nullable=False)
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_names: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

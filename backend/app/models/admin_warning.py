from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ADMIN WARNING (issued to a company/HR account)
#
# A lightweight, auditable record of a warning an admin sent to a company --
# never emailed automatically (no notification system exists for this yet),
# shown to the admin as a history ("has this company been warned before?")
# and intended to be read out to the HR team by the admin directly. Kept
# separate from AssessmentIntegrityEvent (candidate-side proctoring) and
# ApplicationStatusHistory (per-application pipeline state) -- this is
# platform-level, company-level moderation, a different concern from both.
# ============================================================

class AdminWarning(Base):

    __tablename__ = "admin_warnings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)

    message: Mapped[str] = mapped_column(Text, nullable=False)

    issued_by_admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

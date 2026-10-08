from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# HIREFLOW AI - FACE MATCH RESULT
#
# One row per candidate photo that was compared with the candidate's reference photo (the picture taken before the self introduction). The
# comparison is a recruiter aid, never an identity verification: verdict is "same", "different" or "unclear" and a person decides. Only the
# verdict, a confidence number and a one-sentence reason are stored, never an embedding or any biometric template.
# ============================================================

class FaceMatchResult(Base):

    __tablename__ = "face_match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"), nullable=False, index=True)

    # the photo that was compared with the reference (unique: a photo is compared once, then the answer is reused)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("assessment_snapshots.id"), nullable=False, unique=True, index=True)

    reference_snapshot_id: Mapped[int] = mapped_column(ForeignKey("assessment_snapshots.id"), nullable=False)

    stage: Mapped[str] = mapped_column(String(30), nullable=False)

    verdict: Mapped[str] = mapped_column(String(12), nullable=False)          # same | different | unclear

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[str | None] = mapped_column(String(80), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

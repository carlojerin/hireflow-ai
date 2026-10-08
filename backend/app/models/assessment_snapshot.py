from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


# ============================================================
# JOBPILOT AI - ASSESSMENT SNAPSHOT (camera proctoring evidence)
#
# A small JPEG (about 320x240) taken from the candidate's camera stream:
#   reason = "identity_reference"  once at the start of each stage (so the recruiter can compare the person across stages)
#   reason = "periodic"            every couple of minutes
#   reason = <event type>          the moment a camera flag fired (face_missing, multiple_faces_detected, looking_away, ...)
# Metadata only in this table; the image bytes live on the filesystem under data/assessment_snapshots/. file_path is always
# server-generated and relative to that root. No face recognition / embedding is computed from these images: a human
# recruiter looks at them.
# ============================================================

class AssessmentSnapshot(Base):

    __tablename__ = "assessment_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id"), nullable=False, index=True)

    assessment_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    file_path: Mapped[str] = mapped_column(String(255), nullable=False)

    file_size: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

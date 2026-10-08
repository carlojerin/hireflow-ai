from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.database import Base


class TechnicalDomainAssessment(Base):
    """
    Occupies the SAME pipeline slot ProgrammingAssessmentV2 /
    SituationalJudgmentTest occupy, for a technical candidate whose
    required language JobPilot cannot execute in the Docker sandbox
    (SAP/ABAP, Salesforce/Apex, Solidity, RPA, embedded firmware,
    DevOps/infra tooling, networking, etc. -- see
    role_classification.py's job_requires_domain_assessment()).

    Real ATS platforms (TestGorilla, iMocha, Vervoe, Mettl) assess
    exactly these roles the same way: domain-knowledge multiple-
    choice + scenario questions, never a live code sandbox, since
    "write and run Terraform" or "write and run ABAP" isn't a
    meaningfully sandboxable exercise the way Python/Java are.
    Column-for-column mirrors SituationalJudgmentTest/AptitudeTest
    on purpose (same autosave, same server-authoritative timers,
    same status vocabulary).
    """

    __tablename__ = "technical_domain_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Which domain this candidate was actually tested on (e.g.
    # "devops", "salesforce", "sap", "blockchain", "embedded",
    # "networking", "rpa") -- picked from the job's required_skills,
    # see role_classification.py's detect_technical_domain().
    domain: Mapped[str] = mapped_column(String(40), nullable=False)

    # Each question's correct_answer lives in here too -- never
    # returned to the candidate as-is; the API layer strips it.
    questions: Mapped[str] = mapped_column(Text, nullable=False)

    answers: Mapped[str | None] = mapped_column(Text, nullable=True)

    draft_answers: Mapped[str | None] = mapped_column(Text, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_progress")

    question_source: Mapped[str] = mapped_column(String(20), nullable=False, default="fallback")

    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exam_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

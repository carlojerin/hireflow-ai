from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: INTERVIEW CONTEXT (Task 5H)
#
# A structured snapshot of everything the interview brain is
# allowed to know about this one candidate/job/attempt, built
# ONCE per interview from data that already exists elsewhere in
# the pipeline (job posting, candidate profile/resume analysis,
# AptitudeTest, ProgrammingAssessmentEvidenceV2 — see Task 5G).
# Nothing here is invented: every field is either a real stored
# value or an explicit "not available" marker (never fabricated
# to fill a gap — Section 3 of the task spec).
#
# This is intentionally NOT persisted verbatim across turns —
# rebuilding it from the database on every turn is cheap (no
# Gemini call) and guarantees the brain never acts on stale
# data. What IS persisted per-session is the InterviewBlueprintV2
# derived from it (see interview_blueprint_v2.py), which must
# stay stable for the whole attempt.
# ============================================================


class JobContextV2(BaseModel):

    title: str
    company: str
    description: str = ""
    required_skills: str = ""
    experience_level_hint: Optional[str] = None
    # Task 9: the rest of what the job actually says, so questions can be grounded in it.
    responsibilities: str = ""
    requirements: str = ""
    preferred_skills: str = ""
    technologies: str = ""
    employment_type: Optional[str] = None
    work_mode: Optional[str] = None
    experience_required: Optional[str] = None


class CandidateContextV2(BaseModel):

    full_name: Optional[str] = None
    experience_level: Optional[str] = None
    education: Optional[str] = None
    current_role: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    resume_summary: Optional[str] = None
    resume_projects_or_experience: list[str] = Field(default_factory=list)
    # Task 9: what the candidate's own JobPilot profile says (not only the resume analysis).
    headline: Optional[str] = None
    about: Optional[str] = None
    profile_experience: Optional[str] = None
    profile_projects: Optional[str] = None
    certifications: Optional[str] = None
    achievements: Optional[str] = None
    preferred_role: Optional[str] = None


class AptitudeEvidenceV2(BaseModel):

    available: bool = False
    percentage: Optional[float] = None
    passed: Optional[bool] = None
    # category -> "strong" | "weak" | "mixed", derived deterministically
    # from stored per-question category + correctness (never invented).
    category_results: dict[str, str] = Field(default_factory=dict)


class ProgrammingEvidenceSummaryV2(BaseModel):
    """
    A candidate-context-safe projection of programming_assessment_
    evidence_v2.ProgrammingAssessmentEvidenceV2 (Task 5G) — kept as
    a separate, small pydantic model here (rather than importing
    the dataclass directly into the prompt-building path) so this
    module's prompt payload stays exactly as large as it needs to
    be, never the full skill_evidence list with every raw test
    execution detail.
    """

    available: bool = False
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    insufficient_evidence: list[str] = Field(default_factory=list)
    technologies_demonstrated: list[str] = Field(default_factory=list)
    languages_used: list[str] = Field(default_factory=list)
    highest_difficulty_passed: Optional[str] = None

    # ONE short excerpt of the candidate's OWN submitted code for their
    # highest-scoring answered question (question_title/language/code_excerpt),
    # or None when unavailable -- lets the interview ground a follow-up in
    # real code ("why a TreeMap here?") instead of only a derived skill tag.
    # Deliberately just one snippet, not the full submission history, to keep
    # this schema's own "as large as it needs to be" design intact.
    representative_snippet: Optional[dict] = None


class InterviewContextV2(BaseModel):

    job: JobContextV2
    candidate: CandidateContextV2
    aptitude: AptitudeEvidenceV2
    programming: ProgrammingEvidenceSummaryV2

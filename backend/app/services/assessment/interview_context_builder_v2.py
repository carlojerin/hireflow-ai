import json

from backend.app.schemas.interview_context_v2 import (
    AptitudeEvidenceV2,
    CandidateContextV2,
    InterviewContextV2,
    JobContextV2,
    ProgrammingEvidenceSummaryV2,
)
from backend.app.schemas.interview_blueprint_v2 import (
    InterviewBlueprintV2,
    InterviewCompetencyV2,
)
from backend.app.services.ai_candidate_matching import parse_json_list
from backend.app.services.assessment.role_classification import required_skills_are_technical


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2: CONTEXT + BLUEPRINT BUILDERS
# (Task 5H)
#
# Deterministic, Gemini-free construction of InterviewContextV2
# and InterviewBlueprintV2 from data this project already
# computes elsewhere:
#   - job: the Job row itself.
#   - candidate: CandidateProfile + the latest ResumeAnalysis.
#   - aptitude: the candidate's own AptitudeTest row, re-deriving
#     a category-level strong/weak breakdown from its stored
#     questions/answers exactly the way hr_assessment_review.py's
#     review_aptitude() already does (never a second scoring path).
#   - programming: programming_assessment_evidence_v2's own
#     ProgrammingAssessmentEvidenceV2 (Task 5G), reused unchanged.
#
# Nothing here calls Gemini. The BLUEPRINT (which competencies
# matter, and what's already known about each) is intentionally
# deterministic — see Task 5H Section 5: "the exact scoring model
# must be inspectable" applies equally to what gets assessed at
# all, not just how it's scored.
# ============================================================


def _aptitude_category_results(aptitude_test) -> dict[str, str]:

    if not aptitude_test or not aptitude_test.questions:
        return {}

    try:
        stored_questions = json.loads(aptitude_test.questions)
        submitted_answers = json.loads(aptitude_test.answers) if aptitude_test.answers else []
    except (json.JSONDecodeError, TypeError):
        return {}

    selected_by_id = {
        a.get("question_id"): a.get("selected_answer") for a in submitted_answers
    }

    tally: dict[str, list[bool]] = {}

    for question in stored_questions:

        category = question.get("category") or "General"
        correct = question.get("correct_answer")
        selected = selected_by_id.get(question.get("id"))

        tally.setdefault(category, []).append(selected is not None and selected == correct)

    results: dict[str, str] = {}

    for category, outcomes in tally.items():

        if not outcomes:
            continue

        ratio = sum(outcomes) / len(outcomes)

        if ratio >= 0.75:
            results[category] = "strong"
        elif ratio <= 0.25:
            results[category] = "weak"
        else:
            results[category] = "mixed"

    return results


def _plain_text(value) -> str | None:
    """A JSON list (as stored by the resume analyser) or plain text -> one readable string."""

    if value is None:
        return None

    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
        return "; ".join(items) or None

    text = str(value).strip()

    if not text:
        return None

    if text.startswith("["):
        items = parse_json_list(text)
        if items:
            return "; ".join(str(i).strip() for i in items if str(i).strip()) or None

    return text


def _merge_skills(*groups) -> list[str]:

    merged: list[str] = []
    seen: set[str] = set()

    for group in groups:
        for skill in group or []:
            key = str(skill).strip().lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(str(skill).strip())

    return merged


def _name_for_greeting(job, user, resume_analysis) -> str | None:
    """The name written on the candidate's resume, then the name HR typed on the invite, then the account name."""

    from backend.app.services.resume.contact_extractor import candidate_name_from_resume

    name = candidate_name_from_resume(getattr(resume_analysis, "raw_text", None))

    if name:
        return name

    try:
        from sqlalchemy.orm import object_session
        from backend.app.models.candidate_invite import CandidateInvite

        db = object_session(user) or object_session(resume_analysis)
        job_id = getattr(job, "id", None)
        user_id = getattr(user, "id", None)

        if db is not None and job_id and user_id:
            invite = (
                db.query(CandidateInvite)
                .filter(CandidateInvite.claimed_user_id == user_id, CandidateInvite.job_id == job_id)
                .order_by(CandidateInvite.id.desc())
                .first()
            )
            invited = candidate_name_from_resume(getattr(invite, "candidate_name", None)) if invite else None
            if invited:
                return invited
    except Exception:
        pass

    return getattr(user, "full_name", None)


def build_interview_context_v2(
    job,
    user,
    profile,
    resume_analysis,
    aptitude_test,
    programming_evidence,
) -> InterviewContextV2:
    """
    programming_evidence: a programming_assessment_evidence_v2.
    ProgrammingAssessmentEvidenceV2 instance, or None if the
    candidate never took a V2 programming assessment (e.g. legacy
    ProgrammingTest path — see build_programming_evidence_summary_
    for_legacy() below for that case).
    """

    job_ctx = JobContextV2(
        title=getattr(job, "title", "") or "",
        company=getattr(job, "company", "") or "",
        description=getattr(job, "description", "") or "",
        required_skills=getattr(job, "required_skills", "") or "",
        responsibilities=getattr(job, "responsibilities", "") or "",
        requirements=getattr(job, "requirements", "") or "",
        preferred_skills=getattr(job, "preferred_skills", "") or "",
        technologies=getattr(job, "technologies", "") or "",
        employment_type=getattr(job, "employment_type", None),
        work_mode=getattr(job, "work_mode", None),
        experience_required=getattr(job, "experience", None),
    )

    # resume skills first, then anything the candidate's own profile adds (never only one of them)
    resume_skills = parse_json_list(resume_analysis.skills) if resume_analysis and resume_analysis.skills else []
    profile_skills = parse_json_list(profile.skills) if profile and profile.skills else []
    skills = _merge_skills(resume_skills, profile_skills)

    experience_items = (
        parse_json_list(resume_analysis.experience)
        if resume_analysis and resume_analysis.experience
        else []
    )

    experience_level = (
        (resume_analysis and resume_analysis.experience_level)
        or (profile and profile.experience_level)
        or getattr(user, "experience_level", None)
    )

    candidate_ctx = CandidateContextV2(
        full_name=_name_for_greeting(job, user, resume_analysis),
        experience_level=experience_level,
        education=_plain_text(resume_analysis.education if resume_analysis else None) or _plain_text(profile.education if profile else None)
        or _plain_text(getattr(user, "education", None)),
        current_role=getattr(user, "current_role", None),
        skills=skills,
        resume_summary=resume_analysis.summary if resume_analysis else None,
        resume_projects_or_experience=experience_items[:6],
        headline=getattr(profile, "headline", None) if profile else None,
        about=getattr(profile, "about", None) if profile else None,
        profile_experience=_plain_text(getattr(profile, "experience", None)) if profile else None,
        profile_projects=_plain_text(getattr(profile, "projects", None)) if profile else None,
        certifications=_plain_text(getattr(profile, "certifications", None)) if profile else None,
        achievements=_plain_text(getattr(profile, "achievements", None)) if profile else None,
        preferred_role=(getattr(profile, "preferred_roles", None) if profile else None) or getattr(user, "preferred_role", None),
    )

    aptitude_ctx = AptitudeEvidenceV2(
        available=aptitude_test is not None and aptitude_test.status == "completed",
        percentage=(
            round(100.0 * aptitude_test.score / aptitude_test.total_questions, 2)
            if aptitude_test and aptitude_test.score is not None and aptitude_test.total_questions
            else None
        ),
        passed=aptitude_test.passed if aptitude_test else None,
        category_results=_aptitude_category_results(aptitude_test),
    )

    if programming_evidence is not None:

        programming_ctx = ProgrammingEvidenceSummaryV2(
            available=True,
            strengths=list(programming_evidence.strengths),
            weaknesses=list(programming_evidence.weaknesses),
            insufficient_evidence=list(programming_evidence.insufficient_evidence),
            technologies_demonstrated=list(programming_evidence.technologies_demonstrated),
            languages_used=list(programming_evidence.languages_used),
            highest_difficulty_passed=programming_evidence.highest_difficulty_passed,
            representative_snippet=getattr(programming_evidence, "representative_snippet", None),
        )

    else:

        programming_ctx = ProgrammingEvidenceSummaryV2(available=False)

    return InterviewContextV2(
        job=job_ctx,
        candidate=candidate_ctx,
        aptitude=aptitude_ctx,
        programming=programming_ctx,
    )


# ============================================================
# BLUEPRINT DERIVATION
#
# Job-driven, never blind (Task 5H Section 4): the competency list
# comes from the job's OWN required_skills plus two universal
# baseline competencies (Communication, Problem Solving) that apply
# to every role this project supports today. role_shape comes from
# role_classification.py's shared technical/non-technical heuristic
# (whether required_skills names an actual language/technical-stack
# term) — the SAME classifier the pipeline's programming-stage gate
# uses (stage_progression_service.py), so a job is never technical
# in one place and non-technical in the other.
# ============================================================

_UNIVERSAL_COMPETENCIES = ("Communication", "Problem Solving")


def _split_skills(required_skills: str) -> list[str]:

    return [s.strip() for s in required_skills.split(",") if s.strip()]


def build_interview_blueprint_v2(context: InterviewContextV2) -> InterviewBlueprintV2:

    skill_names = _split_skills(context.job.required_skills) or [context.job.title]

    role_shape = (
        "technical"
        if context.programming.available or required_skills_are_technical(
            context.job.required_skills, extra_text=context.job.description
        )
        else "non_technical"
    )

    competencies: list[InterviewCompetencyV2] = []
    seen_names: set[str] = set()

    def _add(name: str, priority: str, target_evidence: str):

        key = name.strip().lower()

        if not name.strip() or key in seen_names:
            return

        seen_names.add(key)

        known_strength = any(name.strip().lower() == s.strip().lower() for s in context.programming.strengths)
        known_weakness = any(name.strip().lower() == s.strip().lower() for s in context.programming.weaknesses)

        if not known_strength and not known_weakness:
            # fall back to aptitude category signal for non-programming skills
            for category, verdict in context.aptitude.category_results.items():
                if category.strip().lower() == key:
                    known_strength = verdict == "strong"
                    known_weakness = verdict == "weak"

        if known_weakness:
            status = "NEEDS_VERIFICATION"
        elif known_strength:
            status = "KNOWN"
        else:
            status = "UNKNOWN"

        source = None
        if known_strength or known_weakness:
            source = "programming" if name.strip().lower() in {
                s.lower() for s in (context.programming.strengths + context.programming.weaknesses)
            } else "aptitude"

        competencies.append(InterviewCompetencyV2(
            name=name.strip(),
            priority=priority,
            target_evidence=target_evidence,
            known_strength=known_strength,
            known_weakness=known_weakness,
            evidence_status=status,
            source=source,
        ))

    for skill in skill_names[:5]:
        _add(
            skill, "high",
            f"Real experience applying {skill}, not just textbook definitions.",
        )

    # Programming weaknesses/insufficient-evidence areas are
    # job-relevant BY DEFINITION (they came from a programming
    # assessment generated for THIS job) — always prioritized even
    # if not literally named in required_skills, per Section 10:
    # "should NOT waste many questions proving what the programming
    # assessment already strongly established" implies the inverse
    # too -- prioritize what it did NOT establish.
    for weak_area in context.programming.weaknesses + context.programming.insufficient_evidence:
        _add(weak_area, "high", f"Evidence of real competence in {weak_area}.")

    for name in _UNIVERSAL_COMPETENCIES:
        _add(name, "medium", f"Clear, well-reasoned explanation demonstrating {name.lower()}.")

    # The closing questions every candidate gets (which AI tools they use + a follow-up on the one they name, expected
    # package, how they would work in the company) are NOT competencies the model picks: interview_integration_v2's
    # _enforce_closing_sequence() asks them, in that order, after the model's own FREE_TURNS resume / job questions.
    # Keeping them out of the blueprint is what stops the model from asking them early or twice.

    return InterviewBlueprintV2(competencies=competencies, role_shape=role_shape)

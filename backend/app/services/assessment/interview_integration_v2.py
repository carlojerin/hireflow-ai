import re
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.aptitude_test import AptitudeTest
from backend.app.models.programming_test import ProgrammingTest
from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
from backend.app.models.job_application import JobApplication
from backend.app.models.job import Job
from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2

from backend.app.services.application_status_history import record_status_change
from backend.app.services.assessment.assignment_service import is_assigned
from backend.app.services.assessment.interview_integration import (
    finalize_interview_if_complete,
)
from backend.app.services.assessment.interview_context_builder_v2 import (
    build_interview_blueprint_v2,
    build_interview_context_v2,
)
from backend.app.services.assessment.interview_brain_v2 import (
    analyze_and_decide_next_v2,
    generate_opening_v2,
)
from backend.app.services.assessment.interview_final_evaluator_v2 import (
    evaluate_interview_final_v2,
)
from backend.app.schemas.interview_blueprint_v2 import InterviewBlueprintV2
from backend.app.schemas.interview_turn_v2 import AnswerAnalysisV2


logger = logging.getLogger("jobpilot.assessment.interview_integration_v2")


# ============================================================
# JOBPILOT AI — AI INTERVIEW V2 <-> LIVE ASSESSMENT BRIDGE
# (Task 5H)
#
# Mirrors interview_integration.py's (legacy) architecture and
# EVERY business rule it already enforces — same eligibility gate
# (must have passed programming), same assignment gate, same
# INTERVIEW_START_STATUS, same application-status vocabulary
# (interview_pending/interview_passed/interview_failed/
# interview_expired) — DELIBERATELY REUSED so every existing HR
# pipeline view/filter that already queries by these status
# strings keeps working unchanged for a V2 candidate, exactly the
# same pattern programming_integration_v2.py established for the
# programming stage in Task 5C.
#
# NEVER modifies the legacy InterviewTest/InterviewResponse tables
# or rows. Persists exclusively into the NEW, additive
# ai_interview_sessions_v2 / ai_interview_turns_v2 tables.
#
# finalize_interview_if_complete() is REUSED, UNCHANGED, from the
# legacy module — it only ever reads `test.status`, so it works
# identically for an AIInterviewSessionV2 instance without any
# code duplication.
#
# NO HIRING DECISION (Section 38): `passed` below is a documented,
# deterministic PIPELINE-PROGRESSION signal (does the application
# advance to "interview_passed" vs "interview_failed" status,
# exactly the same two pre-existing status strings the legacy flow
# already uses) — never a hire/reject verdict. HR reviews the full
# evidence regardless of this boolean.
# ============================================================

INTERVIEW_V2_START_STATUS = "programming_passed"   # kept for compatibility: some older code may still reference the single-value name
INTERVIEW_V2_START_STATUSES = {"programming_passed", "sjt_passed", "tda_passed", "programming_failed", "sjt_failed", "tda_failed"}
INTERVIEW_V2_DEADLINE_MINUTES = 30

# Bounds (Section 18/19): a real conversation needs room to probe
# more than one competency with at least one follow-up each, but
# must stay boundeds for cost/time/candidate-fatigue reasons.
# MIN=4 allows at least 2 competencies with 1 follow-up each before
# END_INTERVIEW is even considered; MAX=8 caps a single attempt at
# roughly the same real-world length as the legacy interview's
# fixed 5 questions plus follow-up room, never unbounded.
INTERVIEW_V2_MIN_TURNS = 8
INTERVIEW_V2_MAX_TURNS = 8

INTERVIEW_V2_READY_STAGES = ("ai_interview", "completed")

# A competency counts as "sufficiently assessed" for the pass/fail
# pipeline signal when its best demonstrated level is at least
# moderate. Documented, deterministic, inspectable (Section 17).
_PASS_LABELS = {"Strong evidence", "Moderate evidence"}


class InterviewIntegrationV2Error(Exception):

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _get_passed_programming_test(db: Session, application: JobApplication) -> ProgrammingTest | None:

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if test and test.status == "completed":
        return test

    return None


def _stage_3_passed(db: Session, application: JobApplication) -> bool:
    """
    "Legacy first, V2 fallback, then SJT/TDA" — the same pattern
    Task 5F/5G established throughout the HR-facing views: a
    candidate takes EXACTLY ONE of the legacy single-language
    ProgrammingTest, the V2/V3 ProgrammingAssessmentV2, the
    Situational Judgment Test, or the Technical Domain Assessment —
    whichever role_classification.py routed them to — never more
    than one. This was previously "_programming_passed" and only
    checked the two programming tables, which meant a non-technical
    or domain candidate who genuinely passed their SJT/TDA could
    never start the AI interview at all: application.status was
    "sjt_passed"/"tda_passed", never "programming_passed", so this
    gate silently failed every one of them. Found and fixed together
    with the matching INTERVIEW_V2_START_STATUS and current_stage
    gates below.
    """

    if _get_passed_programming_test(db, application) is not None:
        return True

    v2_assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(
            ProgrammingAssessmentV2.application_id == application.id,
            ProgrammingAssessmentV2.status == "completed",
                    )
        .first()
    )

    if v2_assessment is not None:
        return True

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest
    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    sjt = (
        db.query(SituationalJudgmentTest)
        .filter(
            SituationalJudgmentTest.application_id == application.id,
            SituationalJudgmentTest.status == "completed",
                    )
        .first()
    )

    if sjt is not None:
        return True

    tda = (
        db.query(TechnicalDomainAssessment)
        .filter(
            TechnicalDomainAssessment.application_id == application.id,
            TechnicalDomainAssessment.status == "completed",
                    )
        .first()
    )

    return tda is not None


# Kept as an alias: both names describe "did the candidate pass whichever
# stage-3 assessment they were actually given", called from several places
# below under the original name.
_programming_passed = _stage_3_passed


def _mcq_category_evidence(questions: list[dict], answers_json: str | None, extra_technologies: list[str] | None = None):
    """
    Builds a ProgrammingAssessmentEvidenceV2-shaped evidence object
    from a plain MCQ attempt (SJT or TDA — both store questions as
    [{id, category, question, options, correct_answer}] and answers
    as [{question_id, selected_answer}], the exact same shape score_sjt/
    score_tda already grade with). "skill" here is the question's own
    "category" (SJT: communication/teamwork/leadership/etc; TDA: the
    single domain every question in the set shares) — real, inspectable
    labels, never invented. A category is a strength only if EVERY
    question answered in it was correct; a weakness if at least one
    was wrong; insufficient_evidence if the category's question was
    never answered at all (e.g. an auto-submitted, partially-done
    attempt).
    """

    from backend.app.services.assessment.programming_assessment_evidence_v2 import (
        ProgrammingAssessmentEvidenceV2,
        SkillEvidenceItem,
    )

    try:
        submitted = json.loads(answers_json) if answers_json else []
    except Exception:
        submitted = []

    selected_by_qid = {
        a.get("question_id"): a.get("selected_answer")
        for a in submitted
        if a.get("question_id") is not None
    }

    skill_evidence = []
    by_category_outcomes: dict[str, list[str]] = {}

    for question in questions:

        category = str(question.get("category") or "general")
        qid = question.get("id")
        selected = selected_by_qid.get(qid)

        if selected is None:
            outcome = "not_attempted"
        elif selected == question.get("correct_answer"):
            outcome = "passed"
        else:
            outcome = "failed"

        by_category_outcomes.setdefault(category, []).append(outcome)

        skill_evidence.append(SkillEvidenceItem(
            skill=category, question_id=qid or 0, difficulty="standard",
            outcome=outcome, earned_fraction=1.0 if outcome == "passed" else 0.0,
            language_used=None,
        ))

    strengths, weaknesses, insufficient = [], [], []

    for category, outcomes in by_category_outcomes.items():

        if all(o == "not_attempted" for o in outcomes):
            insufficient.append(category)
        elif all(o == "passed" for o in outcomes if o != "not_attempted"):
            strengths.append(category)
        else:
            weaknesses.append(category)

    return ProgrammingAssessmentEvidenceV2(
        skill_evidence=skill_evidence,
        strengths=strengths,
        weaknesses=weaknesses,
        insufficient_evidence=insufficient,
        technologies_demonstrated=extra_technologies or [],
        languages_used=[],
        highest_difficulty_passed=None,
    )


def _get_sjt_evidence(db: Session, application: JobApplication):
    """SJT counterpart of _get_programming_evidence() — same honest
    "None if not completed" contract, same evidence shape, so the
    interview blueprint builder never has to know which stage-3
    assessment actually produced it."""

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest

    assessment = (
        db.query(SituationalJudgmentTest)
        .filter(
            SituationalJudgmentTest.application_id == application.id,
            SituationalJudgmentTest.status == "completed",
        )
        .first()
    )

    if not assessment:
        return None

    try:
        questions = json.loads(assessment.questions)
    except Exception:
        return None

    return _mcq_category_evidence(questions, assessment.answers)


def _get_tda_evidence(db: Session, application: JobApplication):
    """Technical Domain Assessment counterpart of _get_programming_evidence()."""

    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    assessment = (
        db.query(TechnicalDomainAssessment)
        .filter(
            TechnicalDomainAssessment.application_id == application.id,
            TechnicalDomainAssessment.status == "completed",
        )
        .first()
    )

    if not assessment:
        return None

    try:
        questions = json.loads(assessment.questions)
    except Exception:
        return None

    return _mcq_category_evidence(questions, assessment.answers, extra_technologies=[assessment.domain])


def _get_programming_evidence(db: Session, application: JobApplication):
    """
    Returns a programming_assessment_evidence_v2.ProgrammingAssessmentEvidenceV2
    for whichever stage-3 assessment the candidate actually completed —
    the V2 programming assessment if they took one, else their SJT or
    TDA attempt (see _get_sjt_evidence/_get_tda_evidence) — or None if
    none is completed yet (honest "not available" — Section 3: never
    fabricate evidence).
    """

    sjt_evidence = _get_sjt_evidence(db, application)
    if sjt_evidence is not None:
        return sjt_evidence

    tda_evidence = _get_tda_evidence(db, application)
    if tda_evidence is not None:
        return tda_evidence

    from backend.app.services.assessment.programming_scoring_v2 import score_assessment
    from backend.app.services.assessment.programming_assessment_evidence_v2 import (
        build_programming_evidence_v2,
    )
    from backend.app.models.programming_submission_v2 import ProgrammingSubmissionV2
    from backend.app.schemas.programming_question_v2 import ProgrammingQuestionSetV2

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(
            ProgrammingAssessmentV2.application_id == application.id,
            ProgrammingAssessmentV2.status == "completed",
        )
        .first()
    )

    if not assessment:
        return None

    try:
        question_set = ProgrammingQuestionSetV2.model_validate_json(assessment.question_set_json)
    except Exception:
        return None

    submission_rows = (
        db.query(ProgrammingSubmissionV2)
        .filter(
            ProgrammingSubmissionV2.assessment_id == assessment.id,
            ProgrammingSubmissionV2.submission_kind == "submit",
        )
        .all()
    )

    from backend.app.api.routes.hr_assessment_review import _reconstruct_question_result

    submission_by_qid = {row.question_id: row for row in submission_rows}
    question_results = []

    for question in question_set.main_questions:

        row = submission_by_qid.get(question.question_id)

        if row and row.result_json:
            question_results.append(
                _reconstruct_question_result(question.question_id, question.points, json.loads(row.result_json))
            )

    score_result = score_assessment(question_set, question_results)

    evidence = build_programming_evidence_v2(question_set, score_result)

    # Fallback question sets carry no skills metadata, so the evidence names skills "question_<id>".
    # The interviewer must never see that placeholder: label them with the question's own title.
    import dataclasses
    import re as _re

    titles = {f"question_{q.question_id}": q.title for q in question_set.main_questions}

    def _label(names):
        return [titles.get(n, n) for n in names]

    # One short excerpt of the candidate's OWN submitted code -- the highest-
    # scoring question they actually answered -- so the interview can ground
    # a follow-up in real, specific code instead of only a derived skill tag.
    # Never the whole submission history (keeps the prompt payload small,
    # matching ProgrammingEvidenceSummaryV2's own stated design principle).
    representative_snippet = None

    scored_answered = [
        (qr, submission_by_qid.get(qr.question_id))
        for qr in question_results
        if submission_by_qid.get(qr.question_id) and submission_by_qid[qr.question_id].source_code
    ]

    if scored_answered:

        best_result, best_row = max(
            scored_answered,
            key=lambda pair: (pair[0].earned_points / pair[0].max_points) if pair[0].max_points else 0.0,
        )

        best_question = next(
            (q for q in question_set.main_questions if q.question_id == best_result.question_id), None,
        )

        if best_question:
            representative_snippet = {
                "question_title": best_question.title,
                "language": best_row.language,
                "code_excerpt": (best_row.source_code or "")[:800],
            }

    try:
        return dataclasses.replace(
            evidence,
            strengths=_label(evidence.strengths),
            weaknesses=_label(evidence.weaknesses),
            insufficient_evidence=_label(evidence.insufficient_evidence),
            representative_snippet=representative_snippet,
        )
    except Exception:
        return evidence


def get_interview_readiness_v2(
    current_stage: str,
    application: JobApplication,
    db: Session,
) -> dict:

    already_advanced = current_stage in INTERVIEW_V2_READY_STAGES
    programming_passed = _programming_passed(db, application)
    assigned = already_advanced or is_assigned(db, application.id, "ai_interview")

    ready = already_advanced or (
        current_stage == "programming" and programming_passed and assigned
    )

    return {"ready": ready, "prior_passed": programming_passed, "assigned": assigned}


def start_interview_v2(
    db: Session,
    session_record,
    application: JobApplication,
    job,
    user,
    profile,
    resume_analysis,
    use_llm: bool = True,
) -> tuple[AIInterviewSessionV2, bool]:

    current_stage = session_record.current_stage

    if current_stage not in INTERVIEW_V2_READY_STAGES and current_stage != "programming":

        raise InterviewIntegrationV2Error(
            400, "Complete the programming assessment before starting the AI interview.",
        )

    existing = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if existing:
        return existing, False

    if not _programming_passed(db, application):

        raise InterviewIntegrationV2Error(
            400, "You must pass the programming assessment before starting the AI interview.",
        )

    if not is_assigned(db, application.id, "ai_interview"):

        raise InterviewIntegrationV2Error(400, "AI Interview has not been assigned yet.")

    if application.status not in INTERVIEW_V2_START_STATUSES:

        raise InterviewIntegrationV2Error(
            400,
            f"This application is not eligible for the AI interview. Current status: {application.status}.",
        )

    aptitude_test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    programming_evidence = _get_programming_evidence(db, application)

    context = build_interview_context_v2(
        job=job, user=user, profile=profile, resume_analysis=resume_analysis,
        aptitude_test=aptitude_test, programming_evidence=programming_evidence,
    )

    blueprint = build_interview_blueprint_v2(context)

    opening, generated_by = generate_opening_v2(context, blueprint, use_llm=use_llm)

    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=INTERVIEW_V2_DEADLINE_MINUTES)

    session = AIInterviewSessionV2(
        application_id=application.id,
        assessment_session_id=getattr(session_record, "id", None),
        status="in_progress",
        current_turn=1,
        blueprint_json=blueprint.model_dump_json(),
        conversation_summary="",
        final_evaluation_json=None,
        generated_by=generated_by,
        assigned_at=now,
        expires_at=expires_at,
        started_at=now,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    db.add(session)
    db.flush()

    first_turn = AIInterviewTurnV2(
        session_id=session.id,
        turn_number=1,
        question=opening.first_question,
        question_type=opening.question_type,
        competency=opening.competency,
        difficulty=opening.difficulty,
        candidate_answer=None,
        answer_analysis_json=None,
        next_action=None,
        generated_by=generated_by,
        asked_at=now,
        answered_at=None,
    )

    db.add(first_turn)

    session_record.current_stage = "ai_interview"
    session_record.updated_at = now

    record_status_change(
        db, application, to_status="interview_pending",
        changed_by_type="candidate", changed_by_id=application.user_id,
        note="AI Interview (V2, adaptive) started automatically after passing the programming assessment.",
    )

    application.updated_date = now

    db.commit()
    db.refresh(session)
    db.refresh(session_record)
    db.refresh(application)

    session._opening_message = opening.opening_message  # transient, not persisted

    return session, True


def _load_blueprint(session: AIInterviewSessionV2) -> InterviewBlueprintV2:
    return InterviewBlueprintV2.model_validate_json(session.blueprint_json)


def candidate_view_v2(db: Session, session: AIInterviewSessionV2, job=None) -> dict:
    """
    Candidate-safe view — NEVER includes answer_analysis_json,
    next_action reasoning, blueprint priorities/known_strength/
    known_weakness, or final_evaluation_json's competency labels
    (Section 20: no hidden evaluation/confidence/HR notes shown to
    the candidate).

    Takes the CALLER's own `db` session (never opens a second,
    independent connection) — reusing the same session every other
    function in this module already receives, and critically what
    keeps this function correct against an isolated test database
    rather than silently reading through to the real app engine.
    """

    turns = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .all()
    )

    current_turn = next((t for t in turns if t.candidate_answer is None), None)

    answered_count = sum(1 for t in turns if t.candidate_answer is not None)

    blueprint = _load_blueprint(session)

    # Progress shown to the candidate is INTENTIONALLY coarse: just
    # "how many turns have touched this competency so far", never a
    # strength/weakness label or numeric score (Section 20).
    touched_counts: dict[str, int] = {}
    for t in turns:
        touched_counts[t.competency] = touched_counts.get(t.competency, 0) + 1

    progress = [
        {"competency": c.name, "turns_touched": touched_counts.get(c.name, 0)}
        for c in blueprint.competencies
    ]

    return {
        "status": session.status,
        "assigned_at": session.assigned_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "current_turn": session.current_turn,
        "min_turns": INTERVIEW_V2_MIN_TURNS,
        "max_turns": INTERVIEW_V2_MAX_TURNS,
        "current_question": (
            {
                "turn_number": current_turn.turn_number,
                "question": current_turn.question,
                "question_type": current_turn.question_type,
                "competency": current_turn.competency,
                "difficulty": current_turn.difficulty,
            }
            if current_turn else None
        ),
        "answered_turns": [
            {"turn_number": t.turn_number, "question": t.question, "answer": t.candidate_answer}
            for t in turns if t.candidate_answer is not None
        ],
        "progress": progress,
        "job": ({"title": job.title, "company": job.company} if job is not None else None),
    }


def expire_if_due_v2(db: Session, session: AIInterviewSessionV2, application: JobApplication, session_record) -> bool:

    if session.status == "expired":
        return True

    if session.status == "in_progress" and datetime.utcnow() > session.expires_at:

        _finalize_session(db, session, application, session_record, reason="Deadline passed")
        return True

    return False



def _resume_summary_text(db: Session, application: JobApplication) -> str:
    """A short text of the candidate's resume analysis (summary, skills, experience) for the HR report."""

    from backend.app.models.resume import Resume
    from backend.app.models.resume_analysis import ResumeAnalysis

    resume = db.query(Resume).filter(Resume.user_id == application.user_id).order_by(Resume.id.desc()).first()
    analysis = db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).first() if resume else None

    if analysis is None:
        return ""

    parts = [
        ("Summary", analysis.summary), ("Skills", analysis.skills), ("Experience", analysis.experience),
        ("Experience level", analysis.experience_level), ("Education", analysis.education),
    ]
    return " | ".join(f"{label}: {str(value)[:500]}" for label, value in parts if value)


def _earlier_stage_facts(db: Session, application: JobApplication) -> dict:
    """How the candidate did in the earlier stages (a failed stage no longer stops them, so HR must see it)."""

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest
    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment

    def _verdict(passed):
        return "passed" if passed else "did not pass"

    facts = {}

    apt = db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).first()
    if apt is not None and apt.status == "completed" and apt.total_questions:
        facts["Aptitude"] = f"{apt.score}/{apt.total_questions} ({round(100 * (apt.score or 0) / apt.total_questions)}%), {_verdict(apt.passed)}"

    for label, model in (("Situational judgment", SituationalJudgmentTest), ("Technical domain test", TechnicalDomainAssessment)):
        row = db.query(model).filter(model.application_id == application.id).first()
        if row is not None and row.status == "completed" and row.total_questions:
            facts[label] = f"{row.score}/{row.total_questions}, {_verdict(row.passed)}"

    prog = db.query(ProgrammingAssessmentV2).filter(ProgrammingAssessmentV2.application_id == application.id).first()
    if prog is not None and prog.status == "completed":
        facts["Programming"] = f"{prog.score}/{prog.total_score}, {_verdict(prog.passed)}"

    return facts



def _store_candidate_report(session_id: int) -> None:
    """Writes the detailed HR report into the finished interview's final evaluation (runs in a background thread)."""

    from backend.app.database.database import SessionLocal
    from backend.app.services.assessment.interview_candidate_report_v2 import generate_interview_candidate_report

    db = SessionLocal()
    try:
        session = db.query(AIInterviewSessionV2).filter(AIInterviewSessionV2.id == session_id).first()
        if session is None or not session.final_evaluation_json:
            return

        application = db.query(JobApplication).filter(JobApplication.id == session.application_id).first()
        job = db.query(Job).filter(Job.id == application.job_id).first() if application else None
        answered = (
            db.query(AIInterviewTurnV2)
            .filter(AIInterviewTurnV2.session_id == session.id, AIInterviewTurnV2.candidate_answer.isnot(None))
            .order_by(AIInterviewTurnV2.turn_number.asc()).all()
        )

        report = generate_interview_candidate_report(
            answered, job, resume_summary=_resume_summary_text(db, application), stage_facts=_earlier_stage_facts(db, application),
        )

        stored = json.loads(session.final_evaluation_json)
        stored["candidate_report"] = report
        session.final_evaluation_json = json.dumps(stored)
        db.commit()

    except Exception as exc:  # noqa: BLE001 - the report is a bonus: never break anything
        logger.warning("Detailed interview report could not be stored: %s", exc)
        db.rollback()
    finally:
        db.close()


def _schedule_candidate_report(db: Session, session_id: int) -> None:
    """The report takes several seconds (an LLM call): do it AFTER the interview is saved, off the request, so the candidate
    hears the closing and the page closes at once. Only for the app's real database (tests use their own in-memory engine)."""

    import threading

    from backend.app.database.database import engine

    if db.get_bind() is not engine:
        return

    threading.Thread(target=_store_candidate_report, args=(session_id,), daemon=True, name="interview-report").start()


def _finalize_session(db: Session, session: AIInterviewSessionV2, application: JobApplication, session_record, reason: str) -> None:

    turns = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .all()
    )

    answered_turns = [t for t in turns if t.candidate_answer is not None]

    now = datetime.utcnow()

    if not answered_turns:

        session.status = "expired"
        session.updated_at = now

        record_status_change(
            db, application, to_status="interview_expired",
            changed_by_type="system", changed_by_id=None,
            note=f"AI Interview (V2) deadline passed without any answered questions. {reason}.",
        )

        application.updated_date = now
        db.commit()
        db.refresh(session)
        db.refresh(application)
        return

    blueprint = _load_blueprint(session)

    analyses = [
        AnswerAnalysisV2.model_validate(json.loads(t.answer_analysis_json))
        for t in answered_turns
        if t.answer_analysis_json
    ]

    job = db.query(Job).filter(Job.id == application.job_id).first()

    # An independent second pass over the WHOLE finished transcript, never just
    # a re-label of the same in-the-moment per-turn judgments -- see
    # interview_final_evaluator_v2.py's own module header. Falls back to the
    # plain compute_final_evaluation_v2() aggregation internally on any
    # failure, so this call itself never raises.
    final_eval = evaluate_interview_final_v2(
        answered_turns, analyses, [c.name for c in blueprint.competencies],
        job_title=getattr(job, "title", "") or "", required_skills=getattr(job, "required_skills", "") or "",
    )

    high_priority_names = {c.name for c in blueprint.competencies if c.priority == "high"}
    high_priority_evals = [e for e in final_eval.competency_evaluations if e.competency in high_priority_names] or final_eval.competency_evaluations

    passed_count = sum(1 for e in high_priority_evals if e.label in _PASS_LABELS)
    passed = bool(high_priority_evals) and passed_count >= max(1, len(high_priority_evals) // 2)

    session.status = "completed"
    session.completed_at = now
    session.updated_at = now
    session.final_evaluation_json = json.dumps({
        **final_eval.model_dump(),
        "passed": passed,
        "candidate_report": None,                 # written a moment later, in the background (see _schedule_candidate_report)
    })

    to_status = "interview_passed" if passed else "interview_failed"

    record_status_change(
        db, application, to_status=to_status,
        changed_by_type="system", changed_by_id=None,
        note=f"AI Interview (V2) completed after {len(answered_turns)} answered turn(s). {reason}.",
    )

    application.updated_date = now

    db.commit()
    db.refresh(session)
    db.refresh(application)

    _schedule_candidate_report(db, session.id)

    if session_record:
        finalize_interview_if_complete(db, session_record, session)


AI_TOOL_COMPETENCY = "AI Tool Fluency"
PACKAGE_COMPETENCY = "Salary Expectations"
WORK_FIT_COMPETENCY = "Closing Thoughts & Fit"

# Six questions about the ROLE, then two about the company side:
#   turns 1-5  the model chooses freely (resume / job / the role's real work)
#   turn 6     how would you use AI to do a real task of THIS role  (AI Tool Fluency)
#   turn 7     expected package                                      (Salary Expectations)
#   turn 8     anything we have not covered + why you fit / how you work (Closing Thoughts & Fit)
FREE_TURNS = 5

AI_TOOL_QUESTIONS = {
    "technical": "Which AI tools have you used recently in your work, like Claude, Cursor, Copilot or ChatGPT, and what do you use them for?",
    "non_technical": "Which AI tools have you used recently in your work, like ChatGPT, Canva, Gemini or Claude, and what do you use them for?",
}

# Known AI tools, matched in the candidate's own words so the follow-up can name the one they actually mentioned.
_AI_TOOL_NAMES = [
    "ChatGPT", "Claude", "Gemini", "Copilot", "GitHub Copilot", "Cursor", "Canva", "Midjourney", "DALL-E", "Notion AI",
    "Grammarly", "Perplexity", "Jasper", "Figma", "Firefly", "Runway", "Leonardo", "Windsurf", "Codeium", "Tabnine",
    "Replit", "v0", "Bolt", "Lovable", "DeepSeek", "Llama", "Mistral", "Zapier", "Otter", "Fireflies", "Copy.ai",
    "Writesonic", "Synthesia", "ElevenLabs", "HubSpot AI", "Salesforce Einstein", "Power BI Copilot", "Tableau",
]


def named_ai_tool(answer_text: str) -> str | None:
    """The AI tool the candidate mentioned FIRST (on a tie at the same spot, the longer name, so 'GitHub Copilot' beats 'Copilot')."""

    text = answer_text or ""
    best = None

    for name in _AI_TOOL_NAMES:
        match = re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", text, re.IGNORECASE)
        if match and (best is None or (match.start(), -len(name)) < (best[0], -len(best[1]))):
            best = (match.start(), name)

    return best[1] if best else None


def _ai_for_role_question(job_title: str | None) -> str:
    """The sixth role question: AI used on a real task of THIS job (not 'which tools do you know')."""

    title = (job_title or "").strip()
    role = f"a {title}" if title else "this"

    return (
        f"Think of one real task you would do every week as {role} role. " if title else "Think of one real task you would do every week in this role. "
    ) + "How would you use AI to get that task done faster or better, and what would you still check yourself?"


def _ai_follow_up_question(answer_text: str) -> str:

    tool = named_ai_tool(answer_text)

    if tool:
        return f"You mentioned {tool}. What exactly do you use it for, and how has it changed the way you work?"

    return "Of the AI tools you mentioned, which one do you rely on most, and what exactly do you use it for?"


def _package_question() -> str:
    return "What salary package are you expecting for this role, and is there any flexibility?"


def _work_fit_question(company: str | None) -> str:
    """The standard last question of a job interview (give the candidate the floor, and let them make their final case):
    'is there anything we have not covered' + 'why are you the right fit'. company is unused: kept for the call signature."""

    return "Is there anything we haven't covered that you'd like us to know, and why do you think you're the right fit for this role?"


def _enforce_closing_sequence(decision, blueprint, all_turns, current_turn_row, answer_text: str = "", company: str | None = None, job_title: str | None = None):
    """
    Product rule: six questions about the role (five chosen by the model, the sixth always "how would you use AI on a real task of
    this job"), then the expected package, then a closing question (anything not covered / why they fit). The model's own turn
    choice cannot be trusted to get there (or to stop an early END_INTERVIEW), so the sequence is enforced here, from what has
    actually been asked so far.
    """

    asked = {(t.competency or "").lower(): 0 for t in all_turns}
    for t in all_turns:
        asked[(t.competency or "").lower()] += 1

    turn_number = current_turn_row.turn_number
    current = (current_turn_row.competency or "").lower()

    if turn_number >= INTERVIEW_V2_MAX_TURNS:
        return decision

    def _force(competency, question, qtype, difficulty, action="DIRECT_NEXT_TOPIC", focus="example"):
        return decision.model_copy(update={
            "next_action": action, "next_competency": competency, "next_question": question,
            "next_question_type": qtype, "next_difficulty": difficulty, "next_question_focus": focus,
        })

    ai_key, package_key, work_key = AI_TOOL_COMPETENCY.lower(), PACKAGE_COMPETENCY.lower(), WORK_FIT_COMPETENCY.lower()
    shape = getattr(blueprint, "role_shape", "technical")

    # strictly in order, each one only once
    if current == work_key:
        return decision                                              # the last question was just answered

    if current == package_key:
        return _force(WORK_FIT_COMPETENCY, _work_fit_question(company), "SCENARIO", "basic")

    if current == ai_key:
        return _force(PACKAGE_COMPETENCY, _package_question(), "EXPERIENCE", "basic")

    if turn_number >= FREE_TURNS and asked.get(ai_key, 0) == 0:
        return _force(AI_TOOL_COMPETENCY, _ai_for_role_question(job_title), "SCENARIO", "intermediate")

    # the model may not end the interview before the fixed questions have been asked, whatever its own reading of the evidence
    if getattr(decision, "next_action", None) == "END_INTERVIEW":
        competency = current_turn_row.competency or (blueprint.competencies[0].name if getattr(blueprint, "competencies", None) else "Experience")
        return _force(competency, "Tell me about another real piece of work from your experience that shows how you would do this job.", "EXPERIENCE", "intermediate")

    return decision


# kept so existing imports keep working
_enforce_ai_tool_questions = _enforce_closing_sequence


def submit_answer_v2(
    db: Session,
    session: AIInterviewSessionV2,
    application: JobApplication,
    session_record,
    answer_text: str,
    use_llm: bool = True,
) -> dict:

    if session.status != "in_progress":
        raise InterviewIntegrationV2Error(409, "This AI interview is not currently active.")

    current_turn_row = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id, AIInterviewTurnV2.candidate_answer.is_(None))
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .first()
    )

    if not current_turn_row:
        raise InterviewIntegrationV2Error(409, "This AI interview has already been completed.")

    all_turns = (
        db.query(AIInterviewTurnV2)
        .filter(AIInterviewTurnV2.session_id == session.id)
        .order_by(AIInterviewTurnV2.turn_number.asc())
        .all()
    )

    covered_competencies = {t.competency.lower() for t in all_turns if t.turn_number != current_turn_row.turn_number}
    follow_ups_on_current = sum(
        1 for t in all_turns
        if t.turn_number != current_turn_row.turn_number and t.competency.lower() == current_turn_row.competency.lower()
    )
    # probe-ladder rungs already targeted for THIS competency (including the current turn's own rung: it counts as
    # used the moment its question was asked, not only once answered) -- see interview_brain_v2's used_probe_rungs.
    used_probe_rungs = frozenset(
        t.question_focus for t in all_turns
        if t.question_focus and t.competency.lower() == current_turn_row.competency.lower()
    )

    # The real, already-persisted structured analysis for every answered turn so far -- see
    # interview_brain_v2.build_structured_memory_v2(). Reuses exactly what _finalize_session() already parses the
    # same way; never re-derived or re-summarized here.
    prior_analyses = tuple(
        (t.competency, AnswerAnalysisV2.model_validate(json.loads(t.answer_analysis_json)))
        for t in all_turns
        if t.turn_number != current_turn_row.turn_number and t.answer_analysis_json
    )

    job = None
    from backend.app.models.job import Job
    job = db.query(Job).filter(Job.id == application.job_id).first()

    from backend.app.models.candidate_profile import CandidateProfile
    from backend.app.models.resume import Resume
    from backend.app.models.resume_analysis import ResumeAnalysis
    from backend.app.models.user import User

    user = db.query(User).filter(User.id == application.user_id).first()
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == application.user_id).first()
    resume = db.query(Resume).filter(Resume.user_id == application.user_id).order_by(Resume.id.desc()).first()
    resume_analysis = (
        db.query(ResumeAnalysis).filter(ResumeAnalysis.resume_id == resume.id).first() if resume else None
    )
    aptitude_test = db.query(AptitudeTest).filter(AptitudeTest.application_id == application.id).first()
    programming_evidence = _get_programming_evidence(db, application)

    context = build_interview_context_v2(
        job=job, user=user, profile=profile, resume_analysis=resume_analysis,
        aptitude_test=aptitude_test, programming_evidence=programming_evidence,
    )
    blueprint = _load_blueprint(session)

    decision, generated_by = analyze_and_decide_next_v2(
        context=context, blueprint=blueprint,
        conversation_summary=session.conversation_summary,
        current_competency=current_turn_row.competency,
        current_difficulty=current_turn_row.difficulty,
        last_question=current_turn_row.question,
        answer_text=answer_text,
        turn_number=current_turn_row.turn_number,
        min_turns=INTERVIEW_V2_MIN_TURNS,
        max_turns=INTERVIEW_V2_MAX_TURNS,
        covered_competencies=covered_competencies,
        follow_ups_on_current=follow_ups_on_current,
        use_llm=use_llm,
        used_probe_rungs=used_probe_rungs,
        prior_analyses=prior_analyses,
    )

    decision = _enforce_closing_sequence(decision, blueprint, all_turns, current_turn_row, answer_text, getattr(job, "company", None), getattr(job, "title", None))

    now = datetime.utcnow()

    current_turn_row.candidate_answer = answer_text
    current_turn_row.answer_analysis_json = decision.answer_analysis.model_dump_json()
    current_turn_row.next_action = decision.next_action
    current_turn_row.answered_at = now

    session.conversation_summary = build_conversation_summary_safe(
        session.conversation_summary, current_turn_row.turn_number,
        current_turn_row.question, answer_text, decision.answer_analysis,
    )
    session.updated_at = now

    hit_max_turns = current_turn_row.turn_number >= INTERVIEW_V2_MAX_TURNS

    if decision.next_action == "END_INTERVIEW" or hit_max_turns:

        db.commit()
        _finalize_session(db, session, application, session_record, reason="Sufficient evidence gathered" if not hit_max_turns else "Maximum turns reached")

        return {"interview_complete": True, "session": candidate_view_v2(db, session, job=job)}

    next_turn = AIInterviewTurnV2(
        session_id=session.id,
        turn_number=current_turn_row.turn_number + 1,
        question=decision.next_question,
        question_type=decision.next_question_type,
        competency=decision.next_competency or current_turn_row.competency,
        difficulty=decision.next_difficulty,
        question_focus=decision.next_question_focus,
        candidate_answer=None,
        answer_analysis_json=None,
        next_action=None,
        generated_by=generated_by,
        asked_at=now,
        answered_at=None,
    )

    db.add(next_turn)
    session.current_turn = next_turn.turn_number

    db.commit()
    db.refresh(session)

    return {"interview_complete": False, "session": candidate_view_v2(db, session, job=job)}


def build_conversation_summary_safe(previous_summary, turn_number, question, answer, analysis):

    from backend.app.services.assessment.interview_brain_v2 import build_conversation_summary

    return build_conversation_summary(previous_summary, turn_number, question, answer, analysis)

import json
import os
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.candidate_invite import CandidateInvite
from backend.app.api.deps import get_current_company, require_company_self, require_csrf
from backend.app.api.routes.company_applications import (
    get_verified_company,
    _delete_job_application_cascade,
)

from backend.app.services.resume.extractor import extract_resume_text
from backend.app.services.resume.analyzer import analyze_resume, TransientAnalysisError
from backend.app.services.resume.contact_extractor import guess_candidate_name, guess_candidate_email
from backend.app.services.email.notifications import send_candidate_assessment_invite_email
from backend.app.services.assessment.role_classification import (
    required_skills_are_technical,
    job_has_executable_language,
    detect_technical_domain,
)


router = APIRouter(
    prefix="/api/company",
    tags=["Company Candidate Invites"],
)


UPLOAD_FOLDER = "data/resumes"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

INVITE_EXPIRY_DAYS = 7  # default when HR doesn't pick a deadline
MIN_DEADLINE_DAYS = 1
MAX_DEADLINE_DAYS = 30


def _frontend_base_url() -> str:
    return (os.getenv("FRONTEND_BASE_URL") or "http://localhost:5500").rstrip("/")


# ============================================================
# HR-SOURCED CANDIDATE INVITE (Phase 1)
#
# HR uploads a resume for a job the candidate hasn't applied to (and may
# not even have an account for yet). The resume is analyzed with the SAME
# analyzer candidates' own resume uploads use, then the candidate is
# emailed a link that auto-creates their account and drops them straight
# into the existing assessment pipeline (self-intro/aptitude/programming/
# AI interview) -- nothing about that pipeline changes.
# ============================================================

@router.post("/{company_id}/extract-resume-contact")
async def extract_resume_contact(
    company_id: int,
    request: Request,
    file: UploadFile = File(...),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    """Best-effort auto-fill: guesses the candidate's name/email straight
    from the resume the moment HR picks the file, so they don't have to
    retype what's already on the page. Never fails the page -- a resume
    this can't confidently parse just leaves the fields blank for HR to
    fill in by hand."""

    require_company_self(current_company, company_id)
    require_csrf(request)

    get_verified_company(company_id, db)

    if not file.filename or "." not in file.filename:
        raise HTTPException(status_code=400, detail="Resume must be a PDF or DOCX file.")

    extension = file.filename.rsplit(".", 1)[-1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported.")

    file_data = await file.read()

    if not file_data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Resume must be smaller than 10 MB.")

    # Not the real invite upload -- just a throwaway probe to read text out
    # of the file, so it's written to a temp path and removed immediately
    # rather than kept in UPLOAD_FOLDER like a real invite's resume.
    temp_path = os.path.join(UPLOAD_FOLDER, f"contact_probe_{uuid.uuid4().hex}.{extension}")

    resume_text = ""

    try:
        with open(temp_path, "wb") as buffer:
            buffer.write(file_data)
        resume_text = extract_resume_text(temp_path, extension) or ""
    except Exception:
        resume_text = ""
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return {
        "candidate_name": guess_candidate_name(resume_text),
        "candidate_email": guess_candidate_email(resume_text),
    }


_ROLE_SHAPE_LABELS = {
    "technical": "Technical — coding assessment",
    "non_technical": "Non-technical — written, data, design or call task",
}


@router.post("/{company_id}/detect-role-type")
async def detect_role_type(
    company_id: int,
    request: Request,
    job_title: str = Form(""),
    job_requirement: str = Form(""),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    """Live preview of what the assessment pipeline will auto-detect
    from the job title/requirement HR is typing -- shown on the
    Invite-a-Candidate form as an editable field (role_shape_override
    on the Job row) so HR always sees and can correct the decision,
    rather than it happening silently (see role_classification.py)."""

    require_company_self(current_company, company_id)
    require_csrf(request)

    extra_text = f"{job_title} {job_requirement}"
    is_technical = required_skills_are_technical("", extra_text)
    shape = "technical" if is_technical else "non_technical"

    domain = None
    if is_technical and not job_has_executable_language("", extra_text):
        domain = detect_technical_domain("", extra_text)

    if domain:
        detail = f"Technical — domain assessment ({domain.replace('_', ' ').title()})"
    else:
        detail = _ROLE_SHAPE_LABELS[shape]

    return {"detected_shape": shape, "detected_domain": domain, "label": detail}


def _public_plan(plan: dict) -> dict:
    """The plan as the invite page shows it (names, reasons, areas)."""

    return {
        "source": plan.get("source"), "summary": plan.get("summary") or "",
        "stages": [
            {
                "slot": s["slot"], "kind": s["kind"], "title": s.get("title") or "", "reason": s.get("reason") or "",
                "custom": bool(s.get("areas")) or s["kind"] == "practical", "areas": [{"label": a["label"], "count": a["count"]} for a in s.get("areas") or []],
                "environment": s.get("environment"), "focus": s.get("focus") or [], "family": s.get("family"), "lab": s.get("lab"),
            }
            for s in plan["stages"]
        ],
        "plan": plan,                       # sent back with the invite so it is not planned twice
    }


@router.post("/{company_id}/assessment-plan")
async def preview_assessment_plan(
    company_id: int,
    request: Request,
    job_title: str = Form(""),
    job_requirement: str = Form(""),
    role_shape_override: str = Form(""),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):
    """What the AI would give candidates for this role: shown on the invite form before anything is sent, so HR can see it, and
    keep the standard tests instead if they prefer. Nothing is stored here."""

    from backend.app.services.assessment.assessment_planner import get_job_plan

    require_company_self(current_company, company_id)
    require_csrf(request)

    job_title, job_requirement = job_title.strip(), job_requirement.strip()

    if not job_title and not job_requirement:
        raise HTTPException(status_code=400, detail="Describe the role first.")

    shape = role_shape_override.strip().lower()
    unsaved = Job(
        title=job_title or "Role", company="", description=job_requirement, requirements=job_requirement, source="invite_only",
        role_shape_override=shape if shape in ("technical", "non_technical") else None,
    )

    from backend.app.services.assessment.role_catalogue import enrich_job
    enrich_job(unsaved)                                 # a coding role with no language in its text gets the role's usual one, as the real job will

    from starlette.concurrency import run_in_threadpool

    plan = await run_in_threadpool(lambda: get_job_plan(unsaved, store=False))

    return _public_plan(plan)


@router.post("/{company_id}/invite-candidate")
async def invite_candidate(
    company_id: int,
    request: Request,
    job_title: str = Form(...),
    job_requirement: str = Form(...),
    candidate_name: str = Form(...),
    candidate_email: str = Form(...),
    deadline_days: int = Form(INVITE_EXPIRY_DAYS),
    role_shape_override: str = Form(""),
    assessment_plan: str = Form(""),
    aptitude_choice: str = Form("ai"),
    stage3_choice: str = Form("ai"),
    file: UploadFile = File(...),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    company = get_verified_company(company_id, db)

    job_title = job_title.strip()
    job_requirement = job_requirement.strip()

    # "auto" (or anything else HR's own field never sends) means no
    # override -- the normal keyword-based auto-detection applies.
    role_shape_override = role_shape_override.strip().lower()
    if role_shape_override not in ("technical", "non_technical"):
        role_shape_override = None

    if not job_title:
        raise HTTPException(status_code=400, detail="Job title is required")

    if not job_requirement:
        raise HTTPException(status_code=400, detail="Job requirement is required")

    # Ad-hoc, invite-only job row -- exists purely so the existing
    # assessment pipeline (which reads Job.title/description/requirements
    # via JobApplication.job_id) keeps working unchanged. source="invite_only"
    # keeps it out of the public candidate job-browse listing.
    job = Job(
        title=job_title,
        company=company.company_name,
        company_id=company_id,
        description=job_requirement,
        requirements=job_requirement,
        source="invite_only",
        status="active",
        role_shape_override=role_shape_override,
    )

    from backend.app.services.assessment.role_catalogue import enrich_job
    enrich_job(job)                                     # a coding role with no language in its text gets the role's usual one

    db.add(job)
    db.flush()

    # The assessments this job's candidates take: the plan HR saw on the form (so it is not planned twice), else planned now
    # (AI, with the rules as fallback). HR's choice to keep the standard tests is applied on top. Never blocks the invite.
    try:
        import json as _json

        from backend.app.services.assessment.assessment_planner import apply_override, get_job_plan, normalise_plan

        plan = None
        if assessment_plan.strip():
            try:
                shown = _json.loads(assessment_plan)
                plan = normalise_plan(shown, job, shown.get("source") or "ai")
            except (ValueError, TypeError, AttributeError):
                plan = None                                              # a tampered or stale plan is ignored: plan it freshly

        if plan is None:
            plan = get_job_plan(job, store=False)

        plan = apply_override(
            plan, job,
            aptitude="standard" if aptitude_choice.strip().lower() == "standard" else None,
            stage3="standard" if stage3_choice.strip().lower() == "standard" else None,
        )
        job.assessment_plan_json = _json.dumps(plan)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("jobpilot.invites").warning("Assessment plan could not be set at invite time (%s): planned when the candidate starts.", exc)

    candidate_name = candidate_name.strip()
    candidate_email = candidate_email.strip().lower()

    if not candidate_name:
        raise HTTPException(status_code=400, detail="Candidate name is required")

    if "@" not in candidate_email:
        raise HTTPException(status_code=400, detail="A valid candidate email is required")

    if not (MIN_DEADLINE_DAYS <= deadline_days <= MAX_DEADLINE_DAYS):
        raise HTTPException(
            status_code=400,
            detail=f"Deadline must be between {MIN_DEADLINE_DAYS} and {MAX_DEADLINE_DAYS} days.",
        )

    # ----------------------------------------------
    # VALIDATE FILE
    # ----------------------------------------------

    if not file.filename or "." not in file.filename:
        raise HTTPException(status_code=400, detail="Resume must be a PDF or DOCX file.")

    extension = file.filename.rsplit(".", 1)[-1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported.")

    file_data = await file.read()

    if not file_data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Resume must be smaller than 10 MB.")

    # ----------------------------------------------
    # SAVE FILE
    # ----------------------------------------------

    unique_filename = f"invite_{job.id}_{uuid.uuid4().hex}.{extension}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)

    try:
        with open(file_path, "wb") as buffer:
            buffer.write(file_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save resume: {exc}")

    # ----------------------------------------------
    # EXTRACT + ANALYZE (same pipeline candidate self-uploads use)
    # ----------------------------------------------

    try:
        resume_text = extract_resume_text(file_path, extension)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Resume text extraction failed: {exc}")

    if not resume_text:
        raise HTTPException(status_code=400, detail="No readable text was found inside the resume.")

    try:
        analysis_data = analyze_resume(
            resume_text,
            job_context=f"Job title: {job_title}\nJob requirement: {job_requirement}",
        )
    except TransientAnalysisError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc) if isinstance(exc, RuntimeError) else "Resume analysis could not be completed.",
        )

    # ----------------------------------------------
    # CREATE INVITE
    # ----------------------------------------------

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()

    invite = CandidateInvite(
        token=token,
        job_id=job.id,
        company_id=company.id,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        resume_file_path=file_path,
        resume_analysis_json=json.dumps(analysis_data),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=deadline_days),
    )

    db.add(invite)
    db.commit()
    db.refresh(invite)

    invite_url = f"{_frontend_base_url()}/start-assessment.html?token={token}"
    schedule_url = f"{_frontend_base_url()}/schedule-assessment.html?token={token}"

    email_sent = send_candidate_assessment_invite_email(
        to_email=candidate_email,
        candidate_name=candidate_name,
        job_title=job.title,
        company_name=company.company_name,
        invite_url=invite_url,
        schedule_url=schedule_url,
        deadline=invite.expires_at,
    )

    return {
        "invite_id": invite.id,
        "status": invite.status,
        "candidate_email": invite.candidate_email,
        "expires_at": invite.expires_at.isoformat(),
        "email_sent": email_sent,
    }


# ============================================================
# DELETE INVITE (permanent)
#
# Covers candidates the "Delete Applicant" endpoint in
# company_applications.py can't reach on its own: an invite
# nobody has clicked yet has no JobApplication at all. If the
# invite WAS claimed, the linked JobApplication (and every row
# under it) is cascade-deleted the same way, so either kind of
# candidate can be removed from Candidate Tracking in one action.
# ============================================================

@router.delete("/{company_id}/invites/{invite_id}")
def delete_invite(
    company_id: int,
    invite_id: int,
    request: Request,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_company_self(current_company, company_id)
    require_csrf(request)

    invite = (
        db.query(CandidateInvite)
        .filter(CandidateInvite.id == invite_id, CandidateInvite.company_id == company_id)
        .first()
    )

    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found for this company.")

    if invite.claimed_user_id:

        application = (
            db.query(JobApplication)
            .filter(
                JobApplication.job_id == invite.job_id,
                JobApplication.user_id == invite.claimed_user_id,
            )
            .first()
        )

        if application:
            _delete_job_application_cascade(db, application)

    db.delete(invite)
    db.commit()

    return {"message": "Candidate deleted successfully."}

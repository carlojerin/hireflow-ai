import html
import mimetypes
import os
import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_company, require_company_self, require_csrf
from backend.app.api.routes.company_applications import get_company_application
from backend.app.database.database import get_db
from backend.app.models.company import Company
from backend.app.models.offer_letter_log import OfferLetterLog
from backend.app.services.email.mailer import send_email


# ============================================================
# OFFER LETTER (candidate intelligence report)
#
# GET  .../draft  -> a ready-to-edit offer email for the candidate who took the test, with their name,
#                    the role, the company and the HR sender filled in. Details only HR knows (start date,
#                    pay, reply-by date) are left as [square bracket] placeholders.
# POST .../send   -> sends the (edited) email, with any attachments, to that candidate's own email address
#                    only. The recipient cannot be changed from the request: HR cannot use this to mail
#                    arbitrary addresses. The company's address is set as Reply-To so replies reach HR.
#
# Same authorization chain as every other company_applications route.
# ============================================================

router = APIRouter(
    prefix="/api/offer-letters",
    tags=["Offer Letters"],
)

MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
    "png", "jpg", "jpeg", "gif", "webp",
}

PLACEHOLDER = re.compile(r"\[[^\[\]\n]{2,60}\]")


def _first_name(full_name: str | None) -> str:
    parts = (full_name or "").split()
    return parts[0] if parts else "there"


def _draft(company: Company, job, user) -> dict:

    role = (job.title or "the role").strip()
    company_name = (job.company or company.company_name or "our company").strip()
    sender = (getattr(company, "hr_name", None) or "").strip() or "The Hiring Team"

    work_bits = [
        str(value).strip()
        for value in (getattr(job, "employment_type", None), getattr(job, "work_mode", None), getattr(job, "location", None))
        if value and str(value).strip()
    ]
    work_line = ", ".join(work_bits) if work_bits else "[Employment type, work mode and location]"

    subject = f"Job Offer - {role} at {company_name}"

    body = (
        f"Dear {_first_name(user.full_name)},\n\n"
        f"Congratulations! We are delighted to offer you the position of {role} at {company_name}. "
        f"Thank you for the time you put into our assessment. Your results stood out, "
        f"and we believe your skills will be a real asset to our team.\n\n"
        f"Here are the key details of your offer:\n\n"
        f"  - Position: {role}\n"
        f"  - Employment: {work_line}\n"
        f"  - Start date: [Start date]\n"
        f"  - Compensation: [Salary and any bonus or benefits]\n"
        f"  - Reporting to: [Manager name and title]\n\n"
        f"Please confirm your acceptance by replying to this email by [Reply-by date]. "
        f"If you have any questions about the role, the compensation or the next steps, "
        f"just reply and we will be happy to help.\n\n"
        f"This offer is subject to the usual pre-employment checks, such as reference and document verification.\n\n"
        f"We are excited about the possibility of you joining {company_name} and look forward to hearing from you.\n\n"
        f"Warm regards,\n"
        f"{sender}\n"
        f"{company_name}\n"
    )

    return {"subject": subject, "body": body}


def _html_version(body: str) -> str:
    paragraphs = "".join(
        f'<p style="margin:0 0 14px">{html.escape(chunk).replace(chr(10), "<br>")}</p>'
        for chunk in body.strip().split("\n\n")
    )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.6;color:#14183a;max-width:640px">'
        f"{paragraphs}</div>"
    )


def _check_access(company_id: int, application_id: int, current_company: Company, db: Session):
    require_company_self(current_company, company_id)
    application, job, user, _profile = get_company_application(company_id, application_id, db)
    return application, job, user


@router.get("/{company_id}/{application_id}/draft")
def get_offer_letter_draft(
    company_id: int,
    application_id: int,
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    application, job, user = _check_access(company_id, application_id, current_company, db)

    draft = _draft(current_company, job, user)

    last = (
        db.query(OfferLetterLog)
        .filter(OfferLetterLog.application_id == application.id, OfferLetterLog.delivered == 1)
        .order_by(OfferLetterLog.id.desc())
        .first()
    )

    return {
        **draft,
        "to_email": user.email,
        "candidate_name": user.full_name,
        "reply_to": current_company.email,
        "limits": {"max_files": MAX_FILES, "max_file_mb": MAX_FILE_BYTES // (1024 * 1024), "extensions": sorted(ALLOWED_EXTENSIONS)},
        "last_sent_at": last.sent_at.isoformat() if last else None,
    }


def _clean_filename(name: str | None) -> str:
    base = os.path.basename((name or "attachment").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120]
    return base or "attachment"


@router.post("/{company_id}/{application_id}/send")
async def send_offer_letter(
    company_id: int,
    application_id: int,
    request: Request,
    subject: str = Form(...),
    body: str = Form(...),
    allow_placeholders: bool = Form(False),
    attachments: list[UploadFile] = File(default=[]),
    current_company: Company = Depends(get_current_company),
    db: Session = Depends(get_db),
):

    require_csrf(request)

    application, job, user = _check_access(company_id, application_id, current_company, db)

    subject = (subject or "").strip()
    body = (body or "").strip()

    if not user.email:
        raise HTTPException(status_code=400, detail="This candidate has no email address on file.")

    if len(subject) < 3 or len(subject) > 300:
        raise HTTPException(status_code=400, detail="Enter a subject line.")

    if len(body) < 20 or len(body) > 20000:
        raise HTTPException(status_code=400, detail="Write the offer message before sending.")

    leftovers = sorted(set(PLACEHOLDER.findall(subject + "\n" + body)))

    if leftovers and not allow_placeholders:
        raise HTTPException(
            status_code=409,
            detail="The letter still has blanks to fill in: " + ", ".join(leftovers[:6]) + ".",
        )

    files = [f for f in attachments if f and f.filename]

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Attach at most {MAX_FILES} files.")

    prepared = []
    total = 0

    for upload in files:

        name = _clean_filename(upload.filename)
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""

        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{name}: this file type cannot be attached.")

        data = await upload.read(MAX_FILE_BYTES + 1)

        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail=f"{name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")

        total += len(data)

        if total > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=400, detail="The attachments are too large together (20 MB limit).")

        prepared.append((name, data, mimetypes.guess_type(name)[0] or "application/octet-stream"))

    delivered = send_email(
        user.email,
        subject,
        body,
        html_body=_html_version(body),
        attachments=prepared,
        reply_to=current_company.email,
    )

    db.add(OfferLetterLog(
        application_id=application.id,
        company_id=company_id,
        to_email=user.email,
        subject=subject,
        body=body,
        attachment_names=", ".join(name for name, _d, _m in prepared) or None,
        delivered=1 if delivered else 0,
        sent_at=datetime.utcnow(),
    ))
    db.commit()

    if not delivered:
        raise HTTPException(
            status_code=502,
            detail="The email could not be sent. Check the email settings (Gmail or SMTP) and try again. Your letter is still on screen.",
        )

    return {"sent": True, "to_email": user.email, "attachments": [name for name, _d, _m in prepared]}

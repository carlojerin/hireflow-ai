import logging
from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.models.company import Company
from backend.app.services.email.mailer import send_email


# ============================================================
# JOBPILOT AI - CANDIDATE RECRUITMENT NOTIFICATIONS
#
# Candidate-facing email content for HR recruitment
# decisions. Keeps subject/body wording and the "which email
# for which status" decision out of the route handlers.
#
# Every function here is best-effort: it never raises, so it
# can never block or undo the HR action that triggered it.
# ============================================================

logger = logging.getLogger("jobpilot.email")


# ============================================================
# SHORTLIST EMAIL
# ============================================================

def send_candidate_shortlisted_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
) -> bool:

    subject = (
        "Your HireFlow AI Application Has Been Shortlisted"
    )

    name = candidate_name or "there"
    title = job_title or "the role you applied for"
    company = company_name or "the company"

    body = (
        f"Hi {name},\n\n"
        f"Good news — your application for {title} at "
        f"{company} has been shortlisted by the hiring team.\n\n"
        "Your profile is now moving forward for further review. "
        "If an assessment or interview is required as a next "
        "step, you will be notified separately once it has "
        "actually been scheduled.\n\n"
        "Thank you for your interest in this opportunity.\n\n"
        "— HireFlow AI"
    )

    return send_email(
        to_email,
        subject,
        body,
    )


# ============================================================
# REJECT EMAIL
# ============================================================

def send_candidate_rejected_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
) -> bool:

    subject = (
        "Update on Your HireFlow AI Application"
    )

    name = candidate_name or "there"
    title = job_title or "the role you applied for"
    company = company_name or "the company"

    body = (
        f"Hi {name},\n\n"
        f"Thank you for applying for {title} at {company}, "
        "and for the time you invested in your application.\n\n"
        "After careful review, we want to let you know that "
        "your application was not selected to proceed at this "
        "stage.\n\n"
        "We encourage you to keep your HireFlow AI profile up "
        "to date and continue exploring other opportunities on "
        "the platform.\n\n"
        "— HireFlow AI"
    )

    return send_email(
        to_email,
        subject,
        body,
    )


# ============================================================
# ASSESSMENT INVITE EMAIL (HR-sourced candidate, Phase 1)
# ============================================================

def send_candidate_assessment_invite_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
    invite_url: str,
    schedule_url: str | None = None,
    deadline: datetime | None = None,
) -> bool:
    """The email is HR-sourced candidate outreach: it frames the invite as
    coming from the hiring company (not just "HireFlow AI"), explains every
    assessment stage up front, and states the deadline HR chose plainly.

    Two real choices are offered: "Start Now" (invite_url, begins right
    away) and "Schedule It For Later" (schedule_url, a small page where the
    candidate picks a time and gets a confirmation + reminder emails before
    the deadline). Every stage is still self-serve/asynchronous -- there's
    no human interviewer to book a slot with -- scheduling just anchors a
    reminder, it never gates when the candidate can actually start.
    """

    subject = (
        f"You've been shortlisted by {company_name or 'a company'} — "
        f"{job_title or 'assessment'} on HireFlow AI"
    )

    name = candidate_name or "there"
    title = job_title or "the role"
    company = company_name or "The hiring team"

    deadline_text = (
        deadline.strftime("%B %d, %Y at %I:%M %p UTC")
        if deadline
        else None
    )

    deadline_line = (
        f"Please complete it by {deadline_text}.\n\n"
        if deadline_text
        else ""
    )

    schedule_line = (
        f"Prefer to pick a time first? Schedule it here and we'll remind you before "
        f"the deadline: {schedule_url}\n\n"
        if schedule_url
        else ""
    )

    body = (
        f"Hi {name},\n\n"
        f"Good news — {company} reviewed your profile and shortlisted you "
        f"for the {title} role. The next step is a short AI-assisted "
        "assessment covering:\n\n"
        "  - A quick self-introduction\n"
        "  - An adaptive aptitude test\n"
        "  - A live coding assessment\n"
        "  - A short AI voice interview\n\n"
        f"{deadline_line}"
        "Start right now:\n\n"
        f"{invite_url}\n\n"
        f"{schedule_line}"
        "Clicking the start link creates your HireFlow AI account automatically "
        "and takes you straight into the assessment.\n\n"
        f"— HireFlow AI, on behalf of {company}"
    )

    deadline_html = (
        f'<p style="margin:0 0 20px;font-size:14px;color:#334155;">'
        f'<strong>Deadline:</strong> {deadline_text}</p>'
        if deadline_text
        else ""
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f6fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 24px;">
    <div style="background:#ffffff;border-radius:16px;padding:32px;box-shadow:0 4px 20px rgba(15,23,42,0.06);">

      <div style="font-size:20px;font-weight:700;color:#111827;margin-bottom:4px;">🚀 HireFlow AI</div>
      <div style="height:1px;background:#e5e7eb;margin:16px 0 24px;"></div>

      <p style="margin:0 0 16px;font-size:15px;color:#111827;">Hi {name},</p>

      <p style="margin:0 0 16px;font-size:15px;color:#334155;line-height:1.6;">
        Good news — <strong>{company}</strong> reviewed your profile and
        <strong>shortlisted you</strong> for the <strong>{title}</strong> role.
        The next step is a short AI-assisted assessment covering:
      </p>

      <ul style="margin:0 0 20px;padding-left:20px;font-size:14px;color:#334155;line-height:1.9;">
        <li>A quick self-introduction</li>
        <li>An adaptive aptitude test</li>
        <li>A live coding assessment</li>
        <li>A short AI voice interview</li>
      </ul>

      {deadline_html}

      <div style="text-align:center;margin:28px 0;">
        <a href="{invite_url}"
           style="display:inline-block;background:#635bff;color:#ffffff;text-decoration:none;
                  font-weight:700;font-size:14px;padding:13px 28px;border-radius:999px;margin:0 6px 10px;">
          Start Assessment Now
        </a>
        <a href="{schedule_url or invite_url}"
           style="display:inline-block;background:#ffffff;color:#635bff;text-decoration:none;
                  font-weight:700;font-size:14px;padding:12px 27px;border-radius:999px;margin:0 6px 10px;
                  border:1.5px solid #635bff;">
          Schedule It For Later
        </a>
      </div>

      <p style="margin:0 0 16px;font-size:12.5px;color:#94a3b8;text-align:center;line-height:1.6;">
        Not ready right now? Pick a time with "Schedule It" and we'll send you a
        reminder before your deadline.
      </p>

      <p style="margin:20px 0 0;font-size:13px;color:#94a3b8;">
        — HireFlow AI, on behalf of {company}
      </p>

    </div>
  </div>
</body>
</html>
"""

    return send_email(
        to_email,
        subject,
        body,
        html_body=html_body,
    )


# ============================================================
# SCHEDULED CONFIRMATION EMAIL
#
# Sent the moment a candidate picks "Schedule It For Later" and confirms a
# time on schedule-assessment.html. Purely a confirmation + reassurance --
# the underlying magic link is unchanged and still works any time before
# the deadline, scheduled time or not.
# ============================================================

def send_company_email_verification(to_email: str | None, hr_name: str | None, company_name: str | None, code: str) -> bool:
    """The 6-digit code that proves an HR account owns its email address (valid for 15 minutes)."""

    name = hr_name or "there"
    company = company_name or "your company"

    subject = f"Your HireFlow AI verification code: {code}"

    body = (
        f"Hi {name},\n\n"
        f"Welcome to HireFlow AI. Enter this code to verify the email address for {company}:\n\n"
        f"{code}\n\n"
        "It expires in 15 minutes. If you did not create this account you can ignore this email.\n\n"
        "-- HireFlow AI"
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f6fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:520px;margin:0 auto;padding:32px 24px;">
    <div style="background:#ffffff;border-radius:16px;padding:32px;box-shadow:0 4px 20px rgba(15,23,42,0.06);">
      <div style="font-size:20px;font-weight:700;color:#111827;margin-bottom:4px;">HireFlow AI</div>
      <div style="height:1px;background:#e5e7eb;margin:16px 0 24px;"></div>
      <p style="margin:0 0 16px;font-size:15px;color:#111827;">Hi {name},</p>
      <p style="margin:0 0 20px;font-size:15px;color:#334155;line-height:1.6;">
        Enter this code to verify the email address for <strong>{company}</strong>:
      </p>
      <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:12px;padding:18px;text-align:center;margin:0 0 20px;">
        <div style="font-size:34px;font-weight:800;letter-spacing:8px;color:#3730a3;">{code}</div>
      </div>
      <p style="margin:0;font-size:13px;color:#64748b;">It expires in 15 minutes. If you did not create this account, you can ignore this email.</p>
    </div>
  </div>
</body>
</html>
"""

    return send_email(to_email, subject, body, html_body=html_body)


def send_candidate_scheduled_confirmation_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
    invite_url: str,
    scheduled_at: datetime,
    deadline: datetime | None = None,
) -> bool:

    name = candidate_name or "there"
    title = job_title or "the role"
    company = company_name or "the hiring team"

    scheduled_text = scheduled_at.strftime("%B %d, %Y at %I:%M %p UTC")
    deadline_text = deadline.strftime("%B %d, %Y at %I:%M %p UTC") if deadline else None

    subject = f"You scheduled your assessment for {scheduled_text}"

    deadline_line = (
        f" Just remember it still needs to be finished by {deadline_text}.\n\n"
        if deadline_text
        else "\n\n"
    )

    body = (
        f"Hi {name},\n\n"
        f"You've scheduled your {title} assessment for {company} on {scheduled_text}.\n\n"
        "You don't have to wait, though -- the link below works any time before your "
        f"deadline, so feel free to start early if you're ready.{deadline_line}"
        f"{invite_url}\n\n"
        "We'll also email you a reminder as your deadline gets close.\n\n"
        f"— HireFlow AI, on behalf of {company}"
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f6fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 24px;">
    <div style="background:#ffffff;border-radius:16px;padding:32px;box-shadow:0 4px 20px rgba(15,23,42,0.06);">

      <div style="font-size:20px;font-weight:700;color:#111827;margin-bottom:4px;">🚀 HireFlow AI</div>
      <div style="height:1px;background:#e5e7eb;margin:16px 0 24px;"></div>

      <p style="margin:0 0 16px;font-size:15px;color:#111827;">Hi {name},</p>

      <p style="margin:0 0 20px;font-size:15px;color:#334155;line-height:1.6;">
        You've scheduled your <strong>{title}</strong> assessment for
        <strong>{company}</strong> on:
      </p>

      <div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:12px;
                  padding:16px 20px;text-align:center;margin:0 0 20px;">
        <div style="font-size:17px;font-weight:700;color:#5b21b6;">{scheduled_text}</div>
      </div>

      <p style="margin:0 0 20px;font-size:14px;color:#334155;line-height:1.6;">
        You don't have to wait, though — your link works any time before your deadline,
        so start early if you're ready.
        {f'<strong>Deadline: {deadline_text}.</strong>' if deadline_text else ''}
      </p>

      <div style="text-align:center;margin:28px 0;">
        <a href="{invite_url}"
           style="display:inline-block;background:#635bff;color:#ffffff;text-decoration:none;
                  font-weight:700;font-size:14px;padding:13px 28px;border-radius:999px;">
          Start Assessment Now
        </a>
      </div>

      <p style="margin:0;font-size:12.5px;color:#94a3b8;text-align:center;">
        We'll also email you a reminder as your deadline gets close.
      </p>

      <p style="margin:20px 0 0;font-size:13px;color:#94a3b8;">
        — HireFlow AI, on behalf of {company}
      </p>

    </div>
  </div>
</body>
</html>
"""

    return send_email(to_email, subject, body, html_body=html_body)


# ============================================================
# DEADLINE REMINDER EMAIL
#
# Sent by the background reminder job (reminder_scheduler.py) at fixed
# thresholds before an invite's deadline -- never more than once per
# threshold per invite (the caller checks/sets the reminder_*_sent flags).
# ============================================================

def send_candidate_deadline_reminder_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
    invite_url: str,
    time_left_label: str,
    deadline: datetime,
) -> bool:

    name = candidate_name or "there"
    title = job_title or "your assessment"
    company = company_name or "the hiring team"
    deadline_text = deadline.strftime("%B %d, %Y at %I:%M %p UTC")

    subject = f"⏰ Only {time_left_label} left to complete your {title} assessment"

    body = (
        f"Hi {name},\n\n"
        f"Just a reminder — you have {time_left_label} left to complete your {title} "
        f"assessment for {company}, due {deadline_text}.\n\n"
        "Pick up right where you left off (or start fresh if you haven't yet):\n\n"
        f"{invite_url}\n\n"
        f"— HireFlow AI, on behalf of {company}"
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f6fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 24px;">
    <div style="background:#ffffff;border-radius:16px;padding:32px;box-shadow:0 4px 20px rgba(15,23,42,0.06);">

      <div style="font-size:20px;font-weight:700;color:#111827;margin-bottom:4px;">🚀 HireFlow AI</div>
      <div style="height:1px;background:#e5e7eb;margin:16px 0 24px;"></div>

      <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;
                  padding:16px 20px;text-align:center;margin:0 0 20px;">
        <div style="font-size:22px;">⏰</div>
        <div style="font-size:17px;font-weight:700;color:#c2410c;margin-top:4px;">
          Only {time_left_label} left
        </div>
      </div>

      <p style="margin:0 0 16px;font-size:15px;color:#111827;">Hi {name},</p>

      <p style="margin:0 0 20px;font-size:14px;color:#334155;line-height:1.6;">
        You have <strong>{time_left_label}</strong> left to complete your
        <strong>{title}</strong> assessment for <strong>{company}</strong>,
        due <strong>{deadline_text}</strong>.
      </p>

      <div style="text-align:center;margin:28px 0;">
        <a href="{invite_url}"
           style="display:inline-block;background:#635bff;color:#ffffff;text-decoration:none;
                  font-weight:700;font-size:14px;padding:13px 28px;border-radius:999px;">
          Continue Assessment
        </a>
      </div>

      <p style="margin:20px 0 0;font-size:13px;color:#94a3b8;">
        — HireFlow AI, on behalf of {company}
      </p>

    </div>
  </div>
</body>
</html>
"""

    return send_email(to_email, subject, body, html_body=html_body)


# ============================================================
# DEADLINE-EXPIRED NOTIFICATION EMAIL
#
# Sent once by the background job (reminder_scheduler.py) at the moment an
# invite's deadline passes -- tells the candidate plainly that the link no
# longer works, rather than leaving them to discover it by clicking a dead
# link with no warning.
# ============================================================

def send_candidate_deadline_expired_email(
    to_email: str | None,
    candidate_name: str | None,
    job_title: str | None,
    company_name: str | None,
    deadline: datetime,
) -> bool:

    name = candidate_name or "there"
    title = job_title or "the role"
    company = company_name or "the hiring team"
    deadline_text = deadline.strftime("%B %d, %Y at %I:%M %p UTC")

    subject = f"Your assessment window for {title} has closed"

    body = (
        f"Hi {name},\n\n"
        f"Your deadline to complete the {title} assessment for {company} "
        f"({deadline_text}) has passed, so your invite link no longer works.\n\n"
        "If you think this is a mistake, or you'd still like to be considered, "
        f"please reach out to {company} directly.\n\n"
        f"— HireFlow AI, on behalf of {company}"
    )

    html_body = f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f6fb;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;padding:32px 24px;">
    <div style="background:#ffffff;border-radius:16px;padding:32px;box-shadow:0 4px 20px rgba(15,23,42,0.06);">

      <div style="font-size:20px;font-weight:700;color:#111827;margin-bottom:4px;">🚀 HireFlow AI</div>
      <div style="height:1px;background:#e5e7eb;margin:16px 0 24px;"></div>

      <div style="background:#f3f4f6;border:1px solid #e5e7eb;border-radius:12px;
                  padding:16px 20px;text-align:center;margin:0 0 20px;">
        <div style="font-size:22px;">⏰</div>
        <div style="font-size:16px;font-weight:700;color:#374151;margin-top:4px;">
          Assessment window closed
        </div>
      </div>

      <p style="margin:0 0 16px;font-size:15px;color:#111827;">Hi {name},</p>

      <p style="margin:0 0 16px;font-size:14px;color:#334155;line-height:1.6;">
        Your deadline to complete the <strong>{title}</strong> assessment for
        <strong>{company}</strong> (<strong>{deadline_text}</strong>) has passed,
        so your invite link no longer works.
      </p>

      <p style="margin:0 0 16px;font-size:14px;color:#334155;line-height:1.6;">
        If you think this is a mistake, or you'd still like to be considered,
        please reach out to <strong>{company}</strong> directly.
      </p>

      <p style="margin:20px 0 0;font-size:13px;color:#94a3b8;">
        — HireFlow AI, on behalf of {company}
      </p>

    </div>
  </div>
</body>
</html>
"""

    return send_email(to_email, subject, body, html_body=html_body)


# ============================================================
# STATUS-CHANGE NOTIFICATION DISPATCHER
#
# Called after a status change has already been committed to
# the database. Decides, from the resulting history record,
# whether a candidate notification is required, and sends it.
#
# - history_record is None whenever the caller's status
#   change was a no-op (status did not actually change) —
#   nothing is sent in that case.
# - Only "shortlisted" and "rejected" transitions currently
#   trigger a candidate email.
# - Never raises. Any failure is logged and swallowed so the
#   HR action that already succeeded is never affected.
# ============================================================

def notify_status_change(
    db: Session,
    application,
    user,
    job,
    company_id: int,
    history_record,
) -> None:

    if history_record is None:
        return

    to_status = getattr(
        history_record,
        "to_status",
        None,
    )

    if to_status not in {
        "shortlisted",
        "rejected",
    }:
        return

    try:

        company = (
            db.query(Company)
            .filter(
                Company.id == company_id
            )
            .first()
        )

        company_name = (
            company.company_name
            if company
            else getattr(
                job,
                "company",
                None,
            )
        )

        candidate_email = getattr(
            user,
            "email",
            None,
        )

        candidate_name = getattr(
            user,
            "full_name",
            None,
        )

        job_title = getattr(
            job,
            "title",
            None,
        )

        if to_status == "shortlisted":

            send_candidate_shortlisted_email(
                to_email=candidate_email,
                candidate_name=candidate_name,
                job_title=job_title,
                company_name=company_name,
            )

        elif to_status == "rejected":

            send_candidate_rejected_email(
                to_email=candidate_email,
                candidate_name=candidate_name,
                job_title=job_title,
                company_name=company_name,
            )

    except Exception as exc:

        application_id = getattr(
            application,
            "id",
            None,
        )

        logger.error(
            "Candidate email notification raised an unexpected "
            "error for application %s (to_status=%s): %s",
            application_id,
            to_status,
            exc,
        )

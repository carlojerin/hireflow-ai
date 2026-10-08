import logging
import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

# Loaded here so this module reads SMTP_* from .env
# regardless of which other module happened to import
# python-dotenv first (matches the pattern already used by
# llm_client.py / jooble.py). Safe to call more than once.
load_dotenv()


# ============================================================
# JOBPILOT AI - EMAIL SENDER
#
# Low-level, isolated SMTP sender. Nothing in this module
# talks to the database or knows about JobApplication,
# candidates, or recruiters — see
# backend/app/services/email/notifications.py for that.
#
# Configuration is read from environment variables only.
# No credentials are ever hard-coded, logged, or returned
# to any API caller.
# ============================================================

logger = logging.getLogger("jobpilot.email")


def get_smtp_config() -> dict:
    """
    Read SMTP configuration from environment variables.

    Expected variables:
    SMTP_HOST
    SMTP_PORT
    SMTP_USERNAME
    SMTP_PASSWORD
    SMTP_FROM_EMAIL
    SMTP_FROM_NAME   (optional, defaults to "HireFlow AI")
    SMTP_USE_TLS     (optional, defaults to "true")
    """

    port_raw = os.getenv("SMTP_PORT")

    port = None

    if port_raw:

        try:
            port = int(port_raw)

        except ValueError:
            port = None

    use_tls_raw = (
        os.getenv("SMTP_USE_TLS", "true")
        or "true"
    ).strip().lower()

    return {
        "host": os.getenv("SMTP_HOST"),
        "port": port,
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from_email": os.getenv("SMTP_FROM_EMAIL"),
        "from_name": (
            os.getenv("SMTP_FROM_NAME")
            or "HireFlow AI"
        ),
        "use_tls": use_tls_raw != "false",
    }


def is_smtp_configured(config: dict | None = None) -> bool:
    """
    True only if the minimum SMTP settings required to
    actually attempt a send are present. Never raises.
    """

    config = config or get_smtp_config()

    return bool(
        config.get("host")
        and config.get("port")
        and config.get("username")
        and config.get("password")
        and config.get("from_email")
    )


def add_attachments(message, attachments, reply_to=None) -> None:
    """attachments: [(filename, bytes, "type/subtype"), ...]. reply_to makes the candidate's Reply go to the sender at the company."""

    if reply_to:
        message["Reply-To"] = reply_to

    for filename, data, mime in attachments or []:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        message.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)


def send_email(
    to_email: str | None,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list | None = None,
    reply_to: str | None = None,
) -> bool:
    """
    Send an email -- Gmail API (OAuth) first if configured, since it doesn't
    depend on an App Password and survives account 2FA changes; falls back
    to raw SMTP otherwise.

    html_body is optional: when given, the email is sent as multipart
    text/plain + text/html (most clients render the HTML part); when
    omitted, it's plain text only, exactly as before.

    Returns True only if the message was handed off successfully. Returns
    False (never raises) if the recipient is missing, neither sender is
    configured, or the send attempt fails for any reason — callers must be
    able to treat this as best-effort and never let a failure here affect
    anything else.
    """

    from backend.app.services.email.gmail_api_mailer import (
        is_gmail_api_configured,
        send_email_via_gmail_api,
    )

    if is_gmail_api_configured():
        return send_email_via_gmail_api(to_email, subject, body, html_body, attachments, reply_to)

    if not to_email:

        logger.warning(
            "Candidate email notification skipped: "
            "no recipient email address on file."
        )

        return False

    config = get_smtp_config()

    if not is_smtp_configured(config):

        logger.warning(
            "Candidate email notification skipped: SMTP is not "
            "configured. Set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, "
            "SMTP_PASSWORD and SMTP_FROM_EMAIL in the environment "
            "to enable real email delivery."
        )

        return False

    message = EmailMessage()

    message["Subject"] = subject

    message["From"] = (
        f'{config["from_name"]} <{config["from_email"]}>'
    )

    message["To"] = to_email

    message.set_content(body)

    if html_body:
        message.add_alternative(html_body, subtype="html")

    add_attachments(message, attachments, reply_to)

    try:

        with smtplib.SMTP(
            config["host"],
            config["port"],
            timeout=10,
        ) as server:

            if config["use_tls"]:
                server.starttls()

            server.login(
                config["username"],
                config["password"],
            )

            server.send_message(message)

        # NEVER log the password or any credential value.
        logger.info(
            "Candidate email notification sent to %s "
            "(subject=%r).",
            to_email,
            subject,
        )

        return True

    except Exception as exc:

        # NEVER log the password or any credential value —
        # only the exception message, which smtplib does not
        # populate with the SMTP_PASSWORD value.
        logger.error(
            "Candidate email notification failed to %s "
            "(subject=%r): %s",
            to_email,
            subject,
            exc,
        )

        return False

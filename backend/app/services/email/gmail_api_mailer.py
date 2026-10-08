import base64
import logging
import os
from email.message import EmailMessage

from dotenv import load_dotenv

# Loaded here so this module reads GOOGLE_*/GMAIL_* from .env regardless of
# which other module happened to import python-dotenv first, matching the
# pattern already used by mailer.py.
load_dotenv()


# ============================================================
# JOBPILOT AI - GMAIL API SENDER (OAuth, real Gmail account)
#
# Preferred over mailer.py's raw SMTP sender when configured. Uses a
# long-lived refresh token (obtained once via backend/scripts/
# gmail_oauth_setup.py) to mint short-lived access tokens automatically --
# no re-consent needed as long as the refresh token stays valid.
#
# Nothing in this module talks to the database or knows about
# JobApplication/candidates -- see notifications.py for that.
# ============================================================

logger = logging.getLogger("jobpilot.email.gmail_api")

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def get_gmail_api_config() -> dict:
    """Read Gmail API OAuth configuration from environment variables.

    Expected variables:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REFRESH_TOKEN
    GMAIL_SENDER_EMAIL
    """

    return {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"),
        "sender_email": os.getenv("GMAIL_SENDER_EMAIL"),
    }


def is_gmail_api_configured(config: dict | None = None) -> bool:
    """True only if every setting required to mint an access token and send
    is present. Never raises."""

    config = config or get_gmail_api_config()

    return bool(
        config.get("client_id")
        and config.get("client_secret")
        and config.get("refresh_token")
        and config.get("sender_email")
    )


def _build_credentials(config: dict):

    from google.oauth2.credentials import Credentials

    return Credentials(
        token=None,
        refresh_token=config["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        scopes=[GMAIL_SEND_SCOPE],
    )


def send_email_via_gmail_api(
    to_email: str | None,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list | None = None,
    reply_to: str | None = None,
) -> bool:
    """Send an email through the Gmail API using the configured OAuth
    refresh token.

    html_body is optional: when given, the message is sent as multipart
    text/plain + text/html so HTML-capable clients render the rich version
    while plain-text clients still get something readable.

    Returns True only if Gmail accepted the message for delivery. Returns
    False (never raises) if the recipient is missing, the Gmail API isn't
    configured, or the send attempt fails for any reason -- callers must be
    able to treat this as best-effort.
    """

    if not to_email:

        logger.warning(
            "Candidate email notification skipped: "
            "no recipient email address on file."
        )

        return False

    config = get_gmail_api_config()

    if not is_gmail_api_configured(config):

        logger.warning(
            "Candidate email notification skipped: Gmail API is not "
            "configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
            "GOOGLE_REFRESH_TOKEN and GMAIL_SENDER_EMAIL in the "
            "environment (see backend/scripts/gmail_oauth_setup.py)."
        )

        return False

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = config["sender_email"]
    message["To"] = to_email

    message.set_content(body)

    if html_body:
        message.add_alternative(html_body, subtype="html")

    from backend.app.services.email.mailer import add_attachments

    add_attachments(message, attachments, reply_to)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    try:

        from googleapiclient.discovery import build

        credentials = _build_credentials(config)

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

        service.users().messages().send(
            userId="me",
            body={"raw": raw_message},
        ).execute()

        logger.info(
            "Candidate email notification sent to %s via Gmail API (subject=%r).",
            to_email,
            subject,
        )

        return True

    except Exception as exc:

        # NEVER log the refresh token or any credential value -- only the
        # exception message.
        logger.error(
            "Candidate email notification failed to %s via Gmail API "
            "(subject=%r): %s",
            to_email,
            subject,
            exc,
        )

        return False

"""The test suite must never send a real email: the developer's .env holds real SMTP / Gmail credentials (see conftest.py)."""

import os

from backend.app.services.email.mailer import is_smtp_configured, get_smtp_config, send_email


def test_the_real_email_credentials_are_blanked_for_every_test():
    for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "GOOGLE_REFRESH_TOKEN"):
        assert os.environ.get(name) == "", name


def test_the_mailer_refuses_to_send_in_tests():
    assert is_smtp_configured(get_smtp_config()) is False
    assert send_email("someone@example.com", "subject", "body") is False

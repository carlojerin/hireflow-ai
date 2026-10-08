"""Offer letter from the candidate report: an editable draft, sent only to the candidate's own address, with attachments."""

import os
import sys
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.app.api.routes.offer_letters as offer_routes
from backend.app.api.deps import get_current_company
from backend.app.database.database import get_db
from backend.app.models.candidate_profile import CandidateProfile
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication
from backend.app.models.offer_letter_log import OfferLetterLog
from backend.app.models.user import User

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup(other_company=False):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    for model in (User, Company, Job, JobApplication, CandidateProfile, OfferLetterLog):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()

    company = Company(
        id=1, company_name="Acme", legal_name="Acme Ltd", email="hr@acme.test", mobile="123", hr_name="Priya Nair",
        hashed_password="x", account_active=True, verification_status="verified", admin_verified=True, role="company",
    )
    db.add(company)
    db.add(Job(id=1, company_id=1, title="Backend Engineer", company="Acme", status="active"))
    db.add(User(id=5, full_name="Riya Suresh", username="riya", email="riya@example.test", mobile=None, hashed_password="x", role="user"))
    db.commit()
    db.add(JobApplication(id=9, user_id=5, job_id=1, status="applied"))
    db.commit()

    api = FastAPI()
    api.include_router(offer_routes.router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_current_company] = lambda: company
    client = TestClient(api, cookies={"csrf_token": "t"}, headers={"X-CSRF-Token": "t"})
    return db, client


def _capture(monkeypatch, result=True):
    sent = {}

    def fake(to_email, subject, body, html_body=None, attachments=None, reply_to=None):
        sent.update(to=to_email, subject=subject, body=body, html=html_body, attachments=attachments, reply_to=reply_to)
        return result

    monkeypatch.setattr(offer_routes, "send_email", fake)
    return sent


FILLED = "Dear Riya,\n\nCongratulations, we would like to offer you the Backend Engineer role starting 1 December.\n\nWarm regards,\nPriya"


def test_the_draft_uses_the_candidates_name_the_role_the_company_and_the_hr_sender():
    _db, client = _setup()

    data = client.get("/api/offer-letters/1/9/draft").json()

    assert data["to_email"] == "riya@example.test" and data["reply_to"] == "hr@acme.test"
    assert data["subject"] == "Job Offer - Backend Engineer at Acme"
    assert data["body"].startswith("Dear Riya,") and "Backend Engineer at Acme" in data["body"] and "Priya Nair" in data["body"]
    assert "[Start date]" in data["body"] and "[Reply-by date]" in data["body"]          # what only HR knows is left to fill in


def test_another_companys_application_is_not_found():
    _db, client = _setup()

    assert client.get("/api/offer-letters/1/999/draft").status_code == 404
    assert client.get("/api/offer-letters/2/9/draft").status_code in (403, 404)


def test_the_edited_letter_goes_only_to_the_candidates_own_address_with_attachments_and_is_logged(monkeypatch):
    db, client = _setup()
    sent = _capture(monkeypatch)

    response = client.post(
        "/api/offer-letters/1/9/send",
        data={"subject": "Job Offer - Backend Engineer", "body": FILLED, "to_email": "someone-else@evil.test"},
        files=[("attachments", ("Offer Letter.pdf", b"%PDF-1.4 test", "application/pdf")), ("attachments", ("logo.png", b"\x89PNG", "image/png"))],
    )

    assert response.status_code == 200 and response.json()["attachments"] == ["Offer Letter.pdf", "logo.png"]
    assert sent["to"] == "riya@example.test"                                   # the request cannot choose the recipient
    assert sent["reply_to"] == "hr@acme.test" and [a[0] for a in sent["attachments"]] == ["Offer Letter.pdf", "logo.png"]
    assert "<p" in sent["html"] and "Congratulations" in sent["html"]

    log = db.query(OfferLetterLog).one()
    assert log.delivered == 1 and log.attachment_names == "Offer Letter.pdf, logo.png"

    assert client.get("/api/offer-letters/1/9/draft").json()["last_sent_at"]


def test_a_letter_with_blanks_left_in_it_is_stopped_unless_hr_confirms(monkeypatch):
    _db, client = _setup()
    sent = _capture(monkeypatch)

    body = FILLED + "\nStart date: [Start date]"

    blocked = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": body})
    assert blocked.status_code == 409 and "[Start date]" in blocked.json()["detail"] and not sent

    allowed = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": body, "allow_placeholders": "true"})
    assert allowed.status_code == 200 and sent["to"] == "riya@example.test"


def test_unsafe_or_oversized_attachments_are_refused(monkeypatch):
    _db, client = _setup()
    sent = _capture(monkeypatch)

    exe = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": FILLED}, files=[("attachments", ("run.exe", b"MZ", "application/octet-stream"))])
    assert exe.status_code == 400 and not sent

    many = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": FILLED}, files=[("attachments", (f"f{i}.txt", b"x", "text/plain")) for i in range(6)])
    assert many.status_code == 400 and "at most" in many.json()["detail"]

    big = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": FILLED}, files=[("attachments", ("big.pdf", b"x" * (10 * 1024 * 1024 + 5), "application/pdf"))])
    assert big.status_code == 400 and "larger" in big.json()["detail"]


def test_a_failed_send_says_so_and_is_logged_as_not_delivered(monkeypatch):
    db, client = _setup()
    _capture(monkeypatch, result=False)

    response = client.post("/api/offer-letters/1/9/send", data={"subject": "Offer", "body": FILLED})

    assert response.status_code == 502 and "could not be sent" in response.json()["detail"]
    assert db.query(OfferLetterLog).one().delivered == 0
    assert client.get("/api/offer-letters/1/9/draft").json()["last_sent_at"] is None


def test_the_mailers_attach_files_and_set_reply_to():
    from email.message import EmailMessage

    from backend.app.services.email.mailer import add_attachments

    message = EmailMessage()
    message.set_content("hi")
    add_attachments(message, [("a.pdf", b"%PDF", "application/pdf"), ("b.png", b"\x89PNG", "image/png")], "hr@acme.test")

    assert message["Reply-To"] == "hr@acme.test"
    assert [part.get_filename() for part in message.iter_attachments()] == ["a.pdf", "b.png"]


def test_the_report_page_has_the_offer_letter_button_and_editor():
    page = open(os.path.join(ROOT, "frontend", "company-candidate-report.html"), encoding="utf-8").read()
    script = open(os.path.join(ROOT, "frontend", "js", "company-offer-letter.js"), encoding="utf-8").read()

    assert 'id="offerLetterBtn"' in page and "company-offer-letter.js" in page and 'id="offerLetterModal"' in page
    assert "/api/offer-letters/" in script and "FormData" in script and "allow_placeholders" in script

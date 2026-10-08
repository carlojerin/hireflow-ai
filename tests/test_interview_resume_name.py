"""The AI interview greets the candidate by the name on their resume, not the account name."""

from types import SimpleNamespace

import pytest

from backend.app.services.assessment.interview_context_builder_v2 import _name_for_greeting
from backend.app.services.resume.contact_extractor import candidate_name_from_resume


@pytest.mark.parametrize("text,expected", [
    ("RIYA SURESH\nriya@example.com", "Riya Suresh"),
    ("Dr. Anita Rao\n+91 99999", "Anita Rao"),
    ("Resume\nArjun Kumar", "Arjun Kumar"),
    ("Software Engineer\nJohn Doe", None),
    ("Full Stack Developer", None),
    ("", None),
])
def test_name_is_read_from_the_top_of_the_resume(text, expected):
    assert candidate_name_from_resume(text) == expected


def test_greeting_name_prefers_the_resume_then_the_account():
    user = SimpleNamespace(id=1, full_name="jerin")
    job = SimpleNamespace(id=1)

    assert _name_for_greeting(job, user, SimpleNamespace(raw_text="Carlo Jerin\nx@y.com")) == "Carlo Jerin"
    assert _name_for_greeting(job, user, SimpleNamespace(raw_text="Software Engineer")) == "jerin"
    assert _name_for_greeting(job, user, None) == "jerin"

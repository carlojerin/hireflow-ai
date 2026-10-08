"""Delivery signals (how it was said), the hint-echo guard, and the interviewer's emotional-intelligence brief."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.assessment.interview_delivery_v2 import analyze_delivery
from backend.app.services.assessment.voice_provider_v2 import is_hint_echo
from backend.app.services.assessment import realtime_interview_v2 as rt


def test_hesitant_answer_gets_a_reassuring_steer():
    d = analyze_delivery("Um, I I built, uh, like, a REST API, I think, maybe with, um, Spring.")
    assert "hesitant" in d["tags"] and "unsure" in d["tags"]
    assert "reassuring" in d["note"] and "concrete" in d["note"]


def test_fluent_specific_answer_gets_a_deeper_steer():
    d = analyze_delivery("I designed the order service in Spring Boot because the checkout queries were slow, so I added Redis caching "
                         "and the response time dropped from two seconds to 300 milliseconds, which reduced timeouts.", seconds=25)
    assert "fluent_and_specific" in d["tags"]
    assert "deeper" in d["note"] or "difficulty" in d["note"]
    assert d["wpm"] and 60 < d["wpm"] < 200


def test_terse_and_long_answers_get_different_steers():
    assert "terse" in analyze_delivery("Yes.")["tags"] and "open" in analyze_delivery("Yes.")["note"]
    long_text = " ".join(["we worked on the service and the deployment pipeline"] * 20)
    assert "long" in analyze_delivery(long_text)["tags"]
    assert analyze_delivery("")["note"] == ""


def test_the_vocabulary_hint_coming_back_is_not_an_answer():
    hint = "Technical job interview. Terms that may be spoken: Python, API, SQL, Git, FastAPI, PostgreSQL, Redis, Docker"
    assert is_hint_echo("Technical job interview Python API SQL Git FastAPI PostgreSQL Redis Docker", hint) is True    # the hint handed back verbatim
    assert is_hint_echo("Python SQL Git Redis Docker", hint) is True
    assert is_hint_echo("I built a FastAPI service with Redis caching", hint) is False          # real speech with other words
    assert is_hint_echo("", hint) is False and is_hint_echo("hello there", None) is False


def test_realtime_brief_teaches_the_interviewer_to_hear_mood_and_answer_like_a_person():
    from types import SimpleNamespace as N
    text = rt.build_realtime_instruction.__code__.co_consts
    joined = " ".join(str(c) for c in text)
    assert "HOW they speak" in joined and "hesitant" in joined and "Never mention that you are analysing" in joined
    assert "under 25 words" in joined and "senior engineer" in joined
    assert "completely normal and NOT a failure" in joined and "Never press" in joined


def test_the_hints_own_lead_in_is_not_an_answer():
    hint = "Technical job interview. Terms that may be spoken: Python, API, SQL"
    assert is_hint_echo("Technical job interview.", hint) is True                 # seen in a real interview (turn 6, application 54)
    assert is_hint_echo("Technical job interview. Terms that may be spoken", hint) is True
    assert is_hint_echo("I have technical job interview experience", hint) is False

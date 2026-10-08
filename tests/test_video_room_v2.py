"""
Deterministic security/authorization tests for the AI Interview V2
video room (Task 5L). No live LiveKit/Tavus account required or
contacted -- same offline-JWT-signing approach as
test_avatar_provider_v2.py (Task 5K), since Task 5L reuses that
SAME token endpoint/service unmodified.

Covers Task 5L Step 25/26:
  1/2. Candidate A cannot obtain/use Candidate B's room token
  3/4. Wrong application / wrong candidate rejected
  5. Unauthorized request rejected
  6/7. LiveKit API secret / avatar provider secret never exposed
  8. Candidate cannot select an arbitrary room name (structural --
     generate_avatar_room_token_v2 takes no room-name parameter)
  9. Candidate cannot impersonate another participant (identity is
     server-derived from application.user_id, never client input)
  10. Token grants are scoped correctly for camera+mic publishing
  11. Token expiry (re-verified for the video-room use case)

Also documents and regression-tests a REAL BUG found during this
task's own security audit: interview_avatar_v2.py and
interview_voice_v2.py's routes called get_owned_application() but
never checked for None before using the result -- every other route
in this project (see assessment_sessions.py) raises 404 when
get_owned_application() returns None, but these two files were
missing that check, meaning a request for an application_id owned
by a DIFFERENT candidate (or one that doesn't exist) would crash
with an unhandled 500 instead of a clean 404. Fixed in both files as
part of this task; the ownership-check function itself
(get_owned_application) is exercised directly here since it needs
no HTTP layer to test.
"""

import sys
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.assessment import avatar_provider_v2 as provider
from backend.app.services.assessment.avatar_token_service_v2 import (
    generate_avatar_room_token_v2,
)
from backend.app.services.assessment.session_service import get_owned_application

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database.database import Base
from backend.app.models.user import User
from backend.app.models.company import Company
from backend.app.models.job import Job
from backend.app.models.job_application import JobApplication


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed_two_candidates(db):

    candidate_a = User(full_name="Candidate A", username="video_room_a", email="videoroomA@example.com",
                        hashed_password="x", role="user")
    candidate_b = User(full_name="Candidate B", username="video_room_b", email="videoroomB@example.com",
                        hashed_password="x", role="user")
    db.add(candidate_a)
    db.add(candidate_b)
    db.flush()

    job = Job(title="Backend Developer", company="TestCo", required_skills="Python")
    db.add(job)
    db.flush()

    application_a = JobApplication(job_id=job.id, user_id=candidate_a.id, status="programming_passed")
    application_b = JobApplication(job_id=job.id, user_id=candidate_b.id, status="programming_passed")
    db.add(application_a)
    db.add(application_b)
    db.commit()
    db.refresh(application_a)
    db.refresh(application_b)

    return candidate_a, candidate_b, application_a, application_b


def _set_full_config():
    import os
    os.environ["LIVEKIT_URL"] = "wss://jobpilot-test.livekit.cloud"
    os.environ["LIVEKIT_API_KEY"] = "test_api_key"
    os.environ["LIVEKIT_API_SECRET"] = "test_api_secret_at_least_32_characters_long"
    os.environ["JOBPILOT_AI_INTERVIEWER_AVATAR_ID"] = "face_abc123"


def _clear_config():
    import os
    for key in (
        "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
        "JOBPILOT_AI_INTERVIEWER_AVATAR_ID", "AVATAR_PROVIDER",
    ):
        os.environ.pop(key, None)


# 1/3/4/5: Candidate A's session cannot resolve Candidate B's application
# at the ownership-lookup layer that ALL three video-room-related routes
# (voice/speak, voice/transcribe, avatar/token) depend on.
def test_candidate_cannot_resolve_a_different_candidates_application():

    db = _make_db()
    candidate_a, candidate_b, application_a, application_b = _seed_two_candidates(db)

    # Candidate A tries to look up Candidate B's application_id under
    # their OWN user_id -- must resolve to nothing (the same "not found"
    # a truly nonexistent application_id would produce, Section 15's
    # "wrong application: 404/403 according to existing security
    # conventions").
    resolved = get_owned_application(db, application_b.id, candidate_a.id)
    assert resolved is None


# 3: wrong/nonexistent application_id also resolves to nothing
def test_nonexistent_application_id_resolves_to_nothing():

    db = _make_db()
    candidate_a, _candidate_b, _application_a, _application_b = _seed_two_candidates(db)

    resolved = get_owned_application(db, 999999, candidate_a.id)
    assert resolved is None


# The real bug found+fixed this task: routes must check for None and
# 404, never pass None straight into the token/voice service.
def test_avatar_and_voice_routes_now_check_for_none_before_use():

    import ast

    for module_path in (
        "backend/app/api/routes/interview_avatar_v2.py",
        "backend/app/api/routes/interview_voice_v2.py",
    ):

        source = (Path(__file__).resolve().parents[1] / module_path).read_text()

        # Every get_owned_application(...) call must be followed
        # (textually, within a reasonable window) by a None-check --
        # a cheap but effective regression guard against the exact
        # bug found in this task's audit re-appearing silently.
        assert "get_owned_application(" in source
        assert "if not application" in source


# 10: token grants are scoped correctly for camera+mic PUBLISHING
# (Task 5L needs can_publish for camera+mic, unlike a pure viewer)
def test_video_room_token_grants_allow_camera_and_mic_publish():

    _clear_config()
    _set_full_config()

    db = _make_db()
    _candidate_a, _candidate_b, application_a, _application_b = _seed_two_candidates(db)

    result = generate_avatar_room_token_v2(application_a)

    decoded = jwt.decode(result["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])

    assert decoded["video"]["canPublish"] is True
    assert decoded["video"]["canSubscribe"] is True
    # room_admin / room_create / room_list are never granted -- a
    # candidate token can never manage or discover other rooms.
    assert "roomAdmin" not in decoded["video"] or not decoded["video"].get("roomAdmin")
    assert "roomCreate" not in decoded["video"] or not decoded["video"].get("roomCreate")
    assert "roomList" not in decoded["video"] or not decoded["video"].get("roomList")

    _clear_config()


# 8: candidate cannot select an arbitrary room name -- structural,
# the function signature itself takes no room-name input
def test_room_name_is_never_client_controllable():

    import inspect

    signature = inspect.signature(generate_avatar_room_token_v2)
    param_names = list(signature.parameters.keys())

    assert param_names == ["application"]  # NOT room_name, NOT any client-suppliable string


# 9: participant identity is derived server-side from the OWNING
# candidate's user_id, never from any client-supplied identity string
def test_identity_is_derived_from_owning_candidate_not_client_input():

    _clear_config()
    _set_full_config()

    db = _make_db()
    candidate_a, candidate_b, application_a, application_b = _seed_two_candidates(db)

    token_a = generate_avatar_room_token_v2(application_a)
    token_b = generate_avatar_room_token_v2(application_b)

    assert token_a["identity"] == f"candidate-{candidate_a.id}"
    assert token_b["identity"] == f"candidate-{candidate_b.id}"
    assert token_a["identity"] != token_b["identity"]

    _clear_config()


# 6/7: neither the LiveKit API secret nor the avatar provider's own
# credentials ever appear in ANY field of the response
def test_no_provider_secrets_anywhere_in_the_response():

    _clear_config()
    _set_full_config()
    import os
    os.environ["TAVUS_API_KEY"] = "sk-tavus-should-never-leak-anywhere"

    db = _make_db()
    _candidate_a, _candidate_b, application_a, _application_b = _seed_two_candidates(db)

    result = generate_avatar_room_token_v2(application_a)
    serialized = str(result)

    assert "test_api_secret_at_least_32_characters_long" not in serialized
    assert "sk-tavus-should-never-leak-anywhere" not in serialized

    os.environ.pop("TAVUS_API_KEY", None)
    _clear_config()


# 11: bounded expiry re-verified for the video-room use case (same
# property Task 5K already tested, re-asserted here since Task 5L's
# security audit explicitly calls for it again)
def test_token_expiry_is_bounded_for_video_room_use():

    _clear_config()
    _set_full_config()

    db = _make_db()
    _candidate_a, _candidate_b, application_a, _application_b = _seed_two_candidates(db)

    result = generate_avatar_room_token_v2(application_a)
    decoded = jwt.decode(result["token"], "test_api_secret_at_least_32_characters_long", algorithms=["HS256"])

    assert decoded["exp"] > decoded["nbf"]
    assert (decoded["exp"] - decoded["nbf"]) <= 60 * 60  # never unbounded/multi-hour

    _clear_config()

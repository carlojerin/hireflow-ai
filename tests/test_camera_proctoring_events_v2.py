"""Camera proctoring event vocabulary: what counts toward the warnings/termination and what is only a recruiter flag."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.assessment import integrity_service as svc
from backend.app.api.routes import hr_assessment_review as hr


def test_covered_camera_counts_as_a_violation_but_looking_away_is_only_a_flag():
    assert "camera_covered" in svc.VIOLATION_EVENT_TYPES
    assert "looking_away" in svc.INFO_EVENT_TYPES and "looking_away" not in svc.VIOLATION_EVENT_TYPES
    for name in ("face_missing", "multiple_faces_detected", "mobile_phone_detected", "camera_covered", "looking_away", "face_present"):
        assert name in svc.CLIENT_ALLOWED_EVENT_TYPES
        assert name not in svc.SERVER_ONLY_EVENT_TYPES


def test_sustained_gaze_away_and_voice_detected_are_real_violations_not_just_flags():
    """A brief glance/quick blip is only ever a flag (gaze_away), but a SUSTAINED 20s
    look-away or ANY voice during a silent stage are real, counted violations -- unlike
    the softer signals, which people trigger through ordinary, innocent behaviour."""

    for name in ("gaze_away_sustained", "voice_detected"):
        assert name in svc.VIOLATION_EVENT_TYPES and name not in svc.INFO_EVENT_TYPES
        assert name in hr._EVENT_CATEGORY and name in hr._EVENT_LABEL


def test_hr_review_labels_every_camera_signal_neutrally():
    for name in ("looking_away", "camera_covered", "gaze_away_sustained", "voice_detected"):
        assert name in hr._EVENT_CATEGORY and name in hr._EVENT_LABEL
    assert "cheat" not in " ".join(hr._EVENT_LABEL.values()).lower()          # describes the signal, never a verdict


def test_possible_third_party_voice_is_a_flag_never_a_counted_violation():
    """A heuristic pitch-deviation signal (voice-identity-proctor.js, AI interview only) --
    deliberately never auto-counted, same reasoning as gaze_away: too uncertain to end an
    assessment on its own, only ever a flag for HR to review alongside the recording."""

    assert "possible_third_party_voice" in svc.INFO_EVENT_TYPES
    assert "possible_third_party_voice" not in svc.VIOLATION_EVENT_TYPES
    assert "possible_third_party_voice" in hr._EVENT_CATEGORY and "possible_third_party_voice" in hr._EVENT_LABEL


def test_browser_guard_events_are_flags_and_metadata_is_sanitised():
    for name in ("gaze_away", "paste_detected", "copy_detected", "cut_detected", "blocked_shortcut", "devtools_opened", "multiple_monitors_detected"):
        assert name in svc.INFO_EVENT_TYPES and name not in svc.VIOLATION_EVENT_TYPES
        assert name in hr._EVENT_CATEGORY and name in hr._EVENT_LABEL
    assert svc._sanitize_metadata("paste_detected", {"chars": 250, "target": "editor", "text": "SECRET CLIPBOARD"}) == {"chars": 250, "target": "editor"}
    assert svc._sanitize_metadata("paste_detected", {"chars": -5, "target": "evil"}) is None
    assert svc._sanitize_metadata("blocked_shortcut", {"key": "F12"}) == {"key": "F12"}
    assert svc._sanitize_metadata("blocked_shortcut", {"key": "rm -rf"}) is None


def test_new_events_are_accepted_recorded_and_only_violations_count():
    """Real record_integrity_event() against an in-memory database: flags never add to the warning count, camera_covered does."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.app.database.database import Base
    import backend.app.models  # noqa: F401
    from backend.app.models import assessment_integrity_event  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()          # the app's own SessionLocal setting

    application = type("A", (), {"id": 1, "user_id": 1})()

    def send(event_type, metadata=None):
        return svc.record_integrity_event(db, application, "programming", event_type, None, metadata)

    for flag, meta in (("looking_away", None), ("gaze_away", None), ("paste_detected", {"chars": 400, "target": "editor"}),
                       ("blocked_shortcut", {"key": "F12"}), ("devtools_opened", None), ("multiple_monitors_detected", None)):
        state = send(flag, meta)
        assert state["violation_count"] == 0, flag                      # flags never move the warning level

    state = send("camera_covered")
    assert state["violation_count"] == 1, state                        # a covered camera is a real violation

    rows = db.query(assessment_integrity_event.AssessmentIntegrityEvent).filter_by(event_type="paste_detected").all()
    assert rows and '"chars": 400' in rows[0].metadata_json and "SECRET" not in rows[0].metadata_json


def test_voice_detected_and_sustained_gaze_away_each_count_as_a_real_violation():
    """Separate, isolated applications (not chained against the 5 s cross-type cooldown
    test_new_events_are_accepted_recorded_and_only_violations_count already covers) --
    each of these two new violation types counts on its own, from zero."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.app.database.database import Base
    import backend.app.models  # noqa: F401

    for event_type in ("voice_detected", "gaze_away_sustained"):

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine, autoflush=False)()

        application = type("A", (), {"id": 1, "user_id": 1})()

        state = svc.record_integrity_event(db, application, "programming", event_type, None, None)
        assert state["violation_count"] == 1, (event_type, state)


def _dummy_insert(Session, table, **values):
    import datetime
    from sqlalchemy import Boolean, DateTime, Float, Integer
    from backend.app.database.database import Base
    t = Base.metadata.tables[table]
    row = dict(values)
    for c in t.columns:
        if c.name in row or c.primary_key or c.nullable or c.default is not None or c.server_default is not None:
            continue
        if c.foreign_keys:
            row[c.name] = 1
        elif isinstance(c.type, Boolean):
            row[c.name] = False
        elif isinstance(c.type, (Integer, Float)):
            row[c.name] = 1
        elif isinstance(c.type, DateTime):
            row[c.name] = datetime.datetime.utcnow()
        else:
            row[c.name] = "x"
    db = Session()
    db.execute(t.insert().values(**row))
    db.commit()
    db.close()


def test_third_violation_terminates_a_programming_v2_assessment_and_an_interview_v2_session():
    """Regression: termination only looked at the legacy tables, so a V2 assessment/interview never ended (and every later
    event wrote another 'assessment_auto_terminated' row)."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.app.database.database import Base
    import backend.app.models  # noqa: F401
    from backend.app.models.job_application import JobApplication
    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    _dummy_insert(Session, "users", id=1, email="t@t.t")
    _dummy_insert(Session, "job_applications", id=1, job_id=1, user_id=1, status="programming_pending")
    _dummy_insert(Session, "programming_assessments_v2", id=1, application_id=1, status="in_progress")
    _dummy_insert(Session, "ai_interview_sessions_v2", id=1, application_id=1, status="in_progress")

    db = Session()
    application = db.query(JobApplication).filter_by(id=1).first()

    # spread the violations beyond the cooldown by back-dating the stored ones
    import datetime
    from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent

    for stage in ("programming", "ai_interview"):
        for i in range(3):
            state = svc.record_integrity_event(db, application, stage, "camera_covered", None, None)
            db.commit()
            for row in db.query(AssessmentIntegrityEvent).filter_by(assessment_type=stage).all():
                row.occurred_at = row.occurred_at - datetime.timedelta(minutes=10)
            db.commit()
        assert state["termination_required"] is True and state["assessment_active"] is False, (stage, state)

    db.commit()
    assert db.query(ProgrammingAssessmentV2).first().status == "terminated"
    assert db.query(AIInterviewSessionV2).first().status == "terminated"
    assert db.query(JobApplication).first().status in ("interview_terminated", "programming_terminated")

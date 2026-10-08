import os
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.app.models.assessment_integrity_event import (
    AssessmentIntegrityEvent,
)
from backend.app.models.job_application import JobApplication


# ============================================================
# JOBPILOT AI - ASSESSMENT INTEGRITY SERVICE
# (Task 4D-STRICT-1, extended by Task 4E)
#
# Centralized, assessment-type-agnostic logic for Strict
# Assessment Mode: recording integrity events, deduplicating
# closely-related signals, counting real violations, deciding
# the current warning level, and triggering server-authoritative
# termination when the policy limit is reached.
#
# IMPORTANT — what this module does NOT do:
# it does not claim a browser can prevent a candidate from
# switching OS-level applications or windows, and it does not
# claim a camera can guarantee no other person or device is
# present in the room. It only reacts to the signals a browser
# genuinely can report (visibility change, window blur/focus,
# fullscreen exit, camera connection health, coarse face
# presence/count from the candidate's own camera feed) — see
# docs/strict-assessment-mode.md. Camera-derived signals are
# strictly presence/count only: no identity, no embeddings, no
# biometric or demographic inference of any kind, and no raw
# image/video data ever reaches this module or the database.
#
# Task 4E integrated "programming" and "ai_interview" alongside
# "aptitude" (see INTEGRATED_ASSESSMENT_TYPES / _FINALIZERS
# below) so camera/face signals — and the pre-existing tab/
# focus/fullscreen signals — now apply consistently across all
# three timed stages, sharing this exact same violation counter,
# cooldown, and warning/termination policy. No second counter,
# no second policy.
# ============================================================

WARNING_LIMIT = 2
TERMINATION_LIMIT = 3

# Demo / pilot override: ASSESSMENT_MAX_VIOLATIONS raises the number of violations that ends an assessment (default 3, the
# production rule). A presenter turning to an audience must not be terminated on stage.
try:
    _override = int(os.getenv("ASSESSMENT_MAX_VIOLATIONS") or 0)
    if _override >= 3:
        TERMINATION_LIMIT = _override
        WARNING_LIMIT = max(2, _override - 1)
except ValueError:
    pass

# If a violation-eligible event arrives within this many seconds
# of the last COUNTED violation for the same application+
# assessment_type, it is treated as the same real-world action
# (e.g. a single tab-switch fires both `tab_hidden` and
# `window_blur`) and is recorded but NOT counted again.
VIOLATION_COOLDOWN_SECONDS = 5

VALID_ASSESSMENT_TYPES = {"aptitude", "programming", "ai_interview", "situational_judgment", "technical_domain"}

# Assessment types with a real, wired-up finalizer. All timed
# stages are integrated, including situational_judgment and
# technical_domain — both occupy the same slot "programming" does
# for a technical candidate whose language IS executable (see
# role_classification.py).
INTEGRATED_ASSESSMENT_TYPES = {"aptitude", "programming", "ai_interview", "situational_judgment", "technical_domain"}

# Events a candidate's browser may report. Deliberately narrow —
# "every mouse movement/keystroke/scroll/click" is never a valid
# event_type here at all, so there is no code path that could
# ever classify normal interaction as a violation.
#
# camera_disconnected / face_missing / multiple_faces_detected
# (Task 4E) are debounced client-side (a single missed frame
# never fires one) before ever reaching this endpoint, and are
# then subject to the exact same cooldown/violation-count/
# warning/termination policy as the pre-existing tab/focus/
# fullscreen events — no separate camera-specific policy exists.
VIOLATION_EVENT_TYPES = {
    "fullscreen_exited",
    "tab_hidden",
    "window_blur",
    "camera_disconnected",
    "face_missing",
    "multiple_faces_detected",
    # Task 5 - a visible mobile phone, reliably confirmed client-
    # side (consecutive-detection + cooldown debouncing happens in
    # phone-detector.js BEFORE this endpoint is ever called - see
    # its module header) subject to the exact same violation
    # counting/cooldown/warning/termination policy as every other
    # violation type here. No second policy, no new scoring system.
    "mobile_phone_detected",
    # Camera proctoring (face-proctor.js): the lens is covered / the picture stays black. Debounced client-side (3 s).
    "camera_covered",
    # Camera proctoring: eyes off the screen for a SUSTAINED 20 s continuously (not the softer
    # 6 s gaze_away flag below) -- long enough that this is no longer an ordinary glance at a
    # second monitor or down to think, so unlike gaze_away this one is a real counted violation.
    "gaze_away_sustained",
    # Second (and later) 10 s look-away of the same stage, after the first was only a spoken warning.
    "looking_away_repeated",
    # The candidate stopped sharing their screen during a test (the page then blocks the test until they share the ENTIRE screen again).
    "screen_share_stopped",
    # Audio proctoring (silent stages only -- aptitude/programming/SJT/TDA, never the AI interview,
    # where talking is expected): sustained speech-level sound for several seconds while the
    # candidate is not supposed to be talking at all. A real violation (most often a second person
    # coaching out loud), not merely a flag -- unlike voice_detected's old classification below.
    "voice_detected",
}

INFO_EVENT_TYPES = {
    # AI interview: the candidate told the interviewer to ignore its rules, asked it for the answer or for a score. A FLAG for the recruiter, never counted.
    "manipulation_attempt",
    "fullscreen_entered",
    "window_focus",
    "assessment_page_left",
    "assessment_page_returned",
    # Task 4E — camera health/presence signals that are NOT
    # themselves violations (a candidate legitimately lacking a
    # working camera, or one that has just reconnected/shown a
    # face again, is not evidence of anything improper on its
    # own; only sustained absence/multiplicity is).
    "camera_connected",
    "camera_reconnected",
    "camera_permission_denied",
    "camera_unsupported",
    "face_present",
    # Camera proctoring: the head stayed turned away from the candidate's own calibrated pose for >= 10 s. A FLAG for the
    # recruiter (people do look away to think), never counted toward the warnings/termination on its own.
    "looking_away",
    "gaze_away",
    # Voice-identity proctoring (AI interview only -- voice-identity-proctor.js): a sustained (4s+)
    # pitch deviation from the candidate's own calibrated voice range. A HEURISTIC, not real speaker
    # verification -- deliberately NEVER a counted violation, only a flag for HR to review alongside
    # the recording. Even commercial proctoring platforms do not auto-decide on this; they flag it.
    "possible_third_party_voice",
    # Browser guards (integrity-guards.js): recruiter FLAGS, never counted toward the warnings on their own.
    "paste_detected",
    # Browser guards: several keystrokes arriving within a few milliseconds (a person cannot type like that; a browser agent or
    # automation tool types a whole string at once). A FLAG for the recruiter, never counted toward the warnings.
    "automated_typing_suspected",
    # The camera picture stayed too dark / too bright / backlit for 20 s even though the candidate was told how to fix it. Information that
    # explains why the camera may have lost the face; never counted.
    "poor_lighting",
    # Screen sharing (screen-proctor.js): started / the candidate picked a window or tab instead of the entire screen / the browser
    # cannot share a screen at all. Information for the recruiter; the page itself refuses to start the test without a valid share.
    "screen_share_started",
    "screen_share_rejected",
    "screen_share_unsupported",
    "copy_detected",
    "cut_detected",
    "blocked_shortcut",
    "devtools_opened",
    "multiple_monitors_detected",
}

CLIENT_ALLOWED_EVENT_TYPES = VIOLATION_EVENT_TYPES | INFO_EVENT_TYPES

# Server-only audit-trail event types — never accepted from a
# client request body, only ever written by this module itself.
SERVER_ONLY_EVENT_TYPES = {"violation_warning", "assessment_auto_terminated"}

# Task 5 — event types allowed to carry a small, whitelisted
# client-supplied metadata dict, and the exact values allowed for
# each key. metadata_json's own docstring says "never free-form
# client content" — _sanitize_metadata() below is what makes that
# actually true: an event type not listed here gets no metadata
# at all, and any dict is rebuilt key-by-key from only these
# allowed keys/value shapes, so a malformed or malicious payload
# (e.g. an attempt to smuggle image data through this field) can
# never reach the database no matter what the client sends.
METADATA_ALLOWED_KEYS_BY_EVENT_TYPE = {
    # Task 5B adds the temporal-tracking fields (track_id,
    # duration_ms, detections, detector, tracker) alongside the
    # original Task 5 fields — still only ever structured,
    # whitelisted metadata, never a frame/image/embedding.
    "mobile_phone_detected": {
        "object", "confidence", "source",
        "track_id", "duration_ms", "detections", "detector", "tracker",
    },
}

METADATA_ALLOWED_KEYS_BY_EVENT_TYPE.update({
    # size of the clipboard text (never the text itself), where it happened, and which blocked key it was
    "paste_detected": {"chars", "target"},
    "automated_typing_suspected": {"chars", "target"},
    "copy_detected": {"chars", "target"},
    "cut_detected": {"chars", "target"},
    "blocked_shortcut": {"key"},
})

METADATA_ALLOWED_TARGETS = {"editor", "answer_box", "page"}
METADATA_ALLOWED_KEYS = {"F12", "Ctrl+Shift+I", "Ctrl+Shift+J", "Ctrl+Shift+C", "Ctrl+U", "Ctrl+S", "Ctrl+P", "PrintScreen", "ContextMenu"}
METADATA_MAX_CHARS = 10_000_000

METADATA_ALLOWED_SOURCES = {"browser_object_detector"}
METADATA_ALLOWED_DETECTORS = {"yolov8n", "coco-ssd"}
METADATA_ALLOWED_TRACKERS = {"iou", "bytetrack"}

# Generous but finite upper bounds — purely a defense-in-depth
# sanity clamp (a real detection episode is realistically a few
# seconds to a few minutes; a track is a handful to a few hundred
# detections), never a real limit a genuine event would hit.
METADATA_MAX_DURATION_MS = 6 * 60 * 60 * 1000  # 6 hours
METADATA_MAX_DETECTIONS = 100000
METADATA_MAX_TRACK_ID = 10**9


def _sanitize_metadata(event_type: str, metadata: dict | None) -> dict | None:

    allowed_keys = METADATA_ALLOWED_KEYS_BY_EVENT_TYPE.get(event_type)

    if not allowed_keys or not isinstance(metadata, dict):
        return None

    sanitized: dict = {}

    if "object" in allowed_keys and isinstance(metadata.get("object"), str):
        sanitized["object"] = metadata["object"].strip()[:60]

    if "confidence" in allowed_keys:

        confidence = metadata.get("confidence")

        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            sanitized["confidence"] = max(0.0, min(1.0, float(confidence)))

    if "source" in allowed_keys:

        source = metadata.get("source")

        if source in METADATA_ALLOWED_SOURCES:
            sanitized["source"] = source

    if "detector" in allowed_keys:

        detector = metadata.get("detector")

        if detector in METADATA_ALLOWED_DETECTORS:
            sanitized["detector"] = detector

    if "tracker" in allowed_keys:

        tracker = metadata.get("tracker")

        if tracker in METADATA_ALLOWED_TRACKERS:
            sanitized["tracker"] = tracker

    if "track_id" in allowed_keys:

        track_id = metadata.get("track_id")

        if (
            isinstance(track_id, int)
            and not isinstance(track_id, bool)
            and 0 <= track_id <= METADATA_MAX_TRACK_ID
        ):
            sanitized["track_id"] = track_id

    if "duration_ms" in allowed_keys:

        duration_ms = metadata.get("duration_ms")

        if (
            isinstance(duration_ms, (int, float))
            and not isinstance(duration_ms, bool)
            and 0 <= duration_ms <= METADATA_MAX_DURATION_MS
        ):
            sanitized["duration_ms"] = int(duration_ms)

    if "chars" in allowed_keys:

        chars = metadata.get("chars")

        if isinstance(chars, (int, float)) and not isinstance(chars, bool) and 0 <= chars <= METADATA_MAX_CHARS:
            sanitized["chars"] = int(chars)

    if "target" in allowed_keys and metadata.get("target") in METADATA_ALLOWED_TARGETS:
        sanitized["target"] = metadata["target"]

    if "key" in allowed_keys and metadata.get("key") in METADATA_ALLOWED_KEYS:
        sanitized["key"] = metadata["key"]

    if "detections" in allowed_keys:

        detections = metadata.get("detections")

        if (
            isinstance(detections, int)
            and not isinstance(detections, bool)
            and 0 <= detections <= METADATA_MAX_DETECTIONS
        ):
            sanitized["detections"] = detections

    return sanitized or None


class IntegrityServiceError(Exception):
    """Carries an HTTP-style status code so the route layer can
    translate it without this service importing FastAPI —
    mirrors AssessmentSessionError / AptitudeIntegrationError."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _warning_level_for_count(violation_count: int) -> str:

    if violation_count >= TERMINATION_LIMIT:
        return "terminated"

    if violation_count >= WARNING_LIMIT:
        return "final_warning"

    if violation_count >= 1:
        return "warning"

    return "none"


def _count_violations(
    db: Session,
    application_id: int,
    assessment_type: str,
) -> int:

    return (
        db.query(AssessmentIntegrityEvent)
        .filter(
            AssessmentIntegrityEvent.application_id == application_id,
            AssessmentIntegrityEvent.assessment_type == assessment_type,
            AssessmentIntegrityEvent.counted_as_violation.is_(True),
        )
        .count()
    )


def _is_within_cooldown_of_last_violation(
    db: Session,
    application_id: int,
    assessment_type: str,
    now: datetime,
) -> bool:

    last_violation = (
        db.query(AssessmentIntegrityEvent)
        .filter(
            AssessmentIntegrityEvent.application_id == application_id,
            AssessmentIntegrityEvent.assessment_type == assessment_type,
            AssessmentIntegrityEvent.counted_as_violation.is_(True),
        )
        .order_by(AssessmentIntegrityEvent.occurred_at.desc())
        .first()
    )

    if not last_violation:
        return False

    cooldown_deadline = last_violation.occurred_at + timedelta(
        seconds=VIOLATION_COOLDOWN_SECONDS
    )

    return now <= cooldown_deadline


# ============================================================
# APTITUDE FINALIZER — the only wired-up finalizer in this task
# ============================================================

def _terminate_aptitude(
    db: Session,
    application: JobApplication,
) -> bool:
    """
    Server-authoritative termination for the aptitude stage.
    Returns True if a real, active AptitudeTest was actually
    terminated by this call; False if there was nothing active
    to terminate (already completed/expired/terminated
    elsewhere) — idempotent, safe to call more than once.
    """

    # Local import avoids a module-level import cycle (this
    # service is imported by aptitude_tests.py's route module).
    from backend.app.models.aptitude_test import AptitudeTest

    test = (
        db.query(AptitudeTest)
        .filter(AptitudeTest.application_id == application.id)
        .first()
    )

    if not test or test.status != "in_progress":
        return False

    from backend.app.services.application_status_history import (
        record_status_change,
    )

    now = datetime.utcnow()

    test.status = "terminated"
    test.passed = False
    test.completed_at = now
    test.updated_at = now

    record_status_change(
        db,
        application,
        to_status="aptitude_terminated",
        changed_by_type="system",
        changed_by_id=None,
        note=(
            "Aptitude assessment automatically terminated: the "
            "maximum number of assessment integrity violations "
            "was reached during the timed exam (strict mode)."
        ),
    )

    application.updated_date = now

    db.flush()

    return True


# ============================================================
# PROGRAMMING FINALIZER (Task 4E) — mirrors _terminate_aptitude
# exactly: same idempotent in_progress-only guard, same additive
# "terminated" status value, same record_status_change() call.
# ============================================================

def _terminate_programming_v2(db: Session, application: JobApplication) -> bool:
    """
    The current programming flow stores ProgrammingAssessmentV2, not the legacy ProgrammingTest. Termination used to look
    only at the legacy row, so at the 3rd violation nothing happened to a V2 assessment: it stayed "in_progress", every
    later event wrote another "assessment_auto_terminated" row, and the candidate carried on.
    """

    from backend.app.models.programming_assessment_v2 import ProgrammingAssessmentV2
    from backend.app.services.application_status_history import record_status_change

    assessment = (
        db.query(ProgrammingAssessmentV2)
        .filter(ProgrammingAssessmentV2.application_id == application.id)
        .first()
    )

    if not assessment or assessment.status != "in_progress":
        return False

    now = datetime.utcnow()

    assessment.status = "terminated"
    assessment.passed = False
    assessment.submitted_at = now
    assessment.updated_at = now

    record_status_change(
        db, application, to_status="programming_terminated", changed_by_type="system", changed_by_id=None,
        note="Programming assessment automatically terminated: the maximum number of assessment integrity violations was reached (strict mode).",
    )

    application.updated_date = now
    db.flush()

    return True


def _terminate_programming(
    db: Session,
    application: JobApplication,
) -> bool:

    from backend.app.models.programming_test import ProgrammingTest

    test = (
        db.query(ProgrammingTest)
        .filter(ProgrammingTest.application_id == application.id)
        .first()
    )

    if not test or test.status != "in_progress":
        return _terminate_programming_v2(db, application)

    from backend.app.services.application_status_history import (
        record_status_change,
    )

    now = datetime.utcnow()

    test.status = "terminated"
    test.passed = False
    test.submitted_at = now
    test.updated_at = now

    record_status_change(
        db,
        application,
        to_status="programming_terminated",
        changed_by_type="system",
        changed_by_id=None,
        note=(
            "Programming assessment automatically terminated: the "
            "maximum number of assessment integrity violations "
            "was reached during the timed exam (strict mode)."
        ),
    )

    application.updated_date = now

    db.flush()

    return True


# ============================================================
# AI INTERVIEW FINALIZER (Task 4E) — same shape again. A bare
# terminal status (rather than InterviewTest's own partial-
# finalization-on-expiry path used by expire_if_due()) is used
# deliberately: expiry is "time simply ran out, grade what
# exists," but a policy termination for integrity violations is
# a punitive stop, matching how _terminate_aptitude already
# treats termination as distinct from a graded outcome.
# ============================================================

def _terminate_ai_interview_v2(db: Session, application: JobApplication) -> bool:
    """Same gap for the AI Interview V2 session (AIInterviewSessionV2): 1146 "assessment_auto_terminated" audit rows piled up."""

    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
    from backend.app.services.application_status_history import record_status_change

    session = (
        db.query(AIInterviewSessionV2)
        .filter(AIInterviewSessionV2.application_id == application.id)
        .first()
    )

    if not session or session.status != "in_progress":
        return False

    now = datetime.utcnow()

    session.status = "terminated"
    session.completed_at = now
    session.updated_at = now

    record_status_change(
        db, application, to_status="interview_terminated", changed_by_type="system", changed_by_id=None,
        note="AI interview automatically terminated: the maximum number of assessment integrity violations was reached (strict mode).",
    )

    application.updated_date = now
    db.flush()

    return True


def _terminate_ai_interview(
    db: Session,
    application: JobApplication,
) -> bool:

    from backend.app.models.interview_test import InterviewTest

    test = (
        db.query(InterviewTest)
        .filter(InterviewTest.application_id == application.id)
        .first()
    )

    if not test or test.status != "in_progress":
        return _terminate_ai_interview_v2(db, application)

    from backend.app.services.application_status_history import (
        record_status_change,
    )

    now = datetime.utcnow()

    test.status = "terminated"
    test.passed = False
    test.submitted_at = now
    test.updated_at = now

    record_status_change(
        db,
        application,
        to_status="interview_terminated",
        changed_by_type="system",
        changed_by_id=None,
        note=(
            "AI interview automatically terminated: the maximum "
            "number of assessment integrity violations was reached "
            "during the timed interview (strict mode)."
        ),
    )

    application.updated_date = now

    db.flush()

    return True


def _terminate_situational_judgment(db: Session, application: JobApplication) -> bool:
    """Same shape again, for the non-technical pipeline's SJT stage
    (occupies the same slot _terminate_programming does for a
    technical candidate — see role_classification.py)."""

    from backend.app.models.situational_judgment_test import SituationalJudgmentTest
    from backend.app.services.application_status_history import record_status_change

    assessment = (
        db.query(SituationalJudgmentTest)
        .filter(SituationalJudgmentTest.application_id == application.id)
        .first()
    )

    if not assessment or assessment.status != "in_progress":
        return False

    now = datetime.utcnow()

    assessment.status = "terminated"
    assessment.passed = False
    assessment.completed_at = now
    assessment.updated_at = now

    record_status_change(
        db, application, to_status="sjt_terminated", changed_by_type="system", changed_by_id=None,
        note="Situational Judgment Test automatically terminated: the maximum number of assessment integrity violations was reached (strict mode).",
    )

    application.updated_date = now
    db.flush()

    return True


def _terminate_technical_domain(db: Session, application: JobApplication) -> bool:
    """Same shape again, for the non-coding technical pipeline's
    Technical Domain Assessment stage (occupies the same slot
    _terminate_programming does for a technical candidate whose
    language IS executable — see role_classification.py)."""

    from backend.app.models.technical_domain_assessment import TechnicalDomainAssessment
    from backend.app.services.application_status_history import record_status_change

    assessment = (
        db.query(TechnicalDomainAssessment)
        .filter(TechnicalDomainAssessment.application_id == application.id)
        .first()
    )

    if not assessment or assessment.status != "in_progress":
        return False

    now = datetime.utcnow()

    assessment.status = "terminated"
    assessment.passed = False
    assessment.completed_at = now
    assessment.updated_at = now

    record_status_change(
        db, application, to_status="tda_terminated", changed_by_type="system", changed_by_id=None,
        note="Technical Domain Assessment automatically terminated: the maximum number of assessment integrity violations was reached (strict mode).",
    )

    application.updated_date = now
    db.flush()

    return True


_FINALIZERS = {
    "aptitude": _terminate_aptitude,
    "programming": _terminate_programming,
    "ai_interview": _terminate_ai_interview,
    "situational_judgment": _terminate_situational_judgment,
    "technical_domain": _terminate_technical_domain,
}


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def record_integrity_event(
    db: Session,
    application: JobApplication,
    assessment_type: str,
    event_type: str,
    assessment_session_id: int | None,
    metadata: dict | None = None,
) -> dict:
    """
    Records one integrity event and returns the current
    integrity state:

        {
            "assessment_active": bool,
            "violation_count": int,
            "warning_level": "none"|"warning"|"final_warning"|"terminated",
            "termination_required": bool,
        }

    Raises IntegrityServiceError for invalid input. Never trusts
    violation_count/severity/termination status from the caller
    — every one of those is computed here from what is already
    stored in the database.
    """

    if assessment_type not in VALID_ASSESSMENT_TYPES:

        raise IntegrityServiceError(
            status_code=400,
            detail="Invalid assessment_type.",
        )

    if assessment_type not in INTEGRATED_ASSESSMENT_TYPES:

        raise IntegrityServiceError(
            status_code=400,
            detail=(
                "Strict Assessment Mode is not yet integrated "
                f"for '{assessment_type}'."
            ),
        )

    if (
        event_type not in CLIENT_ALLOWED_EVENT_TYPES
        or event_type in SERVER_ONLY_EVENT_TYPES
    ):

        raise IntegrityServiceError(
            status_code=400,
            detail="Invalid event_type.",
        )

    now = datetime.utcnow()
    is_violation_type = event_type in VIOLATION_EVENT_TYPES

    counted = False

    if is_violation_type:

        counted = not _is_within_cooldown_of_last_violation(
            db, application.id, assessment_type, now
        )

    severity = (
        "violation" if counted
        else ("duplicate" if is_violation_type else "info")
    )

    sanitized_metadata = _sanitize_metadata(event_type, metadata)

    db.add(
        AssessmentIntegrityEvent(
            application_id=application.id,
            assessment_session_id=assessment_session_id,
            assessment_type=assessment_type,
            event_type=event_type,
            severity=severity,
            counted_as_violation=counted,
            occurred_at=now,
            metadata_json=(
                json.dumps(sanitized_metadata) if sanitized_metadata else None
            ),
            created_at=now,
        )
    )

    violation_count = _count_violations(
        db, application.id, assessment_type
    ) + (1 if counted else 0)

    warning_level = _warning_level_for_count(violation_count)
    termination_required = warning_level == "terminated"

    if counted and warning_level in ("warning", "final_warning"):

        db.add(
            AssessmentIntegrityEvent(
                application_id=application.id,
                assessment_session_id=assessment_session_id,
                assessment_type=assessment_type,
                event_type="violation_warning",
                severity="warning",
                counted_as_violation=False,
                occurred_at=now,
                metadata_json=json.dumps(
                    {
                        "violation_count": violation_count,
                        "warning_level": warning_level,
                    }
                ),
                created_at=now,
            )
        )

    assessment_active = True

    if termination_required:

        finalizer = _FINALIZERS.get(assessment_type)

        terminated_now = finalizer(db, application) if finalizer else False

        assessment_active = False

        db.add(
            AssessmentIntegrityEvent(
                application_id=application.id,
                assessment_session_id=assessment_session_id,
                assessment_type=assessment_type,
                event_type="assessment_auto_terminated",
                severity="violation",
                counted_as_violation=False,
                occurred_at=now,
                metadata_json=json.dumps(
                    {
                        "violation_count": violation_count,
                        "terminated_now": terminated_now,
                    }
                ),
                created_at=now,
            )
        )

    db.commit()

    return {
        "assessment_active": assessment_active,
        "violation_count": violation_count,
        "warning_level": warning_level,
        "termination_required": termination_required,
        "max_violations": TERMINATION_LIMIT,
    }


def get_integrity_state(
    db: Session,
    application: JobApplication,
    assessment_type: str,
) -> dict:
    """
    Read-only current integrity state — used so a page refresh
    can restore the candidate's violation count/warning level
    without generating a new event. Never mutates anything.
    """

    if assessment_type not in VALID_ASSESSMENT_TYPES:

        raise IntegrityServiceError(
            status_code=400,
            detail="Invalid assessment_type.",
        )

    violation_count = _count_violations(
        db, application.id, assessment_type
    )

    warning_level = _warning_level_for_count(violation_count)
    termination_required = warning_level == "terminated"

    return {
        # Matches record_integrity_event()'s response shape
        # exactly, so frontend code can treat both endpoints'
        # responses identically — a field present on one but
        # silently absent on the other is exactly the kind of gap
        # that turns into "undefined is falsy/truthy" bugs.
        "assessment_active": not termination_required,
        "violation_count": violation_count,
        "warning_level": warning_level,
        "termination_required": termination_required,
        "max_violations": TERMINATION_LIMIT,
    }

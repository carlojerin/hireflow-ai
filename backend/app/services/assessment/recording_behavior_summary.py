"""
"Observed behaviour" paragraph for one recorded video, shown under the video in the HR report.

WHAT IT IS: a short, neutral description of how the candidate conducted themselves during that stage, written ONLY from signals the
platform recorded: how often the camera lost their face or saw them looking away, tab and window changes, pasting, a phone or a second
person in view, screen-sharing stops, and (for the self introduction and the interview) how they spoke: pace, filler words, hesitation
markers, answer length.

WHAT IT IS NOT: it does not read emotions, "confidence", personality, honesty or suitability from the face or the video. Facial
"emotion" and "confidence" scoring has no reliable scientific basis, is biased across faces and accents, and is restricted or banned for
hiring in several places. Speech delivery is reported as plain measurements, never as a verdict on the person. The recruiter decides.

The model only turns the measured facts into a readable paragraph (and is told not to add anything); if it is unavailable a plain
template writes the same facts. Never raises.
"""

import json
import logging
import re
from datetime import datetime

from backend.app.services.assessment.interview_llm_provider import HostedChatProvider, InterviewLLMUnavailableError

logger = logging.getLogger("jobpilot.assessment.behavior_summary")

SCREEN_STAGE_RE = re.compile(r"^(self_intro|aptitude|programming|ai_interview)_screen(?:_([23]))?$")

# the integrity events recorded under each stage's own assessment_type
_EVENT_TYPES_BY_STAGE = {
    "self_intro": ("self_intro",),
    "aptitude": ("aptitude",),
    "programming": ("programming", "situational_judgment", "technical_domain"),
    "ai_interview": ("ai_interview",),
}

# (event type, singular phrase, plural phrase)
_SIGNALS = [
    ("face_missing", "the camera lost sight of the candidate's face", "the camera lost sight of the candidate's face"),
    ("multiple_faces_detected", "more than one person was in view", "more than one person was in view"),
    ("looking_away", "the head turned away from the screen for several seconds", "the head turned away from the screen for several seconds"),
    ("looking_away_repeated", "a second look-away followed a spoken warning", "a look-away followed a spoken warning"),
    ("gaze_away", "the eyes were off the screen for several seconds", "the eyes were off the screen for several seconds"),
    ("gaze_away_sustained", "the eyes stayed off the screen for 20 seconds or more", "the eyes stayed off the screen for 20 seconds or more"),
    ("camera_covered", "the camera was covered", "the camera was covered"),
    ("mobile_phone_detected", "a phone was visible", "a phone was visible"),
    ("unauthorized_object_detected", "another device or object was visible", "another device or object was visible"),
    ("tab_hidden", "the test tab was left", "the test tab was left"),
    ("window_blur", "the test window lost focus", "the test window lost focus"),
    ("fullscreen_exited", "full screen was exited", "full screen was exited"),
    ("screen_share_stopped", "screen sharing was stopped", "screen sharing was stopped"),
    ("paste_detected", "text was pasted", "text was pasted"),
    ("copy_detected", "a copy was attempted", "a copy was attempted"),
    ("blocked_shortcut", "a blocked keyboard shortcut was pressed", "a blocked keyboard shortcut was pressed"),
    ("devtools_opened", "browser developer tools looked open", "browser developer tools looked open"),
    ("automated_typing_suspected", "keystrokes arrived faster than a person can type", "keystrokes arrived faster than a person can type"),
    ("voice_detected", "speech-level sound was heard in a silent test", "speech-level sound was heard in a silent test"),
    ("possible_third_party_voice", "a voice that did not match the candidate's range was heard", "a voice that did not match the candidate's range was heard"),
    ("multiple_monitors_detected", "a second screen was reported by the browser", "a second screen was reported by the browser"),
    ("poor_lighting", "the camera picture was too dark, too bright or backlit for a stretch", "the camera picture was too dark, too bright or backlit for a stretch"),
]


def base_stage(stage: str) -> str:
    match = SCREEN_STAGE_RE.fullmatch(stage or "")
    return match.group(1) if match else stage


def _minutes(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _speech_facts(texts: list[str], seconds: float | None) -> dict | None:
    """Plain measurements of how the candidate spoke (from the words of their spoken answers), never a verdict."""

    from backend.app.services.assessment.interview_delivery_v2 import analyze_delivery

    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return None

    parts = [analyze_delivery(t, None) for t in texts]
    words = sum(p["words"] for p in parts)
    if not words:
        return None

    wpm = round(words / (seconds / 60.0)) if seconds and seconds > 5 and len(texts) == 1 else None

    return {
        "answers": len(texts), "words": words, "words_per_answer": round(words / len(texts)),
        "fillers": sum(p["fillers"] for p in parts), "hedges": sum(p["hedges"] for p in parts), "wpm": wpm,
    }


def gather_facts(db, recording, application) -> dict:
    """Everything measured about this recording's stage, as plain numbers and labelled lines."""

    from backend.app.models.ai_interview_session_v2 import AIInterviewSessionV2
    from backend.app.models.ai_interview_turn_v2 import AIInterviewTurnV2
    from backend.app.models.assessment_integrity_event import AssessmentIntegrityEvent
    from backend.app.models.assessment_session import AssessmentSession
    from backend.app.models.assessment_snapshot import AssessmentSnapshot

    stage = base_stage(recording.stage)
    is_screen = bool(SCREEN_STAGE_RE.fullmatch(recording.stage or ""))

    counts: dict[str, int] = {}
    types = _EVENT_TYPES_BY_STAGE.get(stage, (stage,))

    for event in (
        db.query(AssessmentIntegrityEvent)
        .filter(AssessmentIntegrityEvent.application_id == application.id, AssessmentIntegrityEvent.assessment_type.in_(types))
        .all()
    ):
        counts[event.event_type] = counts.get(event.event_type, 0) + 1

    terminated = counts.get("assessment_auto_terminated", 0) > 0
    camera_pictures = (
        db.query(AssessmentSnapshot)
        .filter(AssessmentSnapshot.application_id == application.id, AssessmentSnapshot.assessment_type.in_(types)).count()
    )

    speech = None
    spoken_turns = 0

    if not is_screen and stage == "self_intro":
        session = db.query(AssessmentSession).filter(AssessmentSession.application_id == application.id).first()
        text = getattr(session, "self_intro_text", None)
        speech = _speech_facts([text] if text else [], recording.duration_seconds)

    if not is_screen and stage == "ai_interview":
        interview = db.query(AIInterviewSessionV2).filter(AIInterviewSessionV2.application_id == application.id).first()
        if interview is not None:
            answers = [
                t.candidate_answer for t in db.query(AIInterviewTurnV2)
                .filter(AIInterviewTurnV2.session_id == interview.id, AIInterviewTurnV2.candidate_answer.isnot(None)).all()
            ]
            spoken_turns = len(answers)
            speech = _speech_facts(answers, None)

    signals = []
    for event_type, singular, plural in _SIGNALS:
        n = counts.get(event_type, 0)
        if n:
            signals.append({"label": (singular if n == 1 else plural).capitalize(), "count": n})

    return {
        "stage": stage, "is_screen": is_screen, "partial": recording.status == "partial", "duration_seconds": recording.duration_seconds,
        "counts": counts, "signals": signals, "terminated": terminated, "camera_pictures": camera_pictures,
        "speech": speech, "spoken_turns": spoken_turns, "warnings": counts.get("violation_warning", 0),
    }


_STAGE_NAMES = {"self_intro": "self introduction", "aptitude": "test", "programming": "test", "ai_interview": "interview"}


def facts_paragraph(facts: dict) -> str:
    """The same facts as plain sentences (used when the model is unavailable, and as the model's only source of truth)."""

    stage_name = _STAGE_NAMES.get(facts["stage"], "stage")
    what = f"screen recording of the {stage_name}" if facts["is_screen"] else f"camera recording of the {stage_name}"
    length = _minutes(facts["duration_seconds"])
    sentences = [f"This is the {what}" + (f", {length} long" if length else "") + (", and it covers only part of the stage because the candidate left before finishing" if facts["partial"] else "") + "."]

    signals = facts["signals"]

    if not signals:
        sentences.append("No attention or conduct signals were recorded during it.")
    else:
        listed = "; ".join(f"{s['label'].lower()} ({s['count']}×)" for s in signals[:8])
        sentences.append(f"Recorded signals: {listed}.")

    if facts["warnings"]:
        sentences.append(f"{facts['warnings']} warning(s) were issued to the candidate during this stage.")

    if facts["terminated"]:
        sentences.append("The stage was ended automatically after repeated integrity violations.")

    speech = facts["speech"]
    if speech:
        pace = f", at about {speech['wpm']} words per minute" if speech["wpm"] else ""
        sentences.append(
            f"Speaking: {speech['words']} words across {speech['answers']} answer(s) (about {speech['words_per_answer']} per answer){pace}, "
            f"with {speech['fillers']} filler word(s) and {speech['hedges']} hedging phrase(s) in the transcript."
        )

    if facts["camera_pictures"]:
        sentences.append(f"{facts['camera_pictures']} camera picture(s) were saved for review.")

    return " ".join(sentences)


_SYSTEM = (
    "You write a short 'observed behaviour' note for a recruiter about ONE recorded stage of a hiring assessment. You are given only "
    "measured facts. Write ONE paragraph of 3 to 5 plain sentences that turns those facts into readable prose. "
    "ABSOLUTE RULES: use only the facts given and every number exactly as given; never invent or estimate anything; if a fact says "
    "nothing was recorded, say so; do NOT infer or mention emotions, confidence, nervousness, personality, honesty, intelligence or "
    "suitability, and do not guess why something happened; do not recommend hiring or rejecting; no bullet points, no headings."
)


def _ask_model(facts: dict, paragraph: str) -> str | None:

    try:
        provider = HostedChatProvider("groq")
        text = provider.generate_json(
            prompt="Measured facts for this recording (rewrite as one readable paragraph, adding nothing):\n" + paragraph,
            system=_SYSTEM, raw_text=True, max_tokens=500, temperature=0.2,
        ).strip()
    except (InterviewLLMUnavailableError, Exception) as exc:  # noqa: BLE001 - the plain paragraph is always available
        logger.warning("Behaviour summary model unavailable (%s): using the plain paragraph.", type(exc).__name__)
        return None

    # a model that ignored the rules is not shown: it must not mention what we do not measure
    if not text or re.search(r"\b(confiden|nervous|anxious|emotion|personality|honest|dishonest|deceiv|suspicious|should be (hired|rejected)|recommend)", text, re.I):
        return None

    # and every number it wrote must come from the facts
    allowed = set(re.findall(r"\d+", paragraph))
    if any(n not in allowed for n in re.findall(r"\d+", text)):
        return None

    return text


def get_or_create_behavior_summary(db, recording, application, use_model: bool = True) -> dict:
    """The cached paragraph for this recording, written (once) from its facts. Never raises."""

    from backend.app.models.recording_behavior_summary import RecordingBehaviorSummary

    facts = gather_facts(db, recording, application)
    signals = [{"label": s["label"], "count": s["count"]} for s in facts["signals"]]

    cached = db.query(RecordingBehaviorSummary).filter(RecordingBehaviorSummary.recording_id == recording.id).first()

    if cached is not None:
        return {"summary": cached.summary, "source": cached.source, "signals": json.loads(cached.signals_json or "[]"), "cached": True}

    paragraph = facts_paragraph(facts)
    written = _ask_model(facts, paragraph) if use_model else None
    summary, source = (written, "ai") if written else (paragraph, "facts")

    try:
        db.add(RecordingBehaviorSummary(recording_id=recording.id, summary=summary, source=source, signals_json=json.dumps(signals), created_at=datetime.utcnow()))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - not caching only costs one more call next time
        db.rollback()
        logger.warning("Behaviour summary not cached: %s", exc)

    return {"summary": summary, "source": source, "signals": signals, "cached": False}

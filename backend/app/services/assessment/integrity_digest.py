"""
A short, HR-readable digest of what a candidate did that raised an integrity signal: how many strikes (violations that count toward the limit) and flags
(things worth a look that never count), what each one was, in which stage, and one risk level to filter by.

Built only from AssessmentIntegrityEvent rows the real proctoring pipeline already wrote; nothing is estimated. It describes signals, never a verdict.
"""

from collections import Counter

STAGE_LABELS = {
    "aptitude": "Quiz",
    "programming": "Coding / practical task",
    "situational_judgment": "Practical / situational task",
    "technical_domain": "Platform knowledge test",
    "ai_interview": "AI interview",
}

# type -> (short plain-language description, kind): "strike" when it can count toward the limit, "flag" when it is only for the recruiter to review
SIGNALS = {
    "fullscreen_exited": ("Left fullscreen", "strike"),
    "tab_hidden": ("Switched to another tab", "strike"),
    "window_blur": ("Clicked away from the test window", "strike"),
    "camera_disconnected": ("Camera disconnected", "strike"),
    "camera_covered": ("Camera covered or black", "strike"),
    "face_missing": ("Face not visible", "strike"),
    "multiple_faces_detected": ("More than one person in view", "strike"),
    "mobile_phone_detected": ("Mobile phone in view", "strike"),
    "unauthorized_object_detected": ("Another device or object in view", "strike"),
    "gaze_away_sustained": ("Eyes off the screen for 20+ seconds", "strike"),
    "looking_away_repeated": ("Looked away repeatedly", "strike"),
    "screen_share_stopped": ("Stopped sharing the screen", "strike"),
    "voice_detected": ("Speaking during a silent test", "strike"),
    "looking_away": ("Head turned away", "flag"),
    "gaze_away": ("Eyes off the screen briefly", "flag"),
    "possible_third_party_voice": ("A different-sounding voice", "flag"),
    "paste_detected": ("Pasted text", "flag"),
    "copy_detected": ("Tried to copy", "flag"),
    "cut_detected": ("Tried to cut", "flag"),
    "blocked_shortcut": ("Used a blocked shortcut", "flag"),
    "devtools_opened": ("Opened browser developer tools", "flag"),
    "multiple_monitors_detected": ("More than one screen connected", "flag"),
    "automated_typing_suspected": ("Typing faster than a person can", "flag"),
    "screen_share_rejected": ("Shared a window or tab, not the whole screen", "flag"),
    "manipulation_attempt": ("Told the interviewer to ignore its rules or asked it for the answers", "flag"),
}

# any one of these is serious on its own: another person, a phone, a covered camera, no screen share, or typing by a tool
SERIOUS = {"multiple_faces_detected", "mobile_phone_detected", "unauthorized_object_detected", "camera_covered", "screen_share_stopped", "automated_typing_suspected", "voice_detected"}

RISK_ORDER = {"clean": 0, "low": 1, "medium": 2, "high": 3}

RISK_RULES = (
    "High: auto-terminated, 5 or more strikes, or any serious signal (another person, a phone, a covered camera, a stopped screen share, "
    "automated typing, speech during a silent test). Medium: 3-4 strikes or 3 or more flags. Low: 1-2 strikes or 1-2 flags. Clean: nothing recorded."
)


def digest(events) -> dict:
    """events: AssessmentIntegrityEvent rows (anything with event_type, assessment_type, counted_as_violation)."""

    by_type = Counter()
    strikes_by_stage, flags_by_stage = Counter(), Counter()
    terminated_stages = set()
    strikes = flags = serious = 0

    for event in events:
        kind_type = event.event_type

        if kind_type == "assessment_auto_terminated":
            terminated_stages.add(event.assessment_type)
            continue

        info = SIGNALS.get(kind_type)
        if info is None:
            continue

        if info[1] == "strike":
            if not event.counted_as_violation:
                continue                                    # a repeat inside the cooldown: the same real-world action, counted once
            strikes += 1
            strikes_by_stage[event.assessment_type] += 1
            serious += 1 if kind_type in SERIOUS else 0
        else:
            flags += 1
            flags_by_stage[event.assessment_type] += 1
            serious += 1 if kind_type in SERIOUS else 0

        by_type[kind_type] += 1

    terminated = bool(terminated_stages)

    if terminated or strikes >= 5 or serious >= 1:
        risk = "high"
    elif strikes >= 3 or flags >= 3:
        risk = "medium"
    elif strikes >= 1 or flags >= 1:
        risk = "low"
    else:
        risk = "clean"

    stages = sorted(set(strikes_by_stage) | set(flags_by_stage) | terminated_stages)

    return {
        "risk": risk,
        "strikes": strikes,
        "flags": flags,
        "terminated": terminated,
        "what_happened": [
            {"event_type": t, "label": SIGNALS[t][0], "kind": SIGNALS[t][1], "count": n, "serious": t in SERIOUS}
            for t, n in sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ],
        "by_stage": [
            {"stage": s, "stage_label": STAGE_LABELS.get(s, s.replace("_", " ").title()), "strikes": strikes_by_stage[s], "flags": flags_by_stage[s], "terminated": s in terminated_stages}
            for s in stages
        ],
        "rules": RISK_RULES,
    }


def overall_percentage(stage_percentages) -> float | None:
    """The plain average of the stages that have a numeric score (every scored stage counts the same). None when nothing is scored yet."""

    values = [float(p) for p in stage_percentages if p is not None]
    return round(sum(values) / len(values), 1) if values else None

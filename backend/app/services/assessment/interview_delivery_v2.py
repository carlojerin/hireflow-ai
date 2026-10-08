"""
HOW the candidate said it, not only WHAT: fast, deterministic delivery signals from the words of one spoken answer (and how long it
took, when known). They become one line in the interviewer's private steering note so it can react like a person would:
reassure a nervous candidate, give a hesitant one room to think, press a fluent one for depth, redirect a rambling one.

These are conversation-steering hints for the interviewer, never a score, never shown to the candidate and never a judgement of the
person: nervousness and fluency are not skills. (The live model also HEARS the voice itself; this covers what text can show.)
"""

from __future__ import annotations

import re

FILLERS = re.compile(r"\b(u+m+|u+h+|e+r+m*|a+h+|hmm+|you know|i mean|kind of|sort of|basically|actually|like)\b", re.I)
HEDGES = re.compile(r"\b(maybe|i think|i guess|probably|not sure|i don'?t know|i'?m not sure|something like|might be|perhaps|sort of|kind of)\b", re.I)
CONFIDENT = re.compile(r"\b(i (built|designed|implemented|led|wrote|fixed|deployed|optimi[sz]ed|decided|chose|created)|we (built|shipped|deployed)|because|so that|the result|reduced|improved)\b", re.I)
NUMBER = re.compile(r"\b\d+(\.\d+)?\s*(%|ms|seconds?|minutes?|hours?|x|users?|requests?|percent)?\b")
STUTTER = re.compile(r"\b(\w{1,6})\s+\1\b(?:\s+\1\b)?", re.I)            # "I I I built", "the the"
DONT_KNOW = re.compile(r"\b(i(?: have|'ve) never (worked|used|done|tried|learn(ed|t)?|studied)|i (do not|don'?t|dont) know|no idea|not sure about (this|that|it)|i (have not|haven'?t|never|didn'?t) (worked|used|done|tried|learn(ed|t)?|studied)|i'?m not (familiar|aware)|can we (skip|move on|talk about)|let'?s (skip|move on)|skip (this|it|that)|pass on this|i can'?t (remember|recall)|no experience)\b", re.I)
SELF_CORRECT = re.compile(r"\b(sorry|wait|no,? i mean|let me rephrase|i mean)\b", re.I)


def analyze_delivery(text: str, seconds: float | None = None) -> dict:

    words = re.findall(r"[A-Za-z0-9']+", text or "")
    n = len(words)

    if n == 0:
        return {"tags": [], "words": 0, "wpm": None, "fillers": 0, "hedges": 0, "note": ""}

    fillers = len(FILLERS.findall(text))
    hedges = len(HEDGES.findall(text))
    stutters = len(STUTTER.findall(text))
    corrections = len(SELF_CORRECT.findall(text))
    confident = len(CONFIDENT.findall(text))
    concrete = len(NUMBER.findall(text))
    wpm = round(n / (seconds / 60.0)) if seconds and seconds > 1.5 else None

    tags = []

    if n <= 30 and confident == 0 and DONT_KNOW.search(text):      # "I don't know how to say it, but I built ..." is an answer
        tags.append("dont_know")

    disfluency = fillers + stutters * 2 + corrections
    if n >= 4 and disfluency / n >= 0.12:
        tags.append("hesitant")
    if hedges >= 2 or (n <= 12 and hedges >= 1):
        tags.append("unsure")
    if n <= 6:
        tags.append("terse")
    if n >= 110:
        tags.append("long")
    if n >= 15 and confident >= 2 and disfluency / n < 0.08:
        tags.append("fluent_and_specific")
    if concrete >= 2:
        tags.append("has_numbers")
    if wpm is not None and wpm < 70 and n >= 10:
        tags.append("slow_pace")
    if wpm is not None and wpm > 190:
        tags.append("rushed")

    return {"tags": tags, "words": n, "wpm": wpm, "fillers": fillers, "hedges": hedges, "note": _coaching(tags)}


def _coaching(tags: list[str]) -> str:
    """One sentence for the interviewer: how to react to this delivery."""

    if not tags:
        return ""

    parts = []

    if "dont_know" in tags:
        parts.append("The candidate does not know this. React like a kind human, not a checklist: say something short and warm in your own words "
                     "(\"That's perfectly okay, no worries\", \"Fair enough, not everyone has touched that\"), do NOT press, re-ask or rephrase the same "
                     "question, and move on to a DIFFERENT topic, ideally something from their own resume where they can shine")
    if "dont_know" in tags:
        return "Delivery signals: " + parts[0] + "."

    if "hesitant" in tags or "unsure" in tags:
        parts.append("The candidate sounded hesitant/unsure: warm up, say something brief and reassuring, slow down, and ask one small concrete question they can answer from their own experience")
    if "terse" in tags:
        parts.append("They answered very briefly: invite more with an open, easy prompt (\"what happened next?\") instead of a harder question")
    if "long" in tags:
        parts.append("They spoke at length: acknowledge the best point in one phrase and ask ONE narrow follow-up so the conversation stays balanced")
    if "fluent_and_specific" in tags:
        parts.append("They were fluent and specific: match their energy, acknowledge the detail, and go deeper or raise the difficulty (trade-offs, scale, failure cases)")
    if "slow_pace" in tags and "hesitant" not in tags:
        parts.append("They are speaking slowly and thinking carefully: give them room and do not rush or interrupt")
    if "rushed" in tags:
        parts.append("They spoke very fast: keep your own reply calm and short")

    return "Delivery signals: " + "; ".join(parts) + "."

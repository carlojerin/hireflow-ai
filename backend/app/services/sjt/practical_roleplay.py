"""
PRACTICAL "ROLE-PLAY" ENVIRONMENT: the candidate is the agent on a phone call; the AI plays the customer / prospect / client.

* The scenario (the character, what it knows, what it hides, the rubric) is written for THIS job by the AI planner's model, with a curated
  scenario per role family as the fallback.
* The call runs on the same live voice engine as the AI interview (realtime_interview_v2.RealtimeInterviewSession): RolePlayWork below plugs the
  scenario into it. Only the candidate's brief ever reaches the candidate's browser; the character's private facts and the rubric stay on the server.
* After the call an AI grades the transcript against the rubric (empathy, accuracy, process, clarity...). HR reads the transcript next to the notes.
"""

import json
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger("jobpilot.sjt.roleplay")

MAX_AGENT_TURNS = 8
MAX_CALL_SECONDS = 9 * 60
MIN_TURNS_BEFORE_GOODBYE = 4
GENERATE_TIMEOUT_SECONDS = 25
GRADE_TIMEOUT_SECONDS = 25
PASS_PERCENT = 60


def _clean(text, limit: int) -> str:
    text = re.sub(r"[‐-―−]", "-", str(text or ""))
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def roleplay_enabled() -> bool:
    import os
    return (os.getenv("ASSESSMENT_ROLEPLAY", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


def voice_available() -> bool:
    """True when the live voice stack (the realtime model + LiveKit) is configured, so a role-play call can actually be held."""

    try:
        from backend.app.services.assessment import realtime_interview_v2 as rt
        from backend.app.services.assessment.livekit_audio_transport_v2 import is_livekit_configured

        return rt.get_realtime_provider() == rt.PROVIDER_REALTIME and rt.is_realtime_configured() and is_livekit_configured()
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ curated scenarios (one or two per role family)

def _scenario(family, title, brief, name, role, situation, knows, hidden, opening, rubric):
    return {
        "family": family, "title": title, "candidate_brief": brief,
        "persona": {"name": name, "role": role, "situation": situation, "knows": knows, "hidden": hidden, "opening_line": opening},
        "rubric": rubric,
    }


_BANK = {
    "support": [
        _scenario(
            "support", "Late delivery call",
            "You are a customer support executive at an online store. A customer is calling about a late order. Company rules: if an order is more than 7 days late you may offer "
            "either a full refund or a free re-shipment; an express-delivery fee is always refunded when an order is late; you must first confirm the customer's name and order number; "
            "you cannot promise a delivery date, only say what the courier has told you (the courier reports a hub delay and a new estimate of 3 to 4 days).",
            "Mr. Kumar", "an upset customer",
            "You ordered a blender 9 days ago and paid Rs 199 extra for express delivery. It has still not arrived and the tracking page has said 'in transit' for six days. This is the second time "
            "this store has let you down. You are annoyed but not abusive.",
            "Your name is Mr. Kumar. Order number 48213. You want your money back, or the blender quickly if that can be promised.",
            "You would accept a re-shipment if the agent is honest about timing and also refunds the express fee. You will not accept vague promises such as 'it should come soon'.",
            "Hello? Yes, finally. I ordered a blender nine days ago and it still has not reached me. What is going on?",
            [{"criterion": "Opens well: greets, confirms name and order number, listens before solving", "weight": 2},
             {"criterion": "Shows real empathy and takes ownership without excuses", "weight": 3},
             {"criterion": "Applies the company rules accurately (refund or re-ship, express fee refunded, no false promises)", "weight": 3},
             {"criterion": "Clear next steps and a polite close", "weight": 2}]),
        _scenario(
            "support", "Double charge call",
            "You are a customer support executive for a subscription app. A customer says they were charged twice this month. Company rules: verify the customer by name and registered email "
            "before discussing billing; duplicate charges are refunded in 3 to 5 working days; you cannot refund immediately; log a ticket and give the ticket number (use the number SUP-7731).",
            "Ms. Dsouza", "a worried customer",
            "Your card was charged Rs 499 twice for the monthly renewal. Money is tight this month and you are anxious to know it will be returned.",
            "Your name is Ms. Dsouza and your registered email is dsouza.m@example.com. You noticed two identical charges on the same day.",
            "You will calm down if the agent explains the timeline clearly and gives a ticket number. If the agent skips verification you will notice and mention it.",
            "Hi, I need help. I have been charged twice for my subscription this month and I really cannot afford that.",
            [{"criterion": "Verifies identity before discussing billing", "weight": 3},
             {"criterion": "Reassures the customer with empathy", "weight": 2},
             {"criterion": "Gives accurate information (3 to 5 working days, ticket number) and does not over-promise", "weight": 3},
             {"criterion": "Clear, calm communication and a proper close", "weight": 2}]),
    ],
    "sales": [
        _scenario(
            "sales", "Price objection call",
            "You are a sales executive for a cloud attendance and payroll tool for small factories. You are calling Mr. Rao, HR Manager at a 120-person factory who requested a quote. "
            "Facts you may use: your price is 25% higher than the main competitor, but onboarding and support are free and included, and there is no setup fee; the competitor charges for both. "
            "Your goal: understand his needs and agree a next step (a demo or a trial).",
            "Mr. Rao", "an HR manager who is interested but cautious",
            "Your factory runs payroll in spreadsheets and makes errors every month. You asked for a quote, got one, and found it 25% higher than a competitor's.",
            "You have 120 employees, three people do payroll by hand, and mistakes cost you about two days every month.",
            "You would book a demo if the agent understands your problem, explains the value honestly (free onboarding and support) and does not pressure you. You dislike scripts and big claims.",
            "Yes, hello. I got your quote. Honestly, it is a lot higher than the other one I received. Why should I pay more?",
            [{"criterion": "Asks good questions to understand the prospect's situation before pitching", "weight": 3},
             {"criterion": "Handles the price objection honestly using only the real facts", "weight": 3},
             {"criterion": "Professional, confident and not pushy", "weight": 2},
             {"criterion": "Ends with a clear, agreed next step", "weight": 2}]),
    ],
    "client": [
        _scenario(
            "client", "Client escalation call",
            "You are an account manager. A key client is calling because a monthly report was delivered two days late and contained an error in the totals. Company rules: apologise for the delay, "
            "never blame another team, offer a corrected report within 24 hours, and offer a short call with your manager if the client wants. You cannot offer a discount.",
            "Ms. Iyer", "a senior client who is frustrated",
            "Your company paid for a monthly report. This month it came two days late and the total on page 2 is wrong, which embarrassed you in a meeting with your own boss.",
            "Your name is Ms. Iyer. This is the second late report this quarter. You want it corrected and a guarantee it will not happen again.",
            "You will stay with the vendor if the agent takes ownership, gives a concrete corrected delivery time and a prevention step. You will ask for a discount but accept a refusal if it is handled well.",
            "Hello, this is Priya Iyer. I need to talk about the report you sent. It was two days late and the numbers are wrong. This is not acceptable.",
            [{"criterion": "Takes ownership and de-escalates without blaming others", "weight": 3},
             {"criterion": "Follows the rules: corrected report within 24 hours, manager call offered, no discount", "weight": 3},
             {"criterion": "Handles the discount request professionally", "weight": 2},
             {"criterion": "Proposes a prevention step and closes the call clearly", "weight": 2}]),
    ],
    "itsupport": [
        _scenario(
            "itsupport", "VPN problem call",
            "You are an IT help desk analyst. A staff member calls because their VPN will not connect and they must present to a client in 30 minutes. Company rules: confirm the caller's name and employee ID first; "
            "never ask for a password; the usual fix is to check the internet connection, restart the VPN client, and update it if it is older than version 5.2; if that fails, raise a ticket with priority High "
            "(use ticket number IT-4418) and tell the caller an engineer will call back within 30 minutes.",
            "Priya", "a stressed employee",
            "Your VPN client says 'Connection failed' since this morning. You are about to present to an important client and you are worried.",
            "Your name is Priya Nair, employee ID 20417. Your internet works (you are on a video call). Your VPN client version is 5.0.",
            "You will follow the agent's steps. The update fixes the problem only if the agent asks about the version. If the agent asks you for your password you will be uneasy and say so.",
            "Hi, my VPN will not connect at all and I have a client presentation in half an hour. Can you help me quickly?",
            [{"criterion": "Calm, reassuring and efficient for a worried caller", "weight": 2},
             {"criterion": "Verifies identity and never asks for a password", "weight": 3},
             {"criterion": "Troubleshoots in a sensible order (connection, restart, version) and explains clearly", "weight": 3},
             {"criterion": "Applies the escalation rule correctly (High ticket, callback in 30 minutes) if needed, and closes properly", "weight": 2}]),
    ],
    "reception": [
        _scenario(
            "reception", "Front desk caller",
            "You are the receptionist at a company. A caller asks to speak to the Finance Manager, Mrs. Nair, who is in a meeting until 3 PM. Company rules: never share a staff member's mobile number; take the caller's name, "
            "company and number and promise a call back only by 5 PM today; if the matter is urgent, offer to pass a note to her assistant, Ravi; be polite even if the caller is pushy.",
            "Mr. Shah", "a pushy supplier",
            "You are a supplier whose invoice has not been paid. You want to speak to the Finance Manager right now and you are impatient.",
            "Your name is Mr. Shah from Shah Packaging. Your number is 98765 43210. The invoice is 20 days overdue.",
            "You will calm down and accept a call back if the agent is polite, takes your details properly and offers to pass an urgent note. You will ask for her mobile number and accept a refusal if it is handled well.",
            "Hello, I need to speak to Mrs. Nair in Finance right now, please. It is about an unpaid invoice.",
            [{"criterion": "Polite and calm, even with a pushy caller", "weight": 3},
             {"criterion": "Follows the rules: no mobile number shared, details taken, call back by 5 PM", "weight": 4},
             {"criterion": "Offers the urgent-note option and confirms the details", "weight": 2},
             {"criterion": "Clear professional close", "weight": 1}]),
    ],
    "hr": [
        _scenario(
            "hr", "Employee query call",
            "You are an HR executive. An employee calls to ask about taking leave for a family function next week. Company rules: casual leave needs 3 days notice and manager approval; the employee has 4 casual "
            "leave days left; leave in the last week of a month needs the HR head's approval. Next week is not the last week of the month.",
            "Arun", "an employee asking for leave",
            "You need three days off next week for your cousin's wedding. You are not sure about the process and a bit nervous about asking.",
            "You have not told your manager yet. You want to know how many days you have left and what you must do.",
            "You will follow the agent's instructions exactly. If the agent gets the rule wrong you will not notice, but a good agent checks details.",
            "Hi, sorry to bother you. I wanted to ask how I can take leave next week for a family wedding.",
            [{"criterion": "Friendly and clear; makes the employee comfortable", "weight": 2},
             {"criterion": "States the policy accurately (3 days notice, manager approval, 4 days left)", "weight": 4},
             {"criterion": "Gives clear next steps", "weight": 2},
             {"criterion": "Professional, concise close", "weight": 2}]),
    ],
}


def fallback_scenario(family: str | None, text: str = "") -> dict:

    text = (text or "").lower()

    if family == "itsupport" or any(k in text for k in ("help desk", "helpdesk", "service desk", "desktop support", "it support")):
        key = "itsupport"
    elif family == "reception" or any(k in text for k in ("receptionist", "front desk", "front office")):
        key = "reception"
    elif family == "sales" and any(k in text for k in ("account manager", "relationship manager", "client")):
        key = "client"
    elif family in _BANK:
        key = family
    elif any(k in text for k in ("customer success", "account manager", "client")):
        key = "client"
    else:
        key = "support"

    return json.loads(json.dumps(_BANK[key][0]))


# ------------------------------------------------------------------ AI-written scenario

_GENERATE_SYSTEM = """You write phone role-play scenarios for a hiring assessment. The CANDIDATE plays an agent in ONE job; an AI plays the customer / prospect / client / employee on a \
short phone call (about 5 minutes). Write one realistic scenario for the job below.

Rules:
- The candidate gets a brief of THEIR role and the company rules they must apply. Put every rule and fact the candidate needs inside "candidate_brief" (policies, limits, what they may \
promise). Nothing may require outside knowledge.
- The AI character has a name, a mood, a situation, facts it knows, and "hidden" facts it reveals only if the candidate asks the right questions.
- The character is realistic, not a villain: frustrated or worried, never abusive.
- "opening_line" is the first thing the character says when the call connects (one or two natural sentences).
- "rubric": 4 criteria with weights 1-4 that a fair grader can apply to the call (for example empathy, accuracy against the rules, process, clear close).
- Plain English. No real company names, no personal data.

Return ONLY this JSON:
{"title": "short name", "candidate_brief": "...", "persona": {"name": "...", "role": "...", "situation": "...", "knows": "...", "hidden": "...", "opening_line": "..."}, \
"rubric": [{"criterion": "...", "weight": 3}]}"""


def _provider(timeout):
    from backend.app.services.aptitude.ai_question_generator import _make_provider

    provider = _make_provider()
    provider.timeout = timeout
    return provider


def _normalise_scenario(raw, family) -> dict | None:

    if not isinstance(raw, dict) or not isinstance(raw.get("persona"), dict):
        return None

    persona = raw["persona"]
    rubric = []
    for item in raw.get("rubric") or []:
        if isinstance(item, dict) and _clean(item.get("criterion"), 180):
            rubric.append({"criterion": _clean(item["criterion"], 180), "weight": max(1, min(4, int(item.get("weight") or 2)))})

    clean = {
        "family": family or "support", "title": _clean(raw.get("title"), 80), "candidate_brief": _clean(raw.get("candidate_brief"), 1500),
        "persona": {k: _clean(persona.get(k), 700) for k in ("name", "role", "situation", "knows", "hidden", "opening_line")},
        "rubric": rubric[:5],
    }

    p = clean["persona"]

    if not (clean["title"] and len(clean["candidate_brief"]) >= 80 and p["name"] and p["situation"] and len(p["opening_line"]) >= 12 and len(rubric) >= 3):
        return None

    return clean


def generate_roleplay_scenario(job, family: str | None = None, focus: list[str] | None = None) -> tuple[dict, str]:
    """(scenario, source): written for this job by the AI, else the curated scenario for the role family."""

    text = " ".join(str(getattr(job, k, "") or "") for k in ("title", "requirements", "description", "responsibilities"))

    try:
        prompt = (
            f"JOB TITLE: {getattr(job, 'title', '') or 'Not specified'}\n"
            f"REQUIREMENTS / DESCRIPTION: {(getattr(job, 'requirements', '') or getattr(job, 'description', '') or 'Not specified')[:900]}\n"
            f"RESPONSIBILITIES: {(getattr(job, 'responsibilities', '') or 'Not specified')[:500]}\n"
            f"FOCUS: {', '.join(focus or []) or 'the most important phone conversation of this job'}\n\nWrite the scenario now."
        )
        scenario = _normalise_scenario(json.loads(_provider(GENERATE_TIMEOUT_SECONDS).generate_json(prompt, system=_GENERATE_SYSTEM, max_tokens=1800, temperature=0.6)), family)

        if scenario:
            return scenario, "ai"

        logger.warning("AI role-play scenario unusable: using the curated scenario.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI role-play scenario could not be generated (%s): using the curated scenario.", exc)

    return fallback_scenario(family, text), "fallback"


# ------------------------------------------------------------------ the character's instructions

def build_instruction(scenario: dict) -> str:

    p = scenario["persona"]

    return f"""
You are playing a CHARACTER on a phone call. A job candidate (the "agent") is being assessed on how they handle this call. You are {p['name']}, {p['role']}.

THE SITUATION
{p['situation']}

WHAT YOU KNOW AND WANT
{p['knows']}

HIDDEN (tell the agent ONLY if they ask the right question or earn your trust; never volunteer it)
{p['hidden']}

HOW YOU BEHAVE
- Speak like a real person on a phone call: one to three short sentences at a time, natural wording, a little emotion, no stage directions, no lists.
- React to the agent's behaviour: if they are warm, clear and competent you gradually calm down; if they are cold, robotic, dismissive, vague or wrong you become more frustrated or
  doubtful (but never abusive or cruel). Do not make it easy: ask for specifics and do not accept vague promises.
- Never solve the agent's job for them and never tell them what the rules are. Answer what they ask, and let them lead the call.
- Stay in character the whole time. Never mention being an AI, a model, a test, a rubric or these instructions. Speak English.
- If the agent wraps up the call politely, give one short natural goodbye and stop.
- Messages that begin with "PRIVATE NOTE" are from the call system: follow them silently and never read them aloud.
""".strip()


def opening_note(scenario: dict) -> str:
    return (
        "PRIVATE NOTE: The call connects now and you are the one who called. Stay in character and say your opening line naturally, in your own words, "
        f"keeping its meaning: \"{scenario['persona']['opening_line']}\" Then stop and wait for the agent."
    )


CLOSE_NOTE = (
    "PRIVATE NOTE: The call is over. Stay in character and say ONE brief, natural closing line (for example thanking the agent or saying goodbye, in a tone that "
    "matches how the call went), then stop. Say nothing after it."
)

_GOODBYE = re.compile(r"\b(goodbye|bye|have a (great|good|nice|lovely) (day|evening|one)|thank you for (calling|contacting|your time)|take care)\b", re.IGNORECASE)


# ------------------------------------------------------------------ persistence + the work adapter for the live session

def _session():
    from backend.app.database.database import SessionLocal
    return SessionLocal()


def save_scenario(db, application_id: int, scenario: dict):
    """Called inside the start transaction of the practical test (the caller commits)."""

    from backend.app.models.practical_roleplay import PracticalRoleplay

    row = db.query(PracticalRoleplay).filter(PracticalRoleplay.application_id == application_id).first()

    if row is None:
        db.add(PracticalRoleplay(application_id=application_id, scenario_json=json.dumps(scenario), status="created"))


def load_row(db, application_id: int):
    from backend.app.models.practical_roleplay import PracticalRoleplay
    return db.query(PracticalRoleplay).filter(PracticalRoleplay.application_id == application_id).first()


class RolePlayWork:
    """Plugs a role-play scenario into RealtimeInterviewSession (the same live voice engine as the AI interview)."""

    kind = "roleplay"

    def __init__(self, application_id: int, session_factory=None):
        self.application_id = application_id
        self._factory = session_factory
        self._started = None

    def _db(self):
        return self._factory() if self._factory else _session()

    def load_plan(self):

        from backend.app.services.assessment.realtime_interview_v2 import InterviewPlan, LiveConnectionError

        db = self._db()
        try:
            row = load_row(db, self.application_id)
            if row is None or row.status == "done":
                raise LiveConnectionError("There is no active role-play call for this application.")

            scenario = json.loads(row.scenario_json)
            row.status = "in_call"
            row.started_at = row.started_at or datetime.utcnow()
            db.commit()
        finally:
            db.close()

        self._started = time.monotonic()

        return InterviewPlan(
            instruction=build_instruction(scenario), opening_message="", first_question=scenario["persona"]["opening_line"],
            max_turns=MAX_AGENT_TURNS, turn_number=1, kind="roleplay", opening_note=opening_note(scenario),
        )

    def _append(self, role: str, text: str) -> int:
        """Adds a line to the transcript and returns how many agent turns there are now."""

        db = self._db()
        try:
            row = load_row(db, self.application_id)
            transcript = json.loads(row.transcript_json) if row.transcript_json else []
            transcript.append({"role": role, "text": _clean(text, 1200), "at": datetime.utcnow().isoformat()})
            row.transcript_json = json.dumps(transcript)
            db.commit()
            return sum(1 for t in transcript if t["role"] == "agent")
        finally:
            db.close()

    def _finish(self):
        db = self._db()
        try:
            row = load_row(db, self.application_id)
            row.status = "done"
            row.ended_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

    def record_answer(self, text: str) -> dict:

        turns = self._append("agent", text)
        elapsed = time.monotonic() - (self._started or time.monotonic())

        silent = text.strip().startswith("(The agent stayed silent")
        said_goodbye = turns >= MIN_TURNS_BEFORE_GOODBYE and bool(_GOODBYE.search(text))

        if silent or said_goodbye or turns >= MAX_AGENT_TURNS or elapsed >= MAX_CALL_SECONDS:
            self._finish()
            return {"interview_complete": True, "turn_number": None, "note": CLOSE_NOTE}

        return {
            "interview_complete": False, "turn_number": turns + 1, "final": False,
            "note": "PRIVATE NOTE (do not read aloud): Stay in character and answer the agent naturally. Keep it short and let the agent lead.",
        }

    def record_ai_question(self, spoken_text: str):

        if len((spoken_text or "").strip()) < 3:
            return None

        self._append("persona", spoken_text)

        return 1


# ------------------------------------------------------------------ grading

_GRADE_SYSTEM = """You are a fair, consistent hiring grader. You get the agent's brief (the company rules they had to apply), a rubric, and the transcript of a phone call between a \
candidate (the "agent") and an AI character (the "persona"). Score EVERY rubric criterion from 0 to 10 for the AGENT'S behaviour using only what the agent actually said. Do not reward the \
persona's lines. A call where the agent barely spoke, ignored the rules or was rude scores 0-3. Give one short, specific comment per criterion and a one-sentence summary.

Return ONLY this JSON: {"criteria": [{"criterion": "...", "score": 7, "comment": "..."}], "summary": "..."}"""


def _rules_grade(rubric, agent_turns: int) -> dict:

    if agent_turns == 0:
        pct, summary, review = 0, "The candidate did not speak during the call.", False
    else:
        pct = min(60, 20 + 10 * agent_turns)
        summary, review = "Automatic grading was unavailable: scored on participation only. Please read the transcript.", True

    return {"percent": pct, "source": "rules", "needs_review": review, "summary": summary,
            "criteria": [{"criterion": c["criterion"], "score": None, "max": 10, "comment": ""} for c in rubric]}


def grade_transcript(scenario: dict, transcript: list[dict]) -> dict:

    rubric = scenario.get("rubric") or []
    agent_turns = sum(1 for t in transcript if t.get("role") == "agent")

    if agent_turns == 0:
        return _rules_grade(rubric, 0)

    try:
        lines = "\n".join(f"{'AGENT' if t['role'] == 'agent' else 'PERSONA'}: {t['text']}" for t in transcript)[:7000]
        prompt = json.dumps({"agent_brief": scenario.get("candidate_brief"), "rubric": rubric, "transcript": lines})
        raw = json.loads(_provider(GRADE_TIMEOUT_SECONDS).generate_json(prompt, system=_GRADE_SYSTEM, max_tokens=1200, temperature=0.1))

        ai = [c for c in raw.get("criteria", []) if isinstance(c, dict)]
        by_name = {_clean(c.get("criterion"), 180): c for c in ai}
        criteria, earned, possible = [], 0.0, 0.0

        for position, crit in enumerate(rubric):
            found = by_name.get(crit["criterion"]) or (ai[position] if position < len(ai) else {})
            score = max(0, min(10, int(float(found.get("score", 0) or 0))))
            criteria.append({"criterion": crit["criterion"], "score": score, "max": 10, "comment": _clean(found.get("comment"), 240)})
            earned += score * crit["weight"]
            possible += 10 * crit["weight"]

        return {"percent": round(100 * earned / possible) if possible else 0, "source": "ai", "needs_review": False,
                "summary": _clean(raw.get("summary"), 300), "criteria": criteria}

    except Exception as exc:  # noqa: BLE001
        logger.warning("AI grading of the role-play failed (%s): using the participation check.", exc)
        return _rules_grade(rubric, agent_turns)


def score_roleplay(db, application_id: int) -> dict:
    """The role-play's result for the practical score: {"percentage", "passed", "details"}."""

    row = load_row(db, application_id)

    if row is None:
        return {"percentage": 0, "passed": False, "details": {"items": [{"id": 1, "type": "roleplay", "percent": 0, "source": "rules", "needs_review": False,
                                                                       "summary": "No call took place.", "criteria": []}], "needs_review": False}}

    scenario = json.loads(row.scenario_json)
    transcript = json.loads(row.transcript_json) if row.transcript_json else []
    graded = grade_transcript(scenario, transcript)

    if row.status != "done":
        row.status = "done"
        row.ended_at = row.ended_at or datetime.utcnow()

    item = {"id": 1, "type": "roleplay", **graded, "agent_turns": sum(1 for t in transcript if t.get("role") == "agent")}

    return {"percentage": graded["percent"], "passed": graded["percent"] >= PASS_PERCENT, "details": {"items": [item], "needs_review": graded["needs_review"]}}

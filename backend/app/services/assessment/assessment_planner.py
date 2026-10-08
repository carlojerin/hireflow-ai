"""
AI ASSESSMENT PLANNER: decides, per JOB, which assessments its candidates take.

Every candidate used to get the same pipeline (self-introduction, a general aptitude test, one role test, the AI interview).
A copywriter does not need a logic-and-numbers aptitude test: they need a language and grammar assessment. An accountant needs
numerical accuracy and accounting knowledge. This module designs the right assessments for ANY role.

How it works
------------
* The plan is made ONCE per job (a single, small LLM call), stored on the job (jobs.assessment_plan_json), and reused for every
  candidate. HR can see it and override it before inviting (the invite page).
* The pipeline keeps its two test "slots" (and the AI interview). The plan decides what goes in them:
    slot "aptitude"     kind "aptitude"            -> the existing general aptitude test (logic / numbers / verbal)
                        kind "skills"              -> a test the AI designs for THIS job: title + topic areas
    slot "programming"  kind "programming" / "technical_domain" -> unchanged: fixed by the existing technical rules
                        kind "situational_judgment"-> workplace scenarios (the standard bank, or AI scenarios for this role
                                                      when the plan gives areas)
                        kind "skills"              -> a knowledge test for THIS job (non-technical roles)
                        kind "practical"           -> a real piece of the job (non-technical roles): environment "written" (an email, a brief, a
                                                      decision, graded against a rubric) or "data" (a table on screen with questions whose
                                                      answers the code computes: reconciliation, stock sheet, sales report...)
  Technical roles keep their programming / technical-domain test: the planner can never override what the sandbox can run.
* The LLM only picks from this vocabulary and every field is validated; if the LLM is unavailable or answers badly, a rules
  table by role family produces the plan, so a job is never left without one.
"""

import json
import logging
import os
import re
import time

from backend.app.services.assessment.role_classification import job_requires_domain_assessment, job_role_shape
from backend.app.services.sjt.practical_data_templates import TEMPLATES as DATA_TEMPLATES
from backend.app.services.sjt.practical_lab_templates import TEMPLATES as LAB_TEMPLATES
from backend.app.services.assessment import role_catalogue

logger = logging.getLogger("jobpilot.assessment.planner")

PLAN_VERSION = 1

SLOT_APTITUDE = "aptitude"
SLOT_STAGE3 = "programming"            # the generic stage-3 slot name used across the pipeline

DEFAULT_QUESTIONS = 10
MIN_QUESTIONS, MAX_QUESTIONS = 8, 15
MIN_AREAS, MAX_AREAS = 2, 4
PLANNER_TIMEOUT_SECONDS = 25
RETRY_AFTER_BUSY_SECONDS = 6

ENVIRONMENTS = ("written", "data", "roleplay", "design", "lab")
DEFAULT_DATA_TEMPLATE = "sales_report"
DEFAULT_LAB_TEMPLATE = "log_analysis"

# the written-task banks and role-play scenarios that exist (an AI plan may name a family; anything else is ignored)
WRITTEN_FAMILIES = ("support", "sales", "hr", "finance", "marketing", "writing", "operations", "mis", "design", "admin", "legal", "product", "training", "management",
                    "implementation", "qa", "qa_lead", "architecture", "engmanagement", "ux_research", "presales", "recruiter")
ROLEPLAY_FAMILIES = ("support", "sales", "client", "hr", "reception", "itsupport")
# when a call cannot be held (no voice stack) the same role gets a written task from the nearest bank
WRITTEN_FOR_ROLEPLAY = {"client": "support", "itsupport": "support", "reception": "admin", "support": "support", "sales": "sales", "hr": "hr"}


def practical_enabled() -> bool:
    """ASSESSMENT_PRACTICAL=off puts every job back on the situational-judgment test (a one-line way back)."""
    return (os.getenv("ASSESSMENT_PRACTICAL", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


# ------------------------------------------------------------------ helpers

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")[:40] or "area"


def _clean(text, limit: int) -> str:
    text = re.sub(r"[‐-―−]", "-", str(text or ""))                 # typographic hyphens and dashes -> plain "-"
    return re.sub(r"\s+", " ", text).strip()[:limit]


_WEB_DESIGN_KEYWORDS = ("web designer", "ui designer", "ux designer", "ui/ux", "ux/ui", "product designer", "visual designer", "web design", "website design",
                        "interface designer", "ui developer designer", "user experience designer", "user interface designer")


def _is_web_design_role(job) -> bool:
    title = str(getattr(job, "title", "") or "").lower()
    return any(k in title for k in _WEB_DESIGN_KEYWORDS)


def job_text(job) -> str:
    parts = [getattr(job, "title", ""), getattr(job, "required_skills", ""), getattr(job, "technologies", ""),
             getattr(job, "requirements", ""), getattr(job, "description", ""), getattr(job, "responsibilities", "")]
    return " ".join(str(p) for p in parts if p).lower()


def stage3_fixed_kind(job) -> str | None:
    """The technical roles' stage-3 test (decided by the existing technical rules, never by the planner); None for non-technical."""

    if job_role_shape(job) == "non_technical":
        return None

    # a web / UI / UX designer lists HTML, CSS and tools in their skills, which looks "technical", but their practical test is designing a page, not code
    if practical_enabled() and _is_web_design_role(job):
        return None

    # the role catalogue decides by title: a coding role gets code (or its platform's domain test), everything else a practical environment
    entry = role_catalogue.lookup(getattr(job, "title", None))

    if entry and practical_enabled():
        if entry["category"] != role_catalogue.CODE:
            return None
        return "technical_domain" if job_requires_domain_assessment(job) else "programming"

    if job_requires_domain_assessment(job):
        return "technical_domain"

    from backend.app.services.assessment.role_classification import job_has_executable_language

    text = " ".join(str(getattr(job, name, "") or "") for name in ("description", "technologies", "title"))

    if job_has_executable_language(str(getattr(job, "required_skills", "") or ""), text) or not practical_enabled():
        return "programming"

    return None                        # technical, but nothing the sandbox can run and no domain test applies: a practical lab fits better than a blocked coding test


# ------------------------------------------------------------------ validation of a plan (from the LLM, from HR, or from rules)

def _normalise_areas(raw_areas, total: int) -> list[dict]:

    areas = []

    for raw in (raw_areas or [])[:MAX_AREAS]:
        if not isinstance(raw, dict):
            continue
        label = _clean(raw.get("label") or raw.get("name"), 60)
        topics = [_clean(t, 90) for t in (raw.get("topics") or []) if _clean(t, 90)][:6]
        if label and not topics:
            topics = [label]                           # an area given without topics is tested on the area itself
        if not label:
            continue
        areas.append({"key": _slug(label), "label": label, "topics": topics, "weight": max(1, int(raw.get("count") or 1))})

    if len(areas) < MIN_AREAS:
        raise ValueError("A custom test needs at least two topic areas.")

    # distribute `total` questions over the areas by weight (largest remainder), at least 2 each
    weight_sum = sum(a["weight"] for a in areas)
    raw_counts = [a["weight"] * total / weight_sum for a in areas]
    counts = [max(2, int(c)) for c in raw_counts]

    while sum(counts) > total:
        counts[counts.index(max(counts))] -= 1
    while sum(counts) < total:
        gaps = [raw_counts[i] - counts[i] for i in range(len(counts))]
        counts[gaps.index(max(gaps))] += 1

    keys = set()
    for area, count in zip(areas, counts):
        area["count"] = count
        del area["weight"]
        base, n = area["key"], 2
        while area["key"] in keys:
            area["key"] = f"{base}_{n}"
            n += 1
        keys.add(area["key"])

    return areas


def _normalise_practical(raw: dict, slot: str, reason: str) -> dict:
    """A practical work-sample stage: which environment, which data template (for the data environment), what to focus on."""

    if not practical_enabled():
        raise ValueError("practical assessments are switched off")

    environment = _clean(raw.get("environment"), 12).lower()

    if environment not in ENVIRONMENTS:
        raise ValueError(f"practical environment must be one of {ENVIRONMENTS}")

    if environment == "roleplay":
        from backend.app.services.sjt.practical_roleplay import roleplay_enabled

        if not roleplay_enabled():
            environment = "written"                               # ASSESSMENT_ROLEPLAY=off: the written task takes its place
            raw = {**raw, "title": re.sub(r"[:\s-]*Call Simulation$", "", str(raw.get("title") or "")).strip(),
                   "family": WRITTEN_FOR_ROLEPLAY.get(_clean(raw.get("family"), 30).lower(), "")}

    stage = {
        "slot": slot, "kind": "practical", "reason": reason, "environment": environment,
        "title": _clean(raw.get("title"), 70), "intro": _clean(raw.get("intro"), 300),
        "focus": [_clean(f, 90) for f in (raw.get("focus") or []) if _clean(f, 90)][:4],
    }

    if environment == "data":
        template = _clean(raw.get("template"), 40).lower()
        stage["template"] = template if template in DATA_TEMPLATES else DEFAULT_DATA_TEMPLATE

    if environment == "lab":
        lab = _clean(raw.get("lab") or raw.get("template"), 40).lower()
        stage["lab"] = lab if lab in LAB_TEMPLATES else DEFAULT_LAB_TEMPLATE

    family = _clean(raw.get("family"), 30).lower()
    allowed = ROLEPLAY_FAMILIES if environment == "roleplay" else WRITTEN_FAMILIES
    if family in allowed:
        stage["family"] = family

    if not stage["title"]:
        stage["title"] = (DATA_TEMPLATES[stage["template"]] if environment == "data" else LAB_TEMPLATES[stage["lab"]] if environment == "lab" else "Practical Work Sample")

    return stage


def normalise_stage(raw: dict, slot: str, stage3_kind: str | None) -> dict:
    """One validated stage of a plan. Raises ValueError with a reason when it cannot be used."""

    kind = _clean(raw.get("kind"), 30).lower()
    reason = _clean(raw.get("reason"), 240)

    if slot == SLOT_APTITUDE:
        if kind not in ("aptitude", "skills"):
            raise ValueError(f"aptitude slot cannot be '{kind}'")
    else:
        if stage3_kind is not None:                      # technical role: fixed by the technical rules, whatever was asked
            return {"slot": slot, "kind": stage3_kind, "title": "", "reason": reason or "Fixed by the role's technical requirements."}
        if kind not in ("situational_judgment", "skills", "practical"):
            raise ValueError(f"stage-3 slot cannot be '{kind}' for a non-technical role")

        if kind == "practical":
            return _normalise_practical(raw, slot, reason)

    stage = {"slot": slot, "kind": kind, "reason": reason, "title": _clean(raw.get("title"), 70)}

    custom = kind == "skills" or (kind == "situational_judgment" and raw.get("areas"))

    if custom:
        total = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(raw.get("questions") or DEFAULT_QUESTIONS)))
        stage["questions"] = total
        stage["areas"] = _normalise_areas(raw.get("areas"), total)
        stage["intro"] = _clean(raw.get("intro"), 300)
        stage["style"] = "scenario" if kind == "situational_judgment" else "knowledge"
        if not stage["title"]:
            raise ValueError("a custom test needs a title")
    else:
        stage["title"] = ""

    return stage


def normalise_plan(raw: dict, job, source: str, strict: bool = True) -> dict:
    """The whole plan, validated against the job's role type. Raises ValueError when unusable.

    strict=False (the AI's own plan): a single unusable stage falls back to the STANDARD test for that slot instead of throwing away
    the other, good stage. strict=True (a plan supplied by a browser, which may be stale or tampered with) rejects the whole plan."""

    if not isinstance(raw, dict):
        raise ValueError("plan is not an object")

    stage3_kind = stage3_fixed_kind(job)
    by_slot = {}

    for item in raw.get("stages") or []:
        if isinstance(item, dict) and item.get("slot") in (SLOT_APTITUDE, SLOT_STAGE3):
            by_slot[item["slot"]] = item

    unusable = []

    def _stage(slot, default_kind):
        try:
            return normalise_stage(by_slot.get(slot) or {"kind": default_kind}, slot, stage3_kind)
        except ValueError as exc:
            if strict:
                raise
            logger.warning("AI plan stage '%s' unusable (%s): using the standard test for that slot.", slot, exc)
            unusable.append(slot)
            return normalise_stage({"kind": default_kind}, slot, stage3_kind)

    aptitude = _stage(SLOT_APTITUDE, "aptitude")
    stage3 = _stage(SLOT_STAGE3, "situational_judgment")

    # a stage was unusable AND nothing custom survived: the AI's answer is not worth trusting, the rules plan the job instead
    if unusable and not (aptitude.get("areas") or stage3.get("areas") or stage3.get("kind") == "practical"):
        raise ValueError("The AI plan had no usable stage.")

    return {
        "version": PLAN_VERSION,
        "source": source,
        "summary": _clean(raw.get("summary"), 400),
        "stages": [aptitude, stage3],
    }


# ------------------------------------------------------------------ the rules fallback (no LLM)

def _area(label: str, topics: list[str], count: int) -> dict:
    return {"label": label, "topics": topics, "count": count}


_FAMILIES = [
    ("writing", ("copywrit", "content writer", "content creator", "content strategist", "writer", "editor", "journalis", "proofread",
                 "social media", "seo ", "communications", "public relations", "blog", "technical writer", "translator", "copy editor"),
     "Language & Grammar Assessment",
     "Tests the written-English skills this role depends on: grammar, word choice and editing.",
     [_area("Grammar & usage", ["subject-verb agreement", "tenses", "articles and prepositions", "punctuation", "sentence structure"], 4),
      _area("Vocabulary & word choice", ["synonyms in context", "commonly confused words", "tone and register", "idioms"], 3),
      _area("Editing & style", ["spotting the error in a short paragraph", "choosing the clearest rewrite", "removing wordiness", "consistency and parallelism"], 3)]),
    ("finance", ("accountant", "accounting", "finance", "audit", "bookkeep", "tax ", "payroll", "treasury", "accounts payable",
                 "accounts receivable", "financial analyst", "credit analyst", "banking"),
     "Numerical & Finance Knowledge Assessment",
     "Tests numerical accuracy and the accounting and finance fundamentals this role uses daily.",
     [_area("Numerical accuracy", ["percentages and ratios", "reconciling figures", "interest and discounts", "averages"], 3),
      _area("Accounting & finance fundamentals", ["debits and credits", "accruals and prepayments", "depreciation", "reading financial statements"], 4),
      _area("Data interpretation", ["reading a small table", "variance and trend", "choosing the right figure"], 3)]),
    ("sales", ("sales", "business development", "customer support", "customer service", "customer success", "account manager",
               "telecaller", "call cent", "relationship manager", "inside sales"),
     "Communication & Business Math Assessment",
     "Tests clear business communication and the quick numeracy sales and support work needs.",
     [_area("Business communication", ["writing a clear reply", "tone with an upset customer", "concise wording", "grammar in emails"], 4),
      _area("Sales and customer math", ["discounts and margins", "commission", "targets and percentages", "simple forecasting"], 3),
      _area("Product and process judgement", ["choosing the next step", "prioritising requests", "spotting missing information"], 3)]),
    ("hr", ("human resources", "recruit", "talent acquisition", "people operations", "hr ", "hrbp", "onboarding", "talent partner"),
     "HR Knowledge & Communication Assessment",
     "Tests HR fundamentals and the written communication recruiters and HR partners rely on.",
     [_area("HR fundamentals", ["recruitment process", "onboarding", "performance management", "employee relations basics"], 4),
      _area("Communication & language", ["professional email wording", "grammar and clarity", "tone with candidates"], 3),
      _area("Data and reasoning", ["reading hiring metrics", "percentages and ratios", "logical prioritisation"], 3)]),
    ("design", ("designer", "ux", "ui/", "graphic", "creative director", "illustrator", "video editor", "animator", "art director"),
     "Design Fundamentals Assessment",
     "Tests the design principles and critique skills this role is built on.",
     [_area("Design principles", ["layout and hierarchy", "colour and contrast", "typography", "spacing and alignment"], 4),
      _area("User and brief thinking", ["reading a brief", "accessibility basics", "choosing between two solutions"], 3),
      _area("Tools and workflow", ["file formats", "version handling", "working with feedback"], 3)]),
    ("mis", ("mis executive", "mis analyst", "mis ", "reporting analyst", "reporting executive", "data entry", "excel", "dashboard reporting", "spreadsheet"),
     "Excel & Data Reporting Assessment",
     "Tests the spreadsheet and reporting skills this role uses every day.",
     [_area("Excel formulas & functions", ["lookup functions", "conditional sums and counts", "text and date functions", "pivot tables"], 4),
      _area("Data cleaning & checks", ["finding duplicates and blanks", "validating totals", "consistent formats", "reconciling two lists"], 3),
      _area("Reporting & interpretation", ["choosing the right chart or summary", "reading variance and trend", "writing a clear data note"], 3)]),
    ("operations", ("operations", "administrat", "project manager", "coordinator", "logistics", "supply chain", "procurement",
                    "office manager", "executive assistant", "program manager"),
     "Planning & Analytical Assessment",
     "Tests planning, prioritisation and the numbers operations roles work with.",
     [_area("Planning and prioritisation", ["scheduling", "dependencies", "resource trade-offs", "deadlines"], 4),
      _area("Numerical and data reasoning", ["percentages and ratios", "reading a table", "estimating quantities"], 3),
      _area("Business writing", ["clear status updates", "grammar and tone", "concise instructions"], 3)]),
]


# The practical environment each role family gets from the rules (the AI planner may choose differently; HR can override).
# Design roles have no environment yet (a portfolio upload is a later one) and keep the situational test.
_PRACTICAL_KEYWORDS = [
    ("mis", ("mis executive", "mis analyst", "mis ", "reporting analyst", "reporting executive", "data entry", "excel", "spreadsheet")),
    ("finance", ("accountant", "accounting", "finance", "audit", "bookkeep", "tax ", "payroll", "treasury", "accounts payable",
                 "accounts receivable", "financial analyst", "credit analyst", "banking")),
    ("support", ("customer support", "customer service", "customer success", "call cent", "telecaller", "helpdesk", "help desk", "chat support")),
    ("sales", ("sales", "business development", "account manager", "relationship manager", "inside sales")),
    ("hr", ("human resources", "recruit", "talent acquisition", "people operations", "hr ", "hrbp", "onboarding", "talent partner")),
    ("marketing", ("marketing", "brand", "growth", "campaign", "seo ", "social media", "digital marketing", "performance marketing")),
    ("writing", ("copywrit", "content writer", "content creator", "content strategist", "writer", "editor", "journalis", "proofread",
                 "communications", "public relations", "blog", "technical writer", "translator", "copy editor")),
    ("reception", ("receptionist", "front desk", "front office", "facilities executive")),
    ("admin", ("executive assistant", "personal assistant", "office admin", "administrat", "office executive", "secretary")),
    ("legal", ("legal", "contract executive", "paralegal", "company secretary", "compliance executive")),
    ("product", ("business analyst", "product owner", "product manager", "requirements analyst")),
    ("training", ("learning and development", "l&d", "trainer", "training executive", "instructional")),
    ("implementation", ("implementation specialist", "implementation consultant", "onboarding specialist", "customer onboarding")),
    ("management", ("project manager", "program manager", "delivery manager", "scrum master", "operations manager", "general manager")),
    ("operations", ("operations", "coordinator", "logistics", "supply chain", "procurement", "office manager")),
    ("design", ("designer", "ux", "ui/", "graphic", "creative director", "illustrator", "video editor", "animator", "art director")),
]

_PRACTICAL_BY_FAMILY = {
    "mis": ("data", "sales_report", "MIS Practical: Data Report"),
    "finance": ("data", "bank_reconciliation", "Finance Practical: Bank Reconciliation"),
    "operations": ("data", "inventory_orders", "Operations Practical: Stock Planning"),
    "support": ("roleplay", None, "Customer Support Practical: Call Simulation"),
    "sales": ("roleplay", None, "Sales Practical: Call Simulation"),
    "hr": ("written", None, "HR Practical Task"),
    "marketing": ("written", None, "Marketing Practical Task"),
    "writing": ("written", None, "Writing & Editing Practical"),
    "reception": ("roleplay", None, "Front Desk Practical: Call Simulation"),
    "webdesign": ("design", None, "Web Design Practical: Build a Page"),
    "admin": ("written", None, "Admin Practical"),
    "legal": ("written", None, "Legal Practical: Contract Review"),
    "product": ("written", None, "Product Practical: Requirements"),
    "training": ("written", None, "L&D Practical: Training Outline"),
    "implementation": ("written", None, "Implementation Practical"),
    "management": ("written", None, "Management Practical: Plan & Risks"),
}

_PRACTICAL_FOCUS = {
    "support": ["handling an upset customer on a call", "applying the company rules"],
    "sales": ["handling a price objection on a call", "agreeing a next step"],
    "hr": ["a shortlisting decision with reasons", "a respectful candidate email"],
    "marketing": ["a campaign brief", "platform-ready copy"],
    "writing": ["editing a weak paragraph", "a clear short piece"],
    "reception": ["handling a pushy caller", "following the front-desk rules"],
    "webdesign": ["a clear page structure", "visual hierarchy and readable colours"],
    "admin": ["a calendar conflict", "a reply written on someone's behalf"],
    "legal": ["summarising a clause in plain English", "flagging a contract risk"],
    "product": ["user stories from a messy brief", "prioritising a backlog"],
    "training": ["a training outline", "feedback to a trainer"],
    "implementation": ["a customer onboarding plan", "clear setup instructions"],
    "management": ["a project plan with risks", "a status update to a worried stakeholder"],
}


def practical_family(job) -> str | None:
    """The role family used to pick a practical environment (first matching family wins), or None."""

    if _is_web_design_role(job):
        return "webdesign"                                   # decided by the job TITLE only: a marketing job that mentions web design stays marketing

    title = str(getattr(job, "title", "") or "").lower()

    if any(k in title for k in ("video editor", "video editing", "animator", "motion designer", "graphic designer", "illustrator", "art director", "creative director")):
        return "design"                                      # no practical environment for these (not an IT-company need): "editor" must not make a video editor a writer

    text = job_text(job)

    for family, keywords in _PRACTICAL_KEYWORDS:
        if any(k in text for k in keywords):
            return family

    return None


_LAB_TITLES = {
    "log_analysis": "Technical Lab: Log Analysis", "auth_log": "Technical Lab: Security Log Investigation", "config_debug": "Technical Lab: Configuration Review",
    "network_math": "Technical Lab: Network Addressing", "security_review": "Technical Lab: Secure Code Review", "iam_policy": "Technical Lab: Cloud Access Review",
}

_FAMILY_TITLES = {
    "qa": "QA Practical: Test Design", "qa_lead": "QA Leadership Practical", "architecture": "Architecture Practical: System Design",
    "engmanagement": "Engineering Management Practical", "ux_research": "UX Research Practical", "presales": "Pre-Sales Practical", "recruiter": "Recruiting Practical",
    "support": "Customer Support Practical", "sales": "Sales Communication Practical", "hr": "HR Practical Task", "marketing": "Marketing Practical Task",
    "writing": "Writing & Editing Practical", "admin": "Admin Practical", "legal": "Legal Practical: Contract Review", "product": "Product Practical: Requirements",
    "training": "L&D Practical: Training Outline", "implementation": "Implementation Practical", "management": "Management Practical: Plan & Risks", "finance": "Finance Practical",
    "operations": "Operations Practical", "mis": "MIS Practical", "design": "Design Practical",
}

_DATA_TITLES = {"sales_report": "Data Practical: Sales Report", "bank_reconciliation": "Finance Practical: Bank Reconciliation", "inventory_orders": "Operations Practical: Stock Planning",
                "hr_headcount": "HR Practical: Headcount Report", "support_tickets": "Support Practical: Ticket Analysis"}

_ROLEPLAY_TITLES = {"itsupport": "IT Support Practical: Call Simulation", "client": "Client Practical: Call Simulation", "reception": "Front Desk Practical: Call Simulation",
                    "support": "Customer Support Practical: Call Simulation", "sales": "Sales Practical: Call Simulation", "hr": "HR Practical: Call Simulation"}


def _catalogue_stage3(job) -> dict | None:
    """The stage-3 stage the role catalogue gives this job's title (None: not in the catalogue, or a coding role, whose test the technical rules decide)."""

    entry = role_catalogue.lookup(getattr(job, "title", None))

    if not entry or entry["category"] == role_catalogue.CODE:
        return None

    category = entry["category"]
    base = {"slot": SLOT_STAGE3, "kind": "practical", "intro": "A short, realistic task from this job: show how you would do the work.",
            "reason": f"The role catalogue lists '{entry['title']}' in {entry['group']}: a practical task shows more than multiple-choice questions can."}

    if category == role_catalogue.DATA:
        template = entry.get("template") or DEFAULT_DATA_TEMPLATE
        return {**base, "environment": "data", "template": template, "title": _DATA_TITLES.get(template, "Data Practical"), "focus": ["calculating from a table", "accuracy"]}

    if category == role_catalogue.LAB:
        lab = entry.get("lab") or DEFAULT_LAB_TEMPLATE
        return {**base, "environment": "lab", "lab": lab, "title": _LAB_TITLES[lab], "focus": ["inspecting real artifacts", "finding the fault"]}

    if category == role_catalogue.DESIGN:
        return {**base, "environment": "design", "title": "Web Design Practical: Build a Page", "focus": ["a clear page structure", "visual hierarchy and readable colours"]}

    if category == role_catalogue.ROLEPLAY:
        family = entry.get("family") or "support"
        return {**base, "environment": "roleplay", "family": family, "title": _ROLEPLAY_TITLES.get(family, "Call Simulation"), "focus": ["handling a real call", "applying the company rules"]}

    family = entry.get("family") or "management"
    return {**base, "environment": "written", "family": family, "title": _FAMILY_TITLES.get(family, "Practical Work Sample"), "focus": _PRACTICAL_FOCUS.get(family, [])}


def rules_plan(job) -> dict:
    """A sensible plan from keyword families (no network). Technical roles keep the general aptitude test."""

    text = job_text(job)
    aptitude = {"slot": SLOT_APTITUDE, "kind": "aptitude", "reason": "General reasoning is the best single predictor for this role."}

    if job_role_shape(job) == "non_technical" or _is_web_design_role(job):
        for _family, keywords, title, intro, areas in _FAMILIES:
            if any(k in text for k in keywords):
                aptitude = {"slot": SLOT_APTITUDE, "kind": "skills", "title": title, "intro": intro, "areas": areas,
                            "questions": DEFAULT_QUESTIONS, "reason": f"Chosen from the role: {title.lower()} matches the work."}
                break

    stage3 = {"slot": SLOT_STAGE3, "kind": "situational_judgment", "reason": "Workplace judgement matters in this role."}

    catalogued = _catalogue_stage3(job) if practical_enabled() else None

    if catalogued is not None:
        stage3 = catalogued
    elif practical_enabled() and job_role_shape(job) == "technical" and stage3_fixed_kind(job) is None:
        stage3 = {"slot": SLOT_STAGE3, "kind": "practical", "environment": "lab", "lab": DEFAULT_LAB_TEMPLATE, "title": _LAB_TITLES[DEFAULT_LAB_TEMPLATE],
                  "focus": ["inspecting real artifacts", "finding the fault"], "intro": "A short, hands-on technical task: inspect the evidence and answer.",
                  "reason": "A technical role with no language the coding sandbox can run and no platform test: a hands-on lab fits."}

    family = practical_family(job) if catalogued is None and (job_role_shape(job) == "non_technical" or _is_web_design_role(job)) and practical_enabled() else None

    if family in _PRACTICAL_BY_FAMILY:
        environment, template, title = _PRACTICAL_BY_FAMILY[family]
        stage3 = {
            "slot": SLOT_STAGE3, "kind": "practical", "environment": environment, "title": title, "focus": _PRACTICAL_FOCUS.get(family, []),
            "intro": "A short, realistic task from this job: show how you would do the work.",
            "reason": f"A practical task from the {family} work shows more than multiple-choice questions can.",
            **({"template": template} if template else {}),
        }

    return normalise_plan({"summary": "Assessments chosen by the standard rules for this role type.", "stages": [aptitude, stage3]}, job, "rules")


# ------------------------------------------------------------------ the AI planner

_SYSTEM = """You design pre-employment assessments for a hiring platform. For ONE job you decide which assessments its candidates take, \
so that the tests measure what actually predicts success in THAT job. Be specific to the job: the same plan must not fit every role.

CATALOGUE you may choose from:
- slot "aptitude" (the 2nd stage):
    kind "aptitude": the general aptitude test (logic, numbers, verbal ability). Choose it only when general reasoning is what predicts \
success (graduate or trainee roles, analysts, operations/management, technical roles) or when the job is too unclear to design a better test.
    kind "skills": a custom test YOU design for this job: a title and 2-4 topic areas, each with 3-6 concrete topics. Choose it when a more \
specific skill predicts performance better than general aptitude: for example language and grammar for a copywriter or content writer, \
numerical accuracy and accounting knowledge for an accountant, product or domain knowledge for a specialist, communication for support roles.
- slot "programming" (the 3rd stage):
    {stage3_rule}

Return ONLY this JSON object, nothing else:
{{
  "summary": "1-2 sentences: why this combination suits the job",
  "stages": [
    {{"slot": "aptitude", "kind": "aptitude|skills", "title": "name of the test (for skills)", "intro": "one sentence shown to the candidate", "questions": 10,
      "reason": "why this test for this job", "areas": [{{"label": "Area name", "topics": ["topic", "topic", "topic"], "count": 4}}]}},
    {{"slot": "programming", "kind": "...", "title": "...", "reason": "...", "areas": [...], "environment": "written|data|roleplay|design|lab", "family": "...", "template": "...", "lab": "...", "focus": ["..."]}}
  ]
}}
Rules: "areas" only for kind "skills" or a role-specific "situational_judgment"; "environment", "template" and "focus" only for kind "practical". Topics must be things a multiple-choice question can test. \
Titles must name what is tested (for example "Language & Grammar Assessment"), never just "Test". Do not invent facts about the job."""

_STAGE3_NON_TECHNICAL = (
    'kind "situational_judgment": workplace scenarios. Give "areas" (2-4 areas of situations this role really faces) to get scenarios written for this '
    'role, or omit "areas" for the standard scenario bank.\n    kind "skills": a knowledge test for this job (title + areas), when job-specific knowledge '
    'matters more than judgement.\n    kind "practical": a real piece of the job. PREFER this whenever the work can be shown in a short task. Set "environment" to ONE of: '
    '"roleplay" (a voice call: the candidate speaks with an AI customer, prospect, client or user; give "family" from: support, sales, client, hr, reception, itsupport), '
    '"written" (the candidate writes an email, brief, plan, test cases or a case study, graded against a rubric; give "family" from: support, sales, hr, finance, marketing, writing, '
    'operations, mis, admin, legal, product, training, management, implementation, qa, qa_lead, architecture, engmanagement, ux_research, presales, recruiter), '
    '"data" (a table on screen with calculation questions: accounting and finance, MIS and reporting, operations and inventory, HR analytics, support operations; give "template" from: '
    'sales_report, bank_reconciliation, inventory_orders, hr_headcount, support_tickets), '
    '"lab" (a hands-on technical exercise on a real artifact, for technical roles that do not write code every day: DevOps, cloud, network, systems, security, IAM, support engineering; '
    'give "lab" from: log_analysis, auth_log, config_debug, network_math, security_review, iam_policy), or '
    '"design" (the candidate builds a web page by dragging components onto a canvas: web, UI and UX designers). '
    'Give a "title" and 2-3 short "focus" items (what the task should exercise). Never use it for video editing or animation roles.'
)
_STAGE3_TECHNICAL = (
    'the candidate\'s stage-3 test is FIXED to "{fixed}" because this is a technical role: set kind to "{fixed}" and give no areas.'
)


def _planner_llm_enabled() -> bool:
    return (os.getenv("ASSESSMENT_PLANNER_LLM", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


def _ask_llm(job) -> dict:

    from backend.app.services.aptitude.ai_question_generator import _make_provider

    fixed = stage3_fixed_kind(job)
    rule = _STAGE3_TECHNICAL.format(fixed=fixed) if fixed else _STAGE3_NON_TECHNICAL

    prompt = (
        f"JOB TITLE: {getattr(job, 'title', '') or 'Not specified'}\n"
        f"ROLE TYPE: {'technical' if fixed else 'non-technical'}\n"
        f"REQUIRED SKILLS: {(getattr(job, 'required_skills', '') or 'Not specified')[:400]}\n"
        f"TECHNOLOGIES: {(getattr(job, 'technologies', '') or 'Not specified')[:300]}\n"
        f"REQUIREMENTS / DESCRIPTION: {(getattr(job, 'requirements', '') or getattr(job, 'description', '') or 'Not specified')[:900]}\n"
        f"RESPONSIBILITIES: {(getattr(job, 'responsibilities', '') or 'Not specified')[:500]}\n\n"
        "Design the assessments for this job now."
    )

    provider = _make_provider()
    provider.timeout = PLANNER_TIMEOUT_SECONDS

    raw = provider.generate_json(prompt, system=_SYSTEM.format(stage3_rule=rule), max_tokens=2200, temperature=0.4)

    return json.loads(raw)


def ai_plan(job) -> dict:
    """The AI's plan, validated. Raises on any failure (callers fall back to the rules)."""

    return normalise_plan(_ask_llm(job), job, "ai", strict=False)


# ------------------------------------------------------------------ storage + public API

def _load_stored(job) -> dict | None:

    raw = getattr(job, "assessment_plan_json", None)
    if not raw:
        return None

    try:
        stored = json.loads(raw)
        return normalise_plan(stored, job, stored.get("source") or "stored") if stored.get("version") == PLAN_VERSION else None
    except (ValueError, TypeError, AttributeError):
        return None


def _store(job, plan: dict) -> None:

    job.assessment_plan_json = json.dumps(plan)

    try:
        from sqlalchemy.orm import object_session

        db = object_session(job)
        if db is not None:
            db.commit()
    except Exception as exc:  # noqa: BLE001 - the plan still works for this call; it is regenerated next time
        logger.warning("Assessment plan could not be stored: %s", exc)


def get_job_plan(job, *, allow_llm: bool = True, store: bool = True) -> dict:
    """The job's plan: the stored one, else the AI's (when allowed and enabled), else the rules'. Never raises."""

    stored = _load_stored(job)
    if stored is not None:
        return stored

    plan = None

    if allow_llm and _planner_llm_enabled():
        for attempt in range(2):
            try:
                plan = ai_plan(job)
                break
            except Exception as exc:  # noqa: BLE001 - any LLM / validation failure falls back to the rules
                busy = any(token in str(exc).lower() for token in ("429", "rate limit", "rate_limit", "503", "unavailable"))

                if attempt == 0 and busy:
                    time.sleep(RETRY_AFTER_BUSY_SECONDS)                 # a busy AI service is usually free again within seconds: ask once more before using the rules
                    continue

                logger.warning("AI assessment planner failed for job %s (%s): using the rules.", getattr(job, "id", None), exc)
                break

    if plan is None:
        plan = rules_plan(job)

    if store and getattr(job, "id", None) is not None:
        _store(job, plan)

    return plan


def _stage(plan: dict, slot: str) -> dict | None:
    return next((s for s in plan.get("stages", []) if s.get("slot") == slot), None)


def stage_for_slot(job, slot: str) -> dict | None:
    """The stage spec when this slot needs AI-written custom content, else None (the standard test is used).
    Reads the stored plan, else the rules: it never makes an LLM call, so it is safe on every request."""

    stage = _stage(get_job_plan(job, allow_llm=False, store=False), slot)

    if stage and stage.get("areas") and stage["kind"] in ("skills", "situational_judgment"):
        return stage

    return None


def practical_spec(job) -> dict | None:
    """The stage-3 practical stage of this job's plan (stored plan, else the rules: no LLM call), or None when the job takes another test."""

    if job is None or not practical_enabled():
        return None

    stage = _stage(get_job_plan(job, allow_llm=False, store=False), SLOT_STAGE3)

    return stage if stage and stage.get("kind") == "practical" else None


def stage_title(job, slot: str, default: str) -> str:
    """What this test is called for THIS job (for example "Language & Grammar Assessment"), else the standard name."""

    if slot == SLOT_STAGE3 and job is not None:
        practical = practical_spec(job)
        if practical and practical.get("title"):
            return practical["title"]

    stage = stage_for_slot(job, slot) if job is not None else None
    return stage["title"] if stage and stage.get("title") else default


def apply_override(plan: dict, job, *, aptitude: str | None = None, stage3: str | None = None) -> dict:
    """HR's choice per slot: 'ai' keeps the planner's stage; 'standard' puts the standard test back."""

    stages = []
    for stage in plan["stages"]:
        if stage["slot"] == SLOT_APTITUDE and aptitude == "standard":
            stage = {"slot": SLOT_APTITUDE, "kind": "aptitude", "title": "", "reason": "Chosen by HR: the general aptitude test."}
        elif stage["slot"] == SLOT_STAGE3 and stage3 == "standard" and stage["kind"] in ("skills", "situational_judgment", "practical"):
            stage = {"slot": SLOT_STAGE3, "kind": "situational_judgment", "title": "", "reason": "Chosen by HR: the standard scenario test."}
        stages.append(stage)

    return {**plan, "stages": stages, "source": "hr" if (aptitude == "standard" or stage3 == "standard") else plan.get("source", "ai")}

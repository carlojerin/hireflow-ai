"""
PRACTICAL "DESIGN" ENVIRONMENT: the candidate designs a web page by dragging components onto a canvas (web / UI / UX / product design roles).

The browser sends the finished layout as JSON (every element: type, position, size, text, colours). It is marked in two layers so the mark never rests
on an AI opinion alone:

  1. CODE measures what can be measured exactly: does the page contain what the brief asks for, is the text readable (WCAG contrast), is there a
     typographic hierarchy, is the layout aligned, do elements overlap or fall off the page, was placeholder text left in.
  2. AI judges what needs judgement, reading a structured description of the page plus those measurements: composition and visual hierarchy, fit to
     the brief and audience, and the quality of the copy.

If the AI is unavailable the code's marks are used and the result is flagged as provisional ("please look at the design").
The layout is stored as sent (after validation) so HR can see the page itself in the report.

The AI that judges is a VISION model: the page is drawn as a picture on the server from the validated layout (design_renderer.py, never an image from
the candidate's browser) and the model looks at it. Two independent looks are taken at once and averaged, so one unlucky answer cannot decide a mark.
If the vision model is unavailable a text-only AI judges from a description of the page, and if that is down too the measured marks stand.
"""

import concurrent.futures
import json
import logging
import os
import re
import time

logger = logging.getLogger("jobpilot.sjt.design")

CANVAS_W, CANVAS_H = 1200, 1500
MAX_ELEMENTS = 70
MAX_LAYOUT_CHARS = 90000
MIN_ELEMENTS_TO_JUDGE = 4
GRADE_TIMEOUT_SECONDS = 25
PASS_PERCENT = 60

ELEMENT_TYPES = ("nav", "section", "heading", "text", "button", "image", "card", "input", "footer", "divider")
LEAF_TYPES = ("heading", "text", "button", "image", "input", "divider")
TEXT_TYPES = ("nav", "heading", "text", "button", "card", "input", "footer")

DEFAULT_TEXTS = {"heading text", "paragraph text", "button", "your text here", "heading", "text", "navigation", "footer", "input", "label", "placeholder"}

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


# ------------------------------------------------------------------ the briefs (curated: every checklist item is machine-checkable)

def _item(key, label, **rule):
    return {"key": key, "label": label, "rule": rule}


_NAV = _item("nav", "A navigation bar at the top", type="nav", max_y=200)
_FOOTER = _item("footer", "A footer at the bottom", type="footer", min_y=800)
_HERO_HEADLINE = _item("headline", "A clear headline (the biggest text on the page)", type="heading", min=1, min_size=30)
_CTA = _item("cta", "A call-to-action button", type="button", min=1)
_IMAGE = _item("image", "At least one image", type="image", min=1)

_BRIEFS = [
    {
        "key": "saas", "keywords": ("saas", "software", "app", "platform", "product", "tech", "digital", "ux", "ui"),
        "title": "Landing page for a project-management app",
        "audience": "Small-team managers who are tired of spreadsheets",
        "brand": {"name": "Tasklane", "primary": "#4f5bff", "tone": "clean, friendly, trustworthy"},
        "brief": "Design the landing page of Tasklane, a project-management app. Visitors should understand what it does within five seconds and be pushed to start a free trial.",
        "copy": {"headline": "Plan work. Ship on time.", "subhead": "Tasklane keeps your team's tasks, deadlines and files in one calm place.",
                 "cta": "Start free trial", "features": ["Simple boards", "Deadline reminders", "Team chat"], "testimonial": "\"We cut our status meetings in half.\" Meera, Operations Lead"},
        "checklist": [_NAV, _HERO_HEADLINE, _CTA, _item("features", "A features area with three cards", type="card", min=3), _item("proof", "A testimonial or proof of trust", type="text", keywords=["we ", "\"", "customers", "trusted", "teams"]), _FOOTER],
    },
    {
        "key": "cafe", "keywords": ("cafe", "restaurant", "food", "hospitality", "bakery", "coffee"),
        "title": "Homepage for a neighbourhood cafe",
        "audience": "Local people deciding where to have coffee and lunch",
        "brand": {"name": "Brew & Bloom", "primary": "#8a5a2b", "tone": "warm, inviting, relaxed"},
        "brief": "Design the homepage of Brew & Bloom, a neighbourhood cafe. People should see the mood, find the opening hours and know how to reserve a table.",
        "copy": {"headline": "Good coffee. Slow mornings.", "subhead": "Fresh bakes, single-origin coffee and a corner seat waiting for you.",
                 "cta": "Reserve a table", "features": ["Fresh bakes", "Single-origin coffee", "Weekend brunch"], "testimonial": "Open daily 8 AM to 9 PM, 12 Park Street"},
        "checklist": [_NAV, _HERO_HEADLINE, _CTA, _IMAGE, _item("menu", "Three highlights (cards)", type="card", min=3), _item("hours", "The opening hours or address", type="text", keywords=["open", "am", "pm", "street"]), _FOOTER],
    },
    {
        "key": "course", "keywords": ("education", "course", "training", "learning", "school", "teach", "edtech"),
        "title": "Sign-up page for an online course",
        "audience": "Working professionals who want to learn data skills in the evenings",
        "brand": {"name": "SkillShip", "primary": "#0f766e", "tone": "motivating, clear, professional"},
        "brief": "Design the sign-up page of SkillShip's 8-week data skills course. It must explain what you get, build trust and make enrolling obvious.",
        "copy": {"headline": "Learn data skills in 8 weeks", "subhead": "Live evening classes, real projects and a mentor who answers within a day.",
                 "cta": "Enrol now", "features": ["Live classes", "Real projects", "Mentor support"], "testimonial": "\"I changed careers in three months.\" Arjun, now a data analyst"},
        "checklist": [_NAV, _HERO_HEADLINE, _CTA, _item("benefits", "Three benefits (cards)", type="card", min=3), _item("price", "The price or an enrolment note", type="text", keywords=["rs", "₹", "$", "price", "fee", "per month"]), _item("proof", "A testimonial", type="text", keywords=["\"", "i ", "changed", "career"]), _FOOTER],
    },
    {
        "key": "shop", "keywords": ("ecommerce", "e-commerce", "retail", "shop", "store", "fashion", "commerce"),
        "title": "Product page for an online shop",
        "audience": "Shoppers comparing a pair of running shoes",
        "brand": {"name": "StrideCo", "primary": "#e11d48", "tone": "energetic, confident, simple"},
        "brief": "Design the product page for StrideCo's Aero running shoe. Shoppers must see the product, the price and the buy button immediately, and trust the purchase.",
        "copy": {"headline": "Aero Runner", "subhead": "Light, cushioned and built for daily miles.",
                 "cta": "Add to cart", "features": ["Free returns", "2-year warranty", "Ships in 24 hours"], "testimonial": "Rs 4,999 inclusive of taxes"},
        "checklist": [_NAV, _item("title", "The product name as a heading", type="heading", min=1), _IMAGE, _item("price", "A visible price", type="text", keywords=["rs", "₹", "$", "price"]), _item("buy", "A buy button", type="button", min=1), _item("assurance", "Three reassurance points (cards)", type="card", min=3), _FOOTER],
    },
    {
        "key": "event", "keywords": ("event", "conference", "community", "marketing", "campaign"),
        "title": "Registration page for a one-day tech conference",
        "audience": "Developers and designers deciding whether to attend",
        "brand": {"name": "BuildDay", "primary": "#7c3aed", "tone": "bold, modern, exciting"},
        "brief": "Design the registration page for BuildDay, a one-day tech conference. Visitors should see when and where, why to come, and register quickly.",
        "copy": {"headline": "BuildDay 2026", "subhead": "One day. Twelve talks. Zero fluff. 14 March, Bengaluru.",
                 "cta": "Register now", "features": ["12 talks", "Hands-on workshops", "Networking lunch"], "testimonial": "Early-bird tickets close 20 February"},
        "checklist": [_NAV, _HERO_HEADLINE, _item("when", "The date and place", type="text", keywords=["march", "bengaluru", "2026", "date"]), _CTA, _item("agenda", "Three highlights (cards)", type="card", min=3), _item("form", "A registration field (input)", type="input", min=1), _FOOTER],
    },
    {
        "key": "portfolio", "keywords": ("portfolio", "freelance", "designer", "creative", "photographer", "agency"),
        "title": "Portfolio page for a freelance designer",
        "audience": "Startup founders looking to hire a designer",
        "brand": {"name": "Ira Menon", "primary": "#111827", "tone": "minimal, confident, elegant"},
        "brief": "Design the portfolio page of Ira Menon, a freelance product designer. Show who she is, her best work and an easy way to hire her.",
        "copy": {"headline": "I design products people enjoy using", "subhead": "Product designer with 6 years of experience in fintech and health apps.",
                 "cta": "Work with me", "features": ["Banking app redesign", "Health tracker", "Design system"], "testimonial": "ira@example.com"},
        "checklist": [_NAV, _HERO_HEADLINE, _item("work", "Three work samples (cards or images)", type="card", min=3), _CTA, _item("contact", "A way to get in touch", type="text", keywords=["@", "contact", "email", "hire"]), _FOOTER],
    },
]


def pick_brief(job, seed) -> dict:
    """The brief that fits the job best (by words in the job), else a stable pick for this candidate."""

    text = " ".join(str(getattr(job, k, "") or "") for k in ("title", "description", "requirements", "responsibilities", "required_skills")).lower()

    scored = sorted(((sum(1 for k in b["keywords"] if k in text), -index) for index, b in enumerate(_BRIEFS)), reverse=True)
    best_score, best_index = scored[0][0], -scored[0][1]

    if best_score >= 2:
        return json.loads(json.dumps(_BRIEFS[best_index]))

    return json.loads(json.dumps(_BRIEFS[sum(ord(c) for c in str(seed)) % len(_BRIEFS)]))


def candidate_view_of(brief: dict) -> dict:
    """What the candidate sees: the brief, the brand, the copy they can use and the checklist. Never the grading rubric."""

    return {
        "key": brief["key"], "title": brief["title"], "audience": brief["audience"], "brand": brief["brand"], "brief": brief["brief"], "copy": brief["copy"],
        "checklist": [c["label"] for c in brief["checklist"]], "canvas": {"w": CANVAS_W, "h": CANVAS_H},
    }


# ------------------------------------------------------------------ validating what the browser sends

def _num(value, low, high, default):
    try:
        return max(low, min(high, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _color(value, default):
    return value if isinstance(value, str) and _COLOR.match(value) else default


def sanitize_layout(raw) -> dict:
    """The layout as stored: only known element types, numbers clamped, colours validated, text trimmed. Never raises."""

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        data = None

    elements = []

    for index, item in enumerate((data or {}).get("elements") or []):

        if not isinstance(item, dict) or item.get("type") not in ELEMENT_TYPES or len(elements) >= MAX_ELEMENTS:
            continue

        elements.append({
            "id": _num(item.get("id"), 1, 100000, index + 1), "type": item["type"],
            "x": _num(item.get("x"), -200, CANVAS_W + 200, 0), "y": _num(item.get("y"), -200, CANVAS_H + 400, 0),
            "w": _num(item.get("w"), 8, CANVAS_W + 200, 100), "h": _num(item.get("h"), 2, CANVAS_H + 400, 40),
            "text": re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:300],
            "fontSize": _num(item.get("fontSize"), 8, 96, 16), "fontWeight": 700 if _num(item.get("fontWeight"), 400, 700, 400) >= 600 else 400,
            "color": _color(item.get("color"), "#111827"), "bg": _color(item.get("bg"), ""), "radius": _num(item.get("radius"), 0, 60, 0),
            "align": item.get("align") if item.get("align") in ("left", "center", "right") else "left",
        })

    return {"elements": elements}


# ------------------------------------------------------------------ measuring the page

def _luminance(hex_color: str) -> float:

    def channel(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _contains(outer: dict, inner: dict) -> bool:
    cx, cy = inner["x"] + inner["w"] / 2, inner["y"] + inner["h"] / 2
    return outer["x"] <= cx <= outer["x"] + outer["w"] and outer["y"] <= cy <= outer["y"] + outer["h"]


def _background_of(element: dict, elements: list[dict]) -> str:
    """The colour behind an element's text: its own fill, else the nearest filled container underneath it, else the white page."""

    if element["bg"] and element["type"] != "text" and element["type"] != "heading":
        return element["bg"]

    index = elements.index(element)

    for candidate in reversed(elements[:index]):
        if candidate["bg"] and candidate["type"] in ("section", "card", "nav", "footer", "image", "button", "input") and candidate is not element and _contains(candidate, element):
            return candidate["bg"]

    return element["bg"] or "#ffffff"


def _overlap_ratio(a: dict, b: dict) -> float:
    ix = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    smaller = min(a["w"] * a["h"], b["w"] * b["h"]) or 1
    return (ix * iy) / smaller


def _rule_met(rule: dict, elements: list[dict]) -> bool:

    of_type = [e for e in elements if e["type"] == rule["type"]]

    if "max_y" in rule:
        of_type = [e for e in of_type if e["y"] <= rule["max_y"]]
    if "min_y" in rule:
        of_type = [e for e in of_type if e["y"] >= rule["min_y"]]
    if "min_size" in rule:
        of_type = [e for e in of_type if e["fontSize"] >= rule["min_size"]]

    if "keywords" in rule:
        pool = [e for e in elements if e["type"] in TEXT_TYPES and e["text"]]
        return any(k in e["text"].lower() for e in pool for k in rule["keywords"])

    return len(of_type) >= rule.get("min", 1)


def measure(layout: dict, brief: dict) -> dict:
    """Everything about the page that code can measure exactly."""

    elements = layout["elements"]
    leaves = [e for e in elements if e["type"] in LEAF_TYPES]
    texts = [e for e in elements if e["type"] in TEXT_TYPES and e["text"]]

    checklist = [{"key": c["key"], "label": c["label"], "met": _rule_met(c["rule"], elements)} for c in brief["checklist"]]

    placeholder = [e for e in texts if e["text"].strip().lower() in DEFAULT_TEXTS]

    off_page = [e for e in elements if e["type"] != "section" and (e["x"] < -4 or e["y"] < -4 or e["x"] + e["w"] > CANVAS_W + 4)]

    overlaps = sum(
        1 for i, a in enumerate(leaves) for b in leaves[i + 1:]
        if a["type"] != "divider" and b["type"] != "divider" and _overlap_ratio(a, b) > 0.15
    )

    low_contrast = []
    contrast_values = []

    for e in texts:
        if e["type"] in ("button", "nav", "footer", "card", "input", "heading", "text"):
            ratio = contrast_ratio(e["color"], _background_of(e, elements))
            contrast_values.append(ratio)
            large = e["fontSize"] >= 24 or (e["fontSize"] >= 19 and e["fontWeight"] >= 600)
            if ratio < (3.0 if large else 4.5):
                low_contrast.append({"text": e["text"][:40], "ratio": round(ratio, 1)})

    text_leaves = [e for e in elements if e["type"] in ("heading", "text", "button", "input") and e["text"]]
    sizes = sorted({e["fontSize"] for e in text_leaves})
    headings = [e for e in text_leaves if e["type"] == "heading"]
    body = [e for e in text_leaves if e["type"] == "text"]

    # alignment: how many leaf elements share a left edge or a centre line with another one (within 6 px)
    aligned = 0
    for e in leaves:
        if any(o is not e and (abs(o["x"] - e["x"]) <= 6 or abs((o["x"] + o["w"] / 2) - (e["x"] + e["w"] / 2)) <= 6) for o in leaves):
            aligned += 1

    colors = {c for e in elements for c in (e["color"], e["bg"]) if c}

    return {
        "elements": len(elements), "checklist": checklist, "placeholder_text": len(placeholder), "text_elements": len(texts),
        "off_page": len(off_page), "overlaps": overlaps, "low_contrast": low_contrast[:6], "low_contrast_count": len(low_contrast),
        "font_sizes": sizes, "smallest_font": sizes[0] if sizes else None, "largest_heading": max((e["fontSize"] for e in headings), default=None),
        "body_size": body[0]["fontSize"] if body else None, "aligned_share": round(aligned / len(leaves), 2) if leaves else 0.0,
        "colors_used": len(colors), "brand_color_used": brief["brand"]["primary"].lower() in {c.lower() for c in colors},
        "average_contrast": round(sum(contrast_values) / len(contrast_values), 1) if contrast_values else None,
    }


def auto_scores(m: dict) -> list[dict]:
    """The marks code can give (0-10) with the reason for each."""

    checklist = m["checklist"]
    met = sum(1 for c in checklist if c["met"])

    completeness = 10 * met / len(checklist) if checklist else 0
    if m["text_elements"] and m["placeholder_text"] / m["text_elements"] > 0.3:
        completeness = max(0, completeness - 2)
    missing = [c["label"] for c in checklist if not c["met"]]

    alignment = 10 * m["aligned_share"] - min(4, m["overlaps"] * 1.5) - min(3, m["off_page"] * 1.5)

    if m["low_contrast_count"] == 0:
        readability = 10.0
    else:
        readability = max(0.0, 10 - 2.0 * m["low_contrast_count"])
    if m["smallest_font"] is not None and m["smallest_font"] < 12:
        readability = max(0.0, readability - 2)

    sizes = m["font_sizes"]
    hierarchy = 3.0 if len(sizes) <= 1 else 7.0 if len(sizes) == 2 else 10.0 if len(sizes) <= 5 else 7.0
    if m["largest_heading"] is not None and m["body_size"] is not None and m["largest_heading"] <= m["body_size"]:
        hierarchy = min(hierarchy, 4.0)
    if m["largest_heading"] is None:
        hierarchy = min(hierarchy, 4.0)

    def clip(v):
        return max(0, min(10, int(round(v))))

    return [
        {"criterion": "Completeness against the brief", "weight": 3, "score": clip(completeness), "max": 10, "kind": "code",
         "comment": ("Everything in the brief is on the page." if not missing else "Missing: " + "; ".join(missing[:4]) + ".")},
        {"criterion": "Alignment and tidy layout", "weight": 2, "score": clip(alignment), "max": 10, "kind": "code",
         "comment": f"{int(m['aligned_share'] * 100)}% of elements line up with another; {m['overlaps']} overlap(s); {m['off_page']} off the page."},
        {"criterion": "Readable colours and text size", "weight": 2, "score": clip(readability), "max": 10, "kind": "code",
         "comment": ("All text meets the WCAG contrast guidance." if m["low_contrast_count"] == 0 else f"{m['low_contrast_count']} text element(s) have too little contrast.")},
        {"criterion": "Typographic hierarchy", "weight": 2, "score": clip(hierarchy), "max": 10, "kind": "code",
         "comment": f"{len(sizes)} different text size(s) in use" + (f"; the biggest heading is {m['largest_heading']}px." if m["largest_heading"] else "; there is no heading.")},
    ]


# ------------------------------------------------------------------ the AI's part

_AI_CRITERIA = [
    ("Visual hierarchy and composition", 3), ("Fit to the brief and the audience", 2), ("Quality of the copy", 1),
]

_SYSTEM = """You are a fair, experienced web design reviewer marking a hiring test. The candidate built a web page on a 1200 px wide canvas from a brief by dragging components. \
You get the brief, a structured description of the page (every element, top to bottom, with position, size, text and colours) and objective measurements already taken by code. \
You cannot see the page, so judge only from the description: reading order, grouping, spacing, how the eye would move, whether the call to action is prominent, whether the page suits the audience and \
brand, and whether the copy is clear and persuasive. Do not re-mark what the measurements already cover (contrast, completeness, text sizes). A nearly empty or random page scores 0-3. \
Give one short, specific comment per criterion and a one-sentence summary.

Score each criterion from 0 to 10. Return ONLY this JSON: {"criteria": [{"criterion": "...", "score": 7, "comment": "..."}], "summary": "..."}"""


def describe(layout: dict) -> str:

    lines = []

    for e in sorted(layout["elements"], key=lambda e: (e["y"], e["x"]))[:MAX_ELEMENTS]:
        bits = [f"[{e['type']}] x={e['x']} y={e['y']} w={e['w']} h={e['h']}"]
        if e["text"]:
            bits.append(f"text=\"{e['text'][:120]}\" size={e['fontSize']} weight={e['fontWeight']} align={e['align']} color={e['color']}")
        if e["bg"]:
            bits.append(f"fill={e['bg']}")
        lines.append(" ".join(bits))

    return "\n".join(lines) or "(the page is empty)"


# ------------------------------------------------------------------ the vision model's part

VISION_MODELS_ENV = "AI_DESIGN_VISION_MODELS"
# Tried in this order for every look. Measured on this account: these three answered every request in 2-4 s, while gemini-2.5-flash is no longer offered to new
# users, gemini-3.8-flash / 3.7 / 3.5 / flash-latest were often unavailable or took 20-45 s, and the pro models are over quota.
DEFAULT_VISION_MODELS = "gemini-3-flash-preview,gemini-3.6-flash,gemini-3.1-flash-lite"
VISION_TIMEOUT_MS = 20000
VISION_RETRY_WITHIN_SECONDS = 35                        # a second round over the models only when the first failed fast (a busy service is usually busy for seconds)
VISION_MAX_OUTPUT_TOKENS = 6000                         # these models "think" first: a small limit would cut the JSON answer off
VISION_LOOKS = 2                                       # independent looks, averaged

_VISION_CRITERIA = [
    ("Visual hierarchy and composition", 3), ("Visual polish: spacing, balance and consistency", 2),
    ("Fit to the brief and the audience", 2), ("Quality of the copy", 1),
]

_VISION_SYSTEM = """You are a senior web design reviewer marking a hiring test. The candidate built a web page on a 1200 px wide canvas from a brief, by dragging components. You are \
shown a PICTURE of the page they built, the brief, and exact measurements already taken by code (they are facts: do not contradict them and do not re-mark what they cover: contrast, \
whether each required part exists, the text sizes in use).

Look at the picture as a professional would: where the eye goes first, whether the structure is clear, grouping, spacing and rhythm, balance and whitespace, consistency of style and \
colour, whether the main action is obvious, whether it suits the audience and the brand in the brief, and whether the words are clear and persuasive. Judge only what is visible. \
A nearly empty, random or broken page scores 0-3; a competent but plain page 5-6; a clearly professional page 8-10. Do not give a high score for effort or for following the checklist \
alone. Give one short, specific comment per criterion, one sentence summarising the page, and up to three concrete visual problems.

Score each criterion from 0 to 10. Return ONLY this JSON: {"criteria": [{"criterion": "...", "score": 7, "comment": "..."}], "summary": "...", "problems": ["..."]}"""


def vision_models() -> list[str]:
    """The vision models to try, in order. AI_DESIGN_VISION_MODELS=off switches the vision model off (the text-only AI is used)."""

    raw = (os.getenv(VISION_MODELS_ENV) or DEFAULT_VISION_MODELS).strip()

    if raw.lower() in ("off", "none", "0", "false"):
        return []

    return [m.strip() for m in raw.split(",") if m.strip()]


def _ask_vision_model(model: str, png: bytes, text: str) -> dict:
    """One call to one vision model. Raises on any failure (unavailable, over quota, timed out, unreadable answer)."""

    from google import genai
    from google.genai import types

    started = time.perf_counter()
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options=types.HttpOptions(timeout=VISION_TIMEOUT_MS))      # kept in a variable: a client collected mid-call is closed

    try:
        response = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=png, mime_type="image/png"), text],
            config=types.GenerateContentConfig(system_instruction=_VISION_SYSTEM, response_mime_type="application/json", temperature=0.1, max_output_tokens=VISION_MAX_OUTPUT_TOKENS),
        )
        parsed = json.loads(response.text)
        usage = getattr(response, "usage_metadata", None)

        try:
            from backend.app.services.llm_usage import record_usage
            record_usage("gemini", model, ok=True, seconds=time.perf_counter() - started,
                         prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0, output_tokens=getattr(usage, "candidates_token_count", 0) or 0)
        except Exception:  # noqa: BLE001
            pass

        if not isinstance(parsed, dict) or not parsed.get("criteria"):
            raise ValueError("the vision model's answer had no criteria")

        parsed["_model"] = model
        return parsed

    except Exception as exc:
        try:
            from backend.app.services.llm_usage import note_gemini_error, record_usage
            note_gemini_error(exc, model)
            record_usage("gemini", model, ok=False, rate_limited="429" in str(exc) or "quota" in str(exc).lower())
        except Exception:  # noqa: BLE001
            pass
        raise

    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass


def _one_look(png: bytes, brief: dict, m: dict) -> dict:
    """One look: the picture + the brief + the measurements, sent to the first vision model that answers (a second round only if the first was quick)."""

    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not configured")

    text = json.dumps({
        "brief": brief["brief"], "audience": brief["audience"], "brand": brief["brand"], "the_required_parts_present": [c["label"] for c in m["checklist"] if c["met"]],
        "the_required_parts_missing": [c["label"] for c in m["checklist"] if not c["met"]],
        "measurements": {k: m[k] for k in ("elements", "placeholder_text", "overlaps", "off_page", "font_sizes", "aligned_share", "colors_used", "brand_color_used", "low_contrast_count")},
        "criteria": [c for c, _w in _VISION_CRITERIA],
    })

    models = vision_models()
    last_error = RuntimeError("no vision model is configured")
    began = time.monotonic()

    for round_number in range(2):

        if round_number == 1:
            if time.monotonic() - began > VISION_RETRY_WITHIN_SECONDS:
                break
            time.sleep(1.5)

        for model in models:
            try:
                return _ask_vision_model(model, png, text)
            except Exception as exc:  # noqa: BLE001 - this model is busy, over quota or answered badly: the next one is tried
                last_error = exc
                logger.warning("Vision model %s could not mark the design (%s): trying the next one.", model, str(exc)[:120])

    raise last_error


def _clean_text(value, limit):
    text = re.sub(r"[‐-―−]", "-", str(value or ""))                 # typographic hyphens and dashes -> plain "-"
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _vision_marks(layout: dict, brief: dict, m: dict) -> dict:
    """The vision model's marks: VISION_LOOKS independent looks at the same picture, averaged per criterion."""

    from backend.app.services.sjt.design_renderer import render_png

    png = render_png(layout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=VISION_LOOKS) as pool:
        futures = [pool.submit(_one_look, png, brief, m) for _ in range(VISION_LOOKS)]

    looks, errors = [], []
    for future in futures:
        try:
            looks.append(future.result())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    if not looks:
        raise errors[0]

    criteria = []

    for position, (name, weight) in enumerate(_VISION_CRITERIA):
        scores, comments = [], []
        for look in looks:
            given = [c for c in look.get("criteria", []) if isinstance(c, dict)]
            found = next((c for c in given if _clean_text(c.get("criterion"), 120) == name), given[position] if position < len(given) else {})
            scores.append(max(0, min(10, float(found.get("score", 0) or 0))))
            comments.append(_clean_text(found.get("comment"), 240))
        criteria.append({"criterion": name, "weight": weight, "score": int(round(sum(scores) / len(scores))), "max": 10, "kind": "vision",
                         "comment": next((c for c in comments if c), ""), "spread": round(max(scores) - min(scores), 1)})

    problems = []
    for look in looks:
        for problem in look.get("problems") or []:
            text = _clean_text(problem, 160)
            if text and text not in problems:
                problems.append(text)

    return {"criteria": criteria, "summary": _clean_text(looks[0].get("summary"), 300), "problems": problems[:4], "looks": len(looks), "models": sorted({look.get("_model") for look in looks if look.get("_model")})}


def _provider(timeout):
    from backend.app.services.aptitude.ai_question_generator import _make_provider

    provider = _make_provider()
    provider.timeout = timeout
    return provider


def _ai_marks(brief: dict, layout: dict, m: dict) -> dict:

    prompt = json.dumps({
        "brief": brief["brief"], "audience": brief["audience"], "brand": brief["brand"], "page": describe(layout),
        "measurements": {k: m[k] for k in ("elements", "placeholder_text", "overlaps", "off_page", "font_sizes", "aligned_share", "colors_used", "brand_color_used", "low_contrast_count")},
        "criteria": [c for c, _w in _AI_CRITERIA],
    })

    raw = json.loads(_provider(GRADE_TIMEOUT_SECONDS).generate_json(prompt, system=_SYSTEM, max_tokens=1000, temperature=0.1))
    given = [c for c in raw.get("criteria", []) if isinstance(c, dict)]
    by_name = {str(c.get("criterion") or "").strip(): c for c in given}

    criteria = []
    for position, (name, weight) in enumerate(_AI_CRITERIA):
        found = by_name.get(name) or (given[position] if position < len(given) else {})
        criteria.append({"criterion": name, "weight": weight, "score": max(0, min(10, int(float(found.get("score", 0) or 0)))), "max": 10, "kind": "ai",
                         "comment": re.sub(r"\s+", " ", str(found.get("comment") or "")).strip()[:240]})

    return {"criteria": criteria, "summary": re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()[:300]}


def score_design(answer_text, brief: dict) -> dict:
    """Marks a finished layout. Returns {"percent", "source", "needs_review", "summary", "criteria", "metrics", "layout"}."""

    layout = sanitize_layout(answer_text)
    m = measure(layout, brief)

    if len(layout["elements"]) < MIN_ELEMENTS_TO_JUDGE:
        return {
            "percent": 0, "source": "rules", "needs_review": False, "layout": layout, "metrics": m,
            "summary": "The page was left empty or almost empty." if layout["elements"] else "No design was submitted.",
            "criteria": [{**c, "score": 0} for c in auto_scores(m)] + [{"criterion": n, "weight": w, "score": 0, "max": 10, "kind": "ai", "comment": ""} for n, w in _AI_CRITERIA],
        }

    criteria = auto_scores(m)
    summary, source, needs_review, problems = "", "vision", False, []

    try:
        vision = _vision_marks(layout, brief, m)
        criteria += vision["criteria"]
        summary, problems = vision["summary"], vision["problems"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vision marking of the design failed (%s): trying the text-only AI.", exc)
        try:
            ai = _ai_marks(brief, layout, m)
            criteria += ai["criteria"]
            summary, source = ai["summary"], "ai"
        except Exception as exc2:  # noqa: BLE001
            logger.warning("AI marking of the design failed (%s): using the measured marks only.", exc2)
            source, needs_review = "rules", True
            summary = "AI marking was unavailable: scored on the measured qualities only (completeness, alignment, readability, hierarchy). Please look at the design."

    earned = sum(c["score"] * c["weight"] for c in criteria)
    possible = sum(c["max"] * c["weight"] for c in criteria)

    return {
        "percent": round(100 * earned / possible) if possible else 0, "source": source, "needs_review": needs_review,
        "summary": summary, "criteria": criteria, "metrics": m, "layout": layout, "problems": problems,
    }

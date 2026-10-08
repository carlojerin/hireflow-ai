import re

# ============================================================
# JOBPILOT AI - BLUEPRINT-DRIVEN QUESTION ALLOCATION
#
# How many of the 5 programming-assessment questions are coding / SQL / web is decided by the
# Assessment Blueprint (the job's skills and their priorities), never by a fixed split.
#
#   weight of a track = sum over the blueprint's skills_to_assess of the priority weight
#                       (critical 4, high 3, medium 2, low 1) of every skill that belongs to it,
#                       plus 2 for every job focus area that belongs to it
#   a track needs the plan to support it (programming / sql / frontend assessment types)
#   questions are shared out in proportion to the weights (largest remainder); a track that has
#   at least 15% of the weight gets at least one question
#
#   Backend + SQL          -> for example 4 coding + 1 SQL
#   Data analyst           -> for example 3 SQL + 2 Python/data analysis
# ============================================================

PRIORITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
MIN_SHARE_FOR_ONE_QUESTION = 0.15

SQL_TERMS = ("sql", "postgres", "mysql", "sqlite", "oracle", "database", "dba", "stored procedure", "query", "queries",
             "data warehouse", "etl", "t-sql", "pl/sql")
WEB_TERMS = ("html", "css", "react", "angular", "vue", "frontend", "front-end", "front end", "dom", "tailwind", "bootstrap",
             "sass", "web design", "responsive", "ui ", "ux")

FULLSTACK_TITLE = re.compile(r"full[\s-]?stack", re.I)
BACKEND_ONLY_TITLE = re.compile(r"back[\s-]?end|\bapi\b", re.I)
FRONTEND_TITLE = re.compile(r"\b(front[\s-]?end|react|angular|vue|svelte|next\.?js|ui developer|web developer|javascript developer)\b", re.I)

# Fixed splits for the role families whose title already says what they must be able to do
FRONTEND_SPLIT = {"web": 3, "coding": 2}                       # pages (HTML/CSS/JS, checked in a real browser) + JavaScript logic
FULLSTACK_SPLIT = {"coding": 2, "sql": 1, "web": 2}            # a full stack developer works in all three


def role_family(job) -> str | None:
    """frontend | fullstack | None, from the job title alone ("Full Stack Backend/API Developer" stays backend-weighted)."""

    title = str(getattr(job, "title", "") or "") if job is not None else ""

    if FULLSTACK_TITLE.search(title):
        return None if BACKEND_ONLY_TITLE.search(title) else "fullstack"

    if FRONTEND_TITLE.search(title):
        return "frontend"

    return None


TRACK_PLAN_TYPE = {"coding": "programming", "sql": "sql", "web": "frontend"}


def classify_skill(text: str) -> str:
    """coding | sql | web for one skill / focus-area phrase (everything else is coding)."""

    lowered = f" {text.lower()} "

    if any(re.search(r"(?<![a-z])" + re.escape(term.strip()) + r"(?![a-z])", lowered) for term in WEB_TERMS if term.strip() != "ui"):
        return "web"

    if any(re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", lowered) for term in SQL_TERMS):
        return "sql"

    return "coding"


def track_weights(blueprint, plan, job=None) -> dict[str, int]:

    kinds = {item.assessment_type for item in plan.assessment_types}
    available = {track for track, kind in TRACK_PLAN_TYPE.items() if kind in kinds}

    weights = {track: 0 for track in TRACK_PLAN_TYPE}

    for item in getattr(blueprint, "skills_to_assess", []) or []:
        weights[classify_skill(item.skill)] += PRIORITY_WEIGHT.get(str(item.priority).lower(), 1)

    for area in getattr(blueprint, "job_focus_areas", []) or []:
        weights[classify_skill(str(area))] += 2

    title = str(getattr(job, "title", "") or "") if job is not None else str(getattr(blueprint, "job_title", "") or "")

    if re.search(r"\b(sql|database|dba|data analyst|data engineer|bi developer|reporting)\b", title, re.I):
        weights["sql"] += 3                                              # the role itself is a database role

    if re.search(r"\b(front[- ]?end|web developer|ui developer|html)\b", title, re.I):
        weights["web"] += 3

    return {track: (weight if track in available else 0) for track, weight in weights.items()}


def allocate_questions(blueprint, plan, job=None, total: int = 5) -> tuple[dict[str, int], list[str]]:
    """
    Returns ({track: question_count}, explanation). Empty when the plan supports none of the tracks.
    """

    family = role_family(job)

    if family in ("frontend", "fullstack"):

        split = dict(FRONTEND_SPLIT if family == "frontend" else FULLSTACK_SPLIT)
        scaled = {t: max(1, round(c * total / 5)) for t, c in split.items()}

        while sum(scaled.values()) > total:
            scaled[max(scaled, key=scaled.get)] -= 1
        while sum(scaled.values()) < total:
            scaled[max(scaled, key=scaled.get)] += 1

        return scaled, [f"role family '{family}' (from the job title): fixed split {scaled}"]

    weights = track_weights(blueprint, plan, job)
    positive = {t: w for t, w in weights.items() if w > 0}

    explanation = [f"track weights from the blueprint: {weights}"]

    if not positive:

        kinds = {item.assessment_type for item in plan.assessment_types}

        if "programming" in kinds:
            positive = {"coding": 1}                                     # a programming role whose skills gave no signal
            explanation.append("no skill signal: all coding")
        else:
            return {}, explanation + ["the plan supports no programming, sql or frontend assessment"]

    grand_total = sum(positive.values())
    exact = {t: total * w / grand_total for t, w in positive.items()}
    counts = {t: int(v) for t, v in exact.items()}

    for track, share in ((t, w / grand_total) for t, w in positive.items()):
        if share >= MIN_SHARE_FOR_ONE_QUESTION and counts[track] == 0:
            counts[track] = 1

    while sum(counts.values()) > total:                                  # the minimums may overshoot: trim the biggest track
        counts[max(counts, key=counts.get)] -= 1

    remainders = sorted(positive, key=lambda t: exact[t] - int(exact[t]), reverse=True)
    index = 0

    while sum(counts.values()) < total:
        counts[remainders[index % len(remainders)]] += 1
        index += 1

    counts = {t: c for t, c in counts.items() if c > 0}
    explanation.append(f"allocation of {total} questions: {counts}")

    return counts, explanation

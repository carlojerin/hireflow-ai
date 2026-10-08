import json
import re

from backend.app.services.assessment.language_capability_registry import normalize_language_name

# ============================================================
# JOBPILOT AI - CANDIDATE LANGUAGE RANKING
#
# When a job allows several programming languages, the candidate's own resume decides which
# one is offered first: the language with the strongest evidence. The job's list is NEVER
# widened: a language the job does not allow is never returned.
#
# Evidence (all from the candidate's resume analysis and profile):
#   +4  the language is listed as a skill (+2 more when it is among the first three skills)
#   +2  a framework/tool that implies it is listed (FastAPI -> Python, Spring -> Java, ...)
#   +1  per mention in the summary / experience / projects text (at most 3)
# Ties keep the job's own order.
# ============================================================

FRAMEWORK_IMPLIES = {
    "python": ["fastapi", "django", "flask", "pandas", "numpy", "sqlalchemy", "pytest", "celery", "pydantic"],
    "java": ["spring", "hibernate", "maven", "gradle", "jpa", "junit", "servlet"],
    "javascript": ["node", "nodejs", "express", "react", "vue", "angular", "nestjs", "jquery", "next.js"],
    "typescript": ["angular", "nestjs", "deno"],
    "csharp": [".net", "dotnet", "asp.net", "entity framework", "blazor", "linq"],
    "php": ["laravel", "symfony", "wordpress", "composer"],
    "ruby": ["rails", "sinatra", "rspec"],
    "go": ["gin", "gorm", "goroutine"],
    "kotlin": ["android", "ktor", "jetpack"],
    "rust": ["cargo", "tokio", "actix"],
    "cpp": ["stl", "qt", "boost"],
    "c": ["embedded", "gcc"],
}


def _as_list(value) -> list[str]:

    if not value:
        return []

    if isinstance(value, list):
        return [str(v) for v in value]

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except ValueError:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]

    return [str(value)]


def _text_of(resume_analysis, profile) -> str:

    parts = []

    for name in ("summary", "experience", "raw_text"):
        value = getattr(resume_analysis, name, None) if resume_analysis else None
        if value:
            parts.append(value if isinstance(value, str) else json.dumps(value))

    for name in ("about", "projects", "experience", "headline"):
        value = getattr(profile, name, None) if profile else None
        if value:
            parts.append(value if isinstance(value, str) else json.dumps(value))

    return " ".join(parts).lower()


def _mentions(text: str, term: str) -> int:
    pattern = r"(?<![a-z0-9+#.])" + re.escape(term) + r"(?![a-z0-9+#])"
    return len(re.findall(pattern, text))


def _canonical_skill(skill: str) -> str | None:
    return normalize_language_name(skill.strip().lower()) if skill else None


def language_terms(canonical: str) -> list[str]:
    names = {"csharp": ["c#", "csharp"], "cpp": ["c++", "cpp"], "javascript": ["javascript", "js"], "go": ["golang", "go"]}
    return names.get(canonical, [canonical])


def rank_languages(allowed: list[str], resume_analysis=None, profile=None) -> tuple[list[str], list[dict]]:
    """
    Returns (ranked languages, reasons). `allowed` is the job's language list in the job's own order; the result is a
    permutation of it. Each reason is {language, score, evidence: [...]}.
    """

    allowed = list(dict.fromkeys(a for a in allowed if a))

    skills = _as_list(getattr(resume_analysis, "skills", None) if resume_analysis else None)
    skills += _as_list(getattr(profile, "skills", None) if profile else None)
    skills += _as_list(getattr(profile, "preferred_technologies", None) if profile else None)
    skills = list(dict.fromkeys(skills))
    skill_text = " | ".join(s.lower() for s in skills)
    body = _text_of(resume_analysis, profile)

    scored = []

    for order, language in enumerate(allowed):

        score, evidence = 0, []

        for position, skill in enumerate(skills):
            if _canonical_skill(skill) == language:
                score += 4
                evidence.append(f"listed as a skill ('{skill}')")
                if position < 3:
                    score += 3 - position                       # earlier in the resume's skill list = stronger (3, 2, 1)
                    evidence.append(f"skill number {position + 1} on the resume")
                break

        for framework in FRAMEWORK_IMPLIES.get(language, []):
            if _mentions(skill_text, framework):
                score += 2
                evidence.append(f"framework/tool '{framework}' implies it")

        mentions = sum(_mentions(body, term) for term in language_terms(language))
        if mentions:
            score += min(mentions, 3)
            evidence.append(f"mentioned {mentions}x in the resume text")

        scored.append((-score, order, language, score, evidence))

    scored.sort()

    ranked = [item[2] for item in scored]
    reasons = [
        {"language": item[2], "score": item[3], "evidence": item[4] or ["no resume evidence (job order kept)"]}
        for item in scored
    ]

    return ranked, reasons

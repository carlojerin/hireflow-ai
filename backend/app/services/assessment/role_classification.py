import re

from backend.app.services.assessment.language_capability_registry import (
    normalize_language_name,
)


# ============================================================
# JOBPILOT AI - TECHNICAL / NON-TECHNICAL ROLE CLASSIFICATION
#
# Used by both the AI interview blueprint (role_shape — adapts
# question phrasing) and the pipeline's programming-stage gate
# (skips the coding assessment entirely for a non-technical role,
# e.g. Digital Marketing, Sales, Customer Support, HR, Finance/
# Compliance — roles that exist at every kind of company, including
# a fintech one, regardless of the COMPANY's own industry).
#
# A job is "technical" when its required_skills name at least one
# recognizable programming language OR one common piece of the
# technical stack (framework, cloud/infra platform, database,
# version control, etc.) — never from the job's title or the
# company's industry alone. This is a heuristic, not a claim of
# deep role understanding, same as this project's other role-
# detection heuristics (job_domain() in
# programming_role_orchestrator.py).
# ============================================================

_TECHNICAL_KEYWORDS = {
    # Frameworks / libraries
    "react", "angular", "vue", "svelte", "nextjs", "next.js", "nestjs",
    "django", "flask", "fastapi", "spring", "spring boot", "express",
    "laravel", "rails", "dotnet", ".net", "jquery", "redux",
    # Cloud / infra / devops
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "k8s",
    "terraform", "ansible", "jenkins", "ci/cd", "devops", "linux",
    # Databases
    "mysql", "postgresql", "postgres", "mongodb", "redis", "sqlite",
    "oracle", "dynamodb", "cassandra", "elasticsearch",
    # Version control / tooling
    "git", "github", "gitlab", "bitbucket",
    # Data/ML
    "machine learning", "tensorflow", "pytorch", "pandas", "numpy",
    "data engineering", "etl", "spark", "hadoop",
    # General engineering terms
    "api", "rest api", "graphql", "microservices", "backend", "frontend",
    "full stack", "fullstack", "software engineer", "software development",
    "web development", "mobile development", "android", "ios development",
    # Platform-specific languages not in the executable-language registry
    # (same precedent as Swift: classified as technical here even though
    # programming_assessment_feasible() will honestly report it cannot
    # run a coding test in them yet -- a Salesforce/SAP/blockchain
    # developer role is never silently treated as non-technical just
    # because JobPilot has no sandbox for its language).
    "abap", "apex", "solidity", "salesforce", "cobol", "mainframe",
    "sap", "sap hana", "s/4hana",
    # Embedded / firmware
    "embedded", "firmware", "rtos", "microcontroller",
    # Config/scripting-heavy platform roles (borderline but classified
    # technical here for the same reason: a candidate configuring
    # ArcGIS/UiPath/Workday integrations is not well served by a
    # generic HR/sales-style situational judgment test).
    "rpa", "uipath", "gis", "arcgis", "qgis", "workday",
    # Networking / systems administration (same TDA "networking_systems"
    # bank these feed into below) -- without these, a Network Engineer
    # or System Administrator whose skills never happen to say "Linux"
    # fell through to non_technical/SJT instead of their intended
    # domain assessment.
    "cisco", "networking", "firewall", "vpn", "tcp/ip", "routing",
    "switching", "network security", "windows server", "active directory",
    "system administration", "network administration",
    # Cybersecurity (same "cybersecurity" TDA bank below) -- pentest/
    # SOC/forensics tool and methodology names, not just "security"
    # alone (too broad/ambiguous on its own).
    "siem", "penetration testing", "pen testing", "ethical hacking",
    "nmap", "metasploit", "burp suite", "vulnerability assessment",
    "incident response", "threat intelligence", "malware analysis",
    "digital forensics", "cybersecurity", "cyber security",
    "information security", "iso 27001", "owasp",
    # NoSQL databases (same TDA "nosql" bank below) -- mongodb/redis/
    # cassandra/dynamodb are already covered above under Databases;
    # these are the remaining ones not yet named anywhere else.
    "nosql", "cql", "neo4j", "cypher query", "couchdb", "firestore",
    "document database", "key-value store", "graph database",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _split_skills(required_skills: str) -> list[str]:
    return [s.strip() for s in required_skills.split(",") if s.strip()]


# Word-boundary matching, not plain substring search -- a naive `kw in
# haystack` check lets short keywords false-match inside unrelated words
# ("git" inside "digital marketing", "api" inside "rapid growth" or
# "capital markets"), which would misroute common non-technical titles
# (Digital Marketing Executive, Sales roles mentioning "rapid growth")
# into the technical pipeline. `\b` alone doesn't help here since it's
# an alnum/non-alnum transition either way; lookaround on alnum chars
# specifically is what actually blocks "digital" matching "git".
def _keyword_present(haystack: str, keyword: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def required_skills_are_technical(required_skills: str, extra_text: str = "") -> bool:
    """
    True if `required_skills` (and optionally other job text, e.g.
    description/technologies, passed via extra_text) names at least
    one recognized programming language or common technical-stack
    keyword. False for a skills list like "Excel, Financial
    Modeling, Risk Analysis, Communication" (a Financial Analyst
    role at a fintech company, say) — those are real, valuable
    skills, just not ones a coding assessment would meaningfully
    test.
    """

    skills = _split_skills(required_skills or "")

    for skill in skills:
        if normalize_language_name(skill) is not None:
            return True

    haystack = _normalize(" ".join([required_skills or "", extra_text or ""]))

    return any(_keyword_present(haystack, keyword) for keyword in _TECHNICAL_KEYWORDS)


def job_role_shape(job) -> str:
    """"technical" | "non_technical" for any object exposing
    required_skills (and optionally description/technologies, used
    as extra signal) — the one shared classifier both the interview
    blueprint and the pipeline's programming-stage gate call,
    so a job is never classified differently in one place than
    the other.

    HR can override the auto-detection per job (see Job.role_shape_
    override, set from the Invite-a-Candidate form) when the keyword
    heuristic below gets it wrong for their specific role — checked
    FIRST, before any text is even read."""

    override = str(getattr(job, "role_shape_override", "") or "").strip().lower()
    if override in ("technical", "non_technical"):
        return override

    # the job TITLE is evidence too: "Java Developer" is technical whatever the one-line description says (see role_catalogue.py)
    from backend.app.services.assessment.role_catalogue import lookup as _catalogue_lookup

    entry = _catalogue_lookup(getattr(job, "title", None))
    if entry:
        return entry["shape"]

    required_skills = str(getattr(job, "required_skills", "") or "")

    extra_text = " ".join(
        str(getattr(job, name, "") or "")
        for name in ("description", "technologies", "title")
    )

    return "technical" if required_skills_are_technical(required_skills, extra_text) else "non_technical"


# ============================================================
# TECHNICAL DOMAIN ASSESSMENT ROUTING
#
# Stage 3 for a "technical" job_role_shape() has TWO possible real
# assessments, not one: a live coding test (ProgrammingAssessmentV2)
# when the job names a language JobPilot's Docker sandbox can
# actually execute, or a TechnicalDomainAssessment (domain-knowledge
# MCQ/scenario bank) when it doesn't — SAP/ABAP, Salesforce/Apex,
# Solidity, RPA, embedded firmware, DevOps/infra tooling,
# networking/sysadmin. Real ATS platforms (TestGorilla, iMocha,
# Vervoe, Mettl) assess exactly these roles the same way: domain
# MCQ/scenario, never a live sandbox — "write and run Terraform"
# isn't a meaningfully sandboxable exercise the way Python is.
# ============================================================

# The exact set of language keys the Docker sandbox can run
# (language_runner.RUNNER_REGISTRY) — duplicated as a plain constant
# rather than imported, so classification (called from many light-
# weight places, including the AI interview blueprint builder) never
# triggers that module's Docker-runner registration as a side effect.
_EXECUTABLE_LANGUAGES = {
    "python", "javascript", "typescript", "java", "c", "cpp", "go",
    "rust", "kotlin", "php", "ruby", "csharp", "swift", "sql", "html",
}

_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "salesforce": {"salesforce", "apex", "lightning"},
    "sap": {"sap", "abap", "s/4hana", "sap hana"},
    "blockchain": {"solidity", "blockchain", "ethereum", "smart contract", "web3"},
    "rpa": {"rpa", "uipath", "automation anywhere", "blue prism"},
    "embedded": {"embedded", "firmware", "rtos", "microcontroller"},
    "gis": {"gis", "arcgis", "qgis", "geospatial"},
    "workday": {"workday", "hcm"},
    "devops_cloud": {
        "docker", "kubernetes", "k8s", "terraform", "ansible", "jenkins",
        "ci/cd", "devops", "aws", "azure", "gcp", "google cloud", "cloud",
    },
    "networking_systems": {
        "cisco", "networking", "routing", "switching", "firewall", "vpn",
        "tcp/ip", "linux", "windows server", "active directory",
        "system administration", "network administration",
    },
    "cybersecurity": {
        "siem", "penetration testing", "pen testing", "ethical hacking",
        "nmap", "metasploit", "burp suite", "vulnerability assessment",
        "incident response", "threat intelligence", "malware analysis",
        "digital forensics", "soc analyst", "soar", "ids", "ips",
        "cybersecurity", "cyber security", "information security",
        "iso 27001", "nist", "owasp",
    },
    # NoSQL databases -- NOT SQL (SQLite can't run their query languages at
    # all: MongoDB's JSON-style query methods, Cassandra's CQL, Redis's
    # key-value commands, Neo4j's Cypher graph queries are each their own
    # distinct syntax, unrelated to standard SQL). A role naming only these
    # has no executable-language match, so without this domain bank it fell
    # through to the generic non-technical SJT.
    "nosql": {
        "mongodb", "mongo", "nosql", "cassandra", "cql", "redis",
        "neo4j", "cypher query", "dynamodb", "couchdb", "firestore",
        "document database", "key-value store", "graph database",
    },
}

# Checked in order: a role naming both "Salesforce" and "AWS" lands on
# the more specific salesforce bank, not the broad devops_cloud one.
# "cybersecurity" is checked before "networking_systems" since a SOC/
# pentest role often also mentions firewalls/VPNs as context, but its
# actual assessment should be the security-specific bank, not the
# general sysadmin one. "nosql" is checked before "devops_cloud" since
# a DynamoDB-heavy role also commonly mentions AWS, but the more
# specific NoSQL bank is the right assessment for it.
_DOMAIN_PRIORITY = [
    "salesforce", "sap", "blockchain", "rpa", "embedded", "gis",
    "workday", "cybersecurity", "nosql", "devops_cloud", "networking_systems",
]


#  Conservative keyword set for scanning free-text job descriptions (the
#  HR Invite-a-Candidate form only ever fills `description`, never the
#  structured `required_skills` column -- see company_invites.py). Only
#  unambiguous tokens are included: bare "go" and bare "c" are deliberately
#  left out (too likely to match ordinary English words like "go live" or
#  stray initials), matching the same precision-first approach used for
#  _TECHNICAL_KEYWORDS and _DOMAIN_KEYWORDS elsewhere in this module.
_EXECUTABLE_LANGUAGE_TEXT_KEYWORDS: dict[str, str] = {
    "python": "python", "javascript": "javascript", "typescript": "typescript",
    "java": "java", "c++": "cpp", "golang": "go", "rust": "rust",
    "kotlin": "kotlin", "php": "php", "ruby": "ruby", "c#": "csharp",
    ".net": "csharp", "swift": "swift", "sql": "sql", "html": "html",
}


def job_has_executable_language(required_skills: str, extra_text: str = "") -> bool:
    """True if any listed skill (or, failing that, free-text job
    description/technologies) names a language the Docker sandbox can
    actually run — used to decide between a real coding assessment and
    a domain-knowledge one for a technical role."""

    for skill in _split_skills(required_skills or ""):
        if normalize_language_name(skill) in _EXECUTABLE_LANGUAGES:
            return True

    haystack = _normalize(extra_text or "")

    return any(_keyword_present(haystack, keyword) for keyword in _EXECUTABLE_LANGUAGE_TEXT_KEYWORDS)


def detect_technical_domain(required_skills: str, extra_text: str = "") -> str | None:
    """Which TechnicalDomainAssessment bank applies, or None if this
    role should get a real coding assessment (or isn't technical)."""

    haystack = _normalize(" ".join([required_skills or "", extra_text or ""]))

    for domain in _DOMAIN_PRIORITY:
        if any(_keyword_present(haystack, keyword) for keyword in _DOMAIN_KEYWORDS[domain]):
            return domain

    return None


def job_requires_domain_assessment(job) -> bool:
    """True when a job is technical but names no language JobPilot
    can actually run — these candidates get a TechnicalDomainAssessment
    instead of a live coding assessment, in the same pipeline slot."""

    if job_role_shape(job) != "technical":
        return False

    required_skills = str(getattr(job, "required_skills", "") or "")
    extra_text = " ".join(
        str(getattr(job, name, "") or "")
        for name in ("description", "technologies", "title")
    )

    if job_has_executable_language(required_skills, extra_text):
        return False

    return job_technical_domain(job) is not None


def job_technical_domain(job) -> str | None:
    """The TechnicalDomainAssessment bank for this job, or None."""

    required_skills = str(getattr(job, "required_skills", "") or "")
    extra_text = " ".join(
        str(getattr(job, name, "") or "")
        for name in ("description", "technologies", "title")
    )

    found = detect_technical_domain(required_skills, extra_text)

    if found:
        return found

    # nothing in the text names a platform, but the role does ("SAP Consultant" with a one-line description)
    from backend.app.services.assessment.role_catalogue import lookup as _catalogue_lookup

    entry = _catalogue_lookup(getattr(job, "title", None))

    return entry.get("domain") if entry else None

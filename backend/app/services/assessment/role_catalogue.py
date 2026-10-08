"""
ROLE CATALOGUE: every domain of an IT company, the roles inside it, and which assessment each role gets at stage 3.

Why this exists
---------------
The pipeline used to decide whether a job is technical, and which language / domain it needs, ONLY from keywords in the job's skills and description. HR
often types just a title and a short sentence ("Backend Developer, strong experience"), so a developer could be classified non-technical and a QA tester
could get a coding test. The title says more than the text, so the catalogue maps job TITLES (patterns, so "Senior Java Developer" matches too) to a
category, and the category decides the stage-3 assessment:

  code       the live coding test (programming engine), or a domain-knowledge test when the role has no runnable language (SAP, Salesforce...)
  lab        a hands-on technical lab: an artifact to inspect (log, config, network plan, code with a hidden flaw) and questions with computed answers
  data       a table and calculation questions (Excel / MIS style)
  written    a written work sample for a role family (an email, brief, plan, test cases, case study...)
  roleplay   a voice call with an AI customer / prospect / client / user
  design     build a web page by dragging components

Titles not in the catalogue are NOT a gap: the AI planner and the keyword rules classify them (see assessment_planner.py); the catalogue is the fast, exact,
explainable path for the roles an IT company actually hires. ASSESSMENT_CATALOGUE=off ignores it.
"""

import os
import re
from functools import lru_cache

CODE, LAB, DATA, WRITTEN, ROLEPLAY, DESIGN = "code", "lab", "data", "written", "roleplay", "design"


def catalogue_enabled() -> bool:
    return (os.getenv("ASSESSMENT_CATALOGUE", "on") or "on").strip().lower() not in ("off", "0", "false", "no")


def _r(title, patterns, category, shape, **extra):
    return {"title": title, "patterns": patterns, "category": category, "shape": shape, **extra}


T, N = "technical", "non_technical"

# (domain, [roles]); a role: title, title patterns (lower case substrings), category, shape, and what the category needs (family / template / lab / domain / languages).
DOMAINS = {

    "Software Development": [
        _r("Frontend Developer", ["frontend developer", "front-end developer", "front end developer", "ui developer", "react developer", "angular developer", "vue developer"], CODE, T, languages=["javascript", "html"]),
        _r("Backend Developer", ["backend developer", "back-end developer", "back end developer", "api developer", "node.js developer", "nodejs developer", "django developer"], CODE, T, languages=["python"]),
        _r("Full-Stack Developer", ["full-stack developer", "full stack developer", "fullstack developer", "mern", "mean stack"], CODE, T, languages=["javascript", "python"]),
        _r("Java Developer", ["java developer", "spring developer", "j2ee"], CODE, T, languages=["java"]),
        _r("Python Developer", ["python developer"], CODE, T, languages=["python"]),
        _r(".NET Developer", [".net developer", "dotnet developer", "c# developer", "asp.net"], CODE, T, languages=["csharp"]),
        _r("PHP Developer", ["php developer", "laravel developer", "wordpress developer"], CODE, T, languages=["php"]),
        _r("Mobile App Developer", ["mobile app developer", "android developer", "ios developer", "flutter developer", "react native developer", "mobile developer"], CODE, T, languages=["kotlin", "java"]),
        _r("Embedded / Firmware Engineer", ["embedded engineer", "firmware engineer", "embedded software", "embedded developer"], CODE, T, languages=["c", "cpp"], domain="embedded"),
        _r("Game Developer", ["game developer", "unity developer", "unreal developer", "game programmer"], CODE, T, languages=["cpp", "csharp"]),
        _r("Software Engineer", ["software engineer", "software developer", "programmer", "application developer", "sde", "developer"], CODE, T, languages=["python", "java", "javascript"], generic=True),
        _r("Software Engineer Trainee / Intern", ["engineer trainee", "developer trainee", "software intern", "developer intern", "graduate engineer trainee", "associate software engineer"], CODE, T, languages=["python", "java"]),
    ],

    "Quality Assurance & Testing": [
        _r("Manual QA Tester", ["manual tester", "manual qa", "qa tester", "software tester", "test engineer", "qa analyst", "quality analyst", "qa engineer"], WRITTEN, T, family="qa"),
        _r("QA Automation Engineer", ["automation tester", "automation engineer", "qa automation", "test automation", "selenium", "sdet"], CODE, T, languages=["python", "java", "javascript"]),
        _r("Performance Test Engineer", ["performance test", "performance engineer", "load test"], LAB, T, lab="log_analysis"),
        _r("QA Lead / Test Manager", ["qa lead", "test lead", "test manager", "qa manager", "head of qa"], WRITTEN, T, family="qa_lead"),
    ],

    "DevOps, Cloud & Infrastructure": [
        _r("DevOps Engineer", ["devops engineer", "devops", "release engineer", "build engineer", "ci/cd engineer"], LAB, T, lab="config_debug", domain="devops_cloud"),
        _r("Site Reliability Engineer", ["site reliability", "sre"], LAB, T, lab="log_analysis", domain="devops_cloud"),
        _r("Cloud Engineer", ["cloud engineer", "aws engineer", "azure engineer", "gcp engineer", "cloud administrator", "cloud support"], LAB, T, lab="iam_policy", domain="devops_cloud"),
        _r("Cloud Architect", ["cloud architect", "solutions architect", "infrastructure architect"], WRITTEN, T, family="architecture"),
        _r("Platform Engineer", ["platform engineer", "kubernetes engineer", "infrastructure engineer"], LAB, T, lab="config_debug", domain="devops_cloud"),
        _r("System Administrator", ["system administrator", "systems administrator", "sysadmin", "linux administrator", "windows administrator", "server administrator"], LAB, T, lab="log_analysis", domain="networking_systems"),
        _r("Network Engineer", ["network engineer", "network administrator", "network technician", "ccna"], LAB, T, lab="network_math", domain="networking_systems"),
        _r("Database Administrator", ["database administrator", "dba", "sql developer", "database engineer"], CODE, T, languages=["sql"]),
    ],

    "Data, AI & Machine Learning": [
        _r("Data Analyst", ["data analyst", "business intelligence analyst", "reporting analyst"], DATA, T, template="sales_report"),
        _r("BI Developer", ["bi developer", "power bi developer", "tableau developer", "business intelligence developer"], CODE, T, languages=["sql"]),
        _r("Data Engineer", ["data engineer", "etl developer", "big data engineer"], CODE, T, languages=["python", "sql"]),
        _r("Analytics Engineer", ["analytics engineer"], CODE, T, languages=["sql", "python"]),
        _r("Data Scientist", ["data scientist", "applied scientist", "research scientist"], CODE, T, languages=["python"]),
        _r("Machine Learning Engineer", ["machine learning engineer", "ml engineer", "mlops engineer", "deep learning engineer"], CODE, T, languages=["python"]),
        _r("AI / LLM Engineer", ["ai engineer", "llm engineer", "generative ai", "prompt engineer", "nlp engineer", "computer vision engineer"], CODE, T, languages=["python"]),
        _r("Data Annotation / AI Trainer", ["data annotation", "ai trainer", "data labeling", "annotator"], WRITTEN, N, family="writing"),
    ],

    "Cybersecurity": [
        _r("SOC Analyst", ["soc analyst", "security analyst", "cyber security analyst", "cybersecurity analyst", "threat analyst", "incident responder"], LAB, T, lab="auth_log", domain="cybersecurity"),
        _r("Penetration Tester", ["penetration tester", "pen tester", "ethical hacker", "red team", "vulnerability analyst"], LAB, T, lab="security_review", domain="cybersecurity"),
        _r("Security Engineer", ["security engineer", "application security", "appsec", "devsecops"], LAB, T, lab="security_review", domain="cybersecurity"),
        _r("GRC / Compliance Analyst", ["grc analyst", "compliance analyst", "risk analyst", "information security officer", "iso 27001 auditor"], WRITTEN, N, family="legal"),
        _r("IAM Specialist", ["iam specialist", "identity and access", "iam engineer", "access management"], LAB, T, lab="iam_policy", domain="cybersecurity"),
    ],

    "IT Support & Service Desk": [
        _r("Help Desk Analyst", ["help desk", "helpdesk", "service desk", "it support executive", "it support analyst", "l1 support", "it helpdesk"], ROLEPLAY, T, family="itsupport"),
        _r("Technical Support Engineer", ["technical support engineer", "application support", "l2 support", "l3 support", "production support", "support engineer"], LAB, T, lab="log_analysis"),
        _r("Desktop Support Technician", ["desktop support", "end user support", "field technician", "it technician", "hardware engineer"], ROLEPLAY, T, family="itsupport"),
        _r("IT Asset Coordinator", ["it asset", "asset coordinator", "inventory coordinator"], DATA, N, template="inventory_orders"),
    ],

    "Enterprise Applications & Automation": [
        _r("SAP Consultant", ["sap consultant", "sap developer", "abap", "sap fico", "sap mm", "sap sd", "sap functional"], CODE, T, domain="sap", languages=[]),
        _r("Salesforce Developer / Admin", ["salesforce developer", "salesforce administrator", "salesforce admin", "apex developer", "salesforce consultant"], CODE, T, domain="salesforce", languages=[]),
        _r("ServiceNow Developer", ["servicenow"], CODE, T, languages=["javascript", "sql"]),
        _r("RPA Developer", ["rpa developer", "uipath developer", "automation anywhere", "power automate"], CODE, T, domain="rpa", languages=[]),
        _r("Low-Code / No-Code Developer", ["low-code", "low code", "no-code", "no code", "power apps"], WRITTEN, T, family="implementation"),
        _r("Workday Consultant", ["workday"], CODE, T, domain="workday", languages=[]),
        _r("Blockchain Developer", ["blockchain developer", "smart contract", "web3 developer", "solidity"], CODE, T, domain="blockchain", languages=[]),
    ],

    "Product & Project Management": [
        _r("Product Manager", ["product manager", "associate product manager", "group product manager", "head of product"], WRITTEN, N, family="product"),
        _r("Product Owner", ["product owner"], WRITTEN, N, family="product"),
        _r("Project / Delivery Manager", ["project manager", "delivery manager", "program manager", "project coordinator", "it project manager"], WRITTEN, N, family="management"),
        _r("Scrum Master", ["scrum master", "agile coach", "agile delivery"], WRITTEN, N, family="management"),
        _r("Business Analyst", ["business analyst", "systems analyst", "functional analyst", "requirements analyst", "ba"], WRITTEN, N, family="product"),
        _r("Technical Program Manager", ["technical program manager", "tpm"], WRITTEN, N, family="architecture"),
    ],

    "UI/UX & Design": [
        _r("UI Designer", ["ui designer", "user interface designer", "interface designer", "web designer", "website designer"], DESIGN, N),
        _r("UX Designer", ["ux designer", "ui/ux", "ux/ui", "user experience designer", "interaction designer", "product designer", "visual designer"], DESIGN, N),
        _r("UX Researcher", ["ux researcher", "user researcher", "design researcher"], WRITTEN, N, family="ux_research"),
        _r("Technical Writer", ["technical writer", "documentation engineer", "api documentation", "developer advocate", "developer relations"], WRITTEN, N, family="writing"),
    ],

    "Sales & Business Development": [
        _r("Sales Development Representative", ["sales development", "sdr", "bdr", "lead generation", "business development representative"], ROLEPLAY, N, family="sales"),
        _r("Account Executive", ["account executive", "sales executive", "sales manager", "enterprise sales", "software sales"], ROLEPLAY, N, family="sales"),
        _r("Business Development Manager", ["business development manager", "business development executive", "bd manager", "partnerships manager"], WRITTEN, N, family="sales"),
        _r("Account Manager", ["account manager", "key account", "client partner", "relationship manager", "client relationship"], ROLEPLAY, N, family="client"),
        _r("Pre-Sales Consultant", ["pre-sales", "presales", "solutions consultant", "sales engineer", "solution engineer"], WRITTEN, N, family="presales"),
        _r("Inside Sales / Telesales", ["inside sales", "telesales", "telecaller", "tele caller", "outbound caller"], ROLEPLAY, N, family="sales"),
    ],

    "Marketing & Content": [
        _r("Digital Marketing Executive", ["digital marketing", "marketing executive", "marketing manager", "performance marketing", "marketing specialist"], WRITTEN, N, family="marketing"),
        _r("SEO / SEM Specialist", ["seo", "sem specialist", "search engine", "paid media", "ppc"], DATA, N, template="sales_report"),
        _r("Content Writer", ["content writer", "copywriter", "content creator", "content strategist", "blog writer", "content editor", "copy editor"], WRITTEN, N, family="writing"),
        _r("Social Media Manager", ["social media", "community manager", "influencer marketing"], WRITTEN, N, family="marketing"),
        _r("Growth Marketer", ["growth marketer", "growth manager", "growth hacker", "demand generation", "email marketing", "crm marketing"], DATA, N, template="sales_report"),
        _r("Brand / PR Executive", ["brand manager", "pr executive", "public relations", "communications manager", "corporate communications"], WRITTEN, N, family="marketing"),
    ],

    "Customer Success & Support": [
        _r("Customer Support Executive", ["customer support", "customer service", "customer care", "chat support", "email support", "support executive"], ROLEPLAY, N, family="support"),
        _r("Voice / Call Centre Executive", ["call centre", "call center", "voice process", "bpo", "inbound executive"], ROLEPLAY, N, family="support"),
        _r("Customer Success Manager", ["customer success"], ROLEPLAY, N, family="client"),
        _r("Onboarding / Implementation Specialist", ["onboarding specialist", "implementation specialist", "implementation consultant", "customer onboarding"], WRITTEN, N, family="implementation"),
    ],

    "Human Resources & Talent": [
        _r("Technical Recruiter", ["technical recruiter", "talent acquisition", "recruiter", "recruitment executive", "it recruiter"], WRITTEN, N, family="recruiter"),
        _r("HR Generalist", ["hr generalist", "hr executive", "human resources executive", "hr manager", "hr operations", "people operations"], WRITTEN, N, family="hr"),
        _r("HR Business Partner", ["hr business partner", "hrbp"], WRITTEN, N, family="hr"),
        _r("Learning & Development Specialist", ["learning and development", "learning & development", "l&d", "trainer", "training specialist", "instructional designer"], WRITTEN, N, family="training"),
        _r("Payroll Executive", ["payroll"], DATA, N, template="hr_headcount"),
        _r("Talent Sourcer", ["talent sourcer", "sourcing specialist"], DATA, N, template="hr_headcount"),
    ],

    "Finance & Accounting": [
        _r("Accountant", ["accountant", "accounts executive", "bookkeeper", "accounts assistant", "tax executive", "chartered accountant"], DATA, N, template="bank_reconciliation"),
        _r("Financial Analyst", ["financial analyst", "fp&a", "finance analyst", "finance manager", "financial planning"], DATA, N, template="bank_reconciliation"),
        _r("Accounts Payable / Receivable Executive", ["accounts payable", "accounts receivable", "ap executive", "ar executive", "collections executive"], DATA, N, template="bank_reconciliation"),
        _r("Billing / Revenue Operations Analyst", ["billing analyst", "revenue operations", "billing executive", "revops"], DATA, N, template="sales_report"),
        _r("Internal Auditor", ["internal auditor", "audit executive", "audit associate", "statutory auditor"], WRITTEN, N, family="finance"),
    ],

    "Operations, Admin & Legal": [
        _r("Office / Admin Executive", ["office administrator", "admin executive", "office executive", "administrative assistant", "office manager", "back office"], WRITTEN, N, family="admin"),
        _r("Executive Assistant", ["executive assistant", "personal assistant", "secretary"], WRITTEN, N, family="admin"),
        _r("MIS Executive", ["mis executive", "mis analyst", "mis manager", "reporting executive", "data entry", "data operator"], DATA, N, template="sales_report"),
        _r("Procurement / Vendor Management", ["procurement", "vendor management", "purchase executive", "purchasing", "sourcing executive"], DATA, N, template="inventory_orders"),
        _r("Operations Coordinator", ["operations coordinator", "operations executive", "operations manager", "logistics", "supply chain"], DATA, N, template="inventory_orders"),
        _r("Legal / Contract Executive", ["legal executive", "contract executive", "paralegal", "legal counsel", "company secretary", "contract manager"], WRITTEN, N, family="legal"),
        _r("Receptionist / Front Desk", ["receptionist", "front desk", "front office", "visitor management"], ROLEPLAY, N, family="reception"),
    ],

    "Engineering Leadership": [
        _r("Software Architect", ["software architect", "principal engineer", "staff engineer", "enterprise architect", "technical architect"], WRITTEN, T, family="architecture"),
        _r("Tech Lead", ["tech lead", "technical lead", "team lead engineer", "engineering lead", "lead engineer"], WRITTEN, T, family="architecture"),
        _r("Engineering Manager", ["engineering manager", "development manager", "software manager"], WRITTEN, T, family="engmanagement"),
        _r("Director of Engineering", ["director of engineering", "head of engineering", "vp engineering", "vp of engineering", "cto", "chief technology officer", "chief technical officer"], WRITTEN, T, family="engmanagement"),
    ],
}


def _flatten() -> list[dict]:

    rows = []

    for domain, roles in DOMAINS.items():
        for role in roles:
            rows.append({**role, "group": domain})

    return rows


def all_roles() -> list[dict]:
    return _flatten()


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, dict]]:
    """(pattern, role) pairs, longest pattern first so the most specific title wins ("senior java developer" is Java, not generic "developer")."""

    pairs = [(pattern, role) for role in _flatten() for pattern in role["patterns"]]
    pairs.sort(key=lambda pair: (bool(pair[1].get("generic")), -len(pair[0])))      # specific titles first; the catch-all "developer" only when nothing else fits
    return pairs


def _norm(title) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#./& -]", " ", str(title or "").lower())).strip()


def _contains(text: str, pattern: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(pattern) + r"(?![a-z0-9])", text) is not None


def lookup(job_title) -> dict | None:
    """The role for a job title ("Senior Java Developer (Remote)" finds Java Developer), or None. Short patterns (sre, ba, sdr, cto...) need a whole-word match."""

    if not catalogue_enabled():
        return None

    title = _norm(job_title)

    if len(title) < 2:
        return None

    for pattern, role in _index():
        if _contains(title, pattern):
            return role

    return None


def domains() -> list[str]:
    return list(DOMAINS)


_LANGUAGE_NAMES = {"python": "Python", "javascript": "JavaScript", "html": "HTML", "java": "Java", "csharp": "C#", "php": "PHP", "kotlin": "Kotlin", "cpp": "C++", "c": "C", "sql": "SQL"}


def enrich_job(job) -> None:
    """
    A coding role whose skill fields name no language ("Backend Developer, strong experience") cannot get a coding test: the coding engine plans from the job's
    skills and technologies. The catalogue knows the usual languages of the role, so they are added to the job's technologies (never over what HR wrote).
    Called when a job is created from the invite form.
    """

    entry = lookup(getattr(job, "title", None))

    if not entry or entry["category"] != CODE or not entry.get("languages"):
        return

    from backend.app.services.assessment.role_classification import job_has_executable_language

    # what the coding engine reads is the job's own skill fields: a language only mentioned in the description (or in the title) does not count there
    if job_has_executable_language(str(getattr(job, "required_skills", "") or ""), str(getattr(job, "technologies", "") or "")):
        return

    from backend.app.services.assessment import role_classification as rc

    text = rc._normalize(" ".join(str(getattr(job, f, "") or "") for f in ("requirements", "description")))
    named = [k for kw, k in rc._EXECUTABLE_LANGUAGE_TEXT_KEYWORDS.items() if rc._keyword_present(text, kw)]
    named = [k for k in dict.fromkeys(named) if k in _LANGUAGE_NAMES][:2]          # HR named a language in the requirement: that one, never a different default beside it

    names = [_LANGUAGE_NAMES[k] for k in (named or entry["languages"][:2]) if k in _LANGUAGE_NAMES]

    if names:
        existing = str(getattr(job, "technologies", "") or "").strip()
        job.technologies = ", ".join([p for p in (existing, *names) if p])

        if not str(getattr(job, "required_skills", "") or "").strip():
            job.required_skills = ", ".join(names)                              # the coding engine plans mainly from the required skills

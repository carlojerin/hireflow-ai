import json
import logging
import random
import uuid

from backend.app.schemas.programming_question_v2 import ProgrammingMainQuestionV2
from backend.app.services.assessment.programming_question_builders import build_question
from backend.app.services.assessment.sql_runner import ORDERED_DIRECTIVE, NO_ROWS_TEXT, run_sql_query

logger = logging.getLogger("jobpilot.assessment.sql")

# ============================================================
# JOBPILOT AI - SQL ASSESSMENT QUESTIONS
#
# For database / data roles: each question is a small business scenario with a
# schema, sample data, and a task the candidate answers with ONE SQLite SELECT.
#
#   * Gemini writes the scenario, the schema, three data sets and a reference query.
#   * The SERVER runs the reference query on every data set to get the expected rows,
#     so an answer key can never be wrong; a question whose reference query fails,
#     or returns nothing, is discarded and replaced.
#   * Without Gemini (or for a discarded question) a parametric generator builds an
#     equivalent question from random data, so no two candidates get the same set.
#
# The candidate never sees the hidden data sets or the reference query.
# ============================================================

EXAMPLE_ROWS_LIMIT = 8

FIRST_NAMES = [
    "Aarav", "Diya", "Kavya", "Rohan", "Meera", "Ishaan", "Nisha", "Vikram", "Anaya", "Kabir", "Sana", "Arjun",
    "Tara", "Rahul", "Priya", "Devika", "Manav", "Leela", "Yash", "Zoya", "Neel", "Pooja", "Harsh", "Ira",
]

THEMES = [
    dict(key="shop", dim="customers", did="id", dname="name", dgroup="city", fact="orders", fid="id", fk="customer_id",
         amount="amount", cat="status", date="order_date", groups=["Chennai", "Pune", "Delhi", "Mumbai"],
         cats=["paid", "pending", "refunded"], noun_dim="customer", noun_fact="order", story="an online store"),
    dict(key="clinic", dim="patients", did="id", dname="name", dgroup="ward", fact="appointments", fid="id", fk="patient_id",
         amount="fee", cat="department", date="visit_date", groups=["North", "South", "East", "West"],
         cats=["cardiology", "orthopedics", "dermatology"], noun_dim="patient", noun_fact="appointment", story="a hospital"),
    dict(key="school", dim="students", did="id", dname="name", dgroup="class_name", fact="exam_results", fid="id", fk="student_id",
         amount="marks", cat="subject", date="exam_date", groups=["8A", "8B", "9A", "9B"],
         cats=["maths", "science", "english"], noun_dim="student", noun_fact="exam result", story="a school"),
    dict(key="hr", dim="employees", did="id", dname="name", dgroup="department", fact="timesheets", fid="id", fk="employee_id",
         amount="hours", cat="project", date="work_date", groups=["Engineering", "Sales", "Support", "Finance"],
         cats=["alpha", "beta", "gamma"], noun_dim="employee", noun_fact="timesheet record", story="a software company"),
    dict(key="bank", dim="clients", did="id", dname="name", dgroup="branch", fact="transactions", fid="id", fk="client_id",
         amount="amount", cat="kind", date="txn_date", groups=["Central", "Airport", "Lakeside", "Market"],
         cats=["deposit", "withdrawal", "transfer"], noun_dim="client", noun_fact="transaction", story="a bank"),
    dict(key="streaming", dim="viewers", did="id", dname="name", dgroup="plan", fact="watch_sessions", fid="id", fk="viewer_id",
         amount="minutes", cat="genre", date="watched_on", groups=["free", "basic", "premium", "family"],
         cats=["drama", "comedy", "documentary"], noun_dim="viewer", noun_fact="watch session", story="a streaming service"),
    dict(key="logistics", dim="drivers", did="id", dname="name", dgroup="depot", fact="deliveries", fid="id", fk="driver_id",
         amount="distance_km", cat="priority", date="delivered_on", groups=["Hub1", "Hub2", "Hub3", "Hub4"],
         cats=["express", "standard", "bulk"], noun_dim="driver", noun_fact="delivery", story="a courier company"),
    dict(key="library", dim="members", did="id", dname="name", dgroup="branch", fact="loans", fid="id", fk="member_id",
         amount="late_fee", cat="section", date="loan_date", groups=["Main", "Annex", "Campus", "Kids"],
         cats=["fiction", "science", "history"], noun_dim="member", noun_fact="loan", story="a public library"),
]


def _ddl(t) -> str:

    return (
        f"CREATE TABLE {t['dim']} ({t['did']} INTEGER PRIMARY KEY, {t['dname']} TEXT, {t['dgroup']} TEXT);\n"
        f"CREATE TABLE {t['fact']} ({t['fid']} INTEGER PRIMARY KEY, {t['fk']} INTEGER, {t['amount']} INTEGER, "
        f"{t['cat']} TEXT, {t['date']} TEXT);\n"
    )


def _quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _dataset(t, rng: random.Random, dims: int, facts: int) -> str:
    """INSERT statements: every dataset leaves at least one dimension row without facts and uses distinct amounts."""

    names = rng.sample(FIRST_NAMES, dims)
    with_facts = list(range(1, dims))                                   # the last dimension row never gets facts
    amounts = rng.sample(range(2, 100), facts)

    dim_rows = [f"({i}, {_quote(names[i - 1])}, {_quote(rng.choice(t['groups']))})" for i in range(1, dims + 1)]

    fact_rows = []

    for j in range(1, facts + 1):
        month = rng.choice(range(1, 7))
        day = rng.choice(range(1, 29))
        fact_rows.append(
            f"({j}, {rng.choice(with_facts)}, {amounts[j - 1] * 5}, {_quote(rng.choice(t['cats']))}, "
            f"{_quote('2025-%02d-%02d' % (month, day))})"
        )

    return (
        f"INSERT INTO {t['dim']} VALUES {', '.join(dim_rows)};\n"
        f"INSERT INTO {t['fact']} VALUES {', '.join(fact_rows)};\n"
    )


# ------------------------------------------------------------
# parametric patterns: (difficulty, builder(t) -> title, task, query, ordered, skills)
# ------------------------------------------------------------

def _p_filter_order(t, rng):
    limit = rng.choice([100, 150, 200, 250])
    return ("Filter and sort",
            f"List the {t['fid']} and {t['amount']} of every {t['noun_fact']} whose {t['amount']} is greater than {limit}, "
            f"highest {t['amount']} first (break ties by {t['fid']}).",
            f"SELECT {t['fid']}, {t['amount']} FROM {t['fact']} WHERE {t['amount']} > {limit} ORDER BY {t['amount']} DESC, {t['fid']}",
            True, ["WHERE", "ORDER BY"])


def _p_group_total(t, rng):
    return ("Totals per group",
            f"For each {t['dgroup']} show the {t['dgroup']} and the total {t['amount']} of its {t['noun_dim']}s' {t['noun_fact']}s "
            f"(only groups that have at least one {t['noun_fact']}). Columns: {t['dgroup']}, total.",
            f"SELECT d.{t['dgroup']}, SUM(f.{t['amount']}) FROM {t['dim']} d JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} "
            f"GROUP BY d.{t['dgroup']}",
            False, ["JOIN", "GROUP BY", "SUM"])


def _p_left_join(t, rng):
    return (f"{t['noun_dim'].title()}s without activity",
            f"List the {t['dname']} of every {t['noun_dim']} that has no {t['noun_fact']} at all.",
            f"SELECT d.{t['dname']} FROM {t['dim']} d LEFT JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} WHERE f.{t['fid']} IS NULL",
            False, ["LEFT JOIN", "IS NULL"])


def _p_having(t, rng):
    limit = rng.choice([150, 200, 300])
    return ("Big spenders",
            f"Show the {t['dname']} and the total {t['amount']} of every {t['noun_dim']} whose total {t['amount']} is more than {limit}.",
            f"SELECT d.{t['dname']}, SUM(f.{t['amount']}) FROM {t['dim']} d JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} "
            f"GROUP BY d.{t['did']} HAVING SUM(f.{t['amount']}) > {limit}",
            False, ["GROUP BY", "HAVING"])


def _p_above_category_avg(t, rng):
    return ("Above the category average",
            f"List the {t['fid']} of every {t['noun_fact']} whose {t['amount']} is higher than the average {t['amount']} "
            f"of the {t['noun_fact']}s with the same {t['cat']}.",
            f"SELECT f.{t['fid']} FROM {t['fact']} f WHERE f.{t['amount']} > "
            f"(SELECT AVG(f2.{t['amount']}) FROM {t['fact']} f2 WHERE f2.{t['cat']} = f.{t['cat']})",
            False, ["subquery", "AVG", "correlated subquery"])


def _p_case_bucket(t, rng):
    return ("Size buckets",
            f"Count the {t['noun_fact']}s per size bucket: 'small' ({t['amount']} below 100), 'medium' (100 to 299) and 'large' "
            f"(300 or more). Columns: bucket, count. Only buckets that occur.",
            f"SELECT CASE WHEN {t['amount']} < 100 THEN 'small' WHEN {t['amount']} < 300 THEN 'medium' ELSE 'large' END AS bucket, "
            f"COUNT(*) FROM {t['fact']} GROUP BY bucket",
            False, ["CASE", "GROUP BY"])


def _p_monthly(t, rng):
    return ("Monthly totals",
            f"Show the total {t['amount']} per month. The month is written as YYYY-MM (taken from {t['date']}). Columns: month, total.",
            f"SELECT strftime('%Y-%m', {t['date']}) AS month, SUM({t['amount']}) FROM {t['fact']} GROUP BY month",
            False, ["date functions", "GROUP BY"])


def _p_distinct_count(t, rng):
    return ("Variety per group",
            f"For every {t['noun_dim']} that has {t['noun_fact']}s, show the {t['dname']} and how many DIFFERENT {t['cat']} values appear "
            f"in its {t['noun_fact']}s.",
            f"SELECT d.{t['dname']}, COUNT(DISTINCT f.{t['cat']}) FROM {t['dim']} d JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} "
            f"GROUP BY d.{t['did']}",
            False, ["COUNT DISTINCT", "JOIN"])


def _p_top_per_group(t, rng):
    return ("Best entry per person",
            f"For every {t['noun_dim']} that has {t['noun_fact']}s, show the {t['dname']} and the {t['fid']} of the {t['noun_fact']} with "
            f"the highest {t['amount']}.",
            f"SELECT name, id FROM (SELECT d.{t['dname']} AS name, f.{t['fid']} AS id, ROW_NUMBER() OVER "
            f"(PARTITION BY d.{t['did']} ORDER BY f.{t['amount']} DESC) AS rn FROM {t['dim']} d JOIN {t['fact']} f "
            f"ON f.{t['fk']} = d.{t['did']}) WHERE rn = 1",
            False, ["window functions", "ROW_NUMBER"])


def _p_dense_rank(t, rng):
    return ("Ranking",
            f"Rank the {t['noun_dim']}s that have {t['noun_fact']}s by their total {t['amount']} (highest total = rank 1, equal totals share a "
            f"rank with no gaps, i.e. DENSE_RANK). Columns: {t['dname']}, rank.",
            f"SELECT name, DENSE_RANK() OVER (ORDER BY total DESC) FROM (SELECT d.{t['dname']} AS name, SUM(f.{t['amount']}) AS total "
            f"FROM {t['dim']} d JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} GROUP BY d.{t['did']})",
            False, ["window functions", "DENSE_RANK"])


def _p_running_total(t, rng):
    return ("Running total",
            f"For every {t['noun_dim']}, list each of its {t['noun_fact']}s in date order (ties by {t['fid']}) with the running total of "
            f"{t['amount']} for that {t['noun_dim']}. Columns: {t['dname']}, {t['fid']}, running total. Order the result by {t['dname']}, "
            f"then {t['date']}, then {t['fid']}.",
            f"SELECT d.{t['dname']}, f.{t['fid']}, SUM(f.{t['amount']}) OVER (PARTITION BY d.{t['did']} ORDER BY f.{t['date']}, f.{t['fid']}) "
            f"FROM {t['dim']} d JOIN {t['fact']} f ON f.{t['fk']} = d.{t['did']} ORDER BY d.{t['dname']}, f.{t['date']}, f.{t['fid']}",
            True, ["window functions", "running total"])


def _p_second_highest(t, rng):
    return ("Second highest",
            f"Show the second highest {t['amount']} among all {t['noun_fact']}s (one value).",
            f"SELECT MAX({t['amount']}) FROM {t['fact']} WHERE {t['amount']} < (SELECT MAX({t['amount']}) FROM {t['fact']})",
            False, ["subquery", "MAX"])


PATTERNS = {
    "basic": [_p_filter_order, _p_group_total, _p_left_join],
    "intermediate": [_p_having, _p_above_category_avg, _p_case_bucket, _p_monthly, _p_distinct_count],
    "advanced": [_p_top_per_group, _p_dense_rank, _p_running_total, _p_second_highest],
}

DIFFICULTY_LABEL = {"basic": "easy", "intermediate": "medium", "advanced": "hard"}

BAND_DIFFICULTIES = {
    "fresher": ["basic", "basic", "intermediate", "intermediate", "intermediate"],
    "junior": ["basic", "intermediate", "intermediate", "advanced", "intermediate"],
    "senior": ["intermediate", "advanced", "advanced", "intermediate", "advanced"],
}


def difficulty_slots(band: str, count: int, strong_database: bool = False) -> list[str]:

    slots = list(BAND_DIFFICULTIES.get(band, BAND_DIFFICULTIES["junior"]))

    if strong_database:                                                # a candidate with strong SQL skills is challenged a level higher
        step = {"basic": "intermediate", "intermediate": "advanced", "advanced": "advanced"}
        slots = [step[s] for s in slots]

    return slots[:count]


def _sample_block(t, script: str, expected: str, ordered: bool) -> str:
    """A short, candidate-facing sample: the first rows of each table and the expected result."""

    lines = []

    for statement in script.split("\n"):

        if statement.upper().startswith("INSERT INTO "):

            try:
                table = statement.split()[2]
                values = statement.split(" VALUES ", 1)[1].rstrip(";") if " VALUES " in statement else statement.split(" values ", 1)[1].rstrip(";")
                rows = values.split("), (")
                preview = rows[:EXAMPLE_ROWS_LIMIT]
                text = "(" + "), (".join(r.strip("()") for r in preview) + ")"
                lines.append(f"{table}: {text}" + (" ..." if len(rows) > EXAMPLE_ROWS_LIMIT else ""))
            except (IndexError, ValueError):
                continue                                                # a sample is a convenience, never a blocker

    return "\n".join(lines)


def _make_question(*, title, scenario, task, ddl, datasets, query, ordered, difficulty, skills, technology="SQL"):
    """Server-side assembly: expected rows come from running the reference query on every data set."""

    cases = []

    for index, inserts in enumerate(datasets):

        setup = (ORDERED_DIRECTIVE + "\n" if ordered else "") + ddl + inserts
        expected = run_sql_query(setup, query)

        if expected == NO_ROWS_TEXT and index == 0:
            raise ValueError("the sample data set produces no rows")

        cases.append({
            "stdin": setup,
            "expected_stdout": expected,
            "is_hidden": index != 0,
            "description": "Sample data set" if index == 0 else "Hidden data set",
        })

    sample_setup = cases[0]["stdin"]
    sample_data = _sample_block(None, cases[0]["stdin"].split(ORDERED_DIRECTIVE + "\n", 1)[-1], cases[0]["expected_stdout"], ordered)

    order_note = (
        "The rows must come back in exactly the order described." if ordered
        else "Row order does not matter."
    )

    description = (
        f"{task}\n\nDatabase (SQLite):\n{ddl.strip()}\n\n{order_note} Write ONE SELECT statement (SQLite syntax; "
        f"window functions and CTEs are allowed)."
    )

    return build_question(
        title=title,
        description=description,
        input_specification="The tables above are created and filled with test data before your query runs.",
        output_specification="The rows returned by your query, with the columns in the order the task lists them.",
        difficulty=DIFFICULTY_LABEL.get(difficulty, "medium"),
        allowed_languages=["sql"],
        cases=cases,
        examples=[{"input": sample_data, "output": cases[0]["expected_stdout"]}],
        scenario=scenario,
        requirements=[task],
        skills_assessed=skills,
        technology=technology,
        execution_mode="sql",
        notes="Only read-only SELECT queries run; each test uses a fresh database.",
    )


def parametric_sql_question(difficulty: str, rng: random.Random, used_titles: set | None = None) -> ProgrammingMainQuestionV2:
    """A complete SQL question from random data with computed expected rows."""

    used_titles = used_titles if used_titles is not None else set()

    for _attempt in range(40):

        theme = rng.choice(THEMES)
        pattern = rng.choice(PATTERNS[difficulty])
        title, task, query, ordered, skills = pattern(theme, rng)

        key = (title, theme["key"])

        if key in used_titles:
            continue

        ddl = _ddl(theme)

        try:

            datasets = [
                _dataset(theme, rng, rng.choice([4, 5]), rng.choice([6, 7, 8])),
                _dataset(theme, rng, rng.choice([6, 7]), rng.choice([10, 12, 14])),
                _dataset(theme, rng, rng.choice([7, 8]), rng.choice([12, 15, 18])),
            ]

            question = _make_question(
                title=f"{title} ({theme['noun_dim']}s and {theme['noun_fact']}s)",
                scenario=f"You work with the database of {theme['story']}.",
                task=task, ddl=ddl, datasets=datasets, query=query, ordered=ordered, difficulty=difficulty, skills=skills,
            )

            used_titles.add(key)
            return question

        except (ValueError, RuntimeError) as exc:
            logger.debug("parametric SQL question rejected: %s", exc)
            continue

    raise RuntimeError("could not build a parametric SQL question")


# ------------------------------------------------------------
# Gemini path
# ------------------------------------------------------------

def _gemini_prompt(job, band: str, slots: list[str], candidate_skills: list[str], variation: str) -> str:

    slot_lines = "\n".join(
        f"{index}. difficulty={difficulty}; concept={rng_choice}"
        for index, (difficulty, rng_choice) in enumerate(slots, start=1)
    )

    return f"""
You are a senior database engineer writing a SQL practical test for a real hiring pipeline.

JOB TITLE: {getattr(job, 'title', '')}
JOB DESCRIPTION: {getattr(job, 'description', '') or 'Not specified'}
REQUIRED SKILLS: {getattr(job, 'required_skills', '') or 'Not specified'}
TECHNOLOGIES: {getattr(job, 'technologies', '') or 'Not specified'}
CANDIDATE EXPERIENCE BAND: {band}
CANDIDATE SKILLS FROM THE RESUME: {', '.join(candidate_skills) or 'Not specified'}
VARIATION CODE: {variation}  (use it to pick fresh business domains, table names, column names and data so this test differs from every other candidate's)

Write EXACTLY {len(slots)} questions, one per slot below. Each is a realistic business scenario in a domain that fits the company/job
(or another fresh domain), with its own small schema.
SLOTS:
{slot_lines}

For every question return:
- "title": short name
- "scenario": 1-2 sentences of business context
- "task": what result the SELECT must return, precise about columns, their order and any ordering
- "create_sql": the CREATE TABLE statements (SQLite syntax, 2-3 tables, PRIMARY KEY on ids)
- "datasets": EXACTLY 3 arrays of INSERT statements (strings). Dataset 1 is the small sample the candidate sees (4-8 rows in the main table);
  datasets 2 and 3 are HIDDEN and must include edge cases: NULL values, ties, rows with no match in the other table, duplicates.
- "reference_query": ONE correct SQLite SELECT for the task, valid on all 3 datasets and returning at least one row on the sample dataset
- "ordered": true only if the task demands a specific row order (then reference_query must fully order the rows, ties included)
- "difficulty": basic | intermediate | advanced
- "skills_assessed": short list

RULES: SQLite dialect only (no ILIKE, no RETURNING, no stored procedures); window functions and CTEs are fine. INSERT statements must match
create_sql exactly. Do not write the answer into the task. Return ONLY JSON: {{"questions": [ ... ]}}
""".strip()


def _question_from_gemini(raw: dict) -> ProgrammingMainQuestionV2:

    ddl = str(raw["create_sql"]).strip()
    if not ddl.endswith(";"):
        ddl += ";"
    ddl += "\n"

    datasets = []

    for block in raw["datasets"][:3]:
        text = "\n".join(str(s).strip() if str(s).strip().endswith(";") else str(s).strip() + ";" for s in block) + "\n"
        datasets.append(text)

    if len(datasets) < 3:
        raise ValueError("three datasets required")

    difficulty = str(raw.get("difficulty", "intermediate")).lower()

    if difficulty not in DIFFICULTY_LABEL:
        difficulty = "intermediate"

    return _make_question(
        title=str(raw["title"])[:120],
        scenario=str(raw.get("scenario", ""))[:800] or None,
        task=str(raw["task"]),
        ddl=ddl,
        datasets=datasets,
        query=str(raw["reference_query"]),
        ordered=bool(raw.get("ordered", False)),
        difficulty=difficulty,
        skills=[str(s) for s in raw.get("skills_assessed", [])][:6] or ["SQL"],
    )


def generate_sql_questions(
    count: int,
    job,
    band: str,
    candidate_skills: list[str],
    strong_database: bool = False,
    use_gemini: bool = True,
    rng: random.Random | None = None,
) -> tuple[list[ProgrammingMainQuestionV2], str]:
    """`count` different SQL questions and where they came from ("gemini" only if every one did)."""

    rng = rng or random.Random()
    difficulties = difficulty_slots(band, count, strong_database)
    questions: list[ProgrammingMainQuestionV2] = []
    from_gemini = 0

    if use_gemini:

        try:

            from backend.app.services.assessment.gemini_client import generate_json_content

            concepts = [rng.choice([f.__name__[3:].replace("_", " ") for f in PATTERNS[d]]) for d in difficulties]
            prompt = _gemini_prompt(job, band, list(zip(difficulties, concepts)), candidate_skills, uuid.uuid4().hex[:10])
            parsed = json.loads(generate_json_content(prompt, retries=2))

            for raw in parsed.get("questions", [])[:count]:

                try:
                    questions.append(_question_from_gemini(raw))
                    from_gemini += 1
                except Exception as exc:
                    logger.warning("A Gemini SQL question was rejected: %s", exc)

        except Exception as exc:
            logger.warning("Gemini SQL generation unavailable (%s); using parametric SQL questions.", exc)

    used = set()

    for slot in range(len(questions), count):
        questions.append(parametric_sql_question(difficulties[slot], rng, used))

    return questions, ("gemini" if from_gemini == count else "fallback")

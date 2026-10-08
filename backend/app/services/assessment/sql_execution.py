import re
import sqlite3
from dataclasses import dataclass


# ============================================================
# JOBPILOT AI — SQL EXECUTION CAPABILITY (Programming Engine V2
# task, Step 10)
#
# SQL is explicitly NOT routed through language_runner.py /
# execute_code() / any LanguageRunner. A SQL "submission" is not
# source code that produces stdout to diff against an
# expected_stdout string — it is a QUERY evaluated against a
# fixture database, and its RESULT SET is compared against an
# expected result set. Treating that as a language-neutral
# stdin/stdout ExecutableTestCase would be a category error (the
# task's own explicit instruction: "Do not route SQL through
# Python").
#
# THIS IS A REAL, WORKING EVALUATOR — not a placeholder. It
# creates a genuinely fresh, isolated in-memory SQLite database
# per question (sqlite3.connect(":memory:")), loads the
# question's own fixture schema+seed data, runs the candidate's
# query against it, and compares the actual returned rows against
# the expected result. Nothing here calls Gemini, nothing here
# calls a LanguageRunner, and the candidate's query never touches
# jobpilot.db or any other real database — a fresh in-memory
# connection is created and discarded for every single evaluation.
#
# SAFETY: a candidate query is restricted to read-only statements.
# A single leading statement is required (no `;`-separated
# multi-statement injection), and it must start with SELECT or
# WITH (a CTE feeding a SELECT) — DROP/DELETE/UPDATE/INSERT/
# ATTACH/PRAGMA/etc. against the fixture are rejected outright
# before sqlite ever sees them. This is deliberately conservative
# (it will also reject some exotic-but-legitimate read-only SQL
# forms) in favor of never letting a candidate mutate or escape
# the fixture.
# ============================================================


class UnsafeSQLError(Exception):
    """Raised when a candidate's SQL fails the read-only safety check."""


_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|"
    r"PRAGMA|VACUUM|REINDEX|REPLACE|TRIGGER)\b",
    re.IGNORECASE,
)


def _validate_read_only(candidate_query: str) -> None:

    stripped = candidate_query.strip().rstrip(";")

    if not stripped:
        raise UnsafeSQLError("Empty query.")

    if ";" in stripped:
        raise UnsafeSQLError(
            "Multiple statements are not permitted — submit exactly "
            "one SELECT statement."
        )

    first_word = stripped.split(None, 1)[0].upper()

    if first_word not in ("SELECT", "WITH"):
        raise UnsafeSQLError(
            "Only a single read-only SELECT (optionally preceded by "
            "a WITH clause) is permitted."
        )

    if _FORBIDDEN_KEYWORDS.search(stripped):
        raise UnsafeSQLError(
            "The query contains a keyword that is not permitted in "
            "a read-only submission."
        )


@dataclass(frozen=True)
class SQLFixture:
    """
    A question's isolated database fixture. `setup_sql` is one or
    more `;`-separated DDL/seed statements (CREATE TABLE ...,
    INSERT INTO ...) executed once, fresh, per evaluation.
    """

    setup_sql: str


@dataclass(frozen=True)
class SQLExecutionResult:

    success: bool
    error_type: str  # "success" | "unsafe_query" | "sql_error" | "row_mismatch"
    message: str | None
    actual_rows: list[tuple] | None
    expected_rows: list[tuple] | None


def run_sql_submission(
    fixture: SQLFixture,
    candidate_query: str,
    expected_rows: list[tuple],
    order_sensitive: bool = False,
) -> SQLExecutionResult:
    """
    Runs candidate_query against a FRESH, isolated in-memory SQLite
    database seeded from fixture.setup_sql, and compares the
    result to expected_rows. Never raises for a candidate mistake
    (bad SQL, wrong result) — only for a genuine caller error
    (e.g. a malformed fixture) would this propagate, and even that
    is caught and reported as a normal SQLExecutionResult below.
    """

    try:
        _validate_read_only(candidate_query)
    except UnsafeSQLError as exc:
        return SQLExecutionResult(
            success=False,
            error_type="unsafe_query",
            message=str(exc),
            actual_rows=None,
            expected_rows=expected_rows,
        )

    conn = sqlite3.connect(":memory:")

    try:
        conn.executescript(fixture.setup_sql)

        cursor = conn.execute(candidate_query)
        actual_rows = [tuple(row) for row in cursor.fetchall()]

    except sqlite3.Error as exc:
        return SQLExecutionResult(
            success=False,
            error_type="sql_error",
            message=str(exc),
            actual_rows=None,
            expected_rows=expected_rows,
        )

    finally:
        conn.close()

    normalized_expected = list(expected_rows)
    normalized_actual = actual_rows

    if not order_sensitive:
        normalized_expected = sorted(normalized_expected, key=repr)
        normalized_actual = sorted(normalized_actual, key=repr)

    if normalized_actual == normalized_expected:
        return SQLExecutionResult(
            success=True,
            error_type="success",
            message=None,
            actual_rows=actual_rows,
            expected_rows=expected_rows,
        )

    return SQLExecutionResult(
        success=False,
        error_type="row_mismatch",
        message="The query's result did not match the expected rows.",
        actual_rows=actual_rows,
        expected_rows=expected_rows,
    )

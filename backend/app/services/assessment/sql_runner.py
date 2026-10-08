import sqlite3
import time

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner


# ============================================================
# JOBPILOT AI - SQL RUNNER (Programming Engine V2)
#
# Runs ONE candidate SELECT query against a throw-away in-memory SQLite database
# that is built from the test case's `stdin` (a setup script: CREATE TABLE +
# INSERT statements). The result rows are turned into canonical text, so a test
# case is just  stdin (setup script)  ->  expected_stdout (the reference query's
# rows)  and is compared exactly like any other language's stdout.
#
# Safety (defence in depth, no subprocess needed because SQLite is in-process):
#   * the setup script is limited to CREATE TABLE / CREATE INDEX / INSERT
#   * the candidate query is limited to read-only SELECT / WITH ... SELECT
#     (a sqlite authorizer denies everything else: PRAGMA, ATTACH, DDL, DML)
#   * exactly one statement, a wall-clock limit and a row limit
#   * no file, no network, nothing persisted
#
# The dialect is SQLite (window functions and CTEs included) and questions say so.
# ============================================================

RUNNER_VERSION = f"sqlite-{sqlite3.sqlite_version}-jobpilot-1"

MAX_ROWS = 2000
TIME_LIMIT_SECONDS = 2.0
ORDERED_DIRECTIVE = "-- ORDERED"
NO_ROWS_TEXT = "(no rows)"                      # an empty result still has to be comparable text

_SETUP_ALLOWED = {
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION,
}

_QUERY_ALLOWED = {
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
}


def _authorizer(allowed, schema_updates=False):

    def check(action, arg1, arg2, dbname, source):

        if action in allowed:
            return sqlite3.SQLITE_OK

        # CREATE TABLE itself updates SQLite's own catalog table: only that update is let through
        if schema_updates and action == sqlite3.SQLITE_UPDATE and arg1 in ("sqlite_master", "sqlite_schema", "sqlite_temp_master"):
            return sqlite3.SQLITE_OK

        return sqlite3.SQLITE_DENY

    return check


def _cell(value) -> str:

    if value is None:
        return "NULL"

    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return ("%.6f" % value).rstrip("0").rstrip(".")

    if isinstance(value, bytes):
        return value.hex()

    return str(value).replace("\r", " ").replace("\n", " ")


class SqlError(Exception):
    """A candidate-caused failure (bad SQL, forbidden statement, too slow, too many rows)."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def run_sql_query(setup_script: str, query: str) -> str:
    """
    Builds the database from `setup_script`, runs `query`, returns the canonical result text.
    Raises SqlError for anything the candidate's query caused. A broken setup script raises RuntimeError.
    """

    ordered = (setup_script or "").lstrip().startswith(ORDERED_DIRECTIVE)

    connection = sqlite3.connect(":memory:")

    try:

        connection.set_authorizer(_authorizer(_SETUP_ALLOWED, schema_updates=True))

        try:
            connection.executescript(setup_script or "")
        except sqlite3.Error as exc:
            raise RuntimeError(f"invalid test data script: {exc}") from exc

        connection.set_authorizer(_authorizer(_QUERY_ALLOWED))

        text = (query or "").strip()

        while text.startswith("--"):                                    # leading comment lines
            text = text.split("\n", 1)[1].strip() if "\n" in text else ""

        if not text:
            raise SqlError("runtime_error", "Write a SELECT query.")

        if text.rstrip(";").strip().split(None, 1)[0].lower() not in ("select", "with"):
            raise SqlError("runtime_error", "Only SELECT queries are allowed.")

        started = time.perf_counter()

        def stop_when_slow():
            return 1 if time.perf_counter() - started > TIME_LIMIT_SECONDS else 0

        connection.set_progress_handler(stop_when_slow, 20000)

        try:
            cursor = connection.execute(text)
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                raise SqlError("timeout", "The query took too long.") from exc
            if "not authorized" in message.lower():
                raise SqlError("runtime_error", "Only read-only SELECT queries are allowed.") from exc
            kind = "compile_error" if "syntax" in message.lower() or "no such" in message.lower() else "runtime_error"
            raise SqlError(kind, message[:300]) from exc
        except (sqlite3.Warning, sqlite3.ProgrammingError) as exc:
            raise SqlError("runtime_error", "Run exactly one SELECT statement."[:300]) from exc
        except sqlite3.DatabaseError as exc:
            raise SqlError("runtime_error", str(exc)[:300]) from exc

        if len(rows) > MAX_ROWS:
            raise SqlError("output_limit_exceeded", f"The query returned more than {MAX_ROWS} rows.")

        lines = ["|".join(_cell(value) for value in row) for row in rows]

        if not ordered:
            lines.sort()

        return "\n".join(lines) if lines else NO_ROWS_TEXT

    finally:
        connection.close()


class SqlRunner:

    language = "sql"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return True

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:

            stdout = run_sql_query(request.stdin, request.source_code)

        except SqlError as exc:

            return ExecutionResult(
                success=False, stderr=exc.message, exit_code=1, timed_out=exc.error_type == "timeout",
                execution_time_ms=elapsed(), error_type=exc.error_type, message=exc.message,
                language="sql", runner_version=RUNNER_VERSION,
            )

        except Exception:

            return ExecutionResult(
                success=False, exit_code=1, execution_time_ms=elapsed(), error_type="sandbox_error",
                message="The SQL sandbox could not run this test.", language="sql", runner_version=RUNNER_VERSION,
            )

        return ExecutionResult(
            success=True, stdout=stdout, exit_code=0, execution_time_ms=elapsed(),
            error_type="success", language="sql", runner_version=RUNNER_VERSION,
        )


register_runner(SqlRunner())

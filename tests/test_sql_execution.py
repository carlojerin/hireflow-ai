from backend.app.services.assessment.sql_execution import SQLFixture, run_sql_submission

_FIXTURE = SQLFixture(
    setup_sql="""
        CREATE TABLE customers (id INTEGER, name TEXT);
        CREATE TABLE transactions (id INTEGER, customer_id INTEGER, amount INTEGER);
        INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob');
        INSERT INTO transactions VALUES
            (1, 1, 100), (2, 1, 50), (3, 2, 30);
    """
)


def test_correct_query_scores_success():
    query = (
        "SELECT c.name FROM customers c "
        "JOIN transactions t ON t.customer_id = c.id "
        "GROUP BY c.id ORDER BY SUM(t.amount) DESC LIMIT 1"
    )
    result = run_sql_submission(_FIXTURE, query, expected_rows=[("Alice",)])
    assert result.success is True
    assert result.error_type == "success"


def test_wrong_result_is_row_mismatch_not_success():
    query = "SELECT name FROM customers ORDER BY id"
    result = run_sql_submission(_FIXTURE, query, expected_rows=[("Alice",)])
    assert result.success is False
    assert result.error_type == "row_mismatch"


def test_invalid_sql_is_sql_error_not_a_crash():
    result = run_sql_submission(
        _FIXTURE, "SELECT this is not valid sql", expected_rows=[],
    )
    assert result.success is False
    assert result.error_type == "sql_error"


def test_drop_table_is_rejected_as_unsafe():
    result = run_sql_submission(_FIXTURE, "DROP TABLE customers", expected_rows=[])
    assert result.success is False
    assert result.error_type == "unsafe_query"


def test_delete_is_rejected_as_unsafe():
    result = run_sql_submission(_FIXTURE, "DELETE FROM customers", expected_rows=[])
    assert result.success is False
    assert result.error_type == "unsafe_query"


def test_multi_statement_injection_is_rejected():
    result = run_sql_submission(
        _FIXTURE,
        "SELECT * FROM customers; DROP TABLE customers;",
        expected_rows=[],
    )
    assert result.success is False
    assert result.error_type == "unsafe_query"


def test_fixture_is_fresh_and_isolated_per_call():
    # a prior call's rejected DROP must never have actually taken
    # effect -- this call must still see both original customers.
    query = "SELECT COUNT(*) FROM customers"
    result = run_sql_submission(_FIXTURE, query, expected_rows=[(2,)])
    assert result.success is True


def test_with_clause_is_permitted():
    query = "WITH t AS (SELECT name FROM customers) SELECT COUNT(*) FROM t"
    result = run_sql_submission(_FIXTURE, query, expected_rows=[(2,)])
    assert result.success is True

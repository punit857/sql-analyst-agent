import pytest

from app.agent.validator import MAX_LIMIT, ValidationError, validate_sql

ALLOWED = ["orders", "customers", "products"]


def test_plain_select_gets_a_limit():
    safe, notes = validate_sql("SELECT * FROM orders", ALLOWED)
    assert safe.upper().endswith(f"LIMIT {MAX_LIMIT}")
    assert notes == [f"LIMIT {MAX_LIMIT} added"]


def test_small_limit_is_kept():
    safe, notes = validate_sql("SELECT * FROM orders LIMIT 5", ALLOWED)
    assert safe.upper().endswith("LIMIT 5")
    assert notes == []


def test_huge_limit_is_lowered():
    safe, notes = validate_sql("SELECT * FROM orders LIMIT 99999", ALLOWED)
    assert "99999" not in safe
    assert safe.upper().endswith(f"LIMIT {MAX_LIMIT}")
    assert notes == [f"LIMIT lowered to {MAX_LIMIT}"]


def test_trailing_semicolon_is_accepted():
    safe, _ = validate_sql("SELECT * FROM orders;", ALLOWED)
    assert ";" not in safe


def test_table_names_are_case_insensitive():
    validate_sql("SELECT * FROM ORDERS", ALLOWED)


def test_join_is_allowed():
    sql = ("SELECT c.name, COUNT(*) FROM orders o JOIN customers c "
           "ON c.customer_id = o.customer_id GROUP BY c.name")
    validate_sql(sql, ALLOWED)


def test_cte_is_allowed():
    validate_sql("WITH t AS (SELECT * FROM orders) SELECT * FROM t", ALLOWED)


def test_union_is_allowed():
    safe, _ = validate_sql(
        "SELECT customer_id FROM orders UNION SELECT customer_id FROM customers", ALLOWED)
    assert "UNION" in safe.upper()


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders VALUES (1, 1, 'a', 'b', 'c')",
    "CREATE TABLE x (a INTEGER)",
    "ALTER TABLE orders ADD COLUMN z TEXT",
    "PRAGMA table_info(orders)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1; SELECT 2",
    "hello there",
])
def test_non_select_or_multiple_statements_are_blocked(sql):
    with pytest.raises(ValidationError):
        validate_sql(sql, ALLOWED)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM sqlite_master",
    "SELECT * FROM order_items",
    "SELECT * FROM orders WHERE customer_id IN (SELECT customer_id FROM sqlite_master)",
    "WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x",
    "SELECT * FROM orders o JOIN secrets s ON s.id = o.customer_id",
])
def test_tables_outside_the_allowlist_are_blocked(sql):
    with pytest.raises(ValidationError):
        validate_sql(sql, ALLOWED)


@pytest.mark.parametrize("sql", ["", "   ", None, ";"])
def test_empty_input_is_blocked(sql):
    with pytest.raises(ValidationError):
        validate_sql(sql, ALLOWED)
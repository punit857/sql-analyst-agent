import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import DEMO_DBS, open_readonly, inspect_schema
from app.agent.validator import validate_sql, ValidationError
from app.agent.executor import execute_sql, rows_to_text, ExecutionError

conn = open_readonly(DEMO_DBS["sales"])
tables = list(inspect_schema(conn).keys())

print("===== Part 1: normal path (validator, then executor)")
cases = [
    ("OK count", "SELECT COUNT(*) AS n FROM orders"),
    ("OK group", "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC"),
    ("OK many rows (expect truncated=True)", "SELECT * FROM order_items"),
    ("BAD column", "SELECT no_such_column FROM orders"),
    ("SLOW query (about 5 s)", "SELECT COUNT(*) FROM order_items a, order_items b, order_items c"),
]
for label, sql in cases:
    print("----", label)
    try:
        safe, notes = validate_sql(sql, tables)
        result = execute_sql(conn, safe, tables)
        print(rows_to_text(result, max_rows=3))
        print(f"rows={result['row_count']} truncated={result['truncated']} time={result['latency_ms']}ms")
    except ValidationError as e:
        print("blocked by validator:", e)
    except ExecutionError as e:
        print("execution error:", e)

print()
print("===== Part 2: bypass the validator on purpose; the executor must still refuse")
attacks = [
    ("read sqlite_master", "SELECT * FROM sqlite_master"),
    ("pragma function", "SELECT * FROM pragma_table_info('orders')"),
    ("memory bomb", "SELECT zeroblob(1000000000)"),
    ("load extension", "SELECT load_extension('x')"),
    ("delete", "DELETE FROM orders"),
    ("drop", "DROP TABLE orders"),
]
for label, sql in attacks:
    try:
        execute_sql(conn, sql, tables)
        print(f"[ALLOWED - PROBLEM] {label}")
    except ExecutionError as e:
        print(f"[refused] {label}: {e}")

print()
print("===== Part 3: normal query still works after the attacks")
result = execute_sql(conn, "SELECT COUNT(*) AS n FROM orders", tables)
print(result["rows"])

conn.close()
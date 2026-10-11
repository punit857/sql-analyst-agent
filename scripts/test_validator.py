import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.validator import validate_sql, ValidationError

ALLOWED = ["orders", "customers", "products"]

cases = [
    ("OK plain select", "SELECT * FROM orders"),
    ("OK with limit", "SELECT * FROM orders LIMIT 5"),
    ("OK join", "SELECT c.name, COUNT(*) FROM orders o JOIN customers c ON c.customer_id=o.customer_id GROUP BY c.name"),
    ("OK cte", "WITH t AS (SELECT * FROM orders) SELECT * FROM t"),
    ("BAD delete", "DELETE FROM orders"),
    ("BAD drop", "DROP TABLE orders"),
    ("BAD two statements", "SELECT 1; DROP TABLE orders"),
    ("BAD unknown table", "SELECT * FROM sqlite_master"),
    ("BAD other table", "SELECT * FROM order_items"),
    ("BAD pragma", "PRAGMA table_info(orders)"),
    ("BAD attach", "ATTACH DATABASE 'x.db' AS x"),
    ("BAD insert", "INSERT INTO orders VALUES (1,1,'a','b','c')"),
    ("BAD garbage", "hello there"),
    ("BAD big limit", "SELECT * FROM orders LIMIT 99999"),
]

for label, sql in cases:
    try:
        safe, notes = validate_sql(sql, ALLOWED)
        print(f"[allowed] {label}: {safe}  {notes}")
    except ValidationError as e:
        print(f"[blocked] {label}: {e}")
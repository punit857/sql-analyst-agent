import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import csv_loader
from app.agent.db import open_serialized
from app.agent.executor import ExecutionError, execute_sql
from app.agent.validator import ValidationError, validate_sql
from app.csv_loader import CsvError, load_csv

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def load(text, enc="utf-8"):
    return load_csv(text if isinstance(text, bytes) else text.encode(enc))


def rows(data, sql):
    conn = open_serialized(data)
    try:
        return [list(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def types(profile):
    return {c["name"]: c["type"] for c in profile["columns"]}


def fails(raw, fragment):
    try:
        load(raw)
        return False
    except CsvError as e:
        return fragment.lower() in str(e).lower()


d, p = load("Name,Age,Score\nAnn,30,1.5\nBob,25,2\n")
check("basic: 2 rows", p["rows"] == 2 and rows(d, "SELECT COUNT(*) FROM data") == [[2]])
check("basic: types text, integer, decimal", types(p) == {"name": "TEXT", "age": "INTEGER", "score": "REAL"})
check("integers are real integers in SQLite", rows(d, "SELECT typeof(age) FROM data LIMIT 1") == [["integer"]])
check("sum works on numbers", rows(d, "SELECT SUM(age) FROM data") == [[55]])

d, p = load("a;b;c\n1;2;3\n")
check("semicolon delimiter", p["delimiter"] == ";" and len(p["columns"]) == 3)
d, p = load("a\tb\n1\t2\n")
check("tab delimiter", p["delimiter"] == "\t" and len(p["columns"]) == 2)
d, p = load("name\nAnn\nBob\n")
check("single column file", len(p["columns"]) == 1 and p["rows"] == 2)

d, p = load("Name,City\nZoë,Zürich\n", enc="cp1252")
check("windows-1252 text decoded", p["encoding"] == "windows-1252" and rows(d, "SELECT name FROM data") == [["Zoë"]])
check("non-utf8 gives a warning", len(p["warnings"]) >= 1)
d, p = load("a,b\n1,2\n", enc="utf-16")
check("utf-16 file read", p["rows"] == 1)
d, p = load(b"\xef\xbb\xbfname,age\nAnn,3\n")
check("BOM removed from first header", p["columns"][0]["name"] == "name")

d, p = load("Order ID, order id ,,2nd col,Group\n1,2,3,4,5\n")
check("header cleanup", [c["name"] for c in p["columns"]] ==
      ["order_id", "order_id_2", "column_3", "c_2nd_col", "group_col"])
check("original header kept for display", p["columns"][0]["original"] == "Order ID")

d, p = load("zip\n01234\n02134\n")
check("leading zeros stay text", types(p) == {"zip": "TEXT"} and rows(d, "SELECT zip FROM data LIMIT 1") == [["01234"]])
d, p = load("id\n12345678901234567890\n")
check("20-digit ids stay text", types(p) == {"id": "TEXT"})
d, p = load("id\n999999999999999999\n")
check("18-digit integers stay integer", types(p) == {"id": "INTEGER"})
d, p = load("x\n1\n2.5\n")
check("mixed integers and decimals are decimal", types(p) == {"x": "REAL"} and rows(d, "SELECT x FROM data") == [[1.0], [2.5]])

d, p = load("d\n2024-01-05\n2024-02-10\n")
check("ISO dates detected", types(p) == {"d": "DATE_TEXT"})
check("strftime works on dates", rows(d, "SELECT strftime('%Y-%m', d) FROM data") == [["2024-01"], ["2024-02"]])
d, p = load("order_date\n05/01/2024\n06/01/2024\n")
check("non-ISO dates stay text with a warning", types(p) == {"order_date": "TEXT"} and any("YYYY-MM-DD" in w for w in p["warnings"]))
d, p = load("d\n2024-13-45\n")
check("impossible date stays text", types(p) == {"d": "TEXT"})

d, p = load("a,b\n1,\n,x\n")
check("blank cells become NULL", rows(d, "SELECT COUNT(*) FROM data WHERE b IS NULL") == [[1]] and types(p)["a"] == "INTEGER")

d, p = load("a,b,c\n1,2\n1,2,3,4\n5,6,7\n")
check("ragged rows loaded with a warning", p["rows"] == 3 and len(p["warnings"]) == 1)
check("short row padded with NULL", rows(d, "SELECT COUNT(*) FROM data WHERE c IS NULL") == [[1]])
d, p = load("a,b\n1,2\n\n\n3,4\n")
check("blank lines skipped", p["rows"] == 2)
d, p = load('name,note\n"Ann","line1\nline2"\n"Bob","a,b"\n')
check("quoted commas and newlines", p["rows"] == 2 and rows(d, "SELECT note FROM data WHERE name='Bob'") == [["a,b"]])

check("empty file rejected", fails(b"", "empty"))
check("header-only rejected", fails("a,b\n", "no data"))
check("binary file rejected", fails(b"a,b\n1,\x00\x01\n", "text"))

old = csv_loader.MAX_ROWS
csv_loader.MAX_ROWS = 3
try:
    check("too many rows rejected", fails("a\n1\n2\n3\n4\n", "rows"))
finally:
    csv_loader.MAX_ROWS = old
old = csv_loader.MAX_COLS
csv_loader.MAX_COLS = 3
try:
    check("too many columns rejected", fails("a,b,c,d\n1,2,3,4\n", "columns"))
finally:
    csv_loader.MAX_COLS = old
old = csv_loader.MAX_BYTES
csv_loader.MAX_BYTES = 10
try:
    check("oversized file rejected", fails("a,b\n1,2\n3,4\n5,6\n", "larger"))
finally:
    csv_loader.MAX_BYTES = old

d, p = load("a,b\n1,2\n")
conn = open_serialized(d)
try:
    conn.execute("DELETE FROM data")
    check("uploaded database is read-only", False)
except sqlite3.Error:
    check("uploaded database is read-only", True)
for sql in ("SELECT * FROM sqlite_master", "SELECT * FROM pragma_table_info('data')", "DELETE FROM data"):
    try:
        execute_sql(conn, sql, ["data"])
        check("executor refuses: " + sql, False)
    except ExecutionError:
        check("executor refuses: " + sql, True)
check("normal query works on upload", execute_sql(conn, "SELECT COUNT(*) AS n FROM data", ["data"])["rows"] == [[1]])
conn.close()

for col in ("date", "name", "value", "year", "month", "type"):
    try:
        validate_sql(f"SELECT {col} FROM data GROUP BY {col}", ["data"])
        check(f"validator accepts a column called '{col}'", True)
    except ValidationError as e:
        check(f"validator accepts a column called '{col}' ({e})", False)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
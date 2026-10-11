import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.nodes import result_facts

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def res(cols, rows, truncated=False):
    return {"columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated}


f = result_facts(res(["month", "profit"],
                     [["2024-08", 5.0], ["2024-09", 9.5], ["2024-10", 12.25], ["2024-11", 11.0]]))
check("row count stated", "Number of rows in the result: 4" in f)
check("lowest with label", "lowest 5 at 2024-08" in f)
check("highest is October, not September", "highest 12.25 at 2024-10" in f)
check("first and last", "first 5 at 2024-08" in f and "last 11 at 2024-11" in f)

check("fewer than 3 rows gives no facts", result_facts(res(["a", "b"], [["x", 1], ["y", 2]])) == "")

f = result_facts(res(["order_id", "total"], [[1, 30.0], [2, 10.0], [3, 20.0]]))
check("id column skipped", "order_id:" not in f and "total: lowest 10" in f)
check("no text column falls back to row numbers", "highest 30 at row 1" in result_facts(res(["a", "b"], [[1, 30], [2, 10], [3, 20]])).replace("a: lowest 1 at row 1; highest 3 at row 3; first 1 at row 1; last 3 at row 3\n", ""))

f = result_facts(res(["name", "x"], [["a", 1], ["b", None], ["c", 3]]))
check("nulls ignored", "x: lowest 1 at a; highest 3 at c" in f)
f = result_facts(res(["name", "x"], [["a", 1], ["b", "oops"], ["c", 3]]))
check("mixed text and numbers column skipped", "x:" not in f)
f = result_facts(res(["name", "x"], [["a", 1], ["b", 2], ["c", 3]], truncated=True))
check("truncation is mentioned", "cut off" in f)
f = result_facts(res(["name", "x"], [["a", 5552108.680000001], ["b", 1.0], ["c", 2.0]]))
check("float noise removed", "5552108.68" in f and "680000001" not in f)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
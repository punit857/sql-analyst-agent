import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.chart_spec import validate_chart_spec
from app.agent.nodes import find_ungrounded_numbers

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def res(columns, rows, truncated=False):
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


cats = res(["category", "revenue"], [["A", 10.5], ["B", 7], ["C", 3]])
good = {"type": "bar", "x": "category", "y": "revenue", "title": "Revenue"}

check("valid bar chart accepted", validate_chart_spec(good, cats) == good)
check("unknown chart type rejected", validate_chart_spec({**good, "type": "radar"}, cats) is None)
check("unknown column rejected", validate_chart_spec({**good, "y": "nope"}, cats) is None)
check("x equal to y rejected", validate_chart_spec({**good, "y": "category"}, cats) is None)
check("text y column rejected", validate_chart_spec({**good, "x": "revenue", "y": "category"}, cats) is None)
check("single row rejected", validate_chart_spec(good, res(["category", "revenue"], [["A", 1]])) is None)
check("truncated result rejected", validate_chart_spec(good, res(["category", "revenue"], cats["rows"], True)) is None)
check("none or non-dict spec rejected", validate_chart_spec(None, cats) is None and validate_chart_spec("bar", cats) is None)
check("list as column name rejected", validate_chart_spec({**good, "x": ["category"]}, cats) is None)
check("null in y rejected", validate_chart_spec(good, res(["category", "revenue"], [["A", 1], ["B", None]])) is None)
many = res(["k", "v"], [[str(i), i] for i in range(9)])
check("pie with 9 slices rejected", validate_chart_spec({"type": "pie", "x": "k", "y": "v"}, many) is None)
check("bar with 9 bars accepted", validate_chart_spec({"type": "bar", "x": "k", "y": "v"}, many) is not None)
neg = res(["k", "v"], [["a", 5], ["b", -2]])
check("pie with negative value rejected", validate_chart_spec({"type": "pie", "x": "k", "y": "v"}, neg) is None)
sc = res(["a", "b"], [[1, 2.5], [2, 3.5]])
check("scatter of two numeric columns accepted", validate_chart_spec({"type": "scatter", "x": "a", "y": "b"}, sc) is not None)
check("scatter with text x rejected", validate_chart_spec({"type": "scatter", "x": "category", "y": "revenue"}, cats) is None)
check("title is cut to 80 characters", len(validate_chart_spec({**good, "title": "t" * 500}, cats)["title"]) == 80)

r = res(["category", "revenue"], [["Fashion", 9199941.68], ["Beauty", 6887652.21]])
q = "revenue by category in 2024"
check("exact figure is grounded", find_ungrounded_numbers("Fashion earned 9,199,941.68.", q, r) == [])
check("rounded figure is grounded", find_ungrounded_numbers("Fashion earned about 9199941.7.", q, r) == [])
check("integer rounding is grounded", find_ungrounded_numbers("Fashion earned 9199942.", q, r) == [])
check("year from the question is allowed", find_ungrounded_numbers("In 2024 Fashion led.", q, r) == [])
check("row count is allowed", find_ungrounded_numbers("There are 2 categories.", q, r) == [])
check("invented figure is caught", find_ungrounded_numbers("Fashion earned 12000000.", q, r) == [12000000.0])
check("wrongly rounded figure is caught", find_ungrounded_numbers("Fashion earned 9100000.", q, r) == [9100000.0])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
CHART_TYPES = {"bar", "line", "pie", "scatter"}
MAX_POINTS = 50
MAX_PIE_SLICES = 8


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_chart_spec(spec, result):
    """Return a clean chart spec, or None if a table should be shown instead."""
    if not isinstance(spec, dict) or spec.get("type") not in CHART_TYPES:
        return None
    if result.get("truncated"):
        return None  # a chart of partial data would mislead

    chart_type, x, y = spec["type"], spec.get("x"), spec.get("y")
    columns = result["columns"]
    if not isinstance(x, str) or not isinstance(y, str):
        return None
    if x not in columns or y not in columns or x == y:
        return None

    rows = result["rows"]
    if len(rows) < 2 or len(rows) > MAX_POINTS:
        return None
    if chart_type == "pie" and len(rows) > MAX_PIE_SLICES:
        return None

    xi, yi = columns.index(x), columns.index(y)
    if not all(_is_number(r[yi]) for r in rows):
        return None
    if chart_type == "scatter" and not all(_is_number(r[xi]) for r in rows):
        return None
    if chart_type == "pie" and any(r[yi] < 0 for r in rows):
        return None

    return {"type": chart_type, "x": x, "y": y, "title": str(spec.get("title") or "")[:80]}
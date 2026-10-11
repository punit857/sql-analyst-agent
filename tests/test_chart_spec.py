import pytest

from app.agent.chart_spec import MAX_PIE_SLICES, MAX_POINTS, validate_chart_spec


def make_result(rows, columns=("region", "total"), truncated=False):
    return {"columns": list(columns), "rows": rows, "row_count": len(rows), "truncated": truncated}


GOOD_ROWS = [["East", 10], ["West", 5], ["North", 7]]


def bar_spec(**changes):
    spec = {"type": "bar", "x": "region", "y": "total", "title": "Totals"}
    spec.update(changes)
    return spec


def test_valid_bar_spec_is_returned_clean():
    out = validate_chart_spec(bar_spec(extra="ignored"), make_result(GOOD_ROWS))
    assert out == {"type": "bar", "x": "region", "y": "total", "title": "Totals"}


def test_title_is_cut_to_80_characters():
    out = validate_chart_spec(bar_spec(title="x" * 200), make_result(GOOD_ROWS))
    assert len(out["title"]) == 80


def test_missing_title_becomes_empty_string():
    out = validate_chart_spec(bar_spec(title=None), make_result(GOOD_ROWS))
    assert out["title"] == ""


@pytest.mark.parametrize("spec", [
    None,
    "bar",
    {},
    {"type": "none"},
    {"type": "area", "x": "region", "y": "total"},
])
def test_unusable_specs_give_none(spec):
    assert validate_chart_spec(spec, make_result(GOOD_ROWS)) is None


@pytest.mark.parametrize("changes", [
    {"x": "nope"},
    {"y": "nope"},
    {"x": "total", "y": "total"},
    {"x": 3},
    {"y": None},
])
def test_bad_column_names_give_none(changes):
    assert validate_chart_spec(bar_spec(**changes), make_result(GOOD_ROWS)) is None


def test_truncated_result_gives_none():
    assert validate_chart_spec(bar_spec(), make_result(GOOD_ROWS, truncated=True)) is None


def test_single_row_gives_none():
    assert validate_chart_spec(bar_spec(), make_result([["East", 10]])) is None


def test_point_limit():
    ok = make_result([[f"c{i}", i] for i in range(MAX_POINTS)])
    too_many = make_result([[f"c{i}", i] for i in range(MAX_POINTS + 1)])
    assert validate_chart_spec(bar_spec(), ok) is not None
    assert validate_chart_spec(bar_spec(), too_many) is None


@pytest.mark.parametrize("rows", [
    [["East", "ten"], ["West", "five"]],
    [["East", True], ["West", False]],
    [["East", 10], ["West", None]],
])
def test_y_must_be_numeric(rows):
    assert validate_chart_spec(bar_spec(), make_result(rows)) is None


def test_ints_and_floats_are_both_numeric():
    rows = [["East", 10], ["West", 5.5]]
    assert validate_chart_spec(bar_spec(), make_result(rows)) is not None


def test_scatter_needs_numeric_x_and_y():
    numbers = make_result([[1, 2.5], [2, 3.5]], columns=("a", "b"))
    text_x = make_result([["a", 2.5], ["b", 3.5]], columns=("a", "b"))
    spec = {"type": "scatter", "x": "a", "y": "b", "title": ""}
    assert validate_chart_spec(spec, numbers) is not None
    assert validate_chart_spec(spec, text_x) is None


def test_pie_slice_limit_and_negatives():
    spec = bar_spec(type="pie")
    ok = make_result([[f"c{i}", i + 1] for i in range(MAX_PIE_SLICES)])
    too_many = make_result([[f"c{i}", i + 1] for i in range(MAX_PIE_SLICES + 1)])
    negative = make_result([["East", 10], ["West", -5]])
    assert validate_chart_spec(spec, ok) is not None
    assert validate_chart_spec(spec, too_many) is None
    assert validate_chart_spec(spec, negative) is None
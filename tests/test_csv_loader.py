import sqlite3

import pytest

from app import csv_loader
from app.csv_loader import CsvError, clean_headers, load_csv


def db_rows(data, sql):
    """Open the database bytes that load_csv returned and run a query on them."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(data)
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def types_of(raw):
    return [c["type"] for c in load_csv(raw)[1]["columns"]]


def test_basic_load():
    data, profile = load_csv(b"Name,Amount\nAnn,10\nBob,20\n")
    assert profile["rows"] == 2
    assert [c["name"] for c in profile["columns"]] == ["name", "amount"]
    assert [c["type"] for c in profile["columns"]] == ["TEXT", "INTEGER"]
    assert profile["encoding"] == "utf-8"
    assert profile["delimiter"] == ","
    assert db_rows(data, "SELECT name, amount FROM data ORDER BY amount") == [("Ann", 10), ("Bob", 20)]
    assert db_rows(data, "SELECT SUM(amount) FROM data") == [(30,)]


def test_only_the_data_table_exists():
    data, _ = load_csv(b"a,b\n1,2\n")
    assert db_rows(data, "SELECT name FROM sqlite_master WHERE type = 'table'") == [("data",)]


def test_original_header_is_kept_in_the_profile():
    _, profile = load_csv(b" Order ID ,Amount ($)\n1,2\n")
    cols = profile["columns"]
    assert [c["original"] for c in cols] == ["Order ID", "Amount ($)"]
    assert [c["name"] for c in cols] == ["order_id", "amount"]


@pytest.mark.parametrize("raw, expected", [
    (b"a\n1\n2\n-3\n", ["INTEGER"]),
    (b"a\n1.5\n2\n", ["REAL"]),
    (b"a\n0.5\n0.25\n", ["REAL"]),
    (b"a\n007\n012\n", ["TEXT"]),                      # leading zeros: codes, not numbers
    (b"a\n2024-01-05\n2024-02-10\n", ["DATE_TEXT"]),
    (b"a\n2024-13-45\n", ["TEXT"]),                    # not a real date
    (b"a\n1\nx\n", ["TEXT"]),                          # mixed
    (b"a\n12345678901234567890\n", ["TEXT"]),          # too long to be a safe number
    (b"a,b\n1,\n2,\n", ["INTEGER", "TEXT"]),           # an empty column is TEXT
])
def test_type_detection(raw, expected):
    assert types_of(raw) == expected


def test_leading_zeros_are_preserved():
    data, _ = load_csv(b"zip\n007\n012\n")
    assert db_rows(data, "SELECT zip FROM data") == [("007",), ("012",)]


def test_long_digit_strings_are_preserved():
    data, _ = load_csv(b"id\n12345678901234567890\n")
    assert db_rows(data, "SELECT id, typeof(id) FROM data") == [("12345678901234567890", "text")]


def test_whitespace_is_stripped_and_blank_cells_become_null():
    data, _ = load_csv(b"name,score\n Ann , 10 \nBob,\n")
    assert db_rows(data, "SELECT name, score FROM data ORDER BY name") == [("Ann", 10), ("Bob", None)]


def test_blank_lines_are_skipped():
    _, profile = load_csv(b"a,b\n1,2\n\n3,4\n")
    assert profile["rows"] == 2


def test_windows_line_endings():
    data, profile = load_csv(b"a,b\r\n1,2\r\n3,4\r\n")
    assert profile["rows"] == 2
    assert db_rows(data, "SELECT SUM(b) FROM data") == [(6,)]


# ---------- delimiters and encodings ----------

@pytest.mark.parametrize("raw, delimiter", [
    (b"a;b\n1;2\n", ";"),
    (b"a\tb\n1\t2\n", "\t"),
    (b"a|b\n1|2\n", "|"),
])
def test_delimiter_detection(raw, delimiter):
    _, profile = load_csv(raw)
    assert profile["delimiter"] == delimiter
    assert len(profile["columns"]) == 2


def test_utf8_bom_is_removed():
    _, profile = load_csv(b"\xef\xbb\xbfa,b\n1,2\n")
    assert profile["encoding"] == "utf-8"
    assert [c["name"] for c in profile["columns"]] == ["a", "b"]


def test_windows_1252_is_read_and_a_warning_is_given():
    data, profile = load_csv("name\ncafé\n".encode("cp1252"))
    assert profile["encoding"] == "windows-1252"
    assert any("windows-1252" in w for w in profile["warnings"])
    assert db_rows(data, "SELECT name FROM data") == [("café",)]


def test_utf16_is_read():
    data, profile = load_csv("a,b\n1,2\n".encode("utf-16"))
    assert profile["encoding"] == "utf-16"
    assert db_rows(data, "SELECT a, b FROM data") == [(1, 2)]


# ---------- header cleaning ----------

def test_clean_headers():
    out = clean_headers(["Order ID", "Customer Name", "", "select", "Amount ($)", "Amount ($)", "1st"])
    assert out == ["order_id", "customer_name", "column_3", "select_col", "amount", "amount_2", "c_1st"]


def test_clean_headers_cuts_long_names():
    assert clean_headers(["a" * 60]) == ["a" * 40]


def test_clean_headers_handles_none():
    assert clean_headers([None]) == ["column_1"]


# ---------- warnings ----------

def test_ragged_rows_give_a_warning_and_are_repaired():
    data, profile = load_csv(b"a,b\n1,2\n3\n4,5,6\n")
    assert profile["rows"] == 3
    assert any("2 row(s)" in w and "extra cells in 1 row(s)" in w for w in profile["warnings"])
    assert db_rows(data, "SELECT a, b FROM data ORDER BY a") == [(1, 2), (3, None), (4, 5)]


def test_very_long_cells_are_cut():
    data, profile = load_csv(b"a\n" + b"x" * 1500 + b"\n")
    assert any("1 very long cell(s)" in w for w in profile["warnings"])
    assert db_rows(data, "SELECT length(a) FROM data") == [(1000,)]


def test_date_like_column_in_another_format_gives_a_warning():
    _, profile = load_csv(b"order_date\n05/01/2024\n06/01/2024\n")
    assert [c["type"] for c in profile["columns"]] == ["TEXT"]
    assert any("order_date" in w for w in profile["warnings"])


def test_clean_utf8_file_has_no_warnings():
    _, profile = load_csv(b"a,b\n1,2\n")
    assert profile["warnings"] == []


# ---------- rejected files ----------

def test_empty_file():
    with pytest.raises(CsvError, match="empty"):
        load_csv(b"")


def test_file_over_the_size_limit():
    with pytest.raises(CsvError, match="larger than"):
        load_csv(b"x" * (csv_loader.MAX_BYTES + 1))


def test_only_blank_lines():
    with pytest.raises(CsvError, match="no header"):
        load_csv(b"\n\n")


def test_header_without_data():
    with pytest.raises(CsvError, match="no data rows"):
        load_csv(b"a,b\n")


def test_binary_content():
    with pytest.raises(CsvError, match="text CSV"):
        load_csv(b"a,b\n1,\x00\x01\n")


def test_too_many_columns():
    header = ",".join(f"c{i}" for i in range(csv_loader.MAX_COLS + 1))
    with pytest.raises(CsvError, match="more than 50 columns"):
        load_csv((header + "\n1\n").encode())


def test_too_many_rows(monkeypatch):
    monkeypatch.setattr(csv_loader, "MAX_ROWS", 3)
    with pytest.raises(CsvError, match="more than 3 rows"):
        load_csv(b"a\n1\n2\n3\n4\n")
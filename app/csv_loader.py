import csv
import datetime
import io
import re
import sqlite3

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLS = 50
MAX_CELL_CHARS = 1000
BATCH = 5000

_RESERVED = {
    "select", "from", "where", "group", "order", "by", "limit", "table", "index", "values",
    "as", "join", "on", "in", "is", "not", "null", "and", "or", "case", "when", "then", "else",
    "end", "union", "all", "distinct", "having", "offset", "like", "between", "exists", "key",
    "default", "check", "references",
}

_INT_RE = re.compile(r"^[+-]?(0|[1-9]\d{0,17})$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_LEAD0_RE = re.compile(r"^[+-]?0\d")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")


class CsvError(Exception):
    """A problem with the uploaded file, worded so it can be shown to the user."""


def _is_iso_date(v):
    if not _DATE_RE.match(v):
        return False
    try:
        datetime.date.fromisoformat(v[:10])
    except ValueError:
        return False
    return True


class _Col:
    """Tracks, one cell at a time, which types a column can still be."""
    __slots__ = ("seen", "int_ok", "float_ok", "date_ok", "lead0")

    def __init__(self):
        self.seen = False
        self.int_ok = self.float_ok = self.date_ok = True
        self.lead0 = False

    def feed(self, v):
        self.seen = True
        if self.int_ok and not _INT_RE.match(v):
            self.int_ok = False
        if self.float_ok:
            digits = v.lstrip("+-")
            if not _FLOAT_RE.match(v) or (digits.isdigit() and len(digits) > 15):
                self.float_ok = False
            elif _LEAD0_RE.match(v):
                self.lead0 = True
        if self.date_ok and not _is_iso_date(v):
            self.date_ok = False

    def type(self):
        if not self.seen:
            return "TEXT"
        if self.int_ok:
            return "INTEGER"
        if self.float_ok and not self.lead0:
            return "REAL"
        if self.date_ok:
            return "DATE_TEXT"  # text affinity, ISO format, works with strftime
        return "TEXT"


def decode_bytes(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            raise CsvError("Could not read the text encoding of this file.")
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252"), "windows-1252"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _detect_delimiter(text):
    lines = text[:20000].splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    best, best_n = ",", 1
    for d in (",", ";", "\t", "|"):
        try:
            n = len(next(csv.reader([first], delimiter=d)))
        except (csv.Error, StopIteration):
            continue
        if n > best_n:
            best, best_n = d, n
    return best


def clean_headers(raw):
    names, seen = [], set()
    for i, h in enumerate(raw, 1):
        base = re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")[:40]
        if not base:
            base = f"column_{i}"
        if base[0].isdigit():
            base = "c_" + base
        if base in _RESERVED:
            base += "_col"
        name, n = base, 2
        while name in seen:
            name = f"{base}_{n}"
            n += 1
        seen.add(name)
        names.append(name)
    return names


def _convert(col, sql_type):
    if sql_type == "INTEGER":
        return f"CAST({col} AS INTEGER)"
    if sql_type == "REAL":
        return f"CAST({col} AS REAL)"
    return col


def load_csv(raw):
    """Parse CSV bytes into a SQLite database. Returns (db_bytes, profile). Raises CsvError."""
    if not raw:
        raise CsvError("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise CsvError(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    text, encoding = decode_bytes(raw)
    if "\x00" in text[:8192]:
        raise CsvError("This does not look like a text CSV file.")
    delim = _detect_delimiter(text)

    conn = sqlite3.connect(":memory:")
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        try:
            header = next((r for r in reader if any(c.strip() for c in r)), None)
            if header is None:
                raise CsvError("The file has no header row.")
            ncols = len(header)
            if ncols > MAX_COLS:
                raise CsvError(f"The file has more than {MAX_COLS} columns.")
            names = clean_headers(header)

            conn.execute("CREATE TABLE raw (" + ", ".join(f"c{i} TEXT" for i in range(ncols)) + ")")
            insert = f"INSERT INTO raw VALUES ({','.join('?' * ncols)})"
            cols = [_Col() for _ in range(ncols)]
            batch, n_rows, ragged, extra, cut = [], 0, 0, 0, 0
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                n_rows += 1
                if n_rows > MAX_ROWS:
                    raise CsvError(f"The file has more than {MAX_ROWS:,} rows.")
                if len(row) != ncols:
                    ragged += 1
                    if len(row) > ncols and any(c.strip() for c in row[ncols:]):
                        extra += 1
                    row = row[:ncols] + [""] * (ncols - len(row))
                vals = []
                for i, c in enumerate(row):
                    c = c.strip()
                    if not c:
                        vals.append(None)
                        continue
                    if len(c) > MAX_CELL_CHARS:
                        c = c[:MAX_CELL_CHARS]
                        cut += 1
                    cols[i].feed(c)
                    vals.append(c)
                batch.append(vals)
                if len(batch) >= BATCH:
                    conn.executemany(insert, batch)
                    batch = []
            if batch:
                conn.executemany(insert, batch)
        except csv.Error as e:
            raise CsvError(f"Could not read the file as CSV: {e}")
        if n_rows == 0:
            raise CsvError("The file has a header but no data rows.")

        types = [c.type() for c in cols]
        conn.execute("CREATE TABLE data (" + ", ".join(f'"{n}" {t}' for n, t in zip(names, types)) + ")")
        select = ", ".join(_convert(f"c{i}", t) for i, t in enumerate(types))
        conn.execute(f"INSERT INTO data SELECT {select} FROM raw")
        conn.execute("DROP TABLE raw")
        conn.commit()
        conn.execute("VACUUM")
        data = conn.serialize()
    finally:
        conn.close()

    warnings = []
    if encoding != "utf-8":
        warnings.append(f"The text was read as {encoding}. Check that accented characters look right.")
    if ragged:
        msg = f"{ragged} row(s) had a different number of cells than the header; missing cells were left empty"
        msg += f" and extra cells in {extra} row(s) were ignored." if extra else "."
        warnings.append(msg)
    if cut:
        warnings.append(f"{cut} very long cell(s) were cut to {MAX_CELL_CHARS} characters.")
    for n, t in zip(names, types):
        if t == "TEXT" and ("date" in n or "time" in n):
            warnings.append(f"Column '{n}' looks like a date but is not in YYYY-MM-DD format, "
                            "so grouping by month or year may not work.")

    profile = {
        "rows": n_rows,
        "encoding": encoding,
        "delimiter": delim,
        "columns": [{"name": n, "original": str(h).strip()[:60], "type": t}
                    for n, h, t in zip(names, header, types)],
        "warnings": warnings,
    }
    return data, profile
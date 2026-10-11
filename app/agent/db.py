import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "data" / "demo"

DEMO_DBS = {
    "sales": DEMO_DIR / "sales.sqlite",
    "chinook": DEMO_DIR / "chinook.sqlite",
}

PROFILE_MAX_VALUES = 12       # list the values of a column only if it has at most this many
PROFILE_VALUE_CHARS = 30      # each listed value is cut to this length
PROFILE_MAX_WORDS = 4         # a value with more words than this means free text, not a category
PROFILE_BUDGET_CHARS = 2500   # total profile text per database
PROFILE_TIME_S = 3.0          # best effort: stop profiling after this long

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def open_readonly(path):
    """Open a SQLite file in read-only mode."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def open_serialized(data):
    """Open uploaded database bytes as a fresh in-memory connection that cannot write."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.deserialize(data)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _is_texty(ctype):
    t = (ctype or "").upper()
    if "BLOB" in t:
        return False
    return t == "" or any(k in t for k in ("CHAR", "TEXT", "CLOB", "DATE", "TIME"))


def _clean_value(v, limit=PROFILE_VALUE_CHARS):
    return " ".join(str(v).split())[:limit]


def profile_table(conn, table, columns, row_count, budget):
    """Facts computed from the data: date ranges, and the values of category-like text columns.

    budget is a dict {"left": characters, "deadline": time.perf_counter() value}.
    Returns {column_name: one line of text}.
    """
    lines = {}
    if row_count == 0:
        return lines
    tq = _quote(table)
    for c in columns:
        if budget["left"] <= 0 or time.perf_counter() > budget["deadline"]:
            break
        if c["pk"] or not _is_texty(c["type"]):
            continue
        cq = _quote(c["name"])
        try:
            lo, hi = conn.execute(f"SELECT MIN({cq}), MAX({cq}) FROM {tq}").fetchone()
            if isinstance(lo, str) and isinstance(hi, str) and _ISO_RE.match(lo) and _ISO_RE.match(hi):
                line = f"range of {c['name']}: {_clean_value(lo, 19)} to {_clean_value(hi, 19)}"
            else:
                top = conn.execute(
                    f"SELECT {cq} FROM {tq} WHERE {cq} IS NOT NULL "
                    f"GROUP BY {cq} ORDER BY COUNT(*) DESC LIMIT {PROFILE_MAX_VALUES + 1}"
                ).fetchall()
                if not top or len(top) > PROFILE_MAX_VALUES:
                    continue
                raw = [str(r[0]) for r in top]
                if any(len(v.split()) > PROFILE_MAX_WORDS for v in raw):
                    continue  # looks like free text, not categories
                line = f"values of {c['name']}: " + " | ".join(_clean_value(v) for v in raw)
        except sqlite3.Error:
            continue
        lines[c["name"]] = line
        budget["left"] -= len(line)
    return lines


def inspect_schema(conn, sample_rows=3, profile=True):
    """Return a dict describing every table: columns, keys, foreign keys, sample rows, data profile."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    budget = {"left": PROFILE_BUDGET_CHARS, "deadline": time.perf_counter() + PROFILE_TIME_S}
    schema = {}
    for t in tables:
        cols = []
        for cid, name, ctype, notnull, default, pk in conn.execute(
            f"PRAGMA table_info({_quote(t)})"
        ):
            cols.append({"name": name, "type": ctype or "TEXT", "pk": bool(pk)})
        fks = [
            {"from": r[3], "to_table": r[2], "to_col": r[4]}
            for r in conn.execute(f"PRAGMA foreign_key_list({_quote(t)})")
        ]
        n_rows = conn.execute(f"SELECT COUNT(*) FROM {_quote(t)}").fetchone()[0]
        cur = conn.execute(f"SELECT * FROM {_quote(t)} LIMIT {int(sample_rows)}")
        samples = [list(r) for r in cur.fetchall()]
        schema[t] = {
            "columns": cols,
            "foreign_keys": fks,
            "row_count": n_rows,
            "samples": samples,
            "profile": profile_table(conn, t, cols, n_rows, budget) if profile else {},
        }
    return schema


def schema_to_text(schema, tables=None, max_cell_chars=40):
    """Turn the schema dict into compact text for an LLM prompt."""
    lines = []
    for t, info in schema.items():
        if tables is not None and t not in tables:
            continue
        col_txt = ", ".join(
            f"{c['name']} {c['type']}{' PK' if c['pk'] else ''}" for c in info["columns"]
        )
        lines.append(f"TABLE {t} ({info['row_count']} rows): {col_txt}")
        for fk in info["foreign_keys"]:
            lines.append(f"  FK {t}.{fk['from']} -> {fk['to_table']}.{fk['to_col']}")
        for line in info.get("profile", {}).values():
            lines.append("  " + line)
        for row in info["samples"]:
            cells = [str(v)[:max_cell_chars] for v in row]
            lines.append("  sample: " + " | ".join(cells))
    return "\n".join(lines)


def schema_brief(schema):
    """Names only, no sample rows. Small enough for the router prompt."""
    lines = []
    for t, info in schema.items():
        cols = ", ".join(c["name"] for c in info["columns"])
        lines.append(f"{t}({cols})")
    return "\n".join(lines)
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "data" / "demo"

DEMO_DBS = {
    "sales": DEMO_DIR / "sales.sqlite",
    "chinook": DEMO_DIR / "chinook.sqlite",
}


def open_readonly(path):
    """Open a SQLite file in read-only mode."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def inspect_schema(conn, sample_rows=3):
    """Return a dict describing every table: columns, keys, foreign keys, sample rows."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
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

def open_serialized(data):
    """Open uploaded database bytes as a fresh in-memory connection that cannot write."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.deserialize(data)
    conn.execute("PRAGMA query_only = ON")
    return conn
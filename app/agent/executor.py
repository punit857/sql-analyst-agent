import sqlite3
import time

MAX_ROWS = 200          # rows we keep and show
TIMEOUT_S = 5           # wall-clock limit per query
MAX_VALUE_BYTES = 1_000_000  # blocks huge strings/blobs (memory bombs)

_BLOCKED_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "edit",
    "fts3_tokenizer", "zeroblob", "randomblob",
}


class ExecutionError(Exception):
    pass


def _make_authorizer(allowed_tables):
    """SQLite-level policy: reads of allowed tables only, nothing else."""
    allowed = {t.lower() for t in allowed_tables}

    def authorizer(action, arg1, arg2, dbname, source):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if arg1 and arg1.lower() in allowed:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            name = (arg2 or "").lower()
            if name in _BLOCKED_FUNCTIONS or name.startswith("pragma_"):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorizer


def execute_sql(conn, sql, allowed_tables, max_rows=MAX_ROWS, timeout_s=TIMEOUT_S):
    """Run one query with an authorizer, a time limit and a row cap."""
    start = time.perf_counter()
    deadline = start + timeout_s
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    conn.set_authorizer(_make_authorizer(allowed_tables))
    # SQLite calls this every 10,000 steps; returning 1 aborts the query
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        fetched = cur.fetchmany(max_rows + 1)
    except sqlite3.Error as e:
        msg = str(e)
        low = msg.lower()
        if "interrupted" in low:
            raise ExecutionError(f"Query took longer than {timeout_s} seconds and was stopped.")
        if "not authorized" in low or "prohibited" in low:
            raise ExecutionError("Query uses a table, function or operation that is not allowed.")
        if "too big" in low:
            raise ExecutionError("Query produced a value that is too large.")
        raise ExecutionError(msg)
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)

    truncated = len(fetched) > max_rows
    rows = [list(r) for r in fetched[:max_rows]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "latency_ms": int((time.perf_counter() - start) * 1000),
    }

def _fmt(v, max_cell_chars):
    if isinstance(v, float):
        v = float(f"{v:.12g}")  # removes float noise such as 5552108.680000001
    return str(v)[:max_cell_chars]


def rows_to_text(result, max_rows=20, max_cell_chars=60):
    """Compact text version of a result, for putting into an LLM prompt."""
    lines = [" | ".join(result["columns"])]
    for row in result["rows"][:max_rows]:
        lines.append(" | ".join(_fmt(v, max_cell_chars) for v in row))
    shown = min(len(result["rows"]), max_rows)
    if result["truncated"]:
        lines.append(f"... showing {shown} rows; the result has more than {result['row_count']} rows")
    elif result["row_count"] > shown:
        lines.append(f"... showing {shown} of {result['row_count']} rows")
    return "\n".join(lines)
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import inspect_schema, open_readonly, schema_to_text

DATA_ROOT = Path(__file__).resolve().parent / "data"
GOLD_TIMEOUT_S = 30
RULES_TOKENS = 700      # rough size of the fixed rules + evidence + question in one prompt
RISK_TOKENS = 6000      # Groq allows 8,000 tokens per minute; above this one call is risky


def est(text):
    return len(text) // 4   # rough: about 4 characters per token


def run_gold(conn, sql):
    t0 = time.perf_counter()
    deadline = t0 + GOLD_TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    try:
        rows = conn.execute(sql).fetchall()
        return "ok", len(rows), time.perf_counter() - t0, ""
    except sqlite3.Error as e:
        kind = "timeout" if "interrupted" in str(e).lower() else "error"
        return kind, 0, time.perf_counter() - t0, str(e)[:100]
    finally:
        conn.set_progress_handler(None, 0)


def main():
    found = sorted(DATA_ROOT.rglob("mini_dev_sqlite.json"))
    if not found:
        print("mini_dev_sqlite.json not found under", DATA_ROOT)
        return
    base = found[0].parent
    questions = json.loads(found[0].read_text(encoding="utf-8"))
    db_dir = base / "dev_databases"
    print("questions file:", found[0])
    print("questions:", len(questions))
    print("by difficulty:", dict(Counter(q.get("difficulty") for q in questions)))
    print("questions with empty evidence:", sum(1 for q in questions if not (q.get("evidence") or "").strip()))
    by_db = {}
    for q in questions:
        by_db.setdefault(q["db_id"], []).append(q)

    print()
    print(f"{'database':<26}{'qs':>4}{'tables':>7}{'cols':>6}  {'tokens: current / 1 sample / names only':<40}{'risk':>6}")
    problems = {"error": [], "timeout": []}
    big200, big1000, empty, slow = [], [], [], []
    ok_total = 0
    for db_id in sorted(by_db):
        files = sorted((db_dir / db_id).glob("*.sqlite"))
        if not files:
            print(f"{db_id:<26} NO .sqlite FILE FOUND in {db_dir / db_id}")
            continue
        conn = open_readonly(files[0])
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        t0 = time.perf_counter()
        try:
            full = schema_to_text(inspect_schema(conn))
            one = schema_to_text(inspect_schema(conn, sample_rows=1, profile=False))
            names_schema = inspect_schema(conn, sample_rows=0, profile=False)
            names = schema_to_text(names_schema)
        except Exception as e:
            print(f"{db_id:<26} schema failed: {e}")
            conn.close()
            continue
        n_tables = len(names_schema)
        n_cols = sum(len(i["columns"]) for i in names_schema.values())
        risk = "RISK" if est(full) + RULES_TOKENS > RISK_TOKENS else ""
        print(f"{db_id:<26}{len(by_db[db_id]):>4}{n_tables:>7}{n_cols:>6}  "
              f"{est(full):>8,} / {est(one):>8,} / {est(names):>8,}{'':<9}{risk:>6}"
              f"   ({time.perf_counter() - t0:.1f}s)", flush=True)
        for q in by_db[db_id]:
            status, n, secs, msg = run_gold(conn, q["SQL"])
            qid = q.get("question_id")
            if status == "ok":
                ok_total += 1
                if n == 0:
                    empty.append(qid)
                if n > 200:
                    big200.append((qid, n))
                if n > 1000:
                    big1000.append((qid, n))
                if secs > 5:
                    slow.append((qid, round(secs, 1)))
            else:
                problems[status].append((qid, db_id, msg))
        conn.close()

    print()
    print(f"gold SQL ran fine: {ok_total} of {len(questions)}")
    print(f"gold SQL errors: {len(problems['error'])}   timeouts (>{GOLD_TIMEOUT_S}s): {len(problems['timeout'])}")
    for kind in ("error", "timeout"):
        for item in problems[kind][:10]:
            print("   ", kind, item)
    print(f"gold results with 0 rows: {len(empty)}  ids: {empty[:15]}")
    print(f"gold results over 200 rows: {len(big200)}  (our app's row cap)  e.g. {big200[:8]}")
    print(f"gold results over 1000 rows: {len(big1000)}  (our app's LIMIT)  e.g. {big1000[:8]}")
    print(f"gold queries slower than 5 s: {len(slow)}  e.g. {slow[:8]}")
    print()
    print("Token figures are rough estimates (characters divided by 4), not real counts.")


main()
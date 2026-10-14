import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))

from app.agent.db import open_readonly

DATA_FILE = sorted((BASE / "data").rglob("mini_dev_sqlite.json"))[0]
DB_DIR = DATA_FILE.parent / "dev_databases"
RESULTS = BASE / "results" / "bird_run.jsonl"
TIMEOUT_S = 30


def tolerant(b):
    return b.decode("utf-8", errors="replace")


def run(conn, sql):
    deadline = time.perf_counter() + TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, [tuple(r) for r in cur.fetchall()]
    finally:
        conn.set_progress_handler(None, 0)


def bag(rows):
    return Counter(tuple(sorted(map(str, r))) for r in rows)


def classify(gold_cols, gold, pred_cols, pred):
    if not pred:
        return "predicted query returned no rows"
    if len(pred_cols) != len(gold_cols):
        return f"different number of columns (gold {len(gold_cols)}, predicted {len(pred_cols)})"
    if len(pred) != len(gold):
        return f"different number of rows (gold {len(gold)}, predicted {len(pred)})"
    if bag(pred) == bag(gold):
        return "right values, wrong column order or arrangement"
    return "same shape, different values"


def short(x, n=170):
    return str(x).replace("\n", " ")[:n]


def main():
    questions = {q["question_id"]: q for q in json.loads(DATA_FILE.read_text(encoding="utf-8"))}
    latest = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            latest[r["question_id"]] = r
        except ValueError:
            pass
        dev = set(json.loads((BASE / "order.json").read_text(encoding="utf-8"))["order"][:20])
    scored = [r for r in latest.values() if r["status"] == "done" and r["question_id"] not in dev]
    final_mode = "--final" in sys.argv
    wrong = [r for r in scored if not r["final_correct" if final_mode else "baseline_correct"]]
    print(f"{len(wrong)} wrong first attempts out of {len(scored)} scored (development questions excluded)\n")

    conns, cats = {}, Counter()
    for r in wrong:
        q = questions[r["question_id"]]
        db = q["db_id"]
        if db not in conns:
            conn = open_readonly(sorted((DB_DIR / db).glob("*.sqlite"))[0])
            conn.text_factory = tolerant
            conns[db] = conn
        conn = conns[db]
        pick = -1 if final_mode else 0
        att = r["attempts"][pick] if r["attempts"] else {"status": "none", "sql": ""}
        print(f"=== {q['question_id']} | {db} | {q['difficulty']} | attempts: {[a['status'] for a in r['attempts']]}")
        print("Q:       ", short(q["question"], 300))
        print("HINT:    ", short(q.get("evidence") or "(none)", 300))
        print("GOLD:    ", short(q["SQL"], 500))
        print("PRED:    ", short(att.get("sql"), 500))
        try:
            gold_cols, gold = run(conn, q["SQL"])
        except sqlite3.Error as e:
            print("   gold failed:", e)
            continue
        try:
            pred_cols, pred = run(conn, att.get("sql") or "")
        except sqlite3.Error as e:
            cat = f"predicted SQL fails in plain SQLite: {short(e, 90)}"
            if att["status"] == "blocked":
                cat = f"blocked by our validator ({short(att.get('error'), 80)}); plain SQLite says: {short(e, 80)}"
            print("   ->", cat, "\n")
            low = str(e).lower()
            if "no such column" in low or "no such table" in low:
                cats["SQL error: invented column or table"] += 1
            elif "syntax error" in low or "unrecognized token" in low:
                cats["SQL error: syntax (often unquoted names)"] += 1
            else:
                cats["SQL error: other"] += 1
            continue
        cat = classify(gold_cols, gold, pred_cols, pred)
        if att["status"] == "blocked":
            cat = "WRONGLY BLOCKED by our validator, SQLite runs it fine; " + cat
        print("   ->", cat)
        print("   gold cols:", gold_cols[:6], "rows:", len(gold), short(gold[:2], 140))
        print("   pred cols:", pred_cols[:6], "rows:", len(pred), short(pred[:2], 140), "\n")
        cats[cat.split(" (")[0].split(";")[0]] += 1

    print("Summary of failure types:")
    for k, v in cats.most_common():
        print(f"  {v:>3}  {k}")


main()
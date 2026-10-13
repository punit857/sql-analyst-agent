import argparse
import functools
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.graph import END, START, StateGraph

from app import config  # noqa: F401  (loads .env)
from app.agent import graph as agent_graph
from app.agent import llm, nodes, prompts
from app.agent.db import inspect_schema, open_readonly, schema_to_text
from app.agent.executor import execute_sql
from app.agent.validator import validate_sql

BASE = Path(__file__).resolve().parent
DATA_FILE = sorted((BASE / "data").rglob("mini_dev_sqlite.json"))[0]
DB_DIR = DATA_FILE.parent / "dev_databases"
CACHE_DIR = BASE / "cache"
RESULTS_FILE = BASE / "results" / "bird_run.jsonl"
TIMEOUT_S = 30
MAX_ROWS = 100_000
TPM_LIMIT = 7000
DEV_IDS = set(json.loads((BASE / "order.json").read_text(encoding="utf-8"))["order"][:20])
PROMPT_ID = hashlib.sha256(prompts.SQL_SYSTEM_BENCH.encode("utf-8")).hexdigest()[:8]

# Same agent code, evaluation limits: no 1000-row LIMIT, no 200-row cap, 30 s timeout.
nodes.validate_sql = functools.partial(validate_sql, max_limit=10**9)
nodes.execute_sql = functools.partial(execute_sql, max_rows=MAX_ROWS, timeout_s=TIMEOUT_S)


def _no_gemini(messages, max_tokens):
    raise llm.LLMError("gemini disabled for evaluation")


llm._call_gemini = _no_gemini  # never mix models inside one comparison


class QuotaExhausted(Exception):
    pass


STATS = {"cached": 0, "live": 0}
CURRENT = {"gold": ""}


def tolerant(b):
    return b.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- LLM wrapper
class KeyPool:
    def __init__(self, keys):
        self.keys = keys
        self.used = [deque() for _ in keys]
        self.dead = set()

    def window(self, i, now):
        q = self.used[i]
        while q and now - q[0][0] > 60:
            q.popleft()
        return sum(t for _, t in q)

    def acquire(self, est):
        while True:
            now = time.time()
            live = [i for i in range(len(self.keys)) if i not in self.dead]
            if not live:
                raise QuotaExhausted("all keys are out of daily quota")
            live.sort(key=lambda i: self.window(i, now))
            for i in live:
                w = self.window(i, now)
                if w == 0 or w + est <= TPM_LIMIT:
                    return i
            time.sleep(2)

    def record(self, i, tokens):
        self.used[i].append((time.time(), tokens))


def load_keys():
    keys = []
    for name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        k = os.getenv(name, "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


class CachedChat:
    def __init__(self, pool):
        self.pool = pool
        self.mem = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = CACHE_DIR / "llm_cache.jsonl"
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    self.mem[row["k"]] = row["v"]
                except (ValueError, KeyError):
                    pass

    def __call__(self, messages, max_tokens=1500, prefer="groq"):
        key = hashlib.sha256(
            json.dumps([config.GROQ_MODEL, max_tokens, messages], sort_keys=True).encode("utf-8")
        ).hexdigest()
        if key in self.mem:
            STATS["cached"] += 1
            return dict(self.mem[key])
        est = sum(len(m["content"]) for m in messages) // 3 + 300
        last = "unknown error"
        for _ in range(8):
            i = self.pool.acquire(est)
            config.GROQ_API_KEY = self.pool.keys[i]
            try:
                out = llm.chat.__wrapped__(messages, max_tokens=max_tokens, prefer="groq")
            except llm.LLMError as e:
                msg = str(e)
                last = msg[:300]
                low = msg.lower()
                if "429" in low and "per day" in low:
                    self.pool.dead.add(i)
                    print(f"   (key {i + 1} is out of daily quota: {msg[:160]})")
                    continue
                if "429" in low or "rate limit" in low:
                    time.sleep(12)
                    continue
                time.sleep(5)
                continue
            self.pool.record(i, out["tokens_in"] + out["tokens_out"])
            STATS["live"] += 1
            self.mem[key] = out
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"k": key, "v": out}) + "\n")
            return dict(out)
        raise llm.LLMError("gave up after retries: " + last)


def oracle_chat(messages, max_tokens=1500, prefer="groq"):
    return {"text": "```sql\n" + CURRENT["gold"] + "\n```", "provider": "oracle",
            "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "fallback_errors": []}


# ------------------------------------------------------------------- data
def get_ctx(db_id):
    files = sorted((DB_DIR / db_id).glob("*.sqlite"))
    conn = open_readonly(files[0])
    conn.text_factory = tolerant
    tables = list(inspect_schema(conn, sample_rows=0, profile=False))
    cache_file = CACHE_DIR / f"schema_{db_id}.txt"
    if cache_file.exists():
        text = cache_file.read_text(encoding="utf-8")
    else:
        text = schema_to_text(inspect_schema(conn))
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")
    return {"conn": conn, "schema_text": text, "schema_brief": "", "allowed_tables": tables, "prompt_mode": "benchmark"}


def run_gold(conn, sql):
    deadline = time.perf_counter() + TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10000)
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.set_progress_handler(None, 0)


def strict_equal(pred, gold):
    return set(map(tuple, pred)) == set(map(tuple, gold))


def _norm(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    if isinstance(v, str):
        return v.strip().lower()
    return v


def lenient_equal(pred, gold):
    return {tuple(_norm(v) for v in r) for r in pred} == {tuple(_norm(v) for v in r) for r in gold}


def build_eval_graph(ctx):
    def after_execute(state):
        if state.get("error"):
            return agent_graph._retry_or_stop(state)
        return "finalize"

    g = StateGraph(agent_graph.AgentState)
    g.add_node("sql_generate", agent_graph._wrap(nodes.sql_generate, ctx))
    g.add_node("sql_validate", agent_graph._wrap(nodes.sql_validate, ctx))
    g.add_node("execute", agent_graph._wrap(nodes.execute, ctx))
    g.add_node("finalize", lambda state: {"events": [{"node": "finalize", "decision": "done"}]})
    g.add_edge(START, "sql_generate")
    g.add_conditional_edges("sql_generate", agent_graph._after_generate, ["sql_validate", "finalize"])
    g.add_conditional_edges("sql_validate", agent_graph._after_validate, ["execute", "sql_generate", "finalize"])
    g.add_conditional_edges("execute", after_execute, ["sql_generate", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


def process(q, graph, gold):
    ev = (q.get("evidence") or "").strip()
    text = q["question"] + (f"\nEvidence (a hint about the data): {ev}" if ev else "")
    state0 = agent_graph._initial_state(text, [])
    log, cur = [], None
    tok_in = tok_out = calls = 0
    llm_failed = False
    cached_before = STATS["cached"]
    t0 = time.perf_counter()
    for chunk in graph.stream(state0, config={"recursion_limit": agent_graph.RECURSION_LIMIT},
                              stream_mode="updates"):
        for node, upd in chunk.items():
            for e in upd.get("events", []):
                if "tokens_in" in e:
                    tok_in += e["tokens_in"]
                    tok_out += e["tokens_out"]
                    calls += 1
            if node == "sql_generate":
                cur = {"status": "pending", "correct": False, "lenient": False,
                       "sql": (upd.get("sql_raw") or "")[:1500]}
                log.append(cur)
                if upd.get("llm_failed"):
                    llm_failed = True
                    cur["status"] = "llm_failed"
            elif node == "sql_validate" and cur is not None:
                if upd.get("error"):
                    cur["status"] = "blocked"
                    cur["error"] = str(upd["error"])[:200]
            elif node == "execute" and cur is not None:
                res = upd.get("result")
                if res is not None and not upd.get("error"):
                    cur["status"] = "ok"
                    cur["rows"] = res["row_count"]
                    if not res["truncated"]:
                        pred = [tuple(r) for r in res["rows"]]
                        cur["correct"] = strict_equal(pred, gold)
                        cur["lenient"] = lenient_equal(pred, gold)
                elif upd.get("error") == "empty_result":
                    cur["status"] = "empty"
                else:
                    cur["status"] = "error"
                    cur["error"] = str(upd.get("error"))[:200]
    last = log[-1] if log else {"correct": False, "lenient": False, "status": "none"}
    return {
        "question_id": q["question_id"], "db_id": q["db_id"], "difficulty": q["difficulty"],
        "prompt_id": PROMPT_ID,
        "status": "llm_failed" if llm_failed else "done",
        "baseline_correct": bool(log and log[0]["correct"]),
        "baseline_lenient": bool(log and log[0]["lenient"]),
        "final_correct": bool(last["correct"]), "final_lenient": bool(last["lenient"]),
        "n_attempts": len(log), "attempts": log,
        "tokens_in": tok_in, "tokens_out": tok_out, "llm_calls": calls,
        "cached_calls": STATS["cached"] - cached_before,
        "seconds": round(time.perf_counter() - t0, 1),
    }


# ------------------------------------------------------------------ report
def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def mcnemar_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def errors_only(r):
    """What the answer would be if repairs happened only after errors, not after empty results."""
    for a in r["attempts"]:
        if a["status"] in ("blocked", "error"):
            continue
        return a["status"] == "ok" and a["correct"]
    return False


def report():
    recs = []
    if RESULTS_FILE.exists():
        for line in RESULTS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except ValueError:
                pass
    latest = {}
    for r in recs:
        if r["question_id"] not in DEV_IDS:
            latest[r["question_id"]] = r
    done = [r for r in latest.values() if r["status"] == "done"]
    n = len(done)
    print(f"scored questions: {n}   (llm_failed, not scored: {sum(1 for r in latest.values() if r['status'] == 'llm_failed')})")
    if n == 0:
        return

    def row(label, k):
        lo, hi = wilson(k, n)
        print(f"  {label:<34}{k:>4}/{n}  {100 * k / n:5.1f}%   95% CI {100 * lo:4.1f} to {100 * hi:4.1f}")

    print("\nExecution accuracy (BIRD-style set comparison):")
    row("baseline (first attempt only)", sum(r["baseline_correct"] for r in done))
    row("repair on errors only", sum(errors_only(r) for r in done))
    row("full agent (errors + empty retry)", sum(r["final_correct"] for r in done))
    print("Lenient comparison (rounded floats, case-insensitive):")
    row("baseline", sum(r["baseline_lenient"] for r in done))
    row("full agent", sum(r["final_lenient"] for r in done))
    b = sum(1 for r in done if not r["baseline_correct"] and r["final_correct"])
    c = sum(1 for r in done if r["baseline_correct"] and not r["final_correct"])
    print(f"\nPaired, baseline vs full agent: fixed by repair {b}, broken by repair {c}, "
          f"exact McNemar p = {mcnemar_p(b, c):.3f}")
    print("\nBy difficulty (baseline -> full agent):")
    for d in ("simple", "moderate", "challenging"):
        sub = [r for r in done if r["difficulty"] == d]
        if sub:
            print(f"  {d:<12}{len(sub):>4} questions   {sum(r['baseline_correct'] for r in sub):>3} -> {sum(r['final_correct'] for r in sub):>3}")
    print("\nAttempts per question:", dict(sorted(Counter(r["n_attempts"] for r in done).items())))
    first = Counter(r["attempts"][0]["status"] for r in done if r["attempts"])
    print("First-attempt outcome:", dict(first))
    tok = [r["tokens_in"] + r["tokens_out"] for r in done]
    print(f"Tokens per question: average {sum(tok) / n:,.0f}, largest {max(tok):,}  (cached calls included)")


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--oracle", type=int, default=0, help="feed the gold SQL through the pipeline, no LLM, no quota")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        report()
        return

    questions = {q["question_id"]: q for q in json.loads(DATA_FILE.read_text(encoding="utf-8"))}
    order = json.loads((BASE / "order.json").read_text(encoding="utf-8"))["order"]
    oracle = args.oracle > 0
    if oracle:
        order = order[:args.oracle]
        llm.chat = oracle_chat
        out_file = None
    else:
        order = order[:args.limit]
        keys = load_keys()
        if not keys:
            print("No GROQ_API_KEY found in .env")
            return
        print(f"using {len(keys)} Groq key(s); Gemini is disabled for evaluation")
        pool = KeyPool(keys)
        chat = CachedChat(pool)
        chat.__wrapped__ = None
        real_chat = llm.chat

        def wrapped(messages, max_tokens=1500, prefer="groq"):
            return chat(messages, max_tokens=max_tokens, prefer=prefer)

        # CachedChat calls the real llm.chat through this attribute
        wrapped_real = real_chat
        llm.chat = wrapped
        wrapped.__wrapped__ = wrapped_real
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        out_file = RESULTS_FILE

    done_ids = set()
    if out_file and out_file.exists():
        for line in out_file.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r["status"] in ("done", "gold_failed"):
                    done_ids.add(r["question_id"])
            except (ValueError, KeyError):
                pass

    ctxs, graphs = {}, {}
    base_ok = fin_ok = scored = fails_in_row = 0
    todo = [qid for qid in order if qid not in done_ids]
    print(f"{len(order)} in the order, {len(done_ids)} already done, {len(todo)} to run")
    for n, qid in enumerate(todo, 1):
        q = questions[qid]
        db = q["db_id"]
        if db not in ctxs:
            ctxs[db] = get_ctx(db)
            graphs[db] = build_eval_graph(ctxs[db])
        try:
            gold = run_gold(ctxs[db]["conn"], q["SQL"])
        except sqlite3.Error as e:
            rec = {"question_id": qid, "db_id": db, "difficulty": q["difficulty"], "status": "gold_failed", "error": str(e)[:150]}
            if out_file:
                with out_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
            print(f"[{n}] {qid} gold SQL failed, skipped")
            continue
        CURRENT["gold"] = q["SQL"]
        
        try:
            rec = process(q, graphs[db], gold)
        except QuotaExhausted as e:
            print(f"\nStopped: {e}. Finished questions are saved. Run the same command again later to resume.")
            break
        except Exception as e:
            print(f"[{n}] {qid} PIPELINE CRASH, counted as wrong: {type(e).__name__}: {str(e)[:120]}")
            rec = {"question_id": qid, "db_id": db, "difficulty": q["difficulty"], "prompt_id": PROMPT_ID,
                   "status": "done", "baseline_correct": False, "baseline_lenient": False,
                   "final_correct": False, "final_lenient": False, "n_attempts": 0, "attempts": [],
                   "tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "cached_calls": 0, "seconds": 0,
                   "crash": f"{type(e).__name__}: {str(e)[:200]}"}
        if rec["status"] == "llm_failed":
            fails_in_row += 1
            print(f"[{n}] {qid} LLM call failed, not scored")
            if fails_in_row >= 3:
                print("Three failures in a row, stopping (probably an outage).")
                break
            continue
        fails_in_row = 0
        if out_file:
            with out_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        scored += 1
        base_ok += rec["baseline_correct"]
        fin_ok += rec["final_correct"]
        print(f"[{n:>3}/{len(todo)}] {qid} {db[:18]:<18} {rec['difficulty'][:4]}  first try {'ok   ' if rec['baseline_correct'] else 'WRONG'} "
              f"final {'ok   ' if rec['final_correct'] else 'WRONG'} attempts {rec['n_attempts']} "
              f"tok {rec['tokens_in'] + rec['tokens_out']:>5}  {rec['seconds']}s   "
              f"[this run: first try {base_ok}/{scored}, final {fin_ok}/{scored}]", flush=True)
    print()
    if oracle:
        print(f"ORACLE: gold SQL through the real pipeline scored {fin_ok}/{scored} (expect nearly all).")
        print("Any miss here is a pipeline loss (validator rewrite, authorizer, timeout), not a model error.")
    else:
        print(f"live LLM calls {STATS['live']}, cached {STATS['cached']}")
        report()


main()
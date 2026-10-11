import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import DEMO_DBS, open_readonly, inspect_schema, schema_to_text, schema_brief
from app.agent.nodes import MAX_REPAIRS, analyze, execute, merge, route, sql_generate, sql_validate

CASES = [
    ("sales", "Total revenue from delivered orders by product category"),
    ("sales", "Monthly number of orders in 2024"),
    ("sales", "How many orders were cancelled?"),
    ("sales", "Average order value by payment method"),
    ("sales", "Show every order item"),
    ("sales", "Orders placed in 2030"),
    ("sales", "Delete all customers"),
    ("chinook", "Which genre has the longest average track length?"),
    ("chinook", "Top 5 artists by number of albums"),
]

ctxs = {}
for name in ("sales", "chinook"):
    conn = open_readonly(DEMO_DBS[name])
    schema = inspect_schema(conn)
    ctxs[name] = {"conn": conn, "schema_text": schema_to_text(schema),
                  "schema_brief": schema_brief(schema), "allowed_tables": list(schema)}


def run(question, ctx):
    state = {"question": question, "history": [], "attempts": [], "events": [],
             "empty_retried": False, "result": None, "error": None, "sql": None}
    merge(state, route(state, ctx))
    if state.get("llm_failed"):
        return state
    if state["route"] != "answer":
        return state
    while True:
        merge(state, sql_generate(state, ctx))
        if state.get("llm_failed"):
            return state
        merge(state, sql_validate(state, ctx))
        if not state["error"]:
            merge(state, execute(state, ctx))
        if not state["error"]:
            break
        if len(state["attempts"]) > MAX_REPAIRS:
            return state
    merge(state, analyze(state, ctx))
    return state


for db, q in CASES:
    t0 = time.perf_counter()
    s = run(q, ctxs[db])
    wall = int((time.perf_counter() - t0) * 1000)
    llm_events = [e for e in s["events"] if "provider" in e]
    print("=" * 70)
    print(f"[{db}] Q: {q}")
    print("route:", s.get("route"), "|", s.get("route_message", ""))
    if s.get("error") and not s.get("result"):
        print("FAILED:", s["error"])
    elif s.get("result"):
        print("SQL:", s["sql"])
        print("ANSWER:", s["answer"])
        print("CHART:", s["chart"])
        for c in s["caveats"]:
            print("CAVEAT:", c)
    providers = [e["provider"] for e in llm_events]
    print(f"llm_calls={len(llm_events)} providers={providers} wall={wall}ms")
    for e in s["events"]:
        for x in e.get("fallback_errors") or []:
            print("   fallback:", x[:500])
        if e.get("decision") == "llm_failed":
            print("   LLM FAILED in", e["node"], ":", e.get("detail", ""))
    for a in s.get("attempts", []):
        print("   attempt sql:", a["sql"][:200])
        print("   attempt error:", a["error"][:200])
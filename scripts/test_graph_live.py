import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import DEMO_DBS
from app.agent.graph import build_ctx, build_graph, run_agent
from app.agent.memory import SessionStore

SOURCE, SID = "sales", "live-test"
ctx = build_ctx(DEMO_DBS[SOURCE])
graph = build_graph(ctx)
store = SessionStore()

QUESTIONS = [
    "How many orders were cancelled?",
    "Now break that down by payment method",
    "Delete all customers",
    "What is the weather in Mumbai?",
    "Show me the numbers",
]

for i, q in enumerate(QUESTIONS):
    if i:
        time.sleep(10)  # stay under Groq's tokens-per-minute limit
    s = run_agent(q, ctx, history=store.get_history(SID, SOURCE), graph=graph)
    f = s["final"]
    print("=" * 70)
    print("Q:", q)
    print("STATUS:", f["status"])
    print("MESSAGE:", f["message"])
    for sug in f["suggestions"]:
        print("  suggestion:", sug)
    if f["status"] == "answered":
        print("SQL:", f["sql"])
        print("ROWS:", f["rows"][:6])
        print("CHART:", f["chart"])
        store.add_turn(SID, SOURCE, {"question": q, "sql": f["sql"], "columns": f["columns"],
                                     "row_count": f["row_count"], "answer": f["message"]})
    for c in f["caveats"]:
        print("CAVEAT:", c)
    t = f["totals"]
    print(f"llm_calls={t['llm_calls']} tokens={t['tokens_in']}+{t['tokens_out']} "
          f"providers={t['providers']} wall={t['wall_ms']}ms")
    print("trace:", " > ".join(f"{e['node']}({e['node_ms']}ms)" for e in s["events"]))
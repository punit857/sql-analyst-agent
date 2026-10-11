import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.db import DEMO_DBS, open_readonly, inspect_schema, schema_to_text
from app.agent.executor import rows_to_text
from app.agent.memory import SessionStore
from app.agent.nodes import MAX_REPAIRS, execute, merge, sql_generate, sql_validate

SOURCE = "sales"
SID = "test-session"
conn = open_readonly(DEMO_DBS[SOURCE])
schema = inspect_schema(conn)
ctx = {"conn": conn, "schema_text": schema_to_text(schema), "allowed_tables": list(schema)}
store = SessionStore()


def ask(question):
    state = {"question": question, "history": store.get_history(SID, SOURCE), "attempts": [],
             "events": [], "empty_retried": False, "result": None, "error": None, "sql": None}
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
    r = state["result"]
    store.add_turn(SID, SOURCE, {"question": question, "sql": state["sql"],
                                 "columns": r["columns"], "row_count": r["row_count"]})
    return state


def total(state):
    """Sum of all integer cells, which is the count for a 'how many, split by X' result."""
    return sum(v for row in state["result"]["rows"] for v in row
               if isinstance(v, int) and not isinstance(v, bool))


# (question, expected total). Known truths: cancelled=235, returned=204, customers=300
conversation = [
    ("How many orders were cancelled?", 235),
    ("Now break that down by payment method", 235),
    ("Same thing but for returned orders", 204),
    ("How many customers are there?", 300),
]

passed = 0
for q, expected in conversation:
    state = ask(q)
    print("=" * 70)
    print("Q:", q)
    print("SQL:", state.get("sql") or state.get("sql_raw"))
    if state["error"]:
        print("FAILED:", state["error"])
        continue
    print(rows_to_text(state["result"], max_rows=6))
    got = total(state)
    ok = got == expected
    passed += ok
    print("CHECK:", "PASS" if ok else f"FAIL (expected total {expected}, got {got})")
    print("turns remembered:", len(store.get_history(SID, SOURCE)))

print()
print(f"{passed} of {len(conversation)} follow-up checks passed")
conn.close()
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm
from app.agent.db import DEMO_DBS, open_readonly, inspect_schema, schema_to_text
from app.agent.nodes import MAX_REPAIRS, execute, merge, sql_generate, sql_validate

conn = open_readonly(DEMO_DBS["sales"])
schema = inspect_schema(conn)
ctx = {"conn": conn, "schema_text": schema_to_text(schema), "allowed_tables": list(schema)}

BAD_COL = "```sql\nSELECT no_col FROM orders\n```"
DELETE = "```sql\nDELETE FROM orders\n```"
GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"
EMPTY = "```sql\nSELECT order_id FROM orders WHERE status = 'nope'\n```"


def install_fake(replies, raise_error=False):
    """Replace llm.chat with a fake that returns the given replies in order."""
    seen = []
    queue = list(replies)

    def fake(messages, max_tokens=1500, prefer="groq"):
        seen.append(messages)
        if raise_error:
            raise llm.LLMError("both providers failed (simulated)")
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"text": text, "provider": "fake", "tokens_in": 10, "tokens_out": 5,
                "latency_ms": 1, "fallback_errors": []}

    llm.chat = fake
    return seen


def run_pipeline(question):
    state = {"question": question, "history": [], "attempts": [], "events": [],
             "empty_retried": False, "result": None, "error": None, "sql": None}
    while True:
        merge(state, sql_generate(state, ctx))
        if state.get("llm_failed"):
            return state
        merge(state, sql_validate(state, ctx))
        if not state["error"]:
            merge(state, execute(state, ctx))
        if not state["error"]:
            return state
        if len(state["attempts"]) > MAX_REPAIRS:
            return state


failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


print("Scenario A: bad column, then a blocked DELETE, then a good query")
seen = install_fake([BAD_COL, DELETE, GOOD])
s = run_pipeline("how many orders")
check("ends successfully", s["error"] is None and s["result"]["rows"] == [[3000]])
check("exactly 3 LLM calls", len(seen) == 3)
check("2 failed attempts recorded", len(s["attempts"]) == 2)
last = " ".join(m["content"] for m in seen[-1])
check("3rd call was shown the SQL error", "no such column" in last)
check("3rd call was shown the validation error", "Only SELECT" in last)

print("\nScenario B: query returns 0 rows twice")
seen = install_fake([EMPTY, EMPTY])
s = run_pipeline("orders with status nope")
check("ends without error", s["error"] is None)
check("empty result accepted after one retry", s["result"] is not None and s["result"]["row_count"] == 0)
check("exactly 2 LLM calls", len(seen) == 2)

print("\nScenario C: the LLM never produces a working query")
seen = install_fake([BAD_COL])
s = run_pipeline("anything")
check("gives up with an error", s["error"] is not None and s["result"] is None)
check("1 first try + 3 repairs = 4 LLM calls", len(seen) == 1 + MAX_REPAIRS)

print("\nScenario D: both LLM providers fail")
seen = install_fake([GOOD], raise_error=True)
s = run_pipeline("anything")
check("stops at once with llm_failed", s.get("llm_failed") is True)
check("only 1 LLM call", len(seen) == 1)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
conn.close()

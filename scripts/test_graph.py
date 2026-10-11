import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm
from app.agent.db import DEMO_DBS
from app.agent.graph import build_ctx, build_graph, run_agent, stream_agent

ctx = build_ctx(DEMO_DBS["sales"])
graph = build_graph(ctx)

ROUTE_ANSWER = '{"decision": "answer", "message": "", "suggestions": []}'
ROUTE_REFUSE = '{"decision": "refuse_write", "message": "I can only read data.", "suggestions": ["How many orders are there?"]}'
ROUTE_CLARIFY = '{"decision": "clarify", "message": "Which numbers?", "suggestions": []}'
ANALYSIS = '{"answer": "There are 3000 orders.", "chart": {"type": "none"}}'
GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"
BAD_COL = "```sql\nSELECT no_col FROM orders\n```"

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def install_fake(route_reply=ROUTE_ANSWER, sql_replies=(GOOD,), raise_error=False):
    calls = {"route": 0, "sql": 0, "analyze": 0}
    queue = list(sql_replies)

    def fake(messages, max_tokens=1500, prefer="groq"):
        system = messages[0]["content"]
        if raise_error:
            raise llm.LLMError("both providers failed (simulated)")
        if "gatekeeper" in system:
            kind, text = "route", route_reply
        elif "final answer for a data analyst" in system:
            kind, text = "analyze", ANALYSIS
        else:
            kind = "sql"
            text = queue.pop(0) if len(queue) > 1 else queue[0]
        calls[kind] += 1
        return {"text": text, "provider": "fake", "tokens_in": 10, "tokens_out": 5,
                "latency_ms": 1, "fallback_errors": []}

    llm.chat = fake
    return calls


def nodes_of(state):
    return [e["node"] for e in state["events"]]


print("Scenario A: normal question")
calls = install_fake()
s = run_agent("how many orders", ctx, graph=graph)
f = s["final"]
check("status answered", f["status"] == "answered")
check("rows are [[3000]]", f["rows"] == [[3000]])
check("answer text passed through", f["message"] == "There are 3000 orders.")
check("no chart for a single value", f["chart"] is None)
check("3 LLM calls (route, sql, analyze)", calls == {"route": 1, "sql": 1, "analyze": 1})
check("trace order is correct", nodes_of(s) == ["route", "sql_generate", "sql_validate", "execute", "analyze", "finalize"])
check("totals add up", f["totals"]["llm_calls"] == 3 and f["totals"]["tokens_in"] == 30 and f["totals"]["tokens_out"] == 15)
check("wall time recorded", "wall_ms" in f["totals"])
check("every event has node_ms", all("node_ms" in e for e in s["events"]))

print("\nScenario B: bad column, repaired on the second try")
calls = install_fake(sql_replies=[BAD_COL, GOOD])
s = run_agent("how many orders", ctx, graph=graph)
f = s["final"]
check("status answered", f["status"] == "answered")
check("4 LLM calls", sum(calls.values()) == 4 and calls["sql"] == 2)
check("repair node appears in trace", "repair" in nodes_of(s))
check("caveat says the query was revised", any("revised 1" in c for c in f["caveats"]))

print("\nScenario C: router refuses a write request")
calls = install_fake(route_reply=ROUTE_REFUSE)
s = run_agent("delete everything", ctx, graph=graph)
f = s["final"]
check("status refuse_write", f["status"] == "refuse_write")
check("message and suggestions kept", f["message"] == "I can only read data." and f["suggestions"] == ["How many orders are there?"])
check("only the router ran", calls == {"route": 1, "sql": 0, "analyze": 0})
check("no rows in the result", "rows" not in f)

print("\nScenario D: router asks to clarify")
install_fake(route_reply=ROUTE_CLARIFY)
f = run_agent("show me the numbers", ctx, graph=graph)["final"]
check("status clarify with its question", f["status"] == "clarify" and f["message"] == "Which numbers?")

print("\nScenario E: the LLM never writes a working query")
calls = install_fake(sql_replies=[BAD_COL])
s = run_agent("anything", ctx, graph=graph)
f = s["final"]
check("status failed", f["status"] == "failed")
check("1 try + 3 repairs = 4 SQL calls", calls["sql"] == 4)
check("analysis never ran", calls["analyze"] == 0)
check("last error is reported", "no such column" in (f.get("last_error") or ""))

print("\nScenario F: both providers down")
calls = install_fake(raise_error=True)
f = run_agent("anything", ctx, graph=graph)["final"]
check("status llm_failed", f["status"] == "llm_failed")

print("\nScenario G: unreadable router reply defaults to answering")
install_fake(route_reply="sorry I am confused")
f = run_agent("how many orders", ctx, graph=graph)["final"]
check("still answered", f["status"] == "answered")

print("\nScenario H: streaming yields nodes in order")
install_fake()
order = [name for name, _ in stream_agent("how many orders", ctx, graph=graph)]
check("stream order", order == ["route", "sql_generate", "sql_validate", "execute", "analyze", "finalize"])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
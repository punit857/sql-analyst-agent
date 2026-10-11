import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main
from app.agent import llm
from app.agent.graph import build_graph, run_agent
from app.agent.db import DEMO_DBS, open_readonly
from app.agent.prompts import build_system_prompt

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


check("sales notes loaded", "delivered" in main.STATIC["sales"]["notes"])
check("chinook notes loaded", "Milliseconds" in main.STATIC["chinook"]["notes"])
check("prompt without notes is unchanged", "BUSINESS NOTES" not in build_system_prompt("TABLE x"))
check("prompt with notes includes them", "BUSINESS NOTES" in build_system_prompt("TABLE x", "- a note"))

seen = []


def fake(messages, max_tokens=1500, prefer="groq"):
    system = messages[0]["content"]
    if "gatekeeper" in system:
        text = '{"decision": "answer", "message": "", "suggestions": []}'
    elif "final answer for a data analyst" in system:
        text = '{"answer": "There are 3000 orders.", "chart": {"type": "none"}}'
    else:
        seen.append(system)
        text = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"
    return {"text": text, "provider": "fake", "tokens_in": 1, "tokens_out": 1, "latency_ms": 1, "fallback_errors": []}


llm.chat = fake
ctx = dict(main.STATIC["sales"])
ctx["conn"] = open_readonly(DEMO_DBS["sales"])
run_agent("how many orders", ctx, graph=build_graph(ctx))
check("SQL step receives the notes", len(seen) == 1 and "IN ('delivered', 'shipped')" in seen[0])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
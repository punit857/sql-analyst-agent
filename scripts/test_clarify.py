import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import main
from app.agent import llm

main.ask_limiter.enabled = False
client = TestClient(main.app)

ROUTE_CLARIFY = '{"decision": "clarify", "message": "Which profit do you mean?", "suggestions": []}'
ROUTE_ANSWER = '{"decision": "answer", "message": "", "suggestions": []}'
ANALYSIS = '{"answer": "There are 3000 orders.", "chart": {"type": "none"}}'
GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


router_prompts, sql_prompts = [], []
route_replies = [ROUTE_CLARIFY, ROUTE_ANSWER]


def fake(messages, max_tokens=1500, prefer="groq"):
    system = messages[0]["content"]
    if "gatekeeper" in system:
        router_prompts.append(messages[1]["content"])
        text = route_replies.pop(0) if route_replies else ROUTE_ANSWER
    elif "final answer for a data analyst" in system:
        text = ANALYSIS
    else:
        sql_prompts.append(messages[1]["content"])
        text = GOOD
    return {"text": text, "provider": "fake", "tokens_in": 10, "tokens_out": 5, "latency_ms": 1, "fallback_errors": []}


llm.chat = fake


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def ask(q, sid="session-clr-0001"):
    r = client.post("/api/ask", json={"session_id": sid, "source": "sales", "question": q})
    return parse_sse(r.text)[-1][1]


f1 = ask("show me profit")
check("first turn is a clarifying question", f1["status"] == "clarify" and f1["message"] == "Which profit do you mean?")
check("first router call had no history", "Earlier turns" not in router_prompts[0])

f2 = ask("total profit")
check("reply is answered", f2["status"] == "answered")
check("router saw the original question", "show me profit" in router_prompts[1])
check("router saw what the assistant asked", "Which profit do you mean?" in router_prompts[1])
check("SQL step saw the original question", "show me profit" in sql_prompts[0])
check("SQL step saw what the assistant asked", "Which profit do you mean?" in sql_prompts[0])

hist = main.store.get_history("session-clr-0001", "sales")
check("both turns stored, clarify kind kept", [t["kind"] for t in hist] == ["clarify", "answered"])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
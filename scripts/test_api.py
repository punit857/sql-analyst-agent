import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import main
from app.agent import llm

client = TestClient(main.app)
main.ask_limiter.enabled = False
main.upload_limiter.enabled = False

ROUTE_ANSWER = '{"decision": "answer", "message": "", "suggestions": []}'
ROUTE_REFUSE = '{"decision": "refuse_write", "message": "I can only read data.", "suggestions": ["How many orders are there?"]}'
ANALYSIS = '{"answer": "There are 3000 orders.", "chart": {"type": "none"}}'
GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


sql_prompts = []


def install_fake(route_reply=ROUTE_ANSWER, raise_error=False):
    def fake(messages, max_tokens=1500, prefer="groq"):
        if raise_error:
            raise llm.LLMError("both providers failed (simulated)")
        system = messages[0]["content"]
        if "gatekeeper" in system:
            text = route_reply
        elif "final answer for a data analyst" in system:
            text = ANALYSIS
        else:
            text = GOOD
            sql_prompts.append(messages[1]["content"])
        return {"text": text, "provider": "fake", "tokens_in": 10, "tokens_out": 5,
                "latency_ms": 1, "fallback_errors": []}
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


def ask(question, session="session-aaaa1111", source="sales"):
    return client.post("/api/ask", json={"session_id": session, "source": source, "question": question})


print("Scenario A: normal question streams trace events then a final result")
install_fake()
r = ask("how many orders")
ev = parse_sse(r.text)
check("HTTP 200 and event-stream", r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"))
check("first event is start with a request id", ev[0][0] == "start" and len(ev[0][1]["request_id"]) == 8)
check("trace nodes in order", [d["node"] for n, d in ev if n == "trace"] ==
      ["route", "sql_generate", "sql_validate", "execute", "analyze", "finalize"])
check("last event is final", ev[-1][0] == "final")
final = ev[-1][1]
check("answered with rows [[3000]]", final["status"] == "answered" and final["rows"] == [[3000]])
check("totals include wall_ms and 3 calls", final["totals"]["llm_calls"] == 3 and "wall_ms" in final["totals"])

print("\nScenario B: follow-up in the same session sees history")
sql_prompts.clear()
ask("how many orders", session="session-bbbb2222")
ask("and now by month", session="session-bbbb2222")
check("2nd question prompt contains earlier turn", "Earlier turns" in sql_prompts[1] and "how many orders" in sql_prompts[1])
check("1st question had no history", "Earlier turns" not in sql_prompts[0])

print("\nScenario C: another session does not see it")
sql_prompts.clear()
ask("something else", session="session-cccc3333")
check("fresh session has no history", "Earlier turns" not in sql_prompts[0])

print("\nScenario D: refusal streams only route and finalize")
install_fake(route_reply=ROUTE_REFUSE)
ev = parse_sse(ask("delete everything").text)
check("only route and finalize", [d["node"] for n, d in ev if n == "trace"] == ["route", "finalize"])
check("status refuse_write", ev[-1][1]["status"] == "refuse_write")

print("\nScenario E: both LLM providers down")
install_fake(raise_error=True)
ev = parse_sse(ask("anything").text)
check("final status llm_failed", ev[-1][0] == "final" and ev[-1][1]["status"] == "llm_failed")

print("\nScenario F: bad input is rejected")
install_fake()
check("question over 500 chars gives 422", ask("x" * 501).status_code == 422)
check("blank question gives 422", ask("   ").status_code == 422)
check("short session id gives 422", ask("hi", session="abc").status_code == 422)
check("session id with bad characters gives 422", ask("hi", session="bad id with spaces!").status_code == 422)
check("unknown source gives 400", ask("hi", source="nope").status_code == 400)

print("\nScenario G: busy server returns 429")
taken = 0
for _ in range(main.MAX_CONCURRENT_RUNS):
    main._slots.acquire()
    taken += 1
r = ask("how many orders")
check("429 when all slots are taken", r.status_code == 429)
for _ in range(taken):
    main._slots.release()
r = ask("how many orders")
check("works again once slots are free", r.status_code == 200 and parse_sse(r.text)[-1][0] == "final")

print("\nScenario H: slots are released after every request")
for _ in range(5):
    ask("how many orders")
got = [main._slots.acquire(blocking=False) for _ in range(main.MAX_CONCURRENT_RUNS)]
check("all slots free after 5 requests", all(got))
for g in got:
    if g:
        main._slots.release()

print("\nScenario I: sources endpoint")
data = client.get("/api/sources").json()
ids = [s["id"] for s in data]
check("lists sales and chinook", ids == ["sales", "chinook"])
check("each has example questions", all(len(s["examples"]) >= 3 for s in data))

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
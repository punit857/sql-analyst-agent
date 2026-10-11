import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import main
from app.agent import llm
from app.uploads import UploadStore

client = TestClient(main.app)
main.ask_limiter.enabled = False
main.upload_limiter.enabled = False

ROUTE_ANSWER = '{"decision": "answer", "message": "", "suggestions": []}'
ANALYSIS = '{"answer": "There are 3 rows.", "chart": {"type": "none"}}'
CSV = b"Name,Amount\nAnn,10\nBob,20\nCy,30\n"
GOOD_SQL = "```sql\nSELECT COUNT(*) AS n FROM data\n```"
EVIL_SQL = "```sql\nSELECT * FROM sqlite_master\n```"

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


state = {"sql": GOOD_SQL}
prompts = []


def fake(messages, max_tokens=1500, prefer="groq"):
    system = messages[0]["content"]
    if "gatekeeper" in system:
        text = ROUTE_ANSWER
    elif "final answer for a data analyst" in system:
        text = ANALYSIS
    else:
        text = state["sql"]
        prompts.append(messages[1]["content"])
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


def upload(sid, data=CSV, name="t.csv"):
    return client.post("/api/upload", data={"session_id": sid}, files={"file": (name, data, "text/csv")})


def ask(sid, q="how many rows"):
    return client.post("/api/ask", json={"session_id": sid, "source": "upload", "question": q})


print("Scenario A: upload then ask")
r = upload("session-up-0001")
check("upload returns 200", r.status_code == 200)
j = r.json()
check("profile has rows, columns and file name", j["rows"] == 3 and len(j["columns"]) == 2 and j["filename"] == "t.csv")
check("types detected", [c["type"] for c in j["columns"]] == ["TEXT", "INTEGER"])
ev = parse_sse(ask("session-up-0001").text)
final = ev[-1][1]
check("answered from the uploaded table", ev[-1][0] == "final" and final["status"] == "answered" and final["rows"] == [[3]])

print("\nScenario B: ask before any upload")
r = ask("session-up-none1")
check("400 with a clear message", r.status_code == 400 and "Upload a CSV" in r.json()["detail"])

print("\nScenario C: sessions are isolated")
check("another session cannot use this upload", ask("session-up-0002").status_code == 400)

print("\nScenario D: history, and a new upload resets it")
prompts.clear()
ask("session-up-0001", "first question")
ask("session-up-0001", "second question")
check("follow-up sees earlier turn", "Earlier turns" in prompts[1])
upload("session-up-0001")
prompts.clear()
ask("session-up-0001", "after reupload")
check("history cleared after re-upload", "Earlier turns" not in prompts[0])

print("\nScenario E: the LLM tries to read sqlite_master")
state["sql"] = EVIL_SQL
ev = parse_sse(ask("session-up-0001", "show me everything").text)
check("blocked, ends as failed with no rows", ev[-1][1]["status"] == "failed" and "rows" not in ev[-1][1])
state["sql"] = GOOD_SQL

print("\nScenario F: bad uploads")
check("binary file gives 400", upload("session-up-0001", b"a,b\n1,\x00\x01\n").status_code == 400)
check("empty file gives 400", upload("session-up-0001", b"").status_code == 400)
check("bad session id gives 400", upload("bad id!").status_code == 400)
mid = b"a,b\n" + b"x,1\n" * 1_325_000
check("5.3 MB file rejected by the endpoint (413)", upload("session-up-0001", mid).status_code == 413)
big = b"a,b\n" + b"x,1\n" * 1_400_000
check("5.6 MB file rejected early by the size guard (413)", upload("session-up-0001", big).status_code == 413)
check("upload still works afterwards", upload("session-up-0003").status_code == 200)

print("\nScenario G: upload slot busy")
main._upload_slot.acquire()
check("429 while another upload is processing", upload("session-up-0004").status_code == 429)
main._upload_slot.release()
check("works again once free", upload("session-up-0004").status_code == 200)

print("\nScenario H: expiry")
old = main.uploads.ttl_s
main.uploads.ttl_s = -1
check("expired upload gives 400", ask("session-up-0004").status_code == 400)
main.uploads.ttl_s = old

print("\nScenario I: the question size guard still works")
r = client.post("/api/ask", json={"session_id": "session-up-0001", "source": "upload", "question": "x" * 501})
check("501-character question gives 422", r.status_code == 422)

print("\nScenario J: UploadStore rules")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


c = Clock()
s = UploadStore(ttl_s=10, clock=c)
e1 = s.put("a", b"x" * 10, {}, {})
check("stored and found", s.get("a") is e1)
e2 = s.put("a", b"y" * 10, {}, {})
check("same session replaces and gets a new version", s.get("a")["version"] != e1["version"] and s.count() == 1)
c.t = 20
check("expires after the TTL", s.get("a") is None)
s = UploadStore(max_items=2, clock=c)
for k in ("a", "b", "c"):
    s.put(k, b"x", {}, {})
check("item cap drops the oldest", s.get("a") is None and s.get("b") and s.get("c"))
s = UploadStore(max_total_bytes=25, clock=c)
for k in ("a", "b", "c"):
    s.put(k, b"x" * 10, {}, {})
check("byte cap drops the oldest", s.get("a") is None and s.get("b") and s.get("c"))

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app import config, main
from app.agent import llm
from app.ratelimit import RateLimiter, Rule

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


print("Part 1: the limiter itself")
c = Clock()
lim = RateLimiter([Rule("r", "ip", 2, 60, "msg")], clock=c)
check("1st allowed", lim.check("a").allowed)
check("2nd allowed", lim.check("a").allowed)
d = lim.check("a")
check("3rd refused with message and wait", (not d.allowed) and d.message == "msg" and d.retry_after == 60)
check("another address is independent", lim.check("b").allowed)
c.t = 30
check("wait shrinks as time passes", lim.check("a").retry_after == 30)
c.t = 61
check("allowed again after the window", lim.check("a").allowed)

c = Clock()
lim = RateLimiter([Rule("r", "ip", 1, 60, "m")], clock=c)
lim.check("a")
c.t = 30
lim.check("a")  # refused, must not be recorded
c.t = 61
check("a refused hit is not counted", lim.check("a").allowed)

c = Clock()
lim = RateLimiter([Rule("minute", "global", 2, 60, "minute limit"), Rule("day", "global", 3, 86400, "day limit")], clock=c)
check("global rule counts all addresses", lim.check("a").allowed and lim.check("b").allowed and not lim.check("c").allowed)
c.t = 61
check("third global hit allowed after a minute", lim.check("a").allowed)
c.t = 122
d = lim.check("a")
check("daily cap holds after minute window resets", (not d.allowed) and d.message == "day limit")

c = Clock()
lim = RateLimiter([Rule("ip", "ip", 1, 60, "ip"), Rule("g", "global", 1, 60, "global")], clock=c)
lim.check("a")
check("worst rule is reported", lim.check("b").message == "global")
lim.enabled = False
check("disabled limiter allows everything", lim.check("a").allowed and lim.check("a").allowed)

lim = RateLimiter([Rule("r", "ip", 1, 60, "m")], clock=Clock(), max_keys=3)
for ip in "abcdef":
    lim.check(ip)
check("memory is bounded by max_keys", len(lim._hits["r"]) <= 3)

print("\nPart 2: the API")
client = TestClient(main.app)
ROUTE_ANSWER = '{"decision": "answer", "message": "", "suggestions": []}'
ANALYSIS = '{"answer": "There are 3000 orders.", "chart": {"type": "none"}}'
GOOD = "```sql\nSELECT COUNT(*) AS n FROM orders\n```"


def fake(messages, max_tokens=1500, prefer="groq"):
    system = messages[0]["content"]
    text = ROUTE_ANSWER if "gatekeeper" in system else ANALYSIS if "final answer for a data analyst" in system else GOOD
    return {"text": text, "provider": "fake", "tokens_in": 10, "tokens_out": 5, "latency_ms": 1, "fallback_errors": []}


llm.chat = fake


def ask(headers=None, sid="session-lim-0001"):
    return client.post("/api/ask", json={"session_id": sid, "source": "sales", "question": "how many orders"}, headers=headers or {})


def new_limiter(limit):
    main.ask_limiter = RateLimiter([Rule("ip", "ip", limit, 600, "Too many.")])


new_limiter(2)
check("1st ask ok", ask().status_code == 200)
check("2nd ask ok", ask().status_code == 200)
r = ask()
check("3rd ask gives 429", r.status_code == 429)
check("429 has Retry-After header and friendly text", int(r.headers["retry-after"]) >= 1 and "Too many." in r.json()["detail"])

new_limiter(2)
taken = 0
for _ in range(main.MAX_CONCURRENT_RUNS):
    main._slots.acquire()
    taken += 1
codes = [ask().status_code for _ in range(3)]
for _ in range(taken):
    main._slots.release()
check("busy replies are 429", codes == [429, 429, 429])
check("busy replies did not use up the allowance", ask().status_code == 200 and ask().status_code == 200)

new_limiter(1)
main.upload_limiter = RateLimiter([Rule("up", "ip", 1, 600, "Too many files.")])
csv = {"file": ("t.csv", b"a,b\n1,2\n", "text/csv")}
check("1st upload ok", client.post("/api/upload", data={"session_id": "session-lim-0002"}, files=csv).status_code == 200)
r = client.post("/api/upload", data={"session_id": "session-lim-0002"}, files={"file": ("t.csv", b"a,b\n1,2\n", "text/csv")})
check("2nd upload gives 429", r.status_code == 429 and "Too many files." in r.json()["detail"])
main.upload_limiter.enabled = False

print("\nPart 3: client address handling")
old_hops = config.TRUSTED_PROXY_HOPS
config.TRUSTED_PROXY_HOPS = 0
new_limiter(1)
ask({"X-Forwarded-For": "1.1.1.1"})
check("hops=0 ignores X-Forwarded-For", ask({"X-Forwarded-For": "2.2.2.2"}).status_code == 429)
config.TRUSTED_PROXY_HOPS = 1
new_limiter(1)
ask({"X-Forwarded-For": "9.9.9.9, 5.5.5.5"})
check("hops=1 cannot be dodged by faking the left entry", ask({"X-Forwarded-For": "8.8.8.8, 5.5.5.5"}).status_code == 429)
check("hops=1 treats a different right entry as a different visitor", ask({"X-Forwarded-For": "8.8.8.8, 6.6.6.6"}).status_code == 200)
config.TRUSTED_PROXY_HOPS = old_hops
main.ask_limiter.enabled = False

print("\nPart 4: origin check and headers")
check("no Origin header is accepted", ask().status_code == 200)
check("same-site Origin is accepted", ask({"Origin": "http://testserver"}).status_code == 200)
r = ask({"Origin": "https://evil.example"})
check("foreign Origin gets 403", r.status_code == 403)
check("Origin 'null' gets 403", ask({"Origin": "null"}).status_code == 403)
config.ALLOWED_ORIGINS.add("https://good.example")
check("an allowed extra origin is accepted", ask({"Origin": "https://good.example"}).status_code == 200)
config.ALLOWED_ORIGINS.discard("https://good.example")
r = client.get("/health")
check("security headers present", r.headers.get("x-content-type-options") == "nosniff"
      and r.headers.get("x-frame-options") == "DENY" and r.headers.get("referrer-policy") == "no-referrer")
check("security headers also on the 403", r.headers is not None and ask({"Origin": "https://evil.example"}).headers.get("x-frame-options") == "DENY")
check("page still served", client.get("/").status_code == 200)

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
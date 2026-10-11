import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.memory import SessionStore


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def turn(q):
    return {"question": q, "sql": "SELECT 1", "columns": ["a"], "row_count": 1}


s = SessionStore()
check("unknown session is empty", s.get_history("x", "sales") == [])

s.add_turn("x", "sales", turn("q1"))
h = s.get_history("x", "sales")
check("turn is stored", len(h) == 1 and h[0]["question"] == "q1")
h[0]["question"] = "changed"
check("returned copy cannot change the store", s.get_history("x", "sales")[0]["question"] == "q1")

s.add_turn("y", "sales", turn("other"))
check("sessions are isolated", [t["question"] for t in s.get_history("y", "sales")] == ["other"])

s = SessionStore(max_turns=3)
for i in range(10):
    s.add_turn("x", "sales", turn(f"q{i}"))
qs = [t["question"] for t in s.get_history("x", "sales")]
check("only the last 3 turns are kept", qs == ["q7", "q8", "q9"])

s = SessionStore()
s.add_turn("x", "sales", turn("q1"))
check("other data source sees no history", s.get_history("x", "chinook") == [])
s.add_turn("x", "chinook", turn("q2"))
check("switching source resets history", s.get_history("x", "sales") == [])
check("new source has its own turn", len(s.get_history("x", "chinook")) == 1)

clock = Clock()
s = SessionStore(ttl_s=10, clock=clock)
s.add_turn("x", "sales", turn("q1"))
clock.t = 5
check("still alive before the TTL", len(s.get_history("x", "sales")) == 1)
clock.t = 16
check("expired after the TTL", s.get_history("x", "sales") == [])
check("expired session is removed", s.count() == 0)

s = SessionStore(max_sessions=2)
s.add_turn("a", "sales", turn("qa"))
s.add_turn("b", "sales", turn("qb"))
s.add_turn("c", "sales", turn("qc"))
check("oldest session dropped when full", s.get_history("a", "sales") == [])
check("newer sessions kept", len(s.get_history("b", "sales")) == 1 and len(s.get_history("c", "sales")) == 1)

s = SessionStore()
s.add_turn("x", "sales", turn("z" * 5000))
check("long question is truncated", len(s.get_history("x", "sales")[0]["question"]) == 500)

s.clear("x")
check("clear removes the session", s.get_history("x", "sales") == [])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
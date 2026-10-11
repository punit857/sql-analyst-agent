import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm, nodes
from app.agent.assumptions import assumption_note, find_assumptions

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


def fa(sql, q):
    return find_assumptions(sql, q)


S = "SELECT SUM(x) FROM o WHERE "

check("IN list not mentioned is shown",
      fa(S + "o.status IN ('delivered','shipped')", "What is the total profit?") == ["status is one of delivered, shipped"])
check("IN list fully mentioned is silent",
      fa(S + "o.status IN ('delivered','shipped')", "profit of delivered and shipped orders") == [])
check("IN list partly mentioned is shown",
      fa(S + "o.status IN ('delivered','shipped')", "profit of delivered orders") == ["status is one of delivered, shipped"])
check("NOT IN, user asked for the exclusion, is silent",
      fa(S + "NOT o.status IN ('cancelled','returned')", "top customers excluding cancelled and returned orders") == [])
check("NOT IN not mentioned is shown",
      fa(S + "o.status NOT IN ('cancelled','returned')", "top customers") == ["status is not one of cancelled, returned"])
check("equality mentioned is silent", fa(S + "status = 'cancelled'", "How many orders were cancelled?") == [])
check("equality not mentioned is shown", fa(S + "status = 'cancelled'", "How many orders?") == ["status is cancelled"])
check("not-equal is described", fa(S + "status <> 'x'", "orders") == ["status is not x"])
check("comparison not mentioned is shown", fa(S + "amount > 100", "big orders") == ["amount > 100"])
check("comparison mentioned is silent", fa(S + "amount > 100", "orders over 100") == [])
check("reversed comparison is flipped", fa(S + "100 < amount", "big orders") == ["amount > 100"])
check("filters on functions are ignored", fa(S + "strftime('%Y', order_date) = '2023'", "orders") == [])
check("date filter with its year in the question is silent",
      fa(S + "order_date >= '2024-01-01'", "orders in 2024") == [])
check("date filter without the year is shown",
      fa(S + "order_date >= '2024-01-01'", "recent orders") == ["order_date >= 2024-01-01"])
check("OR conditions are skipped", fa(S + "a = 'x' OR b = 'y'", "q") == [])
check("only the unmentioned AND part is shown",
      fa(S + "status = 'open' AND region = 'East'", "open orders") == ["region is East"])
check("column compared with column is ignored", fa("SELECT 1 FROM a, b WHERE a.id = b.id", "q") == [])
check("subquery IN is ignored",
      fa(S + "customer_id NOT IN (SELECT customer_id FROM orders)", "q") == [])
check("LOWER() is looked through", fa(S + "LOWER(status) = 'open'", "x") == ["status is open"])
check("short values do not match inside other numbers", fa(S + "flag = '1'", "orders in 2024") == ["flag is 1"])
check("LIKE is described", fa(S + "name LIKE '%abc%'", "x") == ["name matches %abc%"])
check("long values are cut to 30 characters", fa(S + "a = '" + "x" * 50 + "'", "q") == ["a is " + "x" * 30])
check("no more than 4 items",
      len(fa(S + " AND ".join(f"c{i} = 'v{i}'" for i in range(6)), "q")) == 4)
check("duplicates are listed once",
      fa("SELECT 1 FROM t WHERE status = 'a' AND id IN (SELECT id FROM u WHERE status = 'a')", "q") == ["status is a"])
check("garbage SQL gives an empty list", fa("not sql at all ((", "q") == [])
check("None SQL gives an empty list", fa(None, "q") == [])
check("no filters gives an empty list", fa("SELECT COUNT(*) FROM orders", "q") == [])
check("empty list gives no note", assumption_note([]) is None)
check("note text", assumption_note(["a is b"]) == "Filters applied that your question did not mention: a is b.")

print("\nIntegration with analyze (fake LLM)")


def fake(messages, max_tokens=1500, prefer="groq"):
    return {"text": '{"answer": "The total is 5.", "chart": {"type": "none"}}', "provider": "fake",
            "tokens_in": 1, "tokens_out": 1, "latency_ms": 1, "fallback_errors": []}


llm.chat = fake
res = {"columns": ["total"], "rows": [[5.0]], "row_count": 1, "truncated": False}
sql = "SELECT SUM(x) AS total FROM o WHERE o.status IN ('delivered', 'shipped')"
out = nodes.analyze({"question": "What is the total profit?", "sql": sql, "result": res, "attempts": []}, {})
check("analyze adds the note", any("status is one of delivered, shipped" in c for c in out["caveats"]))
empty = {"columns": ["total"], "rows": [], "row_count": 0, "truncated": False}
out = nodes.analyze({"question": "What is the total profit?", "sql": sql, "result": empty, "attempts": []}, {})
check("the note also appears for an empty result", any("status is one of" in c for c in out["caveats"]))
out = nodes.analyze({"question": "q", "sql": "SELECT COUNT(*) AS total FROM o", "result": res, "attempts": []}, {})
check("no filters means no note", out["caveats"] == [])

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
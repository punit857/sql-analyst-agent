import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import llm

failures = 0


def check(label, condition):
    global failures
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures += 1


OK = {"text": "hi", "tokens_in": 1, "tokens_out": 1}
msgs = [{"role": "user", "content": "x"}]
calls = []


def make(sequence, name):
    queue = list(sequence)

    def fake(messages, max_tokens):
        calls.append(name)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)

    return fake


print("Scenario A: short rate limit, Groq retried and succeeds")
calls.clear()
llm._call_groq = make([llm.RateLimited("429", 0.1), OK], "groq")
llm._call_gemini = make([OK], "gemini")
out = llm.chat(msgs)
check("answered by groq", out["provider"] == "groq")
check("groq called twice, gemini never", calls == ["groq", "groq"])
check("wait is recorded in the trace", any("waited" in e for e in out["fallback_errors"]))

print("\nScenario B: long rate limit, falls back to Gemini at once")
calls.clear()
llm._call_groq = make([llm.RateLimited("429", 30.0)], "groq")
llm._call_gemini = make([OK], "gemini")
out = llm.chat(msgs)
check("answered by gemini", out["provider"] == "gemini")
check("groq called once", calls == ["groq", "gemini"])

print("\nScenario C: rate limit with no wait time given, falls back")
calls.clear()
llm._call_groq = make([llm.RateLimited("429", None)], "groq")
llm._call_gemini = make([OK], "gemini")
out = llm.chat(msgs)
check("answered by gemini", out["provider"] == "gemini" and calls == ["groq", "gemini"])

print("\nScenario D: still limited after the retry, falls back")
calls.clear()
llm._call_groq = make([llm.RateLimited("429", 0.1), llm.RateLimited("429", 0.1)], "groq")
llm._call_gemini = make([OK], "gemini")
out = llm.chat(msgs)
check("answered by gemini after exactly 2 groq tries", out["provider"] == "gemini" and calls == ["groq", "groq", "gemini"])

print("\nScenario E: both providers fail")
calls.clear()
llm._call_groq = make([llm.LLMError("boom")], "groq")
llm._call_gemini = make([llm.LLMError("boom2")], "gemini")
try:
    llm.chat(msgs)
    check("raises LLMError", False)
except llm.LLMError as e:
    check("raises LLMError mentioning both", "groq" in str(e) and "gemini" in str(e))

print()
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
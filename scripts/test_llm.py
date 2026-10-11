import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm import chat, LLMError

msgs = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Reply with exactly one word: pong"},
]

for prefer in ["groq", "gemini"]:
    try:
        out = chat(msgs, prefer=prefer)
        print(prefer, "->", out["provider"], repr(out["text"]), out["tokens_in"], out["tokens_out"], out["latency_ms"], "ms")
        if out["fallback_errors"]:
            print("   fell back because:", out["fallback_errors"])
    except LLMError as e:
        print(prefer, "FAILED:", e)
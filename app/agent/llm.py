import re
import time

import httpx

from app import config

MAX_WAIT_S = 6        # longest rate-limit wait we accept before falling back
GROQ_TIMEOUT_S = 60
GEMINI_TIMEOUT_S = 25


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_seconds(response):
    """Seconds the provider asks us to wait, or None if it did not say."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = re.search(r"try again in ([\d.]+)s", response.text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _call_groq(messages, max_tokens):
    if not config.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY missing")
    body = {
        "model": config.GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if "gpt-oss" in config.GROQ_MODEL:
        body["reasoning_effort"] = "low"
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json=body,
        timeout=GROQ_TIMEOUT_S,
    )
    if r.status_code == 429:
        raise RateLimited(f"groq http 429: {r.text[:500]}", _retry_seconds(r))
    if r.status_code != 200:
        raise LLMError(f"groq http {r.status_code}: {r.text[:500]}")
    data = r.json()
    usage = data.get("usage", {})
    return {
        "text": data["choices"][0]["message"].get("content") or "",
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }


def _call_gemini(messages, max_tokens):
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY missing")
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    contents = [
        {
            "role": "model" if m["role"] == "assistant" else "user",
            "parts": [{"text": m["content"]}],
        }
        for m in messages
        if m["role"] != "system"
    ]
    body = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0},
    }
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    r = httpx.post(
        url,
        headers={"x-goog-api-key": config.GEMINI_API_KEY},
        json=body,
        timeout=GEMINI_TIMEOUT_S,
    )
    if r.status_code == 429:
        raise RateLimited(f"gemini http 429: {r.text[:500]}", _retry_seconds(r))
    if r.status_code != 200:
        raise LLMError(f"gemini http {r.status_code}: {r.text[:500]}")
    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError):
        raise LLMError("gemini returned no text")
    usage = data.get("usageMetadata", {})
    return {
        "text": text,
        "tokens_in": usage.get("promptTokenCount", 0),
        "tokens_out": usage.get("candidatesTokenCount", 0)
        + usage.get("thoughtsTokenCount", 0),
    }


def chat(messages, max_tokens=1500, prefer="groq"):
    """Try the preferred provider, fall back to the other. Returns text plus trace info.

    On a short rate limit (MAX_WAIT_S or less) the same provider is retried once
    after waiting. Otherwise the next provider is tried.
    """
    order = ["groq", "gemini"] if prefer == "groq" else ["gemini", "groq"]
    errors = []
    for provider in order:
        start = time.perf_counter()
        for attempt in (1, 2):
            try:
                call = _call_groq if provider == "groq" else _call_gemini
                out = call(messages, max_tokens)
                out["provider"] = provider
                out["latency_ms"] = int((time.perf_counter() - start) * 1000)
                out["fallback_errors"] = errors
                return out
            except RateLimited as e:
                if attempt == 1 and e.retry_after is not None and e.retry_after <= MAX_WAIT_S:
                    wait = e.retry_after + 0.25
                    errors.append(f"{provider}: rate limited, waited {wait:.1f}s and retried")
                    time.sleep(wait)
                    continue
                errors.append(f"{provider}: {e}")
                break
            except (LLMError, httpx.HTTPError) as e:
                errors.append(f"{provider}: {e}")
                break
    raise LLMError("Both providers failed: " + " | ".join(errors))
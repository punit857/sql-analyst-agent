import math
import json
import re

from app.agent import llm
from app.agent.executor import ExecutionError, execute_sql, rows_to_text
from app.agent.chart_spec import validate_chart_spec
from app.agent.assumptions import assumption_note, find_assumptions

from app.agent.validator import ValidationError, validate_sql

from app.agent.prompts import (
    build_repair_prompt,
    build_router_system,
    build_router_user,
    build_system_prompt,
    build_user_prompt,
    ANALYZE_SYSTEM,
    build_analyze_user,
)

MAX_REPAIRS = 3
EMPTY_HINT = (
    "The query ran but returned no rows. Check the filter values against the sample rows "
    "(spelling, letter case, date format). If no rows is genuinely the correct answer, "
    "return the same query unchanged."
)


def extract_sql(text):
    """Pull the SQL out of an LLM reply, tolerating missing or unclosed code fences."""
    text = text or ""
    m = re.search(r"```(?:sqlite|sql)?\s*(.*?)```", text, re.S | re.I)
    sql = m.group(1) if m else text
    sql = re.sub(r"^\s*```(?:sqlite|sql)?", "", sql, flags=re.I)
    return sql.strip().strip("`").strip()


def merge(state, updates):
    """Apply a node's updates to the state. New trace events are appended, not replaced."""
    updates = dict(updates)
    state["events"] = state.get("events", []) + updates.pop("events", [])
    state.update(updates)
    return state


def sql_generate(state, ctx):
    """First attempt, or a repair if earlier attempts failed. One LLM call."""
    attempts = state.get("attempts", [])
    messages = [
        {"role": "system", "content": build_system_prompt(ctx["schema_text"], ctx.get("notes", ""), ctx.get("prompt_mode", "app"))},
        {"role": "user", "content": build_user_prompt(state["question"], state.get("history", []))},
    ]
    for a in attempts[-2:]:
        messages.append({"role": "assistant", "content": f"```sql\n{a['sql']}\n```"})
        messages.append({"role": "user", "content": build_repair_prompt(a["error"])})

    node = "repair" if attempts else "sql_generate"
    try:
        out = llm.chat(messages, max_tokens=1500)
    except llm.LLMError as e:
        return {
            "sql_raw": None,
            "error": f"llm_failed: {e}",
            "llm_failed": True,
            "events": [{"node": node, "decision": "llm_failed", "detail": str(e)[:300]}],
        }
    sql = extract_sql(out["text"])
    return {
        "sql_raw": sql,
        "error": None,
        "events": [{
            "node": node,
            "decision": f"attempt {len(attempts) + 1}",
            "provider": out["provider"],
            "tokens_in": out["tokens_in"],
            "tokens_out": out["tokens_out"],
            "latency_ms": out["latency_ms"],
            "fallback_errors": out["fallback_errors"],
        }],
    }


def sql_validate(state, ctx):
    sql = state.get("sql_raw") or ""
    try:
        safe, notes = validate_sql(sql, ctx["allowed_tables"])
    except ValidationError as e:
        attempts = state.get("attempts", []) + [{"sql": sql, "error": f"Validation failed: {e}"}]
        return {
            "error": f"Validation failed: {e}",
            "attempts": attempts,
            "events": [{"node": "sql_validate", "decision": "blocked", "detail": str(e)}],
        }
    return {
        "sql": safe,
        "error": None,
        "events": [{"node": "sql_validate", "decision": "ok", "detail": "; ".join(notes)}],
    }


def execute(state, ctx):
    sql = state["sql"]
    attempts = state.get("attempts", [])
    try:
        result = execute_sql(ctx["conn"], sql, ctx["allowed_tables"])
    except ExecutionError as e:
        return {
            "error": f"Execution failed: {e}",
            "attempts": attempts + [{"sql": sql, "error": f"Execution failed: {e}"}],
            "events": [{"node": "execute", "decision": "error", "detail": str(e)}],
        }

    if result["row_count"] == 0 and not state.get("empty_retried"):
        return {
            "error": "empty_result",
            "empty_retried": True,
            "attempts": attempts + [{"sql": sql, "error": EMPTY_HINT}],
            "events": [{
                "node": "execute", "decision": "empty result, will retry once",
                "latency_ms": result["latency_ms"],
            }],
        }

    return {
        "result": result,
        "error": None,
        "events": [{
            "node": "execute",
            "decision": f"{result['row_count']} rows" + (" (truncated)" if result["truncated"] else ""),
            "latency_ms": result["latency_ms"],
        }],
    }


ROUTE_DECISIONS = {"answer", "clarify", "cannot_answer", "refuse_write"}


def parse_router_json(text):
    """Return the router's JSON object as a dict, or None if it cannot be read."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def route(state, ctx):
    """One LLM call: decide answer / clarify / cannot_answer / refuse_write."""
    messages = [
        {"role": "system", "content": build_router_system(ctx["schema_brief"])},
        {"role": "user", "content": build_router_user(state["question"], state.get("history", []))},
    ]
    try:
        out = llm.chat(messages, max_tokens=800)
    except llm.LLMError as e:
        return {
            "route": None,
            "error": f"llm_failed: {e}",
            "llm_failed": True,
            "events": [{"node": "route", "decision": "llm_failed", "detail": str(e)[:300]}],
        }

    data = parse_router_json(out["text"])
    unreadable = data is None or data.get("decision") not in ROUTE_DECISIONS
    decision = "answer" if unreadable else data["decision"]
    message = "" if unreadable else str(data.get("message") or "")[:400]
    raw = [] if unreadable else data.get("suggestions") or []
    suggestions = [str(s)[:150] for s in raw if isinstance(s, str)][:3] if isinstance(raw, list) else []

    event = {
        "node": "route",
        "decision": decision,
        "provider": out["provider"],
        "tokens_in": out["tokens_in"],
        "tokens_out": out["tokens_out"],
        "latency_ms": out["latency_ms"],
        "fallback_errors": out["fallback_errors"],
    }
    if unreadable:
        event["detail"] = "router reply unreadable, defaulting to answer"
    return {
        "route": decision,
        "route_message": message,
        "route_suggestions": suggestions,
        "events": [event],
    }

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD_NUM_RE = re.compile(r"\b(hundred|thousand|million|billion|lakh|crore)\b", re.I)


def _numbers_in(text):
    """(value, decimals) for every number written in a piece of text."""
    found = []
    for tok in _NUM_RE.findall(str(text)):
        tok = tok.rstrip(",").replace(",", "")
        if not tok:
            continue
        decimals = len(tok.split(".")[1]) if "." in tok else 0
        found.append((float(tok), decimals))
    return found


def find_ungrounded_numbers(answer, question, result):
    """Numbers written in the answer that match nothing in the rows or the question."""
    known = [float(result["row_count"])]
    known += [v for v, _ in _numbers_in(question)]
    for row in result["rows"]:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, (int, float)):
                known.append(float(cell))
            else:
                known += [v for v, _ in _numbers_in(cell)]
    bad = []
    for value, decimals in _numbers_in(answer):
        tol = 0.5 * 10 ** (-decimals) + 1e-9  # allows ordinary rounding
        if not any(math.isclose(value, k, rel_tol=1e-9, abs_tol=tol) for k in known):
            bad.append(value)
    return bad

def _num_txt(v):
    return f"{float(v):.12g}"


_ENDS_RE = re.compile(
    r"\b(first|last|earliest|latest|oldest|newest|start|starting|begin|beginning|end|ending|initial|final)\b",
    re.I,
)


def result_facts(result, question=None):
    """Exact lowest, highest, first and last values per numeric column, computed in code.

    With a question, first and last are included only if the question asks about the start or end.
    Without one (older callers), everything is included.
    """
    rows, cols = result["rows"], result["columns"]
    if len(rows) < 3:
        return ""
    with_ends = question is None or bool(_ENDS_RE.search(question))

    def is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def all_num(i):
        vals = [r[i] for r in rows if r[i] is not None]
        return bool(vals) and all(is_num(v) for v in vals)

    label_i = next((i for i in range(len(cols))
                    if not any(is_num(r[i]) for r in rows) and any(r[i] is not None for r in rows)), None)

    def label(n, r):
        if label_i is not None and r[label_i] is not None:
            return str(r[label_i])[:40]
        return f"row {n}"

    lines = [f"Number of rows in the result: {result['row_count']}"]
    if result["truncated"]:
        lines.append("The result was cut off at the row limit, so the figures below cover only the rows kept.")
    elif question is not None:
        lines.append("The result is complete: no rows are missing.")
    shown = 0
    for i, name in enumerate(cols):
        if shown >= 3:
            break
        low = name.lower()
        if not all_num(i) or low == "id" or low.endswith("_id") or name.endswith("Id"):
            continue
        pairs = [(r[i], label(n + 1, r)) for n, r in enumerate(rows) if r[i] is not None]
        lo = min(pairs, key=lambda p: p[0])
        hi = max(pairs, key=lambda p: p[0])
        text = f"{name}: lowest {_num_txt(lo[0])} at {lo[1]}; highest {_num_txt(hi[0])} at {hi[1]}"
        if with_ends:
            text += f"; first {_num_txt(pairs[0][0])} at {pairs[0][1]}; last {_num_txt(pairs[-1][0])} at {pairs[-1][1]}"
        lines.append(text)
        shown += 1
    return "\n".join(lines)

def analyze(state, ctx):
    """Plain-language answer plus a validated chart spec. One LLM call, or none if empty."""
    result = state["result"]
    caveats = []
    revisions = len(state.get("attempts", []))
    if revisions:
        caveats.append(f"The query was revised {revisions} time(s) before it ran successfully.")
    if result["truncated"]:
        caveats.append("Only the first rows were kept; the full result is larger than what is shown.")
    note = assumption_note(find_assumptions(state.get("sql"), state["question"]))
    if note:
        caveats.append(note)

    if result["row_count"] == 0:
        return {
            "answer": "The query ran correctly but returned no rows, so the data has nothing matching this question.",
            "chart": None,
            "caveats": caveats,
            "events": [{"node": "analyze", "decision": "empty result, no LLM call"}],
        }

    user_text = build_analyze_user(state["question"], state["sql"], rows_to_text(result, max_rows=50))
    facts = result_facts(result, state["question"])
    if facts:
        user_text += "\n\nFACTS computed by code (exact):\n" + facts
    messages = [
        {"role": "system", "content": ANALYZE_SYSTEM},
        {"role": "user", "content": user_text},
    ]
    try:
        out = llm.chat(messages, max_tokens=1200)
    except llm.LLMError as e:
        return {
            "answer": "Here are the results. A written summary could not be produced right now.",
            "chart": None,
            "caveats": caveats,
            "events": [{"node": "analyze", "decision": "llm_failed", "detail": str(e)[:300]}],
        }

    data = parse_router_json(out["text"])
    event = {
        "node": "analyze",
        "provider": out["provider"],
        "tokens_in": out["tokens_in"],
        "tokens_out": out["tokens_out"],
        "latency_ms": out["latency_ms"],
        "fallback_errors": out["fallback_errors"],
    }
    if data is None or not isinstance(data.get("answer"), str) or not data["answer"].strip():
        event["decision"] = "reply unreadable, showing table only"
        return {
            "answer": "Here are the results.",
            "chart": None,
            "caveats": caveats,
            "events": [event],
        }

    answer = data["answer"].strip()[:1200]
    chart = validate_chart_spec(data.get("chart"), result)
    ungrounded = find_ungrounded_numbers(answer, state["question"], result)
    if ungrounded:
        shown = ", ".join(f"{v:g}" for v in ungrounded[:5])
        caveats.append(f"These figures in the summary could not be matched to the result rows: {shown}.")
    if _WORD_NUM_RE.search(answer):
        caveats.append("The summary writes some numbers in words, so they could not be checked against the rows.")
        ungrounded = ungrounded or ["words"]
    event["decision"] = ("chart: " + chart["type"]) if chart else "table only"
    if ungrounded:
        event["detail"] = f"{len(ungrounded)} ungrounded number(s)"
    return {"answer": answer, "chart": chart, "caveats": caveats, "events": [event]}
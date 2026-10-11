import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.db import inspect_schema, open_readonly, schema_brief, schema_to_text
from app.agent.nodes import MAX_REPAIRS

RECURSION_LIMIT = 40  # the longest normal path is about 15 steps


class AgentState(TypedDict, total=False):
    question: str
    history: list
    events: Annotated[list, operator.add]  # trace events are appended, never replaced
    attempts: list
    empty_retried: bool
    route: Any
    route_message: str
    route_suggestions: list
    sql_raw: Any
    sql: Any
    result: Any
    error: Any
    llm_failed: bool
    answer: str
    chart: Any
    caveats: list
    final: dict


def build_ctx_from_conn(conn):
    """Gather everything the nodes need from an open read-only connection."""
    schema = inspect_schema(conn)
    return {
        "conn": conn,
        "schema_text": schema_to_text(schema),
        "schema_brief": schema_brief(schema),
        "allowed_tables": list(schema),
    }


def build_ctx(db_path):
    """Open a database file read-only and gather everything the nodes need."""
    return build_ctx_from_conn(open_readonly(db_path))


def _wrap(fn, ctx):
    """Run a node and stamp its trace events with the node's total time."""
    def run(state):
        t0 = time.perf_counter()
        update = fn(state, ctx)
        ms = int((time.perf_counter() - t0) * 1000)
        for event in update.get("events", []):
            event["node_ms"] = ms
        return update
    return run


def finalize(state):
    events = state.get("events", [])
    llm_events = [e for e in events if "provider" in e]
    totals = {
        "llm_calls": len(llm_events),
        "tokens_in": sum(e.get("tokens_in", 0) for e in llm_events),
        "tokens_out": sum(e.get("tokens_out", 0) for e in llm_events),
        "revisions": len(state.get("attempts", [])),
        "providers": sorted({e["provider"] for e in llm_events}),
    }
    final = {"totals": totals, "suggestions": [], "caveats": state.get("caveats", [])}
    route = state.get("route")
    result = state.get("result")

    if state.get("llm_failed"):
        final.update(
            status="llm_failed",
            message="The language model services are busy or unavailable right now. Please try again in a minute.",
        )
    elif route in ("clarify", "cannot_answer", "refuse_write"):
        defaults = {
            "clarify": "Could you tell me a bit more about what you want to know?",
            "cannot_answer": "This database does not contain the data needed to answer that.",
            "refuse_write": "I can only read data, not change it.",
        }
        final.update(
            status=route,
            message=state.get("route_message") or defaults[route],
            suggestions=state.get("route_suggestions", []),
        )
    elif result is not None and not state.get("error"):
        final.update(
            status="answered",
            message=state.get("answer", ""),
            sql=state.get("sql"),
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            truncated=result["truncated"],
            chart=state.get("chart"),
        )
    else:
        final.update(
            status="failed",
            message="I could not produce a working query for this question after several tries. Try rephrasing it.",
            last_sql=state.get("sql_raw"),
            last_error=state.get("error"),
        )
    return {"final": final, "events": [{"node": "finalize", "decision": final["status"], "node_ms": 0}]}


def _after_route(state):
    if state.get("llm_failed") or state.get("route") != "answer":
        return "finalize"
    return "sql_generate"


def _after_generate(state):
    return "finalize" if state.get("llm_failed") else "sql_validate"


def _retry_or_stop(state):
    return "finalize" if len(state.get("attempts", [])) > MAX_REPAIRS else "sql_generate"


def _after_validate(state):
    return _retry_or_stop(state) if state.get("error") else "execute"


def _after_execute(state):
    return _retry_or_stop(state) if state.get("error") else "analyze"


def build_graph(ctx):
    g = StateGraph(AgentState)
    g.add_node("route", _wrap(nodes.route, ctx))
    g.add_node("sql_generate", _wrap(nodes.sql_generate, ctx))
    g.add_node("sql_validate", _wrap(nodes.sql_validate, ctx))
    g.add_node("execute", _wrap(nodes.execute, ctx))
    g.add_node("analyze", _wrap(nodes.analyze, ctx))
    g.add_node("finalize", finalize)

    g.add_edge(START, "route")
    g.add_conditional_edges("route", _after_route, ["sql_generate", "finalize"])
    g.add_conditional_edges("sql_generate", _after_generate, ["sql_validate", "finalize"])
    g.add_conditional_edges("sql_validate", _after_validate, ["execute", "sql_generate", "finalize"])
    g.add_conditional_edges("execute", _after_execute, ["analyze", "sql_generate", "finalize"])
    g.add_edge("analyze", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def _initial_state(question, history):
    return {
        "question": question,
        "history": history or [],
        "events": [],
        "attempts": [],
        "empty_retried": False,
        "result": None,
        "error": None,
        "sql": None,
        "caveats": [],
    }


def run_agent(question, ctx, history=None, graph=None):
    """Run the whole agent and return the final state. state['final'] is what the UI shows."""
    graph = graph or build_graph(ctx)
    t0 = time.perf_counter()
    state = graph.invoke(_initial_state(question, history), config={"recursion_limit": RECURSION_LIMIT})
    state["final"]["totals"]["wall_ms"] = int((time.perf_counter() - t0) * 1000)
    return state


def stream_agent(question, ctx, history=None, graph=None):
    """Yield (node_name, update) as each node finishes. The API will turn these into live trace events."""
    graph = graph or build_graph(ctx)
    for chunk in graph.stream(
        _initial_state(question, history),
        config={"recursion_limit": RECURSION_LIMIT},
        stream_mode="updates",
    ):
        for node_name, update in chunk.items():
            yield node_name, update
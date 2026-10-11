import json
import logging
import queue
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from pathlib import Path

from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.agent.db import DEMO_DBS, open_readonly
from app.agent.graph import build_ctx, build_graph, stream_agent
from app.agent.memory import SessionStore
from app.sources import SOURCE_META

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CONCURRENT_RUNS = 2
KEEPALIVE_S = 15

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("sql_analyst")

app = FastAPI(title="SQL Analyst Agent")
store = SessionStore()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)


def _load_static():
    """Read each demo schema once at startup. Connections are opened per request."""
    out = {}
    for name, path in DEMO_DBS.items():
        ctx = build_ctx(path)
        ctx.pop("conn").close()
        out[name] = ctx
    return out


STATIC = _load_static()


class AskRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    source: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("question is empty")
        return v


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _log(**fields):
    log.info(json.dumps(fields, default=str))


def _worker(req, request_id, out, stop):
    """Runs the agent in its own thread and pushes events onto the queue."""
    t0 = time.perf_counter()
    conn = None
    try:
        ctx = dict(STATIC[req.source])
        conn = open_readonly(DEMO_DBS[req.source])
        ctx["conn"] = conn
        history = store.get_history(req.session_id, req.source)
        graph = build_graph(ctx)
        final = None
        for _node, update in stream_agent(req.question, ctx, history=history, graph=graph):
            if stop.is_set():
                _log(event="ask_abandoned", request_id=request_id)
                return
            for ev in update.get("events", []):
                out.put(("trace", ev))
            if "final" in update:
                final = update["final"]
        if final is None:
            raise RuntimeError("graph finished without a final result")
        final["totals"]["wall_ms"] = int((time.perf_counter() - t0) * 1000)
        if final["status"] == "answered":
            store.add_turn(req.session_id, req.source, {
                "question": req.question,
                "sql": final["sql"],
                "columns": final["columns"],
                "row_count": final["row_count"],
                "answer": final["message"],
            })
        out.put(("final", final))
        _log(event="ask_done", request_id=request_id, source=req.source,
             status=final["status"], question_chars=len(req.question), **final["totals"])
    except Exception:
        log.exception("ask_failed request_id=%s", request_id)
        out.put(("error", {"message": "Something went wrong on the server. Please try again."}))
    finally:
        if conn is not None:
            conn.close()
        _slots.release()
        out.put(None)  # tells the response stream to finish


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/sources")
def sources():
    return [
        {"id": name, **SOURCE_META.get(name, {"label": name, "description": "", "examples": []}),
         "tables": len(STATIC[name]["allowed_tables"])}
        for name in DEMO_DBS
    ]


@app.post("/api/ask")
def ask(req: AskRequest):
    if req.source not in DEMO_DBS:
        raise HTTPException(status_code=400, detail="Unknown data source.")
    if not _slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="The server is busy with other questions. Please try again in a few seconds.",
        )
    request_id = uuid.uuid4().hex[:8]
    out = queue.Queue()
    stop = threading.Event()
    try:
        threading.Thread(target=_worker, args=(req, request_id, out, stop), daemon=True).start()
    except Exception:
        _slots.release()
        raise

    def stream():
        yield _sse("start", {"request_id": request_id, "source": req.source})
        try:
            while True:
                try:
                    item = out.get(timeout=KEEPALIVE_S)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield _sse(item[0], item[1])
        finally:
            stop.set()  # if the browser leaves, the agent stops after its current node

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
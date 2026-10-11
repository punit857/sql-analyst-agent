import json
import logging
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app import csv_loader
from app.agent.db import DEMO_DBS, open_readonly, open_serialized
from app.agent.graph import build_ctx, build_ctx_from_conn, build_graph, stream_agent
from app.agent.memory import SessionStore
from app.sources import SOURCE_META
from app.uploads import UploadStore

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_CONCURRENT_RUNS = 2
KEEPALIVE_S = 15
JSON_BODY_LIMIT = 20_000
UPLOAD_BODY_LIMIT = csv_loader.MAX_BYTES + 100_000
SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("sql_analyst")


class BodyLimit:
    """Rejects POST requests whose declared size is too large, before the body is read."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            limit = UPLOAD_BODY_LIMIT if scope["path"] == "/api/upload" else JSON_BODY_LIMIT
            for key, value in scope["headers"]:
                if key == b"content-length":
                    try:
                        too_big = int(value) > limit
                    except ValueError:
                        too_big = True
                    if too_big:
                        resp = JSONResponse({"detail": "That request is too large."}, status_code=413)
                        await resp(scope, receive, send)
                        return
        await self.app(scope, receive, send)


app = FastAPI(title="SQL Analyst Agent")
app.add_middleware(BodyLimit)
store = SessionStore()
uploads = UploadStore()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
_upload_slot = threading.BoundedSemaphore(1)


def _load_static():
    """Read each demo schema once at startup. Connections are opened per request."""
    out = {}
    for name, path in DEMO_DBS.items():
        ctx = build_ctx(path)
        ctx.pop("conn").close()
        out[name] = ctx
    return out


STATIC = _load_static()


def _static_from_bytes(data):
    conn = open_serialized(data)
    ctx = build_ctx_from_conn(conn)
    ctx.pop("conn").close()
    return ctx


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
        if req.source == "upload":
            entry = uploads.get(req.session_id)
            if entry is None:
                out.put(("error", {"message": "Your uploaded file has expired. Please upload it again."}))
                return
            ctx = dict(entry["static"])
            conn = open_serialized(entry["data"])
            source_key = f"upload:{entry['version']}"  # a new upload starts a fresh history
        else:
            ctx = dict(STATIC[req.source])
            conn = open_readonly(DEMO_DBS[req.source])
            source_key = req.source
        ctx["conn"] = conn
        history = store.get_history(req.session_id, source_key)
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
            store.add_turn(req.session_id, source_key, {
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


def _read_limited(file):
    chunks, total = [], 0
    while True:
        chunk = file.file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > csv_loader.MAX_BYTES:
            raise HTTPException(status_code=413, detail="The file is larger than 5 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/upload")
def upload(session_id: str = Form(...), file: UploadFile = File(...)):
    if not SID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session.")
    if not _upload_slot.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Another file is being processed. Please try again in a few seconds.")
    try:
        raw = _read_limited(file)
        try:
            data, profile = csv_loader.load_csv(raw)
        except csv_loader.CsvError as e:
            raise HTTPException(status_code=400, detail=str(e))
        static = _static_from_bytes(data)
        uploads.put(session_id, data, profile, static)
        name = Path(file.filename or "upload.csv").name[:80] or "upload.csv"
        _log(event="upload_done", rows=profile["rows"], columns=len(profile["columns"]), bytes=len(raw))
        return {"filename": name, "ttl_minutes": 30, **profile}
    finally:
        _upload_slot.release()


@app.post("/api/ask")
def ask(req: AskRequest):
    if req.source == "upload":
        if uploads.get(req.session_id) is None:
            raise HTTPException(
                status_code=400,
                detail="No CSV found for this session. Upload a CSV file first; uploads expire after 30 minutes.",
            )
    elif req.source not in DEMO_DBS:
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
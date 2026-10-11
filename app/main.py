import sqlite3
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
SALES_DB = ROOT / "data" / "demo" / "sales.sqlite"

app = FastAPI(title="SQL Analyst Agent")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/demo-query")
def demo_query():
    # Read-only connection, same idea we will use in the real agent
    uri = SALES_DB.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"sql": "orders by status", "rows": rows}
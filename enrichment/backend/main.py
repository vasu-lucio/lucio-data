import asyncio
import io
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timezone
from typing import Any, List, Optional

import httpx
import pandas as pd
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

APP_PASSWORD = os.getenv("APP_PASSWORD", "lucio2025")
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "data", "enrichment.db"),
)

app = FastAPI(title="Enrichment API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Database ───────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads during background writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            columns    TEXT NOT NULL,
            row_count  INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rows (
            id         TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            row_index  INTEGER NOT NULL,
            data       TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS enrichment_results (
            id             TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            row_id         TEXT NOT NULL,
            column_name    TEXT NOT NULL,
            value          TEXT,
            status         TEXT NOT NULL DEFAULT 'not_found',
            source_url     TEXT,
            updated_at     TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS templates (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            config     TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id             TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            progress_done  INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            error          TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        );
    """)

    # FIX: add unique index on enrichment_results(row_id, column_name)
    # This makes upserts safe and prevents duplicate rows on re-run.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_enrichment_unique
        ON enrichment_results(row_id, column_name)
    """)

    conn.commit()
    conn.close()


init_db()


# ── Auth ───────────────────────────────────────────────────────────────────────

def verify_password(password: Optional[str]):
    if password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Helpers ────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_upload(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
    elif name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content), dtype=str).fillna("")
    else:
        try:
            return pd.read_csv(io.BytesIO(content), dtype=str).fillna("")
        except Exception:
            return pd.read_excel(io.BytesIO(content), dtype=str).fillna("")


def detect_firm_col(columns: List[str]) -> str:
    for c in columns:
        if c.lower() in ("firm name", "firm", "law firm", "company", "name"):
            return c
    return columns[0]


def detect_website_col(columns: List[str]) -> Optional[str]:
    for c in columns:
        if any(k in c.lower() for k in ("website", "url", "site", "domain", "web")):
            return c
    return None


# ── Background enrichment job ──────────────────────────────────────────────────

def _db_update_job(job_id: str, **kwargs):
    """Thread-safe job status update — opens its own connection."""
    conn = get_db()
    sets = ["updated_at=?"]
    vals: list = [now_iso()]
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()


def _db_save_result(session_id: str, row_id: str, col: str, item: dict):
    """Upsert one enrichment result. Safe to call concurrently (WAL mode)."""
    conn = get_db()
    conn.execute(
        """
        INSERT INTO enrichment_results
            (id, session_id, row_id, column_name, value, status, source_url, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(row_id, column_name) DO UPDATE SET
            value      = excluded.value,
            status     = excluded.status,
            source_url = excluded.source_url,
            updated_at = excluded.updated_at
        """,
        (
            str(uuid.uuid4()), session_id, row_id, col,
            item.get("value"), item.get("status", "not_found"),
            item.get("source_url"), now_iso(),
        ),
    )
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
    conn.commit()
    conn.close()


async def _run_enrichment_job(
    job_id: str,
    session_id: str,
    target_rows: List[dict],
    data_points: List[dict],
    firm_col: str,
    website_col: Optional[str],
    new_columns: List[str],
    existing_cols: List[str],
):
    """Background task: enriches rows one batch at a time, updates job progress."""
    from enrichment import enrich_row  # local import keeps enrichment.py decoupled

    _db_update_job(job_id, status="running")

    # Ensure new columns are registered on the session upfront
    if new_columns:
        added = [c for c in new_columns if c not in existing_cols]
        if added:
            conn = get_db()
            updated = json.dumps(existing_cols + added)
            conn.execute(
                "UPDATE sessions SET columns=?, updated_at=? WHERE id=?",
                (updated, now_iso(), session_id),
            )
            conn.commit()
            conn.close()

    semaphore = asyncio.Semaphore(5)
    done = 0

    async def process_one(row: dict):
        nonlocal done
        async with semaphore:
            firm = row["data"].get(firm_col, "")
            site = (row["data"].get(website_col, "") or None) if website_col else None
            try:
                results = await enrich_row(firm, site, data_points, client)
            except Exception:
                results = [
                    {"column": dp.get("column", ""), "value": None,
                     "status": "not_found", "source_url": None}
                    for dp in data_points
                ]
            for item in results:
                col = item.get("column", "")
                if col:
                    _db_save_result(session_id, row["id"], col, item)
            done += 1
            _db_update_job(job_id, progress_done=done)

    try:
        async with httpx.AsyncClient() as client:
            await asyncio.gather(
                *[process_one(r) for r in target_rows],
                return_exceptions=True,
            )
        _db_update_job(job_id, status="done", progress_done=len(target_rows))
    except Exception as e:
        _db_update_job(job_id, status="failed", error=str(e)[:500])


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/verify")
def verify_auth(body: dict):
    password = body.get("password", "")
    if password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    return {"ok": True}


@app.get("/api/sessions")
def list_sessions(x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, columns, row_count, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "columns": json.loads(r["columns"]),
            "row_count": r["row_count"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@app.post("/api/sessions")
async def create_session(file: UploadFile = File(...), x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    df = parse_upload(file)
    if df.empty:
        raise HTTPException(status_code=400, detail="File is empty or unreadable")

    df.columns = [str(c).strip() for c in df.columns]
    session_id = str(uuid.uuid4())
    ts = now_iso()
    columns = list(df.columns)
    conn = get_db()

    conn.execute(
        "INSERT INTO sessions (id, name, columns, row_count, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (session_id, file.filename or "upload", json.dumps(columns), len(df), ts, ts),
    )
    for idx, (_, row) in enumerate(df.iterrows()):
        conn.execute(
            "INSERT INTO rows (id, session_id, row_index, data) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), session_id, idx, json.dumps(row.to_dict())),
        )
    conn.commit()
    conn.close()
    return {"id": session_id, "name": file.filename, "columns": columns, "row_count": len(df)}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    db_rows = conn.execute(
        "SELECT id, row_index, data FROM rows WHERE session_id=? ORDER BY row_index",
        (session_id,),
    ).fetchall()

    enrichments = conn.execute(
        "SELECT row_id, column_name, value, status, source_url FROM enrichment_results WHERE session_id=?",
        (session_id,),
    ).fetchall()
    conn.close()

    enrich_map: dict = {}
    for e in enrichments:
        enrich_map.setdefault(e["row_id"], {})[e["column_name"]] = {
            "value": e["value"],
            "status": e["status"],
            "source_url": e["source_url"],
        }

    rows_out = []
    for r in db_rows:
        data = json.loads(r["data"])
        for col, info in enrich_map.get(r["id"], {}).items():
            data[col] = info["value"] if info["value"] is not None else info["status"]
        rows_out.append({"id": r["id"], "row_index": r["row_index"], "data": data})

    return {
        "id": session["id"],
        "name": session["name"],
        "columns": json.loads(session["columns"]),
        "row_count": session["row_count"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "rows": rows_out,
    }


class CellUpdate(BaseModel):
    row_id: str
    column: str
    value: str


@app.patch("/api/sessions/{session_id}/cells")
def update_cell(session_id: str, update: CellUpdate, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    row = conn.execute(
        "SELECT id, data FROM rows WHERE id=? AND session_id=?",
        (update.row_id, session_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Row not found")

    data = json.loads(row["data"])
    data[update.column] = update.value
    conn.execute("UPDATE rows SET data=? WHERE id=?", (json.dumps(data), update.row_id))
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), session_id))
    conn.commit()
    conn.close()
    return {"ok": True}


class AddColumnsRequest(BaseModel):
    columns: List[str]


@app.post("/api/sessions/{session_id}/columns")
def add_columns(session_id: str, body: AddColumnsRequest, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    session = conn.execute("SELECT columns FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    existing = json.loads(session["columns"])
    updated = existing + [c for c in body.columns if c not in existing]
    conn.execute(
        "UPDATE sessions SET columns=?, updated_at=? WHERE id=?",
        (json.dumps(updated), now_iso(), session_id),
    )
    conn.commit()
    conn.close()
    return {"columns": updated}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    conn.execute("DELETE FROM enrichment_results WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM rows WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/sessions/{session_id}/export")
def export_session(session_id: str, fmt: str = "csv", x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    db_rows = conn.execute(
        "SELECT id, row_index, data FROM rows WHERE session_id=? ORDER BY row_index",
        (session_id,),
    ).fetchall()
    enrichments = conn.execute(
        "SELECT row_id, column_name, value, status FROM enrichment_results WHERE session_id=?",
        (session_id,),
    ).fetchall()
    conn.close()

    enrich_map: dict = {}
    for e in enrichments:
        enrich_map.setdefault(e["row_id"], {})[e["column_name"]] = e["value"] or e["status"]

    columns = json.loads(session["columns"])
    records = []
    for r in db_rows:
        data = json.loads(r["data"])
        for col, val in enrich_map.get(r["id"], {}).items():
            data[col] = val
        records.append({c: data.get(c, "") for c in columns})

    df = pd.DataFrame(records, columns=columns)
    safe_name = session["name"].rsplit(".", 1)[0]

    if fmt == "xlsx":
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_enriched.xlsx"'},
        )
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            io.BytesIO(buf.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_enriched.csv"'},
        )


# ── Templates ──────────────────────────────────────────────────────────────────

class Template(BaseModel):
    name: str
    config: dict


@app.get("/api/templates")
def list_templates(x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    rows = conn.execute("SELECT id, name, config, created_at FROM templates ORDER BY name").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "config": json.loads(r["config"]), "created_at": r["created_at"]} for r in rows]


@app.post("/api/templates")
def save_template(body: Template, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO templates (id, name, config, created_at) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), body.name, json.dumps(body.config), now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.execute("UPDATE templates SET config=? WHERE name=?", (json.dumps(body.config), body.name))
        conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: str, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Enrichment ─────────────────────────────────────────────────────────────────

class EnrichRequest(BaseModel):
    row_ids: List[str]       # empty list = all rows
    data_points: List[dict]  # [{column, prompt, preset}]
    dry_run: bool = False


@app.post("/api/sessions/{session_id}/enrich")
async def enrich_session_route(
    session_id: str,
    body: EnrichRequest,
    background_tasks: BackgroundTasks,
    x_password: Optional[str] = Header(None),
):
    verify_password(x_password)
    conn = get_db()
    session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    columns = json.loads(session["columns"])
    db_rows = conn.execute(
        "SELECT id, row_index, data FROM rows WHERE session_id=? ORDER BY row_index",
        (session_id,),
    ).fetchall()
    conn.close()

    all_rows = [{"id": r["id"], "row_index": r["row_index"], "data": json.loads(r["data"])} for r in db_rows]
    target_rows = [r for r in all_rows if r["id"] in set(body.row_ids)] if body.row_ids else all_rows

    firm_col = detect_firm_col(columns)
    website_col = detect_website_col(columns)
    new_columns = [dp["column"] for dp in body.data_points if dp.get("column")]

    # Dry run: return cost estimate immediately, no job needed
    if body.dry_run:
        # Batched model: 1–2 Tavily calls + 1–2 Gemini calls per FIRM (not per data point)
        rows_n = len(target_rows)
        tavily_cost = rows_n * 1.5 * 0.002   # avg 1.5 searches/firm at $0.002 each
        # Gemini 2.0 Flash: ~3000 input tokens (batched context) + ~200 output tokens
        gemini_cost = rows_n * 1.5 * (3000 * 0.10 / 1_000_000 + 200 * 0.40 / 1_000_000)
        return {
            "dry_run": True,
            "rows": rows_n,
            "data_points": len(body.data_points),
            "estimated_api_calls": round(rows_n * 1.5 * 2),  # searches + LLM calls
            "estimated_cost_usd": round(tavily_cost + gemini_cost, 4),
            "estimated_time_min": round(rows_n / 5 * 6 / 60, 1),  # ~6s/firm with batching
        }

    # Create job record
    job_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        "INSERT INTO jobs (id, session_id, status, progress_done, progress_total, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (job_id, session_id, "pending", 0, len(target_rows), now_iso(), now_iso()),
    )
    conn.commit()
    conn.close()

    # Kick off background task — returns immediately
    background_tasks.add_task(
        _run_enrichment_job,
        job_id, session_id, target_rows,
        body.data_points, firm_col, website_col,
        new_columns, columns[:],
    )

    return {
        "job_id": job_id,
        "rows": len(target_rows),
        "data_points": len(body.data_points),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, x_password: Optional[str] = Header(None)):
    verify_password(x_password)
    conn = get_db()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job["id"],
        "session_id": job["session_id"],
        "status": job["status"],
        "progress_done": job["progress_done"],
        "progress_total": job["progress_total"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


# ── Serve React frontend (production) ─────────────────────────────────────────

_DIST = os.path.join(os.path.dirname(__file__), "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="static")

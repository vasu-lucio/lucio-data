import asyncio
import io
import json
import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# ── Page config — MUST be the very first Streamlit call ───────────────────────
st.set_page_config(
    page_title="Lucio Enrichment",
    page_icon="⚖️",
    layout="wide",
)

# ── Inject secrets into env before importing enrichment ───────────────────────
# enrichment.py reads API keys at module level via os.getenv, so they must
# be in the environment before the import runs.
load_dotenv()
try:
    for _key in ("APP_PASSWORD", "OPENROUTER_API_KEY", "TAVILY_API_KEY"):
        if _key not in os.environ:
            _val = st.secrets.get(_key, "")
            if _val:
                os.environ[_key] = _val
except Exception:
    pass  # no secrets file locally — that's fine

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import enrichment as _enrichment_mod
from enrichment import enrich_row, PRESET_PROMPTS  # noqa: E402

# Patch module-level key variables in case they were captured before secrets loaded
_enrichment_mod.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
_enrichment_mod.TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY", "")

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
# /tmp is always writable on Streamlit Cloud; the repo directory is read-only
SESSIONS_DIR = Path("/tmp/enrichment_sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

PRESETS = {
    "COO Name":               "coo_name",
    "COO Email":              "coo_email",
    "COO Phone":              "coo_phone",
    "COO LinkedIn":           "coo_linkedin",
    "Managing Partner Name":  "mp_name",
    "Managing Partner Email": "mp_email",
    "Attorney Count":         "attorney_count",
    "Website":                "website",
    "Office Locations":       "offices",
    "Practice Areas":         "practice_areas",
}

# ── Session helpers ────────────────────────────────────────────────────────────

def save_session(session_id: str, name: str, df: pd.DataFrame):
    df.to_parquet(SESSIONS_DIR / f"{session_id}.parquet", index=False)
    meta = {
        "id": session_id,
        "name": name,
        "row_count": len(df),
        "columns": list(df.columns),
        "updated_at": datetime.utcnow().isoformat(),
    }
    (SESSIONS_DIR / f"{session_id}.json").write_text(json.dumps(meta))


def list_sessions():
    sessions = []
    for p in SESSIONS_DIR.glob("*.json"):
        try:
            sessions.append(json.loads(p.read_text()))
        except Exception:
            pass
    return sorted(sessions, key=lambda s: s.get("updated_at", ""), reverse=True)


def load_df(session_id: str) -> Optional[pd.DataFrame]:
    p = SESSIONS_DIR / f"{session_id}.parquet"
    return pd.read_parquet(p) if p.exists() else None


# ── Async helper ───────────────────────────────────────────────────────────────

def run_async(coro):
    """Run a coroutine in a fresh thread+event loop. No Streamlit calls inside."""
    result = {}
    error  = {}
    def _run():
        try:
            result["value"] = asyncio.run(coro)
        except Exception as e:
            error["msg"] = str(e)
    t = threading.Thread(target=_run)
    t.start()
    t.join()
    if error:
        raise RuntimeError(error["msg"])
    return result.get("value")


async def _enrich_one(firm: str, site: Optional[str], data_points: list) -> list:
    """Enrich a single firm. Creates its own httpx client."""
    async with httpx.AsyncClient(timeout=60) as client:
        return await enrich_row(firm, site, data_points, client)


# ── Auth ───────────────────────────────────────────────────────────────────────

if not st.session_state.get("authed"):
    _, col, _ = st.columns([1, 1, 1])
    with col:
        st.markdown("## ⚖️ Lucio Enrichment")
        st.caption("Data enrichment for law firm outreach")
        pwd = st.text_input("Password", type="password")
        if st.button("Enter", type="primary", use_container_width=True):
            if pwd == APP_PASSWORD:
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("Incorrect password")
    st.stop()

# ── Navigation ─────────────────────────────────────────────────────────────────

if "page" not in st.session_state:
    st.session_state.page = "sessions"
if "session_id" not in st.session_state:
    st.session_state.session_id = None

# ══════════════════════════════════════════════════════════════════════════════
# SESSIONS LIST
# ══════════════════════════════════════════════════════════════════════════════

if st.session_state.page == "sessions":

    c1, c2 = st.columns([4, 1])
    with c1:
        st.markdown("## ⚖️ Lucio Enrichment")
        st.caption("Data enrichment for law firm outreach")
    with c2:
        if st.button("Log out", use_container_width=True):
            st.session_state.authed = False
            st.rerun()

    st.divider()

    uploaded = st.file_uploader(
        "Upload a CSV or Excel file to create a new session",
        type=["csv", "xlsx", "xls"],
    )

    if uploaded:
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
        df = df.fillna("").astype(str)
        sid = str(uuid.uuid4())
        save_session(sid, uploaded.name, df)
        st.session_state.session_id = sid
        st.session_state.page = "spreadsheet"
        st.rerun()

    st.divider()

    sessions = list_sessions()
    if not sessions:
        st.info("No sessions yet — upload a file above to get started.")
    else:
        st.caption(f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}")
        for s in sessions:
            c1, c2 = st.columns([5, 1])
            with c1:
                label = f"📊 **{s['name']}** — {s['row_count']} rows · {len(s['columns'])} columns"
                if st.button(label, key=s["id"], use_container_width=True):
                    st.session_state.session_id = s["id"]
                    st.session_state.page = "spreadsheet"
                    st.rerun()
            with c2:
                if st.button("Delete", key=f"del_{s['id']}", use_container_width=True):
                    (SESSIONS_DIR / f"{s['id']}.parquet").unlink(missing_ok=True)
                    (SESSIONS_DIR / f"{s['id']}.json").unlink(missing_ok=True)
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# SPREADSHEET VIEW
# ══════════════════════════════════════════════════════════════════════════════

elif st.session_state.page == "spreadsheet":

    sid  = st.session_state.session_id
    df   = load_df(sid)
    if df is None:
        st.error("Session not found.")
        st.session_state.page = "sessions"
        st.rerun()

    meta = json.loads((SESSIONS_DIR / f"{sid}.json").read_text())
    job  = st.session_state.get("enrich_job")

    # ── Header ─────────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns([1, 5, 1, 1])
    with c1:
        if st.button("← Back"):
            st.session_state.enrich_job = None
            st.session_state.page = "sessions"
            st.rerun()
    with c2:
        st.markdown(f"### {meta['name']}")
        st.caption(f"{meta['row_count']} rows · {len(meta['columns'])} columns")
    with c3:
        st.download_button(
            "⬇️ CSV",
            df.to_csv(index=False).encode(),
            f"{meta['name']}.csv",
            "text/csv",
            use_container_width=True,
        )
    with c4:
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        st.download_button(
            "⬇️ Excel",
            buf.getvalue(),
            f"{meta['name']}.xlsx",
            use_container_width=True,
        )

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### ✨ Enrich Data")

        firm_col = st.selectbox("Firm name column", df.columns.tolist())

        st.markdown("**Data points**")
        n = int(st.number_input("How many", 1, 6, 1))

        data_points = []
        for i in range(n):
            with st.expander(f"Point {i + 1}", expanded=True):
                use_preset = st.toggle("Use preset", key=f"use_preset_{i}")
                if use_preset:
                    label = st.selectbox("Preset", list(PRESETS.keys()), key=f"preset_{i}")
                    data_points.append({"column": label, "prompt": "", "preset": PRESETS[label]})
                else:
                    col_name = st.text_input("Column name", key=f"col_{i}", placeholder="e.g. COO Name")
                    prompt   = st.text_area("What to find", key=f"prompt_{i}", height=80,
                                            placeholder="e.g. Find the Chief Operating Officer")
                    if col_name.strip():
                        data_points.append({"column": col_name.strip(), "prompt": prompt.strip(), "preset": None})

        scope   = st.radio("Scope", ["All rows", "Re-run not found"], horizontal=True)
        dry_run = st.checkbox("Dry run (estimate only)")

        st.divider()

        if job and job.get("sid") == sid:
            if st.button("⛔ Cancel", use_container_width=True):
                st.session_state.enrich_job = None
                st.rerun()
        else:
            if st.button("✨ Enrich", type="primary", use_container_width=True, disabled=not data_points):
                if dry_run:
                    target_count = len(df)
                    if scope == "Re-run not found":
                        cols_to_check = [dp["column"] for dp in data_points if dp["column"] in df.columns]
                        if cols_to_check:
                            target_count = int(df[cols_to_check].isin(["not_found", "not_sure"]).any(axis=1).sum())
                    cost = round(target_count * 0.002, 2)
                    mins = max(1, round(target_count * 0.25))
                    st.info(f"**Estimate**  \n{target_count} rows · {len(data_points)} data points  \n~${cost} · ~{mins} min")
                else:
                    if scope == "Re-run not found":
                        cols_to_check = [dp["column"] for dp in data_points if dp["column"] in df.columns]
                        mask = df[cols_to_check].isin(["not_found", "not_sure"]).any(axis=1) if cols_to_check else pd.Series(True, index=df.index)
                        target_idx = df[mask].index.tolist()
                    else:
                        target_idx = df.index.tolist()

                    if not target_idx:
                        st.warning("No rows to enrich.")
                    else:
                        for dp in data_points:
                            if dp["column"] and dp["column"] not in df.columns:
                                df[dp["column"]] = ""
                        save_session(sid, meta["name"], df)

                        st.session_state.enrich_job = {
                            "sid":         sid,
                            "target_idx":  target_idx,
                            "data_points": data_points,
                            "firm_col":    firm_col,
                            "current":     0,
                            "logs":        [],
                            "error":       None,
                        }
                        st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # ENRICHMENT — one row per script run, rerun until all done
    # ══════════════════════════════════════════════════════════════════════════

    if job and job.get("sid") == sid:
        total   = len(job["target_idx"])
        current = job["current"]

        st.progress(current / total if total else 1,
                    text=f"Enriched {current} / {total} rows")

        # Show log of completed rows
        icon_map = {"found": "✅", "not_sure": "⚠️", "not_available": "🚫"}
        for entry in job["logs"]:
            parts = "  ·  ".join(
                f"{icon_map.get(r['status'], '—')} **{r['column']}**: {r.get('value') or r['status']}"
                for r in entry["results"]
            )
            st.caption(f"🔍 **{entry['firm']}** — {parts}")

        if job["error"]:
            st.error(f"Stopped on row {current + 1}: {job['error']}")
            st.session_state.enrich_job = None

        elif current < total:
            idx  = job["target_idx"][current]
            df2  = load_df(sid)
            firm = str(df2.loc[idx].get(job["firm_col"], ""))
            site = str(df2.loc[idx].get("Website", "")) if "Website" in df2.columns else None

            with st.spinner(f"Row {current + 1}/{total}: {firm}…"):
                try:
                    results = run_async(_enrich_one(firm, site, job["data_points"]))
                    for r in results:
                        df2.at[idx, r["column"]] = r.get("value") or r.get("status", "not_found")
                    save_session(sid, meta["name"], df2)
                    job["logs"].append({"firm": firm, "results": results})
                    job["current"] += 1
                    job["error"] = None
                except Exception as e:
                    job["error"] = str(e)

            st.rerun()

        else:
            st.success(f"Done! Enriched {total} rows.")
            st.session_state.enrich_job = None
            st.rerun()

    else:
        # ── Editable table ─────────────────────────────────────────────────────
        df = load_df(sid)
        edited = st.data_editor(
            df,
            use_container_width=True,
            height=600,
            num_rows="fixed",
            key=f"editor_{sid}",
        )
        if not edited.equals(df):
            save_session(sid, meta["name"], edited)

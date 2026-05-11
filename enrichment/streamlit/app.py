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
    for _key in ("APP_PASSWORD", "OPENROUTER_API_KEY", "TAVILY_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"):
        if _key not in os.environ:
            _val = st.secrets.get(_key, "")
            if _val:
                os.environ[_key] = _val
except Exception:
    pass  # no secrets file locally — that's fine

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
import enrichment as _enrichment_mod
from enrichment import enrich_row, PRESET_PROMPTS  # noqa: E402

# Patch module-level keys — headers are now built dynamically so this is
# just a safety net for anything that still reads these at call time
_enrichment_mod.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
_enrichment_mod.TAVILY_API_KEY     = os.getenv("TAVILY_API_KEY", "")

APP_PASSWORD  = os.getenv("APP_PASSWORD", "")
SUPABASE_URL  = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY  = os.getenv("SUPABASE_KEY", "")
USE_SUPABASE  = bool(SUPABASE_URL and SUPABASE_KEY)

# Local fallback (ephemeral on Streamlit Cloud — wiped on reboot)
SESSIONS_DIR = Path("/tmp/enrichment_sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

PRESETS = {
    # Leadership pack — all bundled into one targeted search
    "Managing Partner Name":    "mp_name",
    "Managing Partner Email":   "mp_email",
    "Managing Partner Phone":   "mp_phone",
    "Managing Partner LinkedIn":"mp_linkedin",
    "COO Name":                 "coo_name",
    "COO Email":                "coo_email",
    "COO Phone":                "coo_phone",
    "COO LinkedIn":             "coo_linkedin",
    "CIO Name":                 "cio_name",
    "CIO Email":                "cio_email",
    "CIO Phone":                "cio_phone",
    "CIO LinkedIn":             "cio_linkedin",
    # Firm info
    "Attorney Count":           "attorney_count",
    "Website":                  "website",
    "Office Locations":         "offices",
    "Practice Areas":           "practice_areas",
}

# ── Session helpers ────────────────────────────────────────────────────────────
# If SUPABASE_URL + SUPABASE_KEY are set, sessions persist across reboots.
# Otherwise, /tmp is used (wiped on reboot).

import base64

def _supa_post(path: str, payload: dict) -> dict:
    r = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json() if r.content else {}


def _supa_get(path: str, params: dict = None) -> list:
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
        params=params or {},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _supa_delete(path: str, params: dict) -> None:
    httpx.delete(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
        params=params,
        timeout=15,
    )


def _df_to_b64(df: pd.DataFrame) -> str:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return base64.b64encode(buf.getvalue()).decode()


def _b64_to_df(b64: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(base64.b64decode(b64)))


def save_session(session_id: str, name: str, df: pd.DataFrame):
    now = datetime.utcnow().isoformat()
    if USE_SUPABASE:
        _supa_post("sessions", {
            "id":         session_id,
            "name":       name,
            "row_count":  len(df),
            "columns":    json.dumps(list(df.columns)),
            "data":       _df_to_b64(df),
            "updated_at": now,
        })
    else:
        df.to_parquet(SESSIONS_DIR / f"{session_id}.parquet", index=False)
        (SESSIONS_DIR / f"{session_id}.json").write_text(json.dumps({
            "id": session_id, "name": name,
            "row_count": len(df), "columns": list(df.columns), "updated_at": now,
        }))


def list_sessions() -> list:
    if USE_SUPABASE:
        try:
            rows = _supa_get("sessions", {"select": "id,name,row_count,columns,updated_at", "order": "updated_at.desc"})
            for r in rows:
                r["columns"] = json.loads(r["columns"]) if isinstance(r["columns"], str) else r["columns"]
            return rows
        except Exception:
            return []
    else:
        sessions = []
        for p in SESSIONS_DIR.glob("*.json"):
            try:
                sessions.append(json.loads(p.read_text()))
            except Exception:
                pass
        return sorted(sessions, key=lambda s: s.get("updated_at", ""), reverse=True)


def load_df(session_id: str) -> Optional[pd.DataFrame]:
    if USE_SUPABASE:
        try:
            rows = _supa_get("sessions", {"select": "data", "id": f"eq.{session_id}"})
            return _b64_to_df(rows[0]["data"]) if rows else None
        except Exception:
            return None
    else:
        p = SESSIONS_DIR / f"{session_id}.parquet"
        return pd.read_parquet(p) if p.exists() else None


def delete_session(session_id: str):
    if USE_SUPABASE:
        _supa_delete("sessions", {"id": f"eq.{session_id}"})
    else:
        (SESSIONS_DIR / f"{session_id}.parquet").unlink(missing_ok=True)
        (SESSIONS_DIR / f"{session_id}.json").unlink(missing_ok=True)


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
                    delete_session(s["id"])
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

        st.divider()
        st.markdown("**Roles to find**")
        st.caption("Each role → Name, Email, Phone, LinkedIn")

        if "role_titles" not in st.session_state:
            st.session_state.role_titles = ["Managing Partner", "COO"]

        for i, title in enumerate(st.session_state.role_titles):
            c1, c2 = st.columns([5, 1])
            with c1:
                st.session_state.role_titles[i] = st.text_input(
                    f"Role {i+1}", value=title, key=f"role_{i}",
                    label_visibility="collapsed", placeholder="e.g. COO"
                )
            with c2:
                if st.button("×", key=f"del_role_{i}", help="Remove"):
                    st.session_state.role_titles.pop(i)
                    st.rerun()

        if st.button("+ Add role", use_container_width=True):
            st.session_state.role_titles.append("")
            st.rerun()

        # Build data_points from role titles — each title expands to 4 fields
        data_points = []
        for title in st.session_state.role_titles:
            t = title.strip()
            if not t:
                continue
            for field in ("Name", "Email", "Phone", "LinkedIn"):
                data_points.append({
                    "column": f"{t} {field}",
                    "prompt": f"{field} of the {t}.",
                    "preset": None,
                    "role_title": t,
                    "field_type": field.lower(),
                })

        st.divider()
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

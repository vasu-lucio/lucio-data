import streamlit as st
import sqlite3
import json
import glob
import os
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime
import sys
sys.path.insert(0, os.path.dirname(__file__))
import gmail_helper

# ── Config ─────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Lucio — Outreach CRM",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(os.path.dirname(__file__), "outreach.db")

STATUSES   = ["To Contact", "Email Sent", "Replied", "Meeting Booked", "Not a Fit"]
STATUS_COLORS = {
    "To Contact":     "#6B7280",
    "Email Sent":     "#3B82F6",
    "Replied":        "#F59E0B",
    "Meeting Booked": "#10B981",
    "Not a Fit":      "#EF4444",
}

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background: #0F1117; }
    [data-testid="stSidebar"] { background: #1A1D27; }
    .firm-card {
        background: #1E2130;
        border: 1px solid #2D3148;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 12px;
        transition: border-color 0.2s;
    }
    .firm-card:hover { border-color: #5B6AD0; }
    .contact-row {
        background: #252840;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 6px 0;
    }
    .badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
    }
    .email-box {
        background: #1A1D27;
        border: 1px solid #2D3148;
        border-radius: 8px;
        padding: 16px;
        font-family: monospace;
        font-size: 13px;
        white-space: pre-wrap;
        line-height: 1.6;
    }
    .stat-card {
        background: #1E2130;
        border: 1px solid #2D3148;
        border-radius: 12px;
        padding: 24px;
        text-align: center;
    }
    .metric-value { font-size: 2.2rem; font-weight: 700; color: #7C8DFF; }
    .metric-label { font-size: 0.85rem; color: #9CA3AF; margin-top: 4px; }
    h1, h2, h3 { color: #E5E7EB !important; }
    .stSelectbox label, .stTextInput label { color: #9CA3AF !important; }
    div[data-testid="stMetricValue"] { color: #7C8DFF; }
</style>
""", unsafe_allow_html=True)

# ── Database ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS status (
            firm_slug TEXT PRIMARY KEY,
            firm_name TEXT,
            status    TEXT DEFAULT 'To Contact',
            notes     TEXT DEFAULT '',
            updated   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            firm_slug    TEXT PRIMARY KEY,
            to_name      TEXT,
            to_title     TEXT,
            to_email     TEXT,
            cc_emails    TEXT DEFAULT '',
            subject      TEXT,
            body         TEXT,
            updated      TEXT,
            draft_saved  INTEGER DEFAULT 0,
            draft_sender TEXT DEFAULT ''
        )
    """)
    # migrate: add columns if missing
    for col, definition in [
        ("cc_emails",    "TEXT DEFAULT ''"),
        ("draft_saved",  "INTEGER DEFAULT 0"),
        ("draft_sender", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {col} {definition}")
        except Exception:
            pass
    conn.commit()
    return conn

def get_status(conn, slug):
    row = conn.execute("SELECT status, notes FROM status WHERE firm_slug=?", (slug,)).fetchone()
    return (row[0], row[1]) if row else ("To Contact", "")

def set_status(conn, slug, firm_name, status, notes):
    conn.execute("""
        INSERT INTO status (firm_slug, firm_name, status, notes, updated)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(firm_slug) DO UPDATE SET
            status=excluded.status, notes=excluded.notes, updated=excluded.updated
    """, (slug, firm_name, status, notes, datetime.now().isoformat()))
    conn.commit()

def get_email(conn, slug, fallback_em: dict):
    """Return email dict — from DB if edited, else from JSON."""
    row = conn.execute(
        "SELECT to_name, to_title, to_email, cc_emails, subject, body, draft_saved, draft_sender FROM emails WHERE firm_slug=?", (slug,)
    ).fetchone()
    if row:
        return {"to_name": row[0], "to_title": row[1], "to_email": row[2],
                "cc_emails": row[3] or "", "subject": row[4], "body": row[5],
                "draft_saved": bool(row[6]), "draft_sender": row[7] or ""}
    return {**fallback_em, "cc_emails": "", "draft_saved": False, "draft_sender": ""}

def mark_draft_saved(conn, slug, sender):
    conn.execute("""
        INSERT INTO emails (firm_slug, draft_saved, draft_sender, updated)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(firm_slug) DO UPDATE SET
            draft_saved=1, draft_sender=excluded.draft_sender, updated=excluded.updated
    """, (slug, sender, datetime.now().isoformat()))
    conn.commit()

def save_email(conn, slug, to_name, to_title, to_email, cc_emails, subject, body):
    conn.execute("""
        INSERT INTO emails (firm_slug, to_name, to_title, to_email, cc_emails, subject, body, updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(firm_slug) DO UPDATE SET
            to_name=excluded.to_name, to_title=excluded.to_title, to_email=excluded.to_email,
            cc_emails=excluded.cc_emails, subject=excluded.subject, body=excluded.body,
            updated=excluded.updated
    """, (slug, to_name, to_title, to_email, cc_emails, subject, body, datetime.now().isoformat()))
    conn.commit()

# ── Data loading ───────────────────────────────────────────────────────────────
@st.cache_data
def load_firms():
    firms = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            if "firm_data" in data and "email" in data:
                slug = os.path.basename(path).replace(".json", "")
                data["_slug"] = slug
                firms.append(data)
        except Exception:
            pass
    return firms

def slug(name):
    import re
    s = re.sub(r"[^\w\s]", "", name).strip()
    return re.sub(r"\s+", "_", s)[:60].lower()

def person_summary(p):
    if not p or not p.get("name"):
        return None
    parts = [p["name"]]
    if p.get("title"):  parts.append(p["title"])
    return " · ".join(parts)

# ── Email helpers ───────────────────────────────────────────────────────────────
INPERSON_KEYWORDS = ["new york", "ny", "brooklyn", "manhattan", "bronx", "queens",
                     "new jersey", "nj", "newark", "hoboken", "jersey city",
                     "philadelphia", "philly", "pa"]

def is_inperson_eligible(city: str) -> bool:
    c = (city or "").lower()
    return any(kw in c for kw in INPERSON_KEYWORDS)

COO_KEYWORDS = ["coo", "chief operating", "director of operations", "operations director",
                "chief operations", "director, operations", "operations manager",
                "chief administrative", "cao", "firm administrator", "office manager",
                "executive director", "director of administration"]

def is_coo_title(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in COO_KEYWORDS)

def get_coo_contact(people: dict) -> dict:
    """Return best ops contact: prefer COO/ops title in tech_contact, then any role with ops title, then tech_contact, then MP."""
    # First pass: find anyone with a COO-like title
    for role in ["tech_contact", "ai_contact", "co_managing_partner", "managing_partner"]:
        p = people.get(role) or {}
        if p.get("name") and is_coo_title(p.get("title","")):
            return p
    for p in (people.get("additional_contacts") or []):
        if p.get("name") and is_coo_title(p.get("title","")):
            return p
    # Fallback: tech_contact, then managing_partner
    for role in ["tech_contact", "ai_contact", "co_managing_partner", "managing_partner"]:
        p = people.get(role) or {}
        if p.get("name"): return p
    return {}

def get_cc_contact(people: dict, primary: dict) -> dict:
    """Return best CC contact — managing partner if primary isn't MP, else next available."""
    primary_name = (primary.get("name") or "").lower()
    # Prefer managing partner if they're not the primary
    mp = people.get("managing_partner") or {}
    if mp.get("name") and mp.get("name","").lower() != primary_name and mp.get("email"):
        return mp
    # Then co-managing partner
    for role in ["co_managing_partner", "tech_contact", "ai_contact"]:
        p = people.get(role) or {}
        if p.get("name") and p.get("name","").lower() != primary_name and p.get("email"):
            return p
    return {}

import datetime as _dt
import random as _random
import hashlib as _hashlib

def next_week_slots(seed: str = "") -> tuple:
    """
    Return (slot1_date, time1, slot2_date, time2) for two different days next week.
    Uses a seed (e.g. firm slug) so slots are consistent per firm but vary across firms.
    Days: Mon–Fri only. Times vary: 9am, 10am, 11am, 2pm, 3pm, 4pm.
    The two slots are always on different days and different times of day (one AM, one PM).
    """
    today = _dt.date.today()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    next_monday = today + _dt.timedelta(days=days_until_monday)

    # Seed randomness from firm slug so it's stable per firm
    rng = _random.Random(_hashlib.md5(seed.encode()).hexdigest())

    # Pick 2 different weekdays (0=Mon … 4=Fri)
    day_offsets = rng.sample(range(5), 2)
    day_offsets.sort()  # earlier slot first
    date1 = next_monday + _dt.timedelta(days=day_offsets[0])
    date2 = next_monday + _dt.timedelta(days=day_offsets[1])

    # AM slot: 9, 10, or 11am — PM slot: 2, 3, or 4pm
    am_times = [_dt.time(9, 0), _dt.time(10, 0), _dt.time(11, 0)]
    pm_times = [_dt.time(14, 0), _dt.time(15, 0), _dt.time(16, 0)]
    # Randomly assign AM/PM to the two slots
    if rng.random() > 0.5:
        time1 = rng.choice(am_times)
        time2 = rng.choice(pm_times)
    else:
        time1 = rng.choice(pm_times)
        time2 = rng.choice(am_times)

    return date1, time1, date2, time2

def auto_subject(firm_name: str) -> str:
    return f"{firm_name} / Lucio AI"

def _build_signature(name: str, phone: str, email: str, linkedin: str) -> str:
    """Build a clean HTML email signature with the Lucio logo."""
    import base64
    logo_path = os.path.join(os.path.dirname(__file__), "lucio_logo.png")
    logo_tag = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        logo_tag = f'<img src="data:image/png;base64,{b64}" width="100" style="display:block;margin-top:8px;">'
    return f"""
<br><br>
<span style="font-family:Arial,sans-serif;font-size:14px;">Best,<br><br>
<strong>{name}</strong><br>
Lucio AI<br>
<a href="tel:{phone}" style="color:inherit;text-decoration:none;">{phone}</a><br>
<a href="mailto:{email}" style="color:inherit;text-decoration:none;">{email}</a><br>
<a href="{linkedin}" style="color:inherit;text-decoration:none;">LinkedIn</a> &nbsp;|&nbsp;
<a href="https://www.lucioai.com" style="color:inherit;text-decoration:none;">lucioai.com</a>
</span><br>
{logo_tag}"""

SENDER_SIGNATURES = {
    "Vasu": _build_signature(
        name="Vasu",
        phone="+1 (213) 883-3255",
        email="vasu@lucioai.com",
        linkedin="https://www.linkedin.com/in/vasulucio/",
    ),
    "Anshul": _build_signature(
        name="Anshul",
        phone="+1 (650) 283 5574",
        email="anshul@lucioai.com",
        linkedin="https://www.linkedin.com/in/anshulbutani/",
    ),
}

# ── Password Gate ──────────────────────────────────────────────────────────────
USERS_AUTH = {
    "Vasu":   "Vasu123456",
    "Anshul": "Anshul123456",
}

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
    st.session_state.sender = "Vasu"

if not st.session_state.authenticated:
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_c = st.columns([1, 1, 1])[1]
    with col_c:
        st.markdown("## ⚖️ Lucio CRM")
        name = st.selectbox("Who are you?", ["Vasu", "Anshul"])
        pwd = st.text_input("Password", type="password")
        if st.button("Enter", use_container_width=True):
            if pwd == USERS_AUTH.get(name):
                st.session_state.authenticated = True
                st.session_state.sender = name
                st.rerun()
            else:
                st.error("Incorrect password")
    st.stop()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚖️ Lucio CRM")
    st.markdown("---")
    page = st.radio("Navigate", ["📊 Dashboard", "🏢 Firm Browser", "📋 Pipeline", "✉️ Emails"])
    st.markdown("---")

    sender = st.session_state.get("sender", "Vasu")
    st.markdown(f"**Logged in as: {sender}**")
    if st.button("Log out", key="logout"):
        st.session_state.authenticated = False
        st.session_state.sender = "Vasu"
        st.rerun()

    st.markdown("**Gmail**")
    if gmail_helper.is_authorized(sender):
        st.success(f"✅ {sender} connected")
        if st.button("Disconnect", key="gmail_revoke"):
            gmail_helper.revoke_token(sender)
            st.session_state.pop("gmail_flow", None)
            st.session_state.pop("gmail_flow_user", None)
            st.rerun()
    elif gmail_helper.has_credentials(sender):
        # Step 1: generate auth URL
        if st.session_state.get("gmail_flow_user") != sender:
            if st.button(f"🔗 Authorize {sender}", key="gmail_auth"):
                auth_url, flow, err = gmail_helper.get_auth_url(sender)
                if err:
                    st.error(f"Error: {err}")
                else:
                    st.session_state["gmail_flow"] = flow
                    st.session_state["gmail_flow_user"] = sender
                    st.session_state["gmail_auth_url"] = auth_url
                    st.rerun()
        else:
            # Step 2: show stored link + code input (do NOT regenerate flow)
            auth_url = st.session_state.get("gmail_auth_url", "")
            st.markdown(f"**[👉 Click to authorize {sender}]({auth_url})**")
            code = st.text_input("Paste the code from Google here:", key="gmail_code")
            if st.button("✅ Submit Code", key="gmail_submit"):
                err = gmail_helper.exchange_code(st.session_state["gmail_flow"], code.strip(), sender)
                if err:
                    st.error(f"Error: {err}")
                else:
                    st.session_state.pop("gmail_flow", None)
                    st.session_state.pop("gmail_flow_user", None)
                    st.session_state.pop("gmail_auth_url", None)
                    st.success("Authorized!")
                    st.rerun()
            if st.button("Cancel", key="gmail_cancel"):
                st.session_state.pop("gmail_flow", None)
                st.session_state.pop("gmail_flow_user", None)
                st.session_state.pop("gmail_auth_url", None)
                st.rerun()
    else:
        st.error(f"❌ No credentials for {sender}")
        st.markdown(
            f"<small>Add `gmail_credentials_{sender.lower()}.json` to the app folder</small>",
            unsafe_allow_html=True
        )
    st.markdown("---")
    st.markdown(f"<small style='color:#6B7280'>Data dir: {DATA_DIR}</small>", unsafe_allow_html=True)

conn  = get_db()
firms = load_firms()

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊 Dashboard":
    st.title("📊 Outreach Dashboard")
    st.markdown("---")

    # Gather stats
    all_statuses = []
    total_contacts_with_email = 0
    total_ai_signals = 0
    total_people = 0
    cities = {}

    for rec in firms:
        fd = rec["firm_data"]
        s_slug = rec["_slug"]
        status, _ = get_status(conn, s_slug)
        all_statuses.append(status)

        city = fd.get("city", "Unknown") or "Unknown"
        cities[city] = cities.get(city, 0) + 1

        people = fd.get("people", {}) or {}
        for role in ["managing_partner","co_managing_partner","tech_contact","ai_contact"]:
            p = people.get(role) or {}
            if p.get("name"): total_people += 1
            if p.get("email"): total_contacts_with_email += 1
        for p in (people.get("founders") or []) + (people.get("additional_contacts") or []):
            if p.get("name"): total_people += 1
            if p.get("email"): total_contacts_with_email += 1

        if fd.get("technology_signals") or (people.get("ai_contact") or {}).get("ai_notes"):
            total_ai_signals += 1

    status_counts = {s: all_statuses.count(s) for s in STATUSES}

    # Top metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    metrics = [
        ("⚖️ Firms", len(firms)),
        ("👥 People Found", total_people),
        ("📧 Emails Found", total_contacts_with_email),
        ("🤖 AI Signals", total_ai_signals),
        ("📅 Meetings", status_counts.get("Meeting Booked", 0)),
    ]
    for col, (label, val) in zip([col1,col2,col3,col4,col5], metrics):
        col.markdown(f"""
        <div class="stat-card">
            <div class="metric-value">{val}</div>
            <div class="metric-label">{label}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("Pipeline Status")
        fig = go.Figure(go.Bar(
            x=list(status_counts.values()),
            y=list(status_counts.keys()),
            orientation="h",
            marker_color=[STATUS_COLORS[s] for s in status_counts.keys()],
            text=list(status_counts.values()),
            textposition="outside",
        ))
        fig.update_layout(
            plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
            font_color="#E5E7EB", margin=dict(l=10,r=30,t=10,b=10),
            xaxis=dict(showgrid=False, color="#6B7280"),
            yaxis=dict(color="#E5E7EB"),
            height=280,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        st.subheader("Top Cities")
        top_cities = sorted(cities.items(), key=lambda x: -x[1])[:10]
        fig2 = go.Figure(go.Bar(
            x=[c[1] for c in top_cities],
            y=[c[0] for c in top_cities],
            orientation="h",
            marker_color="#5B6AD0",
            text=[c[1] for c in top_cities],
            textposition="outside",
        ))
        fig2.update_layout(
            plot_bgcolor="#1E2130", paper_bgcolor="#1E2130",
            font_color="#E5E7EB", margin=dict(l=10,r=30,t=10,b=10),
            xaxis=dict(showgrid=False, color="#6B7280"),
            yaxis=dict(color="#E5E7EB"),
            height=280,
        )
        st.plotly_chart(fig2, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: FIRM BROWSER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏢 Firm Browser":
    st.title("🏢 Firm Browser")

    # Row 1: search + status + city
    col_s, col_f, col_city = st.columns([3, 2, 2])
    with col_s:
        search = st.text_input("🔍 Search firms", placeholder="Type firm name...")
    with col_f:
        filter_status = st.selectbox("Status", ["All"] + STATUSES)
    with col_city:
        all_cities = sorted({(rec["firm_data"].get("city") or "Unknown") for rec in firms})
        filter_city = st.selectbox("City", ["All"] + all_cities)

    # Row 2: size filter + sort
    SIZE_ORDER = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+"]
    col_sz, col_sort, col_blank = st.columns([2, 2, 3])
    with col_sz:
        available_sizes = sorted(
            {rec["firm_data"].get("linkedin_size") for rec in firms if rec["firm_data"].get("linkedin_size")},
            key=lambda x: SIZE_ORDER.index(x) if x in SIZE_ORDER else 99
        )
        filter_size = st.selectbox("Firm Size (LinkedIn)", ["All"] + available_sizes)
    with col_sort:
        sort_by = st.selectbox("Sort by", ["Default", "A → Z", "Z → A", "Largest First", "Smallest First"])

    st.markdown("---")

    def size_rank(rec):
        s = rec["firm_data"].get("linkedin_size", "")
        return SIZE_ORDER.index(s) if s in SIZE_ORDER else 99

    def lawyer_count(rec):
        try:    return int(rec["firm_data"].get("lawyer_count") or 0)
        except: return 0

    filtered = []
    for rec in firms:
        fd   = rec["firm_data"]
        s, _ = get_status(conn, rec["_slug"])
        city = fd.get("city") or "Unknown"
        if search and search.lower() not in fd.get("firm_name","").lower(): continue
        if filter_status != "All" and s != filter_status: continue
        if filter_city   != "All" and city != filter_city: continue
        if filter_size   != "All" and fd.get("linkedin_size") != filter_size: continue
        filtered.append((rec, s))

    # Apply sort
    if sort_by == "A → Z":
        filtered.sort(key=lambda x: x[0]["firm_data"].get("firm_name","").lower())
    elif sort_by == "Z → A":
        filtered.sort(key=lambda x: x[0]["firm_data"].get("firm_name","").lower(), reverse=True)
    elif sort_by == "Largest First":
        filtered.sort(key=lambda x: (size_rank(x[0]), -lawyer_count(x[0])))
    elif sort_by == "Smallest First":
        filtered.sort(key=lambda x: (size_rank(x[0]), lawyer_count(x[0])))

    st.markdown(f"<small style='color:#9CA3AF'>Showing {len(filtered)} firms</small>", unsafe_allow_html=True)

    for rec, status in filtered:
        fd     = rec["firm_data"]
        em     = rec["email"]
        people = fd.get("people", {}) or {}
        color  = STATUS_COLORS.get(status, "#6B7280")

        with st.expander(f"**{fd.get('firm_name','')}**  ·  {fd.get('city','')}  ·  {fd.get('lawyer_count') or '?'} lawyers", expanded=False):
            col1, col2 = st.columns([2,1])

            with col1:
                st.markdown(f"*{fd.get('firm_description','')}*")
                if fd.get("practice_areas"):
                    st.markdown("**Practice Areas:** " + " · ".join(fd["practice_areas"][:6]))

                st.markdown("**People**")
                role_labels = [
                    ("managing_partner",    "👔 Managing Partner"),
                    ("co_managing_partner",  "👔 Co-Managing Partner"),
                    ("tech_contact",         "💻 Tech/Ops"),
                    ("ai_contact",           "🤖 AI Contact"),
                ]
                for key, label in role_labels:
                    p = people.get(key) or {}
                    if not p.get("name"): continue
                    parts = []
                    if p.get("email"): parts.append(f"📧 `{p['email']}`")
                    if p.get("phone"): parts.append(f"📞 {p['phone']}")
                    if p.get("linkedin_url"): parts.append(f"[LinkedIn]({p['linkedin_url']})")
                    ai_note = p.get("ai_notes","") if key == "ai_contact" else ""
                    st.markdown(f"""<div class="contact-row">
                        <strong>{label}</strong>: {p['name']} · <em>{p.get('title','')}</em><br>
                        {"&nbsp;&nbsp;".join(parts)}
                        {"<br><small>"+ai_note+"</small>" if ai_note else ""}
                    </div>""", unsafe_allow_html=True)

                for p in (people.get("founders") or []):
                    if not p.get("name"): continue
                    parts = []
                    if p.get("email"): parts.append(f"📧 `{p['email']}`")
                    if p.get("phone"): parts.append(f"📞 {p['phone']}")
                    st.markdown(f"""<div class="contact-row">
                        <strong>🏛 Founder</strong>: {p['name']} · <em>{p.get('title','')}</em><br>
                        {"&nbsp;&nbsp;".join(parts)}
                    </div>""", unsafe_allow_html=True)

                if fd.get("technology_signals"):
                    st.markdown("**🤖 Tech Signals**")
                    for sig in fd["technology_signals"]:
                        st.markdown(f"- {sig}")

            with col2:
                st.markdown(f"**Status:** <span style='color:{color}'>{status}</span>", unsafe_allow_html=True)
                new_status = st.selectbox("Update status", STATUSES,
                    index=STATUSES.index(status), key=f"status_{rec['_slug']}")
                notes_val = get_status(conn, rec["_slug"])[1]
                new_notes = st.text_area("Notes", value=notes_val, key=f"notes_{rec['_slug']}", height=80)
                if st.button("💾 Save", key=f"save_{rec['_slug']}"):
                    set_status(conn, rec["_slug"], fd.get("firm_name",""), new_status, new_notes)
                    st.success("Saved!")

                st.markdown("---")
                _city = fd.get("city","")
                _inperson = is_inperson_eligible(_city)
                if _inperson:
                    st.markdown("📍 **In-person meeting eligible** (NY/NJ/Philadelphia)")
                else:
                    st.markdown("💻 **Remote only** (outside NY/NJ/Philadelphia)")

                st.markdown("**Cold Email**")
                em_live   = get_email(conn, rec["_slug"], em)
                _coo      = get_coo_contact(people)
                _cc_auto  = get_cc_contact(people, _coo)
                _def_subj = auto_subject(fd.get("firm_name",""))
                _def_cc   = em_live.get("cc_emails","") or _cc_auto.get("email","")
                e_name  = st.text_input("To (name)",    value=em_live.get("to_name","") or _coo.get("name",""),   key=f"en_{rec['_slug']}")
                e_title = st.text_input("To (title)",   value=em_live.get("to_title","") or _coo.get("title",""), key=f"et_{rec['_slug']}")
                e_email = st.text_input("To (email)",   value=em_live.get("to_email","") or _coo.get("email",""), key=f"ee_{rec['_slug']}")
                e_cc    = st.text_input("CC (comma-separated emails)", value=_def_cc,                              key=f"ecc_{rec['_slug']}")
                e_subj  = st.text_input("Subject",      value=em_live.get("subject","") or _def_subj,             key=f"es_{rec['_slug']}")

                # Time slot helper — randomized per firm, stable across reloads
                _slot1_def, _time1_def, _slot2_def, _time2_def = next_week_slots(rec["_slug"])
                with st.expander("📅 Insert meeting slots"):
                    sc1, sc2 = st.columns(2)
                    slot1 = sc1.date_input("Slot 1", key=f"s1_{rec['_slug']}", value=_slot1_def)
                    time1 = sc1.time_input("Time 1", key=f"t1_{rec['_slug']}", value=_time1_def)
                    slot2 = sc2.date_input("Slot 2", key=f"s2_{rec['_slug']}", value=_slot2_def)
                    time2 = sc2.time_input("Time 2", key=f"t2_{rec['_slug']}", value=_time2_def)
                    if st.button("Insert slots into body", key=f"ins_{rec['_slug']}"):
                        _meeting = "in person" if _inperson else "over a call"
                        _slot_text = (f"\n\nWould love to connect {_meeting}. Here are two times that work:\n"
                                      f"• {slot1.strftime('%A, %d %B')} at {time1.strftime('%I:%M %p')}\n"
                                      f"• {slot2.strftime('%A, %d %B')} at {time2.strftime('%I:%M %p')}\n"
                                      f"Please let me know which works best for you.")
                        st.session_state[f"eb_{rec['_slug']}"] = st.session_state.get(f"eb_{rec['_slug']}", em_live.get("body","")) + _slot_text
                        st.rerun()

                e_body  = st.text_area("Body", value=em_live.get("body",""), height=180, key=f"eb_{rec['_slug']}")
                col_save, col_draft = st.columns(2)
                with col_save:
                    if st.button("💾 Save Email", key=f"saveem_{rec['_slug']}"):
                        save_email(conn, rec["_slug"], e_name, e_title, e_email, e_cc, e_subj, e_body)
                        st.success("Saved!")
                with col_draft:
                    if st.button("📨 Save Gmail Draft", key=f"draft_{rec['_slug']}"):
                        _sender = st.session_state.get("sender", "Vasu")
                        if not gmail_helper.is_authorized(_sender):
                            st.warning(f"Connect {_sender}'s Gmail first (see sidebar)")
                        elif not e_body.strip():
                            st.warning("Email body is empty")
                        else:
                            _sig  = SENDER_SIGNATURES.get(_sender, "")
                            _body_html = "<div style='font-family:Arial,sans-serif;font-size:14px;'>" + e_body.rstrip().replace("\n", "<br>") + "</div>"
                            _full = _body_html + _sig
                            save_email(conn, rec["_slug"], e_name, e_title, e_email, e_cc, e_subj, e_body)
                            draft_id, err = gmail_helper.create_draft(e_email, e_subj, _full, user=_sender, attach_pdf=True, cc_emails=e_cc)
                            if draft_id:
                                st.success(f"✅ Draft saved via {_sender}'s Gmail! (ID: {draft_id[:12]}…)")
                            else:
                                st.error(f"❌ {err}")

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Pipeline":
    st.title("📋 Pipeline Tracker")

    rows = []
    for rec in firms:
        fd = rec["firm_data"]
        s, notes = get_status(conn, rec["_slug"])
        people = fd.get("people", {}) or {}
        tc = people.get("tech_contact") or {}
        mp = people.get("managing_partner") or {}
        contact = tc if tc.get("name") else mp
        rows.append({
            "Firm": fd.get("firm_name",""),
            "City": fd.get("city",""),
            "Lawyers": fd.get("lawyer_count"),
            "Contact": contact.get("name",""),
            "Title": contact.get("title",""),
            "Email": contact.get("email",""),
            "Status": s,
            "Notes": notes,
            "_slug": rec["_slug"],
        })

    df = pd.DataFrame(rows)

    # Kanban-style columns
    cols = st.columns(len(STATUSES))
    for col, status in zip(cols, STATUSES):
        color = STATUS_COLORS[status]
        subset = df[df["Status"] == status]
        col.markdown(f"""
        <div style='background:{color}22; border-top:3px solid {color};
             border-radius:8px; padding:10px; margin-bottom:8px;'>
        <strong style='color:{color}'>{status}</strong>
        <span style='float:right; background:{color}; color:white;
              padding:1px 8px; border-radius:10px; font-size:12px'>{len(subset)}</span>
        </div>""", unsafe_allow_html=True)
        for _, row in subset.iterrows():
            col.markdown(f"""
            <div style='background:#1E2130; border:1px solid #2D3148;
                 border-radius:8px; padding:10px; margin-bottom:6px; font-size:13px;'>
            <strong>{row['Firm'][:35]}</strong><br>
            <span style='color:#9CA3AF'>{row['Contact']}</span><br>
            <span style='color:#6B7280; font-size:11px'>{row['City']} · {int(row['Lawyers']) if pd.notna(row['Lawyers']) else '?'} lawyers</span>
            </div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Full Table")
    display_df = df[["Firm","City","Lawyers","Contact","Title","Email","Status","Notes"]].copy()
    st.dataframe(display_df, use_container_width=True, height=400)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: EMAILS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "✉️ Emails":
    st.title("✉️ Email Copy")

    # ── Filters ──────────────────────────────────────────────────────────────
    col_se, col_sz2, col_coo, col_draft_f = st.columns([3, 2, 2, 2])
    with col_se:
        search_e = st.text_input("🔍 Search", placeholder="Firm or recipient name...")
    with col_sz2:
        SIZE_ORDER = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+"]
        avail_sizes_e = sorted(
            {rec["firm_data"].get("linkedin_size") for rec in firms if rec["firm_data"].get("linkedin_size")},
            key=lambda x: SIZE_ORDER.index(x) if x in SIZE_ORDER else 99
        )
        filter_size_e = st.selectbox("Firm Size", ["All"] + avail_sizes_e, key="email_size_filter")
    with col_coo:
        filter_coo = st.selectbox("COO / Ops Contact", ["All", "Has COO", "No COO"], key="email_coo_filter")
    with col_draft_f:
        filter_draft = st.selectbox("Draft Status", ["All", "✅ Draft Saved", "⏳ Not Yet Saved"], key="email_draft_filter")

    only_with_email = st.checkbox("Only show firms with a direct email address", value=True)
    st.markdown("---")

    def _has_coo(rec):
        ps = rec["firm_data"].get("people", {}) or {}
        for role in ["tech_contact", "ai_contact", "co_managing_partner", "managing_partner"]:
            p = ps.get(role) or {}
            if p.get("name") and is_coo_title(p.get("title","")): return True
        for p in (ps.get("additional_contacts") or []):
            if p.get("name") and is_coo_title(p.get("title","")): return True
        return False

    for rec in firms:
        fd  = rec["firm_data"]
        em  = rec["email"]
        em_live = get_email(conn, rec["_slug"], em)

        # Apply filters
        if search_e and search_e.lower() not in fd.get("firm_name","").lower() \
                     and search_e.lower() not in (em.get("to_name","") or "").lower():
            continue
        if only_with_email and not em.get("to_email"):
            continue
        if filter_size_e != "All" and fd.get("linkedin_size") != filter_size_e:
            continue
        if filter_coo == "Has COO" and not _has_coo(rec):
            continue
        if filter_coo == "No COO" and _has_coo(rec):
            continue
        if filter_draft == "✅ Draft Saved" and not em_live.get("draft_saved"):
            continue
        if filter_draft == "⏳ Not Yet Saved" and em_live.get("draft_saved"):
            continue
        people    = fd.get("people", {}) or {}
        _coo      = get_coo_contact(people)
        _cc_auto  = get_cc_contact(people, _coo)
        _city     = fd.get("city","")
        _inperson = is_inperson_eligible(_city)
        _def_subj = auto_subject(fd.get("firm_name",""))
        _def_cc   = em_live.get("cc_emails","") or _cc_auto.get("email","")
        _saved    = em_live.get("draft_saved", False)
        _sender_tag = f" · saved by {em_live['draft_sender']}" if _saved and em_live.get("draft_sender") else ""
        _size_tag = f" · {fd.get('linkedin_size','')}" if fd.get("linkedin_size") else ""
        _coo_tag  = " · 👔 COO" if _has_coo(rec) else ""
        _draft_badge = " ✅ Draft saved" if _saved else ""
        label = f"{'✅ ' if _saved else ''}**{fd.get('firm_name','')}**{_size_tag}{_coo_tag} → {em_live.get('to_name','(no recipient yet)')} ({em_live.get('to_title','')}){_draft_badge}{_sender_tag}"
        with st.expander(label):
            if _inperson:
                st.markdown("📍 **In-person eligible** (NY/NJ/Philadelphia)")
            else:
                st.markdown("💻 **Remote only**")
            c1, c2 = st.columns([1, 2])
            with c1:
                e_name  = st.text_input("To (name)",  value=em_live.get("to_name","") or _coo.get("name",""),    key=f"pn_{rec['_slug']}")
                e_title = st.text_input("To (title)", value=em_live.get("to_title","") or _coo.get("title",""),  key=f"pt_{rec['_slug']}")
                e_email = st.text_input("To (email)", value=em_live.get("to_email","") or _coo.get("email",""),  key=f"pe_{rec['_slug']}")
                e_cc    = st.text_input("CC (comma-separated)", value=_def_cc,                                    key=f"pcc_{rec['_slug']}")
                e_subj  = st.text_input("Subject",    value=em_live.get("subject","") or _def_subj,              key=f"ps_{rec['_slug']}")
                _slot1_def, _time1_def, _slot2_def, _time2_def = next_week_slots(rec["_slug"])
                with st.expander("📅 Meeting slots"):
                    slot1 = st.date_input("Slot 1", key=f"ps1_{rec['_slug']}", value=_slot1_def)
                    time1 = st.time_input("Time 1", key=f"pt1_{rec['_slug']}", value=_time1_def)
                    slot2 = st.date_input("Slot 2", key=f"ps2_{rec['_slug']}", value=_slot2_def)
                    time2 = st.time_input("Time 2", key=f"pt2_{rec['_slug']}", value=_time2_def)
                    if st.button("Insert into body", key=f"pins_{rec['_slug']}"):
                        _meeting = "in person" if _inperson else "over a call"
                        _slot_text = (f"\n\nWould love to connect {_meeting}. Here are two times that work:\n"
                                      f"• {slot1.strftime('%A, %d %B')} at {time1.strftime('%I:%M %p')}\n"
                                      f"• {slot2.strftime('%A, %d %B')} at {time2.strftime('%I:%M %p')}\n"
                                      f"Please let me know which works best for you.")
                        st.session_state[f"pb_{rec['_slug']}"] = st.session_state.get(f"pb_{rec['_slug']}", em_live.get("body","")) + _slot_text
                        st.rerun()
            with c2:
                e_body = st.text_area("Email Body", value=em_live.get("body",""), height=220, key=f"pb_{rec['_slug']}")
            col_ps, col_pd = st.columns(2)
            with col_ps:
                if st.button("💾 Save", key=f"psave_{rec['_slug']}"):
                    save_email(conn, rec["_slug"], e_name, e_title, e_email, e_cc, e_subj, e_body)
                    st.success("Saved!")
            with col_pd:
                if st.button("📨 Save Gmail Draft", key=f"pdraft_{rec['_slug']}"):
                    _sender = st.session_state.get("sender", "Vasu")
                    if not gmail_helper.is_authorized(_sender):
                        st.warning(f"Connect {_sender}'s Gmail first (see sidebar)")
                    elif not e_body.strip():
                        st.warning("Email body is empty")
                    else:
                        _sig  = SENDER_SIGNATURES.get(_sender, "")
                        _full = e_body.rstrip() + _sig
                        save_email(conn, rec["_slug"], e_name, e_title, e_email, e_cc, e_subj, e_body)
                        draft_id, err = gmail_helper.create_draft(e_email, e_subj, _full, user=_sender, attach_pdf=True, cc_emails=e_cc)
                        if draft_id:
                            mark_draft_saved(conn, rec["_slug"], _sender)
                            st.success(f"✅ Draft saved via {_sender}'s Gmail! (ID: {draft_id[:12]}…)")
                            st.rerun()
                        else:
                            st.error(f"❌ {err}")

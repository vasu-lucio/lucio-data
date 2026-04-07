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
            firm_slug   TEXT PRIMARY KEY,
            to_name     TEXT,
            to_title    TEXT,
            to_email    TEXT,
            subject     TEXT,
            body        TEXT,
            updated     TEXT
        )
    """)
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
        "SELECT to_name, to_title, to_email, subject, body FROM emails WHERE firm_slug=?", (slug,)
    ).fetchone()
    if row:
        return {"to_name": row[0], "to_title": row[1], "to_email": row[2],
                "subject": row[3], "body": row[4]}
    return fallback_em

def save_email(conn, slug, to_name, to_title, to_email, subject, body):
    conn.execute("""
        INSERT INTO emails (firm_slug, to_name, to_title, to_email, subject, body, updated)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(firm_slug) DO UPDATE SET
            to_name=excluded.to_name, to_title=excluded.to_title, to_email=excluded.to_email,
            subject=excluded.subject, body=excluded.body, updated=excluded.updated
    """, (slug, to_name, to_title, to_email, subject, body, datetime.now().isoformat()))
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

# ── Password Gate ──────────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_c = st.columns([1, 1, 1])[1]
    with col_c:
        st.markdown("## ⚖️ Lucio CRM")
        pwd = st.text_input("Password", type="password")
        if st.button("Enter", use_container_width=True):
            if pwd == "ABC123456":
                st.session_state.authenticated = True
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

    # Gmail connection status
    st.markdown("**Gmail**")
    if gmail_helper.is_authorized():
        st.success("✅ Connected")
        if st.button("Disconnect", key="gmail_revoke"):
            gmail_helper.revoke_token()
            st.rerun()
    elif gmail_helper.has_credentials():
        st.warning("⚠️ Not authorized")
        if st.button("🔗 Authorize Gmail", key="gmail_auth"):
            _, err = gmail_helper.get_service()
            if err == "ok":
                st.success("Authorized!")
                st.rerun()
            else:
                st.error(f"Error: {err}")
    else:
        st.error("❌ No credentials file")
        st.markdown("<small>Add `gmail_credentials.json` to the app folder</small>", unsafe_allow_html=True)
        with st.expander("Setup instructions"):
            st.markdown("""
1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. APIs & Services → Credentials
3. Create **OAuth 2.0 Client ID** (Desktop app)
4. Download JSON → save as `lucio_app/gmail_credentials.json`
5. Click **Authorize Gmail** above
""")
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
                st.markdown("**Cold Email**")
                em_live = get_email(conn, rec["_slug"], em)
                e_name  = st.text_input("To (name)",    value=em_live.get("to_name",""),  key=f"en_{rec['_slug']}")
                e_title = st.text_input("To (title)",   value=em_live.get("to_title",""), key=f"et_{rec['_slug']}")
                e_email = st.text_input("To (email)",   value=em_live.get("to_email",""), key=f"ee_{rec['_slug']}")
                e_subj  = st.text_input("Subject",      value=em_live.get("subject",""),  key=f"es_{rec['_slug']}")
                e_body  = st.text_area("Body", value=em_live.get("body",""), height=180,  key=f"eb_{rec['_slug']}")
                col_save, col_draft = st.columns(2)
                with col_save:
                    if st.button("💾 Save Email", key=f"saveem_{rec['_slug']}"):
                        save_email(conn, rec["_slug"], e_name, e_title, e_email, e_subj, e_body)
                        st.success("Saved!")
                with col_draft:
                    if st.button("📨 Save Gmail Draft", key=f"draft_{rec['_slug']}"):
                        if not gmail_helper.is_authorized():
                            st.warning("Connect Gmail first (see sidebar)")
                        elif not e_body.strip():
                            st.warning("Email body is empty")
                        else:
                            save_email(conn, rec["_slug"], e_name, e_title, e_email, e_subj, e_body)
                            draft_id, err = gmail_helper.create_draft(e_email, e_subj, e_body, attach_pdf=True)
                            if draft_id:
                                st.success(f"✅ Draft saved to Gmail! (ID: {draft_id[:12]}…)")
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

    search_e = st.text_input("🔍 Search", placeholder="Firm or recipient name...")
    only_with_email = st.checkbox("Only show firms with a direct email address", value=True)

    for rec in firms:
        fd = rec["firm_data"]
        em = rec["email"]
        if search_e and search_e.lower() not in fd.get("firm_name","").lower() \
                     and search_e.lower() not in (em.get("to_name","") or "").lower():
            continue
        if only_with_email and not em.get("to_email"):
            continue

        em_live = get_email(conn, rec["_slug"], em)
        label = f"**{fd.get('firm_name','')}** → {em_live.get('to_name','(no recipient yet)')} ({em_live.get('to_title','')})"
        with st.expander(label):
            c1, c2 = st.columns([1, 2])
            with c1:
                e_name  = st.text_input("To (name)",  value=em_live.get("to_name",""),  key=f"pn_{rec['_slug']}")
                e_title = st.text_input("To (title)", value=em_live.get("to_title",""), key=f"pt_{rec['_slug']}")
                e_email = st.text_input("To (email)", value=em_live.get("to_email",""), key=f"pe_{rec['_slug']}")
                e_subj  = st.text_input("Subject",    value=em_live.get("subject",""),  key=f"ps_{rec['_slug']}")
            with c2:
                e_body = st.text_area("Email Body", value=em_live.get("body",""), height=220, key=f"pb_{rec['_slug']}")
            col_ps, col_pd = st.columns(2)
            with col_ps:
                if st.button("💾 Save", key=f"psave_{rec['_slug']}"):
                    save_email(conn, rec["_slug"], e_name, e_title, e_email, e_subj, e_body)
                    st.success("Saved!")
            with col_pd:
                if st.button("📨 Save Gmail Draft", key=f"pdraft_{rec['_slug']}"):
                    if not gmail_helper.is_authorized():
                        st.warning("Connect Gmail first (see sidebar)")
                    elif not e_body.strip():
                        st.warning("Email body is empty")
                    else:
                        save_email(conn, rec["_slug"], e_name, e_title, e_email, e_subj, e_body)
                        draft_id, err = gmail_helper.create_draft(e_email, e_subj, e_body, attach_pdf=True)
                        if draft_id:
                            st.success(f"✅ Draft saved to Gmail! (ID: {draft_id[:12]}…)")
                        else:
                            st.error(f"❌ {err}")

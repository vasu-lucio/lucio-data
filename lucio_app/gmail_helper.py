"""
Gmail helper for Lucio app — multi-user support (Vasu & Anshul).
Uses a manual OAuth flow that works on Streamlit Cloud (no local browser needed).
"""

import os
import base64
import json
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

APP_DIR  = os.path.dirname(__file__)
PDF_PATH = os.path.join(APP_DIR, "Lucio_Legal_AI.pdf")
SCOPES   = ["https://www.googleapis.com/auth/gmail.compose"]

USERS = {
    "Vasu": {
        "creds_file": os.path.join(APP_DIR, "gmail_credentials_vasu.json"),
        "token_file": os.path.join(APP_DIR, "gmail_token_vasu.json"),
        "secret_key": "gmail_credentials_vasu",
    },
    "Anshul": {
        "creds_file": os.path.join(APP_DIR, "gmail_credentials_anshul.json"),
        "token_file": os.path.join(APP_DIR, "gmail_token_anshul.json"),
        "secret_key": "gmail_credentials_anshul",
    },
}


def _ensure_creds_file(user: str) -> str:
    """Return path to credentials file, writing from st.secrets if file doesn't exist locally."""
    cfg = USERS[user]
    if os.path.exists(cfg["creds_file"]):
        return cfg["creds_file"]
    try:
        import streamlit as st
        secret_key = cfg["secret_key"]
        if secret_key in st.secrets:
            # Secret is stored as a raw JSON string
            raw = st.secrets[secret_key]
            with open(cfg["creds_file"], "w") as f:
                f.write(raw if isinstance(raw, str) else json.dumps(raw))
            return cfg["creds_file"]
    except Exception:
        pass
    return cfg["creds_file"]  # may not exist, callers handle that


def get_auth_url(user: str = "Vasu") -> tuple:
    """
    Generate an OAuth authorization URL for the user to visit manually.
    Returns (auth_url, flow) — flow must be kept in session_state to exchange the code later.
    """
    try:
        from google_auth_oauthlib.flow import Flow
        creds_file = _ensure_creds_file(user)
        if not os.path.exists(creds_file):
            return None, None, "missing_credentials"
        flow = Flow.from_client_secrets_file(
            creds_file,
            scopes=SCOPES,
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        auth_url, _ = flow.authorization_url(prompt="consent")
        return auth_url, flow, None
    except Exception as e:
        return None, None, str(e)


def exchange_code(flow, code: str, user: str = "Vasu") -> str:
    """Exchange the auth code for a token and save it. Returns error string or None."""
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open(USERS[user]["token_file"], "w") as f:
            f.write(creds.to_json())
        return None
    except Exception as e:
        return str(e)


def get_service(user: str = "Vasu"):
    """Return an authenticated Gmail service using saved token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        cfg = USERS[user]
        if not os.path.exists(cfg["token_file"]):
            return None, "not_authorized"

        creds = Credentials.from_authorized_user_file(cfg["token_file"], SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(cfg["token_file"], "w") as f:
                    f.write(creds.to_json())
            else:
                return None, "token_expired"

        service = build("gmail", "v1", credentials=creds)
        return service, "ok"

    except Exception as e:
        return None, str(e)


def create_draft(to_email: str, subject: str, body: str,
                 user: str = "Vasu", attach_pdf: bool = True,
                 cc_emails: str = "") -> tuple:
    """Create a Gmail draft. Returns (draft_id, error_message)."""
    service, status = get_service(user)
    if service is None:
        return None, status

    try:
        msg = MIMEMultipart("mixed")
        msg["to"]      = to_email or ""
        msg["subject"] = subject or ""
        if cc_emails and cc_emails.strip():
            msg["cc"] = cc_emails.strip()
        # Send as HTML if body contains HTML tags, else plain text
        if "<br" in body or "<p" in body or "<img" in body:
            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body, "html"))
            msg.attach(alt)
        else:
            msg.attach(MIMEText(body, "plain"))

        if attach_pdf and os.path.exists(PDF_PATH):
            with open(PDF_PATH, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                'attachment; filename="Lucio_Legal_AI.pdf"',
            )
            msg.attach(part)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()
        return draft["id"], None

    except Exception as e:
        return None, str(e)


def is_authorized(user: str = "Vasu") -> bool:
    return os.path.exists(USERS[user]["token_file"])


def has_credentials(user: str = "Vasu") -> bool:
    return os.path.exists(_ensure_creds_file(user))


def revoke_token(user: str = "Vasu"):
    token_file = USERS[user]["token_file"]
    if os.path.exists(token_file):
        os.remove(token_file)

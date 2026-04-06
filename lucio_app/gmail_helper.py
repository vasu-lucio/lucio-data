"""
Gmail helper for Lucio app.
Handles OAuth2 auth + draft creation with PDF attachment.

First-time setup:
  1. Go to console.cloud.google.com → APIs & Services → Credentials
  2. Create OAuth 2.0 Client ID (Desktop app)
  3. Download and save as lucio_app/gmail_credentials.json
  4. Click "Authorize Gmail" in the app — browser opens, you approve, token saved.
"""

import os
import base64
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

APP_DIR    = os.path.dirname(__file__)
CREDS_FILE = os.path.join(APP_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(APP_DIR, "gmail_token.json")
PDF_PATH   = os.path.join(APP_DIR, "Lucio_Legal_AI.pdf")
SCOPES     = ["https://www.googleapis.com/auth/gmail.compose"]


def get_service():
    """Return an authenticated Gmail service, or None if not set up."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = None

        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists(CREDS_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return None, "missing_credentials"

            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        return service, "ok"

    except Exception as e:
        return None, str(e)


def create_draft(to_email: str, subject: str, body: str,
                 attach_pdf: bool = True) -> tuple:
    """
    Create a Gmail draft with optional PDF attachment.
    Returns (draft_id, error_message).
    draft_id is None on failure.
    """
    service, status = get_service()
    if service is None:
        return None, status

    try:
        if attach_pdf and os.path.exists(PDF_PATH):
            msg = MIMEMultipart()
            msg["to"]      = to_email or ""
            msg["subject"] = subject or ""
            msg.attach(MIMEText(body, "plain"))

            with open(PDF_PATH, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="Lucio_Legal_AI.pdf"',
            )
            msg.attach(part)
        else:
            msg = MIMEMultipart()
            msg["to"]      = to_email or ""
            msg["subject"] = subject or ""
            msg.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw}},
        ).execute()

        return draft["id"], None

    except Exception as e:
        return None, str(e)


def is_authorized() -> bool:
    return os.path.exists(TOKEN_FILE)


def has_credentials() -> bool:
    return os.path.exists(CREDS_FILE)


def revoke_token():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)

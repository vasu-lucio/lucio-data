"""
Gmail helper for Lucio app — multi-user support (Vasu & Anshul).
Each user has their own credentials and token file.

Setup per user:
  1. Go to console.cloud.google.com → APIs & Services → Credentials
  2. Create OAuth 2.0 Client ID (Desktop app)
  3. Download JSON → save as lucio_app/gmail_credentials_vasu.json (or _anshul)
  4. Click "Authorize Gmail" for that user in the sidebar — browser opens, approve, token saved.
"""

import os
import base64
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
        "signature": (
            "\n\nBest,\n"
            "Vasu Lucio\n"
            "Lucio Legal AI\n"
            "vasu@lucio.ai"
        ),
    },
    "Anshul": {
        "creds_file": os.path.join(APP_DIR, "gmail_credentials_anshul.json"),
        "token_file": os.path.join(APP_DIR, "gmail_token_anshul.json"),
        "signature": (
            "\n\nBest,\n"
            "Anshul Lucio\n"
            "Lucio Legal AI\n"
            "anshul@lucio.ai"
        ),
    },
}


def get_service(user="Vasu"):
    """Return an authenticated Gmail service for the given user."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        cfg = USERS[user]
        creds = None

        if os.path.exists(cfg["token_file"]):
            creds = Credentials.from_authorized_user_file(cfg["token_file"], SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists(cfg["creds_file"]):
                flow = InstalledAppFlow.from_client_secrets_file(cfg["creds_file"], SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return None, "missing_credentials"

            with open(cfg["token_file"], "w") as f:
                f.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        return service, "ok"

    except Exception as e:
        return None, str(e)


def create_draft(to_email: str, subject: str, body: str,
                 user: str = "Vasu", attach_pdf: bool = True) -> tuple:
    """
    Create a Gmail draft for the given user.
    Returns (draft_id, error_message).
    """
    service, status = get_service(user)
    if service is None:
        return None, status

    try:
        msg = MIMEMultipart()
        msg["to"]      = to_email or ""
        msg["subject"] = subject or ""
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


def get_signature(user: str = "Vasu") -> str:
    return USERS.get(user, USERS["Vasu"])["signature"]


def is_authorized(user: str = "Vasu") -> bool:
    return os.path.exists(USERS[user]["token_file"])


def has_credentials(user: str = "Vasu") -> bool:
    return os.path.exists(USERS[user]["creds_file"])


def revoke_token(user: str = "Vasu"):
    token_file = USERS[user]["token_file"]
    if os.path.exists(token_file):
        os.remove(token_file)

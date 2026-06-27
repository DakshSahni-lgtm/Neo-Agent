"""
Gmail tool for the local agent.

Provides 4 agent-callable functions:
  gmail_list   — fetch recent emails (subject, sender, snippet, id)
  gmail_read   — read full body of one email by id
  gmail_draft  — compose a reply (stores it in memory, never sends automatically)
  gmail_send   — send the stored draft (only called after user confirms)

Auth:
  Requires credentials.json (OAuth client secret) in the project root.
  On first run, opens a browser for one-time Google login.
  Saves token.json locally — auto-refreshes forever after that.

Scopes:
  gmail.modify  — read + send + label (no delete)
"""

import os
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional import guard — give a clear error if google libs aren't installed
# ---------------------------------------------------------------------------
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

BASE_DIR        = Path(__file__).parent.parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH       = BASE_DIR / "token.json"
SCOPES           = ["https://www.googleapis.com/auth/gmail.modify"]

# In-memory draft store — holds the last composed reply so gmail_send can use it
_pending_draft: dict | None = None


# ── Auth ────────────────────────────────────────────────────────────────────

def _get_service():
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed.\n"
            "Run: pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client"
        )
    if not CREDENTIALS_PATH.exists():
        raise RuntimeError(
            f"credentials.json not found at {CREDENTIALS_PATH}\n"
            "Download it from Google Cloud Console → APIs & Services → Credentials"
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _decode_body(payload: dict) -> str:
    """
    Recursively extract readable body from Gmail message payload.
    Strategy:
      1. Collect ALL text/plain parts across the full MIME tree
      2. If none found, fall back to stripping HTML from text/html parts
    This handles multipart/alternative, multipart/mixed, and nested structures.
    """
    plain_parts = []
    html_parts  = []
    _collect_parts(payload, plain_parts, html_parts)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        # Strip HTML tags as a basic fallback
        import re
        raw = "\n\n".join(html_parts)
        raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()
    return "(no readable body found)"


def _collect_parts(payload: dict, plain: list, html: list) -> None:
    """Walk the full MIME tree and collect all text parts."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if body_data:
        decoded = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        if mime_type == "text/plain":
            plain.append(decoded)
            return
        if mime_type == "text/html":
            html.append(decoded)
            return

    for part in payload.get("parts", []):
        _collect_parts(part, plain, html)


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


# ── Tool functions ───────────────────────────────────────────────────────────

def gmail_list(args: dict) -> str:
    """
    List recent emails from inbox.
    Args:
      count       (int, default 10)
      query       (str, default 'in:inbox')
      read_latest (bool, default false) — if true, fully reads the first email
    """
    try:
        service     = _get_service()
        count       = int(args.get("count", 10))
        query       = args.get("query", "in:inbox")
        read_latest = str(args.get("read_latest", "false")).lower() == "true"

        result   = service.users().messages().list(
            userId="me", q=query, maxResults=count
        ).execute()
        messages = result.get("messages", [])

        if not messages:
            return "No emails found."

        # If asked to read the latest, return full content of first email
        if read_latest:
            return gmail_read({"id": messages[0]["id"]})

        lines = []
        for msg in messages:
            m       = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = m["payload"]["headers"]
            lines.append(
                f"ID: {msg['id']}\n"
                f"  From:    {_header(headers, 'From')}\n"
                f"  Subject: {_header(headers, 'Subject')}\n"
                f"  Date:    {_header(headers, 'Date')}\n"
                f"  Snippet: {m.get('snippet', '')[:120]}"
            )

        return "\n\n".join(lines)

    except Exception as e:
        return f"Error listing emails: {e}"


def gmail_read(args: dict) -> str:
    """
    Read the full body of a specific email. Never truncates.
    Args: id (str, required) — the Gmail message ID from gmail_list
    """
    msg_id = args.get("id", "").strip()
    if not msg_id:
        return "Error: 'id' argument is required"

    try:
        service = _get_service()
        m       = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()
        headers = m["payload"]["headers"]
        body    = _decode_body(m["payload"])

        return (
            f"From:    {_header(headers, 'From')}\n"
            f"To:      {_header(headers, 'To')}\n"
            f"Subject: {_header(headers, 'Subject')}\n"
            f"Date:    {_header(headers, 'Date')}\n"
            f"ID:      {msg_id}\n"
            f"\n{body}"
        )

    except Exception as e:
        return f"Error reading email {msg_id}: {e}"


def gmail_draft(args: dict) -> str:
    """
    Compose a reply draft and store it for review.
    Does NOT send — always show the draft to Daksh before sending.
    Args:
      to      (str) — recipient email address
      subject (str) — email subject
      body    (str) — full reply body text
      reply_to_id (str, optional) — original message ID to thread correctly
    """
    global _pending_draft

    to          = args.get("to", "").strip()
    subject     = args.get("subject", "").strip()
    body        = args.get("body", "").strip()
    reply_to_id = args.get("reply_to_id", "").strip()

    if not to or not subject or not body:
        return "Error: 'to', 'subject', and 'body' are all required"

    _pending_draft = {
        "to": to,
        "subject": subject,
        "body": body,
        "reply_to_id": reply_to_id,
    }

    return (
        f"Draft ready (NOT sent). Review it below, then say 'send it' to send.\n\n"
        f"To:      {to}\n"
        f"Subject: {subject}\n"
        f"\n{body}"
    )


def gmail_send(args: dict) -> str:
    """
    Send the pending draft created by gmail_draft.
    Only call this after Daksh explicitly confirms (e.g. 'yes', 'send it', 'confirm').
    Args: none (uses the stored draft)
    """
    global _pending_draft

    if not _pending_draft:
        return (
            "No draft pending. Use gmail_draft first to compose a reply, "
            "then confirm to send it."
        )

    try:
        service = _get_service()
        draft   = _pending_draft

        msg = MIMEMultipart()
        msg["To"]      = draft["to"]
        msg["Subject"] = draft["subject"]
        msg.attach(MIMEText(draft["body"], "plain"))

        # Thread the reply if we have the original message ID
        if draft.get("reply_to_id"):
            original = service.users().messages().get(
                userId="me", id=draft["reply_to_id"], format="metadata",
                metadataHeaders=["Message-ID"]
            ).execute()
            original_message_id = _header(original["payload"]["headers"], "Message-ID")
            if original_message_id:
                msg["In-Reply-To"] = original_message_id
                msg["References"]  = original_message_id
            msg["threadId"] = original.get("threadId", "")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        body_payload = {"raw": raw}
        if draft.get("reply_to_id"):
            original_full = service.users().messages().get(
                userId="me", id=draft["reply_to_id"]
            ).execute()
            body_payload["threadId"] = original_full.get("threadId", "")

        service.users().messages().send(
            userId="me", body=body_payload
        ).execute()

        sent_to      = draft["to"]
        sent_subject = draft["subject"]
        _pending_draft = None  # clear after sending

        return f"Email sent to {sent_to} — Subject: {sent_subject}"

    except Exception as e:
        return f"Error sending email: {e}"

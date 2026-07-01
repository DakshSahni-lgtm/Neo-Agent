"""
Google Calendar tool for the local agent.

Provides 4 agent-callable functions:
  calendar_list    — list upcoming events
  calendar_create  — create a new event
  calendar_update  — update an existing event by ID
  calendar_delete  — delete an event (requires confirmation)

Auth:
  Uses the same credentials.json as Gmail but a separate token file
  (calendar_token.json) since Calendar needs a different OAuth scope.
  First run opens a browser for one-time Google login.

Scope:
  https://www.googleapis.com/auth/calendar — full read/write access
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

BASE_DIR         = Path(__file__).parent.parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH       = BASE_DIR / "calendar_token.json"
SCOPES           = ["https://www.googleapis.com/auth/calendar"]

# Pending delete store — same pattern as gmail_draft/gmail_send
_pending_delete: dict | None = None


# ── Auth ─────────────────────────────────────────────────────────────────────

def _get_service():
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed.\n"
            "Run: pip install google-auth google-auth-oauthlib "
            "google-auth-httplib2 google-api-python-client --break-system-packages"
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
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_datetime(dt_str: str) -> datetime | None:
    """
    Parse a flexible datetime string into a datetime object.
    Supports formats like:
      '2026-06-28'
      '2026-06-28 14:30'
      '2026-06-28T14:30:00'
      'tomorrow 3pm'  (basic natural language)
    """
    dt_str = dt_str.strip()

    # Natural language shortcuts
    now = datetime.now()
    if dt_str.lower() == "today":
        return now.replace(hour=9, minute=0, second=0, microsecond=0)
    if dt_str.lower() == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _format_event(event: dict) -> str:
    """Format a calendar event for display."""
    start = event.get("start", {})
    end   = event.get("end", {})

    start_str = start.get("dateTime", start.get("date", "?"))
    end_str   = end.get("dateTime", end.get("date", "?"))

    # Format datetime for readability
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(start_str[:19], fmt[:len(fmt)])
            start_str = dt.strftime("%a %d %b %Y, %I:%M %p")
            break
        except Exception:
            pass

    return (
        f"ID:       {event.get('id', '?')}\n"
        f"Title:    {event.get('summary', '(no title)')}\n"
        f"Start:    {start_str}\n"
        f"End:      {end_str}\n"
        f"Location: {event.get('location', '-')}\n"
        f"Desc:     {(event.get('description') or '-')[:120]}"
    )


# ── Tool functions ────────────────────────────────────────────────────────────

def calendar_list(args: dict) -> str:
    """
    List upcoming calendar events.
    Args:
      days  (int, optional, default 7)  — how many days ahead to look
      max   (int, optional, default 10) — max events to return
    """
    days     = int(args.get("days", 7))
    max_evts = int(args.get("max", 10))

    try:
        service  = _get_service()
        now      = datetime.now(timezone.utc)
        end_time = now + timedelta(days=days)

        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end_time.isoformat(),
            maxResults=max_evts,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = result.get("items", [])
        if not events:
            return f"No events in the next {days} days."

        lines = [f"Upcoming events (next {days} days):\n"]
        for e in events:
            lines.append(_format_event(e))
            lines.append("")
        return "\n".join(lines)

    except Exception as e:
        return f"Error listing calendar events: {e}"


def calendar_create(args: dict) -> str:
    """
    Create a new calendar event.
    Args:
      title       (str) — event title
      start       (str) — start datetime e.g. '2026-06-28 14:00'
      end         (str, optional) — end datetime; defaults to 1 hour after start
      description (str, optional) — event description
      location    (str, optional) — event location
    """
    title       = (args.get("title") or "").strip()
    start_str   = (args.get("start") or "").strip()
    end_str     = (args.get("end") or "").strip()
    description = (args.get("description") or "").strip()
    location    = (args.get("location") or "").strip()

    if not title:
        return "Error: 'title' is required"
    if not start_str:
        return "Error: 'start' datetime is required (e.g. '2026-06-28 14:00')"

    start_dt = _parse_datetime(start_str)
    if not start_dt:
        return f"Error: couldn't parse start datetime '{start_str}'"

    end_dt = _parse_datetime(end_str) if end_str else start_dt + timedelta(hours=1)
    if not end_dt:
        return f"Error: couldn't parse end datetime '{end_str}'"

    event_body = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Asia/Kolkata"},
    }
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location

    try:
        service  = _get_service()
        created  = service.events().insert(
            calendarId="primary", body=event_body
        ).execute()
        return (
            f"Event created successfully!\n"
            f"{_format_event(created)}\n"
            f"Link: {created.get('htmlLink', '-')}"
        )
    except Exception as e:
        return f"Error creating event: {e}"


def calendar_update(args: dict) -> str:
    """
    Update an existing calendar event by ID.
    Args:
      id          (str) — event ID from calendar_list
      title       (str, optional) — new title
      start       (str, optional) — new start datetime
      end         (str, optional) — new end datetime
      description (str, optional) — new description
      location    (str, optional) — new location
    """
    event_id = (args.get("id") or "").strip()
    if not event_id:
        return "Error: 'id' is required — get it from calendar_list"

    try:
        service = _get_service()
        event   = service.events().get(
            calendarId="primary", eventId=event_id
        ).execute()

        # Apply only the fields provided
        if args.get("title"):
            event["summary"] = args["title"].strip()
        if args.get("description"):
            event["description"] = args["description"].strip()
        if args.get("location"):
            event["location"] = args["location"].strip()
        if args.get("start"):
            dt = _parse_datetime(args["start"])
            if not dt:
                return f"Error: couldn't parse start datetime '{args['start']}'"
            event["start"] = {"dateTime": dt.isoformat(), "timeZone": "Asia/Kolkata"}
        if args.get("end"):
            dt = _parse_datetime(args["end"])
            if not dt:
                return f"Error: couldn't parse end datetime '{args['end']}'"
            event["end"] = {"dateTime": dt.isoformat(), "timeZone": "Asia/Kolkata"}

        updated = service.events().update(
            calendarId="primary", eventId=event_id, body=event
        ).execute()

        return f"Event updated!\n{_format_event(updated)}"

    except Exception as e:
        return f"Error updating event: {e}"


def calendar_delete(args: dict) -> str:
    """
    Delete a calendar event. Stores it as pending — requires confirmation.
    Args:
      id (str) — event ID from calendar_list
    """
    global _pending_delete
    event_id = (args.get("id") or "").strip()
    if not event_id:
        return "Error: 'id' is required"

    try:
        service = _get_service()
        event   = service.events().get(
            calendarId="primary", eventId=event_id
        ).execute()

        _pending_delete = {"id": event_id, "title": event.get("summary", "?")}
        return (
            f"About to delete: '{event.get('summary', '?')}'\n"
            f"Say 'confirm delete' to permanently remove it, or 'cancel' to abort."
        )
    except Exception as e:
        return f"Error fetching event to delete: {e}"


def calendar_confirm_delete(args: dict) -> str:
    """
    Confirm and execute the pending calendar event deletion.
    Only call after calendar_delete and explicit user confirmation.
    Verifies the deletion actually happened before reporting success.
    """
    global _pending_delete
    if not _pending_delete:
        return "No pending deletion. Use calendar_delete first to select an event."

    event_id = _pending_delete["id"]
    title    = _pending_delete["title"]

    try:
        service = _get_service()
        service.events().delete(
            calendarId="primary", eventId=event_id
        ).execute()

        # Verify the deletion actually worked — Google Calendar API sometimes
        # returns success but the event persists if the ID is wrong/stale
        try:
            still_exists = service.events().get(
                calendarId="primary", eventId=event_id
            ).execute()
            status = still_exists.get("status", "")
            if status == "cancelled":
                # "cancelled" is Google's term for a deleted event — success
                _pending_delete = None
                return f"Event '{title}' deleted successfully."
            else:
                return (
                    f"Warning: delete API returned success but the event may still exist "
                    f"(status: {status}). Check Google Calendar manually.\n"
                    f"Event ID: {event_id}"
                )
        except Exception:
            # A 404/410 error when fetching means the event is truly gone
            _pending_delete = None
            return f"Event '{title}' deleted successfully."

    except Exception as e:
        return f"Error deleting event '{title}': {e}\nEvent ID: {event_id}"

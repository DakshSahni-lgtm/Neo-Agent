"""
Google Sheets and Drive tools for the local agent.

Sheets tools (contact-focused):
  sheets_search_contact  — fuzzy name search in your contact sheet
  sheets_add_contact     — add a new contact row
  sheets_read            — read any range from any sheet (general purpose)

Drive tools:
  drive_search           — search for files by name
  drive_read             — read content of a Google Doc or Sheet

Auth:
  Uses credentials.json (same as Gmail/Calendar) with a separate
  token file (sheets_token.json) for Sheets+Drive scopes.
  First run opens a browser for one-time permission grant.

Config (set in .env):
  CONTACT_SHEET_ID  — the Google Sheet ID from the URL
                      (docs.google.com/spreadsheets/d/<THIS_PART>/edit)

Sheet format (row 1 = headers):
  Name | Email | Phone | Company | Relationship | Notes
"""

import os
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
TOKEN_PATH       = BASE_DIR / "sheets_token.json"
SCOPES           = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

CONTACT_SHEET_ID = os.environ.get("CONTACT_SHEET_ID", "")
CONTACT_RANGE    = "Sheet1!A:F"   # Name|Email|Phone|Company|Relationship|Notes


# ── Auth ─────────────────────────────────────────────────────────────────────

def _get_sheets_service():
    return _get_service("sheets", "v4")

def _get_drive_service():
    return _get_service("drive", "v3")

def _get_service(api: str, version: str):
    if not GOOGLE_LIBS_AVAILABLE:
        raise RuntimeError(
            "Google API libraries not installed.\n"
            "Run: pip install google-auth google-auth-oauthlib "
            "google-auth-httplib2 google-api-python-client --break-system-packages"
        )
    if not CREDENTIALS_PATH.exists():
        raise RuntimeError(f"credentials.json not found at {CREDENTIALS_PATH}")

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

    return build(api, version, credentials=creds)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sheet_to_dicts(values: list[list]) -> list[dict]:
    """Convert sheet rows (first row = headers) to list of dicts."""
    if not values or len(values) < 2:
        return []
    headers = [h.strip() for h in values[0]]
    rows = []
    for i, row in enumerate(values[1:], start=2):
        # Pad short rows with empty strings
        padded = row + [""] * (len(headers) - len(row))
        rows.append({"_row": i, **dict(zip(headers, padded))})
    return rows


def _format_contact(c: dict) -> str:
    """Format a contact dict for display."""
    parts = []
    for field in ("Name", "Email", "Phone", "Company", "Relationship", "Notes"):
        val = c.get(field, "").strip()
        if val:
            parts.append(f"  {field}: {val}")
    return "\n".join(parts)


def _fuzzy_match(query: str, name: str) -> bool:
    """Return True if query matches the name (case-insensitive, partial)."""
    q = query.lower().strip()
    n = name.lower().strip()
    # Full match, starts with, or all query words appear in name
    return (q == n or n.startswith(q) or q in n or
            all(w in n for w in q.split()))


# ── Sheets contact tools ──────────────────────────────────────────────────────

def sheets_search_contact(args: dict) -> str:
    """
    Search for contacts by name in your Google Sheets contact list.
    Returns all matching rows with full details for disambiguation.
    Args:
      name       (str) — name to search for (partial names work)
      sheet_id   (str, optional) — override CONTACT_SHEET_ID from .env
    """
    query    = (args.get("name") or "").strip()
    sheet_id = (args.get("sheet_id") or CONTACT_SHEET_ID).strip()

    if not query:
        return "Error: 'name' argument is required"
    if not sheet_id:
        return (
            "Error: CONTACT_SHEET_ID not set.\n"
            "Add it to your .env file:\n"
            "  CONTACT_SHEET_ID=your_sheet_id_here\n"
            "(get it from the Google Sheets URL)"
        )

    try:
        service = _get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=CONTACT_RANGE,
        ).execute()

        rows = _sheet_to_dicts(result.get("values", []))
        if not rows:
            return "Contact sheet is empty or has no data rows."

        matches = [r for r in rows if _fuzzy_match(query, r.get("Name", ""))]

        if not matches:
            return f"No contacts found matching '{query}'."

        if len(matches) == 1:
            c = matches[0]
            return (
                f"Found 1 contact matching '{query}':\n\n"
                f"{_format_contact(c)}"
            )

        # Multiple matches — return all with enough info to disambiguate
        lines = [f"Found {len(matches)} contacts matching '{query}' — which one?\n"]
        for i, c in enumerate(matches, 1):
            name     = c.get("Name", "?")
            company  = c.get("Company", "")
            rel      = c.get("Relationship", "")
            email    = c.get("Email", "")
            disambig = " | ".join(filter(None, [company, rel, email]))
            lines.append(f"{i}. {name}  ({disambig})")

        lines.append(
            "\nPlease specify which one — e.g. 'the one from River Tech' "
            "or 'Vansh Sahni' (full name)."
        )
        return "\n".join(lines)

    except Exception as e:
        return f"Error searching contacts: {e}"


def sheets_add_contact(args: dict) -> str:
    """
    Add a new contact to the Google Sheets contact list.
    Args:
      name         (str) — full name
      email        (str) — email address
      phone        (str, optional)
      company      (str, optional)
      relationship (str, optional) — e.g. Client, Friend, Business Partner
      notes        (str, optional)
      sheet_id     (str, optional) — override CONTACT_SHEET_ID from .env
    """
    name         = (args.get("name") or "").strip()
    email        = (args.get("email") or "").strip()
    phone        = (args.get("phone") or "").strip()
    company      = (args.get("company") or "").strip()
    relationship = (args.get("relationship") or "").strip()
    notes        = (args.get("notes") or "").strip()
    sheet_id     = (args.get("sheet_id") or CONTACT_SHEET_ID).strip()

    if not name:
        return "Error: 'name' is required"
    if not email:
        return "Error: 'email' is required"
    if not sheet_id:
        return "Error: CONTACT_SHEET_ID not set in .env"

    try:
        service = _get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=CONTACT_RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[name, email, phone, company, relationship, notes]]},
        ).execute()
        return (
            f"Contact added successfully!\n"
            f"  Name:         {name}\n"
            f"  Email:        {email}\n"
            f"  Phone:        {phone or '-'}\n"
            f"  Company:      {company or '-'}\n"
            f"  Relationship: {relationship or '-'}\n"
            f"  Notes:        {notes or '-'}"
        )
    except Exception as e:
        return f"Error adding contact: {e}"


def sheets_read(args: dict) -> str:
    """
    Read a range from any Google Sheet.
    Args:
      sheet_id  (str) — the Google Sheet ID
      range     (str, optional) — A1 notation range e.g. 'Sheet1!A1:E20' (default: full Sheet1)
      as_table  (bool, optional) — format as readable table (default true)
    """
    sheet_id = (args.get("sheet_id") or CONTACT_SHEET_ID).strip()
    rng      = (args.get("range") or "Sheet1").strip()
    as_table = str(args.get("as_table", "true")).lower() != "false"

    if not sheet_id:
        return "Error: 'sheet_id' is required"

    try:
        service = _get_sheets_service()
        result  = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=rng,
        ).execute()

        values = result.get("values", [])
        if not values:
            return "No data found in that range."

        if not as_table or len(values) < 2:
            # Raw output
            return "\n".join("\t".join(str(c) for c in row) for row in values)

        # Table output with headers
        headers = values[0]
        rows    = values[1:]
        col_widths = [max(len(str(h)), max((len(str(r[i])) if i < len(r) else 0) for r in rows), 1)
                      for i, h in enumerate(headers)]

        def fmt_row(row):
            return " | ".join(
                str(row[i] if i < len(row) else "").ljust(col_widths[i])
                for i in range(len(headers))
            )

        sep = "-+-".join("-" * w for w in col_widths)
        lines = [fmt_row(headers), sep] + [fmt_row(r) for r in rows]
        return f"Sheet data ({len(rows)} rows):\n\n" + "\n".join(lines)

    except Exception as e:
        return f"Error reading sheet: {e}"


# ── Drive tools ───────────────────────────────────────────────────────────────

def drive_list(args: dict) -> str:
    """
    List files and folders in Google Drive.
    Args:
      folder_id (str, optional) — list contents of a specific folder ID
                                   (default: root / My Drive)
      max       (int, optional, default 20) — max files to return
      type      (str, optional) — filter: 'doc', 'sheet', 'pdf', 'folder'
    """
    folder_id = (args.get("folder_id") or "root").strip()
    max_res   = int(args.get("max", 20))
    filetype  = (args.get("type") or "").lower()

    mime_map = {
        "doc":    "application/vnd.google-apps.document",
        "sheet":  "application/vnd.google-apps.spreadsheet",
        "pdf":    "application/pdf",
        "folder": "application/vnd.google-apps.folder",
    }

    q_parts = [f"'{folder_id}' in parents", "trashed = false"]
    if filetype in mime_map:
        q_parts.append(f"mimeType = '{mime_map[filetype]}'")

    try:
        service = _get_drive_service()
        result  = service.files().list(
            q=" and ".join(q_parts),
            pageSize=max_res,
            orderBy="folder,name",
            fields="files(id, name, mimeType, modifiedTime, size, webViewLink)",
        ).execute()

        files = result.get("files", [])
        if not files:
            return "No files found in Google Drive (or Drive is empty)."

        lines = [f"Google Drive contents ({len(files)} items):\n"]
        for f in files:
            mime  = f.get("mimeType", "")
            # Human-readable type label
            if "folder" in mime:
                icon = "📁"
                type_label = "Folder"
            elif "document" in mime:
                icon = "📝"
                type_label = "Google Doc"
            elif "spreadsheet" in mime:
                icon = "📊"
                type_label = "Google Sheet"
            elif "presentation" in mime:
                icon = "📊"
                type_label = "Google Slides"
            elif "pdf" in mime:
                icon = "📄"
                type_label = "PDF"
            else:
                icon = "📎"
                type_label = mime.split("/")[-1]

            mtime = f.get("modifiedTime", "")[:10]
            lines.append(
                f"{icon} {f['name']}\n"
                f"   Type: {type_label}  |  Modified: {mtime}\n"
                f"   ID: {f['id']}"
            )
        return "\n".join(lines)

    except Exception as e:
        return f"Error listing Drive files: {e}"



    """
    Search for files in Google Drive by name.
    Args:
      query    (str) — file name or partial name to search for
      max      (int, optional, default 10) — max results
      type     (str, optional) — filter by type: 'doc', 'sheet', 'pdf', 'folder'
    """
    query    = (args.get("query") or "").strip()
    max_res  = int(args.get("max", 10))
    filetype = (args.get("type") or "").lower()

    if not query:
        return "Error: 'query' argument is required"

    mime_map = {
        "doc":    "application/vnd.google-apps.document",
        "sheet":  "application/vnd.google-apps.spreadsheet",
        "pdf":    "application/pdf",
        "folder": "application/vnd.google-apps.folder",
    }

    q_parts = [f"name contains '{query}'", "trashed = false"]
    if filetype in mime_map:
        q_parts.append(f"mimeType = '{mime_map[filetype]}'")

    try:
        service = _get_drive_service()
        result  = service.files().list(
            q=" and ".join(q_parts),
            pageSize=max_res,
            fields="files(id, name, mimeType, modifiedTime, size, webViewLink)",
        ).execute()

        files = result.get("files", [])
        if not files:
            return f"No files found matching '{query}'."

        lines = [f"Found {len(files)} file(s) matching '{query}':\n"]
        for f in files:
            mime  = f.get("mimeType", "").split(".")[-1].replace("google-apps.", "")
            mtime = f.get("modifiedTime", "")[:10]
            lines.append(
                f"• {f['name']}\n"
                f"  ID: {f['id']}\n"
                f"  Type: {mime}  |  Modified: {mtime}\n"
                f"  Link: {f.get('webViewLink', '-')}"
            )
        return "\n".join(lines)

    except Exception as e:
        return f"Error searching Drive: {e}"


def drive_read(args: dict) -> str:
    """
    Read the text content of a Google Doc or exported text from a Sheet.
    Args:
      file_id   (str) — the Drive file ID (from drive_search)
      max_chars (int, optional, default 6000) — max characters to return
    """
    file_id   = (args.get("file_id") or "").strip()
    max_chars = int(args.get("max_chars", 6000))

    if not file_id:
        return "Error: 'file_id' is required — get it from drive_search"

    try:
        service = _get_drive_service()

        # Get file metadata to determine type
        meta = service.files().get(
            fileId=file_id, fields="name,mimeType"
        ).execute()
        mime = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        # Export Google Docs/Sheets as plain text
        if "google-apps.document" in mime:
            resp = service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
            text = resp.decode("utf-8") if isinstance(resp, bytes) else str(resp)
        elif "google-apps.spreadsheet" in mime:
            resp = service.files().export(
                fileId=file_id, mimeType="text/csv"
            ).execute()
            text = resp.decode("utf-8") if isinstance(resp, bytes) else str(resp)
        else:
            # Binary file — can't read as text
            return (
                f"'{name}' is a binary file ({mime}) — can only read "
                "Google Docs and Sheets as text."
            )

        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars]"

        return f"Content of '{name}':\n\n{text}"

    except Exception as e:
        return f"Error reading Drive file: {e}"

# ── Sheets creation and editing ───────────────────────────────────────────────

def sheets_create(args: dict) -> str:
    """
    Create a new Google Spreadsheet.
    Args:
      name     (str) — spreadsheet title
      headers  (list or str, optional) — column headers for row 1
                e.g. ["Name", "Revenue", "Date"] or "Name,Revenue,Date"
      data     (list, optional) — initial rows of data (list of lists)
    Returns the new spreadsheet ID and URL.
    """
    import json

    name    = (args.get("name") or "").strip()
    headers = args.get("headers")
    data    = args.get("data")

    if not name:
        return "Error: 'name' is required"

    # Parse headers — accept list or comma-separated string
    header_row = []
    if isinstance(headers, str) and headers.strip():
        header_row = [h.strip() for h in headers.split(",")]
    elif isinstance(headers, list):
        header_row = [str(h) for h in headers]

    # Parse data — accept list of lists or JSON string
    data_rows = []
    if isinstance(data, str) and data.strip():
        try:
            data_rows = json.loads(data)
        except json.JSONDecodeError:
            return "Error: 'data' must be a JSON array of arrays e.g. [[\"a\",1],[\"b\",2]]"
    elif isinstance(data, list):
        data_rows = data

    try:
        service = _get_sheets_service()

        # Create the spreadsheet
        spreadsheet = service.spreadsheets().create(
            body={"properties": {"title": name}},
            fields="spreadsheetId,spreadsheetUrl",
        ).execute()

        sheet_id  = spreadsheet["spreadsheetId"]
        sheet_url = spreadsheet["spreadsheetUrl"]

        # Write headers and initial data if provided
        all_rows = []
        if header_row:
            all_rows.append(header_row)
        if data_rows:
            all_rows.extend(data_rows)

        if all_rows:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range="Sheet1!A1",
                valueInputOption="USER_ENTERED",
                body={"values": all_rows},
            ).execute()

        result = (
            f"Spreadsheet created: '{name}'\n"
            f"ID:  {sheet_id}\n"
            f"URL: {sheet_url}\n"
        )
        if header_row:
            result += f"Headers: {', '.join(header_row)}\n"
        if data_rows:
            result += f"Initial rows written: {len(data_rows)}"

        return result

    except Exception as e:
        return f"Error creating spreadsheet: {e}"


def sheets_write(args: dict) -> str:
    """
    Write (overwrite) data to a specific range in any Google Sheet.
    Use this to set cell values, update a table, or write headers.
    Args:
      sheet_id  (str) — spreadsheet ID (from sheets_create or Drive URL)
      range     (str) — A1 notation e.g. 'Sheet1!A1' or 'Sheet1!B2:D5'
      data      (list or str) — 2D array of values:
                  [["Name","Age"],["Daksh",18]] or JSON string of same
      clear_first (bool, optional) — clear the range before writing (default false)
    """
    import json

    sheet_id    = (args.get("sheet_id") or "").strip()
    rng         = (args.get("range") or "Sheet1!A1").strip()
    data        = args.get("data")
    clear_first = str(args.get("clear_first", "false")).lower() == "true"

    if not sheet_id:
        return "Error: 'sheet_id' is required"
    if data is None:
        return "Error: 'data' is required"

    # Parse data
    if isinstance(data, str):
        try:
            rows = json.loads(data)
        except json.JSONDecodeError:
            # Try treating as a single row of comma-separated values
            rows = [[v.strip() for v in data.split(",")]]
    elif isinstance(data, list):
        rows = data
    else:
        return f"Error: 'data' must be a list or JSON string, got {type(data).__name__}"

    # Ensure rows is a list of lists
    if rows and not isinstance(rows[0], list):
        rows = [rows]  # wrap single row

    try:
        service = _get_sheets_service()

        if clear_first:
            service.spreadsheets().values().clear(
                spreadsheetId=sheet_id, range=rng
            ).execute()

        result = service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=rng,
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

        updated_cells = result.get("updatedCells", "?")
        updated_range = result.get("updatedRange", rng)
        return (
            f"Written successfully to '{updated_range}'\n"
            f"Rows written: {len(rows)}  |  Cells updated: {updated_cells}\n"
            f"Sheet ID: {sheet_id}"
        )

    except Exception as e:
        return f"Error writing to sheet: {e}"


def sheets_append(args: dict) -> str:
    """
    Append new rows to the end of existing data in a Google Sheet.
    Finds the last row with data and adds below it — safe to use repeatedly.
    Args:
      sheet_id  (str) — spreadsheet ID
      data      (list or str) — rows to append:
                  [["Daksh",18],["Vansh",20]] or JSON string of same
      sheet_name (str, optional) — sheet/tab name (default: Sheet1)
    """
    import json

    sheet_id   = (args.get("sheet_id") or "").strip()
    data       = args.get("data")
    sheet_name = (args.get("sheet_name") or "Sheet1").strip()

    if not sheet_id:
        return "Error: 'sheet_id' is required"
    if data is None:
        return "Error: 'data' is required"

    # Parse data
    if isinstance(data, str):
        try:
            rows = json.loads(data)
        except json.JSONDecodeError:
            rows = [[v.strip() for v in data.split(",")]]
    elif isinstance(data, list):
        rows = data
    else:
        return f"Error: 'data' must be a list or JSON string"

    if rows and not isinstance(rows[0], list):
        rows = [rows]

    try:
        service = _get_sheets_service()
        result  = service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

        updated_range = result.get("updates", {}).get("updatedRange", "?")
        return (
            f"Appended {len(rows)} row(s) to '{sheet_name}'\n"
            f"Written to: {updated_range}\n"
            f"Sheet ID: {sheet_id}"
        )

    except Exception as e:
        return f"Error appending to sheet: {e}"

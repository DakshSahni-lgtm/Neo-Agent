# Tool registry (already in your context — no tool call needed to read this)

Call tools by exact name in your JSON response. Do NOT invent tool names.
Auto-generated from core/tools.py — edit tools there, then run sync_skills.py.


## get_current_time
Get the current date and time.
Args: none

## read_memory
Read the full contents of memory.md. Only call this if you need to verify memory changed after an append — it is already loaded in your system prompt.
Args: none

## append_memory
Save a durable fact or note to memory.md so it persists across sessions.
Args: note (string — the fact or note to remember)

## gmail_list
List recent emails from Gmail inbox. Returns ID, sender, subject, date, and snippet for each. Use read_latest=true to immediately read the full body of the newest email instead of just listing.
Args: count (int (optional, default 10) — number of emails to fetch), query (string (optional, default 'in:inbox') — Gmail search query e.g. 'is:unread' or 'from:someone@example.com'), read_latest (bool (optional, default false) — if true, returns full body of the first/latest email)

## gmail_read
Read the full body of a specific email by its ID (from gmail_list).
Args: id (string — Gmail message ID from gmail_list)

## gmail_draft
Compose an email reply and store it as a pending draft. ALWAYS use this before gmail_send — never send without drafting first. Show the draft output to Daksh and wait for explicit confirmation before calling gmail_send.
Args: to (string — recipient email address), subject (string — email subject line), body (string — full plain-text email body), reply_to_id (string (optional) — original message ID to thread the reply correctly)

## gmail_send
Send the pending draft created by gmail_draft. ONLY call this after Daksh has explicitly confirmed (e.g. 'yes', 'send it', 'confirm'). Never call this speculatively or without confirmation.
Args: none

## gmail_read_and_speak
Read an email and convert its body to a voice message in one step. Use this when Daksh asks to hear/read an email out loud or as a voice message. More efficient than chaining gmail_read + speak separately.
Args: id (string (optional) — Gmail message ID; if omitted, reads the latest inbox email)

## calendar_list
List upcoming Google Calendar events. Shows title, time, location and ID for each event.
Args: days (int (optional, default 7) — how many days ahead to look), max (int (optional, default 10) — max events to return)

## calendar_create
Create a new event on Google Calendar. Always confirm the details with Daksh before creating.
Args: title (string — event title), start (string — start datetime e.g. '2026-06-28 14:00' or 'tomorrow 3pm'), end (string (optional) — end datetime; defaults to 1 hour after start), description (string (optional) — event description or notes), location (string (optional) — event location)

## calendar_update
Update an existing calendar event by its ID (from calendar_list). Only provide fields to change.
Args: id (string — event ID from calendar_list), title (string (optional) — new title), start (string (optional) — new start datetime), end (string (optional) — new end datetime), description (string (optional) — new description), location (string (optional) — new location)

## calendar_delete
Initiate deletion of a calendar event. Shows the event and waits for Daksh's confirmation before deleting.
Args: id (string — event ID from calendar_list)

## calendar_confirm_delete
Confirm and execute a pending calendar event deletion. Only call after calendar_delete and explicit user confirmation.
Args: none

## sheets_search_contact
Search for a contact by name in the Google Sheets contact list. Returns email, phone, company and relationship for all matches. If multiple people share a name, shows disambiguation info and asks Daksh which one to use before proceeding. Always use this before emailing someone by name — never guess an email address.
Args: name (string — name to search for (partial names and first names work))

## sheets_add_contact
Add a new contact to the Google Sheets contact list.
Args: name (string — full name), email (string — email address), phone (string (optional)), company (string (optional)), relationship (string (optional) — e.g. Client, Friend, Business Partner), notes (string (optional))

## sheets_read
Read data from any Google Sheet by ID and range. Returns formatted table output.
Args: sheet_id (string — the Google Sheet ID from its URL), range (string (optional) — A1 notation e.g. 'Sheet1!A1:E20' (default: full Sheet1))

## sheets_create
Create a new Google Spreadsheet. Returns the new sheet's ID and URL. Optionally create it directly inside a specific Drive folder.
Args: name (string — spreadsheet title), headers (list or comma-separated string (optional) — column headers for row 1), data (list of lists (optional) — initial data rows), folder_name (string (optional) — create inside this Drive folder (searched by name)), folder_id (string (optional) — create inside this folder ID (more precise than folder_name))

## sheets_write
Write (overwrite) data to a specific range in any Google Sheet. Use to update existing cells, set headers, or rewrite a table section.
Args: sheet_id (string — spreadsheet ID), range (string — A1 notation e.g. 'Sheet1!A1' or 'Sheet1!B2:D10'), data (list of lists or JSON string — rows to write e.g. [['Name','Age'],['Daksh',18]]), clear_first (bool (optional, default false) — clear the range before writing)

## sheets_append
Append new rows to the end of existing data in a Google Sheet. Safe to call repeatedly — always adds below the last row.
Args: sheet_id (string — spreadsheet ID), data (list of lists or JSON string — rows to append e.g. [['Daksh',1000,'June']]), sheet_name (string (optional, default 'Sheet1') — which tab to append to)

## drive_move
Move a file or spreadsheet to a different folder in Google Drive. Search by folder name — if multiple folders match, shows options to disambiguate.
Args: file_id (string — ID of the file to move (from drive_list or drive_search)), folder_name (string — name of the destination folder (searches Drive by name)), folder_id (string — destination folder ID (use instead of folder_name if you have the ID))

## drive_list
List files and folders in Google Drive. Use this when Daksh asks what files he has, to browse Drive contents, or to find a folder. Use drive_search when looking for a specific file by name.
Args: folder_id (string (optional) — list a specific folder's contents by ID; default lists root My Drive), max (int (optional, default 20) — max items to return), type (string (optional) — filter by type: 'doc', 'sheet', 'pdf', 'folder')

## drive_search
Search for files in Google Drive by name. Returns file names, IDs, types, and links.
Args: query (string — file name or partial name to search for), max (int (optional, default 10) — max results), type (string (optional) — filter by type: 'doc', 'sheet', 'pdf', 'folder')

## drive_read
Read RAW text content of a file from Google Drive — supports Google Docs, Sheets, Slides, Word (.docx), PowerPoint (.pptx), Excel (.xlsx), plain text/markdown/CSV files, and PDFs. Returns unprocessed extracted text. Use drive_read_and_explain instead if Daksh wants analysis/summary, not raw text.
Args: file_id (string — Drive file ID from drive_search), max_chars (int (optional, default 6000) — max characters to return)

## drive_read_and_explain
Read a Drive file AND analyze/explain its contents using the LLM — e.g. count invoices, extract names and amounts from a document, summarize key points. Use this whenever Daksh asks 'what's inside', 'tell me about', or asks a specific question about a document's content, rather than wanting raw text dumped.
Args: file_id (string — Drive file ID from drive_list or drive_search), question (string (optional) — specific question to answer about the content, e.g. 'how many invoices and what amounts?')

## schedule_daily_task
Schedule a task to run automatically every day at a specific time — e.g. morning briefings, daily reminders. The prompt runs as if Daksh typed it himself, and the result is sent to him automatically.
Args: name (string — short name for the task, e.g. 'Morning briefing'), prompt (string — the instruction to run each time, e.g. 'Check my calendar for today and summarize unread emails'), time (string — 24-hour time e.g. '08:00' or '17:30')

## schedule_interval_task
Schedule a task to run automatically every N minutes (minimum 15). Use for frequent recurring checks.
Args: name (string — short name for the task), prompt (string — the instruction to run each time), minutes (int — how often to run, minimum 15 minutes)

## list_scheduled_tasks
List all currently scheduled proactive tasks with their timing and IDs.
Args: none

## cancel_scheduled_task
Cancel a scheduled task by its ID. Get the ID from list_scheduled_tasks first.
Args: id (string — task ID from list_scheduled_tasks)

## generate_diagram
Generate a diagram from a plain English description. Converts the description to Mermaid syntax and renders it to a PNG image (viewable inline in Discord/Telegram) that opens automatically. Supports flowcharts, sequence diagrams, ER diagrams, mind maps, Gantt charts, state diagrams, and more.
Args: description (string — plain English description of what the diagram should show), format (string (optional, default 'png') — output format: 'png' (renders inline in Discord/Telegram) or 'svg' (downloads only))

## render_mermaid
Render raw Mermaid diagram syntax directly to an SVG/PNG file. Use this when you already have valid Mermaid code.
Args: syntax (string — valid Mermaid diagram syntax), format (string (optional, default 'svg') — 'svg' or 'png')

## web_search
Search the web using DuckDuckGo (free, no API key). Use for current events, recent news, factual lookups, prices, anything that requires up-to-date information beyond training data. Returns titles, URLs and snippets for the top results.
Args: query (string — the search query), max (int (optional, default 5) — max number of results to return)

## web_fetch
Fetch and extract readable text from a specific URL. Use after web_search to read the full content of a promising result. Automatically strips navigation, ads, scripts and other noise.
Args: url (string — the full URL to fetch (must start with http:// or https://)), max_chars (int (optional, default 8000) — max characters to return)

## speak
Convert text to a spoken voice message (.wav file) using local Piper TTS. ONLY use this when Daksh explicitly says something like 'say this out loud', 'send me a voice message', 'speak this', or 'read this to me' in the current message. NEVER call this automatically just because the input was a voice message — always reply in text by default. Do not use for regular responses.
Args: text (string — the text to convert to speech (max ~2000 chars)), voice (string (optional, default 'en_US-lessac-medium') — Piper voice model name)

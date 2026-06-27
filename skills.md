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

"""
Tool registry.

To add a new tool:
  1. Write a function in tools/ (takes dict, returns str)
  2. Import and register it in TOOLS below
  3. Run: python sync_skills.py
"""
import datetime
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent
MEMORY_PATH  = BASE_DIR / "memory.md"


# ── Core tool functions ──────────────────────────────────────────────────────

def get_current_time(args: dict) -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_memory(args: dict) -> str:
    if not MEMORY_PATH.exists():
        return "(memory.md is empty)"
    return MEMORY_PATH.read_text()


def append_memory(args: dict) -> str:
    note = (args.get("note") or "").strip()
    if not note:
        return "Error: 'note' argument is required"
    with MEMORY_PATH.open("a") as f:
        f.write(f"\n- {note}")
    return f"Saved to memory: {note}"


# ── Gmail tool functions ─────────────────────────────────────────────────────

def _gmail_list(args: dict) -> str:
    from tools.gmail import gmail_list
    return gmail_list(args)

def _gmail_read(args: dict) -> str:
    from tools.gmail import gmail_read
    return gmail_read(args)

def _gmail_draft(args: dict) -> str:
    from tools.gmail import gmail_draft
    return gmail_draft(args)

def _gmail_send(args: dict) -> str:
    from tools.gmail import gmail_send
    return gmail_send(args)


# ── Diagram tool functions ───────────────────────────────────────────────────

def generate_diagram(args: dict) -> str:
    from tools.diagrams import generate_diagram
    return generate_diagram(args)

def _clean_for_speech(text: str) -> str:
    """
    Strip HTML, URLs, and other non-speakable content from email text
    before passing to TTS.
    """
    import re

    # Remove HTML tags
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)

    # Decode common HTML entities
    text = text.replace("&amp;", "and").replace("&nbsp;", " ")
    text = text.replace("&lt;", "").replace("&gt;", "")
    text = text.replace("&#39;", "'").replace("&quot;", '"')

    # Remove URLs entirely — they sound terrible spoken aloud
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)

    # Remove email addresses
    text = re.sub(r"\S+@\S+\.\S+", "", text)

    # Remove hex color codes and CSS values
    text = re.sub(r"#[0-9a-fA-F]{3,8}\b", "", text)
    text = re.sub(r"\b\d+px\b", "", text)
    text = re.sub(r"\b\d+%", "", text)

    # Remove emoji and non-ASCII symbols — crash on Windows cp1252 stdin
    text = re.sub(r"[^\x00-\x7F]+", " ", text)

    # Collapse whitespace and blank lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _gmail_read_and_speak(args: dict) -> str:
    """Read an email and convert a spoken summary to a voice message."""
    from tools.gmail import gmail_read, gmail_list
    from tools.tts import speak

    msg_id = (args.get("id") or "").strip()
    if not msg_id:
        email_text = gmail_list({"count": 1, "read_latest": True})
    else:
        email_text = gmail_read({"id": msg_id})

    if email_text.startswith("Error") or email_text.startswith("No emails"):
        return email_text

    # Extract sender/subject for context
    sender = ""
    subject = ""
    for line in email_text.splitlines():
        if line.startswith("From:"):
            sender = line.replace("From:", "").strip()
        elif line.startswith("Subject:"):
            subject = line.replace("Subject:", "").strip()

    # Clean the body for speech
    spoken_text = _clean_for_speech(email_text)

    # If the cleaned text is still long, create a concise spoken summary
    if len(spoken_text) > 1200:
        lines = [l.strip() for l in spoken_text.splitlines() if l.strip()]
        content_lines = []
        char_count = 0
        for line in lines:
            if any(line.startswith(h) for h in ("From:", "To:", "Subject:", "Date:", "ID:")):
                continue
            content_lines.append(line)
            char_count += len(line)
            if char_count > 1500:
                break

        summary = " ".join(content_lines)
        spoken_text = (
            f"Email from {sender.split('<')[0].strip() or 'unknown'}. "
            f"Subject: {subject}. "
            f"{summary}"
        )
        if char_count > 1500:
            spoken_text += " ... message continues."

    # Final length cap — 2000 chars is ~2 minutes of speech, reasonable max
    if len(spoken_text) > 2000:
        spoken_text = spoken_text[:2000] + " ... message truncated."

    tts_result = speak({"text": spoken_text})
    if tts_result.startswith("Error") or "failed" in tts_result.lower():
        return f"TTS failed: {tts_result}\n\nEmail content:\n{email_text}"

    return f"{tts_result}\n\nEmail content:\n{email_text}"





# ── Diagram tool functions ───────────────────────────────────────────────────

def _generate_diagram(args: dict) -> str:
    from tools.diagrams import generate_diagram
    return generate_diagram(args)

def _render_mermaid(args: dict) -> str:
    from tools.diagrams import render_mermaid
    return render_mermaid(args)


# ── Web tool functions ───────────────────────────────────────────────────────

def _web_search(args: dict) -> str:
    from tools.web import web_search
    return web_search(args)

def _web_fetch(args: dict) -> str:
    from tools.web import web_fetch
    return web_fetch(args)


# ── TTS tool functions ───────────────────────────────────────────────────────

def _speak(args: dict) -> str:
    from tools.tts import speak
    return speak(args)


# ── Registry ─────────────────────────────────────────────────────────────────

TOOLS = {
    # Core
    "get_current_time": {
        "func": get_current_time,
        "description": "Get the current date and time.",
        "args_schema": {},
    },
    "read_memory": {
        "func": read_memory,
        "description": "Read the full contents of memory.md. Only call this if you need to verify memory changed after an append — it is already loaded in your system prompt.",
        "args_schema": {},
    },
    "append_memory": {
        "func": append_memory,
        "description": "Save a durable fact or note to memory.md so it persists across sessions.",
        "args_schema": {"note": "string — the fact or note to remember"},
    },

    # Gmail
    "gmail_list": {
        "func": _gmail_list,
        "description": "List recent emails from Gmail inbox. Returns ID, sender, subject, date, and snippet for each. Use read_latest=true to immediately read the full body of the newest email instead of just listing.",
        "args_schema": {
            "count": "int (optional, default 10) — number of emails to fetch",
            "query": "string (optional, default 'in:inbox') — Gmail search query e.g. 'is:unread' or 'from:someone@example.com'",
            "read_latest": "bool (optional, default false) — if true, returns full body of the first/latest email",
        },
    },
    "gmail_read": {
        "func": _gmail_read,
        "description": "Read the full body of a specific email by its ID (from gmail_list).",
        "args_schema": {
            "id": "string — Gmail message ID from gmail_list",
        },
    },
    "gmail_draft": {
        "func": _gmail_draft,
        "description": (
            "Compose an email reply and store it as a pending draft. "
            "ALWAYS use this before gmail_send — never send without drafting first. "
            "Show the draft output to Daksh and wait for explicit confirmation before calling gmail_send."
        ),
        "args_schema": {
            "to": "string — recipient email address",
            "subject": "string — email subject line",
            "body": "string — full plain-text email body",
            "reply_to_id": "string (optional) — original message ID to thread the reply correctly",
        },
    },
    "gmail_send": {
        "func": _gmail_send,
        "description": (
            "Send the pending draft created by gmail_draft. "
            "ONLY call this after Daksh has explicitly confirmed (e.g. 'yes', 'send it', 'confirm'). "
            "Never call this speculatively or without confirmation."
        ),
        "args_schema": {},
    },
    "gmail_read_and_speak": {
        "func": _gmail_read_and_speak,
        "description": (
            "Read an email and convert its body to a voice message in one step. "
            "Use this when Daksh asks to hear/read an email out loud or as a voice message. "
            "More efficient than chaining gmail_read + speak separately."
        ),
        "args_schema": {
            "id": "string (optional) — Gmail message ID; if omitted, reads the latest inbox email",
        },
    },

    # Diagrams
    "generate_diagram": {
        "func": _generate_diagram,
        "description": (
            "Generate a diagram from a plain English description. "
            "Converts the description to Mermaid syntax and renders it to a PNG image "
            "(viewable inline in Discord/Telegram) that opens automatically. "
            "Supports flowcharts, sequence diagrams, ER diagrams, mind maps, "
            "Gantt charts, state diagrams, and more."
        ),
        "args_schema": {
            "description": "string — plain English description of what the diagram should show",
            "format": "string (optional, default 'png') — output format: 'png' (renders inline in Discord/Telegram) or 'svg' (downloads only)",
        },
    },
    "render_mermaid": {
        "func": _render_mermaid,
        "description": "Render raw Mermaid diagram syntax directly to an SVG/PNG file. Use this when you already have valid Mermaid code.",
        "args_schema": {
            "syntax": "string — valid Mermaid diagram syntax",
            "format": "string (optional, default 'svg') — 'svg' or 'png'",
        },
    },

    # Web
    "web_search": {
        "func": _web_search,
        "description": (
            "Search the web using DuckDuckGo (free, no API key). "
            "Use for current events, recent news, factual lookups, prices, "
            "anything that requires up-to-date information beyond training data. "
            "Returns titles, URLs and snippets for the top results."
        ),
        "args_schema": {
            "query": "string — the search query",
            "max": "int (optional, default 5) — max number of results to return",
        },
    },
    "web_fetch": {
        "func": _web_fetch,
        "description": (
            "Fetch and extract readable text from a specific URL. "
            "Use after web_search to read the full content of a promising result. "
            "Automatically strips navigation, ads, scripts and other noise."
        ),
        "args_schema": {
            "url": "string — the full URL to fetch (must start with http:// or https://)",
            "max_chars": "int (optional, default 8000) — max characters to return",
        },
    },

    # Text-to-speech
    "speak": {
        "func": _speak,
        "description": (
            "Convert text to a spoken voice message (.wav file) using local Piper TTS. "
            "ONLY use this when Daksh explicitly says something like 'say this out loud', "
            "'send me a voice message', 'speak this', or 'read this to me' in the current message. "
            "NEVER call this automatically just because the input was a voice message — "
            "always reply in text by default. Do not use for regular responses."
        ),
        "args_schema": {
            "text": "string — the text to convert to speech (max ~2000 chars)",
            "voice": "string (optional, default 'en_US-lessac-medium') — Piper voice model name",
        },
    },
}


def run_tool(name: str, args: dict) -> str:
    if name not in TOOLS:
        return f"Error: unknown tool '{name}'. Available: {', '.join(TOOLS)}"
    try:
        return TOOLS[name]["func"](args)
    except Exception as e:
        return f"Error running tool '{name}': {e}"


def init_tools(llm_client) -> None:
    """
    Call this after creating the LLM client to wire it into tools that need it.
    Currently used by the diagram tool to generate Mermaid syntax.
    """
    from tools.diagrams import set_llm_client
    set_llm_client(llm_client)
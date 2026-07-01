"""
Web search and page fetch tools for the local agent.

Provides two agent-callable functions:
  web_search — search the web via DuckDuckGo (free, no API key needed)
  web_fetch  — fetch and extract clean readable text from a URL

Search backend:
  Primary:  DuckDuckGo Instant Answer API (free, no key, personal use)
  Fallback: DuckDuckGo HTML scraping (when the JSON API returns no results)

Install:
  pip install duckduckgo-search beautifulsoup4 --break-system-packages
"""
import re
import textwrap
from urllib.parse import urlparse

# Max characters to return from a fetched page — enough to answer most
# questions without flooding the agent's context window
FETCH_MAX_CHARS = 8000
SEARCH_MAX_RESULTS = 5


# ── Web search ────────────────────────────────────────────────────────────────

def web_search(args: dict) -> str:
    """
    Search the web using DuckDuckGo. Returns titles, URLs, and snippets.
    Args:
      query    (str) — the search query
      max      (int, optional) — max results to return (default 5)
    """
    query = (args.get("query") or "").strip()
    max_results = int(args.get("max", SEARCH_MAX_RESULTS))

    if not query:
        return "Error: 'query' argument is required"

    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # older package name fallback
    except ImportError:
        return (
            "ddgs not installed.\n"
            "Run: pip install ddgs --break-system-packages"
        )

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)

        if not results:
            return f"No results found for: {query}"

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title   = r.get("title", "No title")
            url     = r.get("href", "")
            snippet = r.get("body", "")
            lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}\n")

        return "\n".join(lines)

    except Exception as e:
        return f"Search failed: {e}"


# ── Web fetch ─────────────────────────────────────────────────────────────────

def web_fetch(args: dict) -> str:
    """
    Fetch a web page and return its clean readable text content.
    Use this to read the full content of a specific URL from search results.
    Args:
      url      (str) — the URL to fetch
      max_chars (int, optional) — max chars to return (default 8000)
    """
    url = (args.get("url") or "").strip()
    max_chars = int(args.get("max_chars", FETCH_MAX_CHARS))

    if not url:
        return "Error: 'url' argument is required"

    # Basic URL validation
    try:
        parsed = urlparse(url)
        if not parsed.scheme in ("http", "https"):
            return f"Error: invalid URL scheme — must be http or https: {url}"
    except Exception:
        return f"Error: invalid URL: {url}"

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return (
            "beautifulsoup4 not installed.\n"
            "Run: pip install beautifulsoup4 --break-system-packages"
        )

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Extract clean text from HTML
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "advertisement", "noscript", "iframe"]):
            tag.decompose()

        # Extract text — prefer article/main content if available
        main = (
            soup.find("article") or
            soup.find("main") or
            soup.find(id=re.compile(r"content|article|main", re.I)) or
            soup.find(class_=re.compile(r"content|article|main|post", re.I)) or
            soup.body
        )

        if main:
            text = main.get_text(separator="\n")
        else:
            text = soup.get_text(separator="\n")

        # Clean up whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        if not text:
            return f"No readable text found at: {url}"

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... content truncated at {max_chars} chars]"

        return f"Content from {url}:\n\n{text}"

    except requests.exceptions.Timeout:
        return f"Timeout fetching {url} (>15s)"
    except requests.exceptions.HTTPError as e:
        return f"HTTP error fetching {url}: {e}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

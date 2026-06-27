"""
Diagram generation tool for the local agent.

Two agent-callable functions:
  generate_diagram  — plain English → Mermaid syntax → rendered SVG/PNG
  render_mermaid    — raw Mermaid syntax → rendered SVG/PNG

Rendering strategy (in order):
  1. mmdc CLI with explicit Chrome path (puppeteer-config.json)
  2. mmdc CLI without config (fallback)
  3. Save .mmd + open mermaid.live in browser (always works)

Chrome detection: runs a Node.js script that walks all known Puppeteer
cache locations and returns the first real executable it finds.
"""

import os
import re
import json
import subprocess
import shutil
import base64
import webbrowser
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

# Global LLM client — set by tools.py via set_llm_client()
_llm_client = None

def set_llm_client(client) -> None:
    global _llm_client
    _llm_client = client


# ── Chrome / mmdc detection ──────────────────────────────────────────────────

def _find_mmdc() -> str | None:
    found = shutil.which("mmdc")
    if found:
        return found
    # Windows: npm global bin may not be in PATH inside venv
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidate = Path(appdata) / "npm" / "mmdc.cmd"
        if candidate.exists():
            return str(candidate)
    return None


# Node.js script that searches every known Puppeteer cache location
_CHROME_FINDER_JS = r"""
const fs = require('fs');
const path = require('path');
const os = require('os');

const home = os.homedir();
const appdata = process.env.APPDATA || '';
const localappdata = process.env.LOCALAPPDATA || '';

// All known Puppeteer/playwright cache roots
const roots = [
  path.join(home, '.cache', 'puppeteer'),
  path.join(home, '.cache', 'ms-playwright'),
  path.join(appdata, 'npm', 'node_modules', '@mermaid-js', 'mermaid-cli', 'node_modules', 'puppeteer', '.local-chromium'),
  path.join(appdata, 'npm', 'node_modules', 'puppeteer', '.local-chromium'),
  path.join(localappdata, 'ms-playwright'),
];

// Executable names to look for (in priority order)
const exeNames = ['chrome-headless-shell.exe', 'chrome.exe', 'chrome-headless-shell', 'chrome'];

function findExe(dir) {
  if (!fs.existsSync(dir)) return null;
  try {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        const found = findExe(full);
        if (found) return found;
      } else if (exeNames.includes(entry.name)) {
        return full;
      }
    }
  } catch (e) {}
  return null;
}

// Also try require('puppeteer') if installed
try {
  const p = require('puppeteer');
  const ep = p.executablePath ? p.executablePath() : null;
  if (ep && fs.existsSync(ep)) { console.log(ep); process.exit(0); }
} catch(e) {}

for (const root of roots) {
  const found = findExe(root);
  if (found) { console.log(found); process.exit(0); }
}
process.exit(1);
"""

def _find_chrome() -> str | None:
    """Locate Chrome/Puppeteer executable."""

    # Fast path: known exact locations (most specific first)
    home = Path.home()
    quick_checks = [
        # Hardcoded path confirmed on this machine
        home / ".cache/puppeteer/chrome-headless-shell/win64-149.0.7827.22/chrome-headless-shell-win64/chrome-headless-shell.exe",
        home / ".cache/puppeteer/chrome/win64-149.0.7827.22/chrome-win64/chrome.exe",
        # Glob for any version (future-proof)
        *sorted((home / ".cache/puppeteer").glob(
            "chrome-headless-shell/win64-*/chrome-headless-shell-win64/chrome-headless-shell.exe"
        ), reverse=True),
        *sorted((home / ".cache/puppeteer").glob(
            "chrome/win64-*/chrome-win64/chrome.exe"
        ), reverse=True),
    ]
    for p in quick_checks:
        if isinstance(p, Path) and p.exists():
            return str(p)

    # Full Node.js search as fallback
    script_path = OUTPUTS_DIR / "_find_chrome.js"
    script_path.write_text(_CHROME_FINDER_JS, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True, text=True, timeout=15,
        )
        path = result.stdout.strip()
        if path and Path(path).exists():
            return path
    except Exception:
        pass
    return None


def _write_puppeteer_config(chrome_path: str) -> Path:
    config = {
        "executablePath": chrome_path,
        "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    }
    config_path = OUTPUTS_DIR / "puppeteer-config.json"
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


# ── mermaid.live fallback ────────────────────────────────────────────────────

def _mermaid_live_url(syntax: str) -> str:
    """
    Build a mermaid.live URL using plain base64 encoding.
    mermaid.live accepts #base64:<base64url(json)> in addition to pako.
    This is simpler and 100% reliable from Python.
    """
    state = json.dumps({
        "code": syntax,
        "mermaid": {"theme": "default"},
        "autoSync": True,
        "rough": False,
        "updateDiagram": True,
    }, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(state.encode("utf-8")).decode("utf-8")
    return f"https://mermaid.live/edit#base64:{encoded}"


def _open_url(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _open_file(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))
        else:
            cmd = "open" if Path("/usr/bin/open").exists() else "xdg-open"
            subprocess.Popen([cmd, str(path)])
    except Exception:
        pass


# ── Core render function ─────────────────────────────────────────────────────

def _clean_syntax(syntax: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    return re.sub(r"^```(mermaid)?\s*|\s*```$", "", syntax.strip(), flags=re.MULTILINE).strip()


def _render_mermaid_syntax(syntax: str, output_format: str = "svg") -> tuple[bool, str]:
    """
    Try to render with mmdc. Falls back to mermaid.live on any failure.
    Always saves the .mmd source file regardless.
    """
    syntax = _clean_syntax(syntax)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mmd_path  = OUTPUTS_DIR / f"diagram_{timestamp}.mmd"
    out_path  = OUTPUTS_DIR / f"diagram_{timestamp}.{output_format}"
    mmd_path.write_text(syntax, encoding="utf-8")

    mmdc = _find_mmdc()
    if not mmdc:
        # No mmdc — go straight to mermaid.live
        url = _mermaid_live_url(syntax)
        _open_url(url)
        return False, f"mmdc not found. Opened in mermaid.live.\nSource: {mmd_path}\nInstall: npm install -g @mermaid-js/mermaid-cli"

    # Find Chrome and write puppeteer config
    chrome_path = _find_chrome()
    cmd = [mmdc, "-i", str(mmd_path), "-o", str(out_path), "--quiet"]
    if chrome_path:
        config_path = _write_puppeteer_config(chrome_path)
        cmd += ["--puppeteerConfigFile", str(config_path)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

        if result.returncode == 0 and out_path.exists():
            _open_file(out_path)
            return True, str(out_path)

        # Render failed — fall back to mermaid.live
        err = (result.stderr + result.stdout).strip()[:400]
        url = _mermaid_live_url(syntax)
        _open_url(url)
        return False, (
            f"mmdc render failed. Opened in mermaid.live.\n"
            f"Chrome: {chrome_path or 'not found'}\n"
            f"Error: {err}\n"
            f"Source: {mmd_path}"
        )

    except subprocess.TimeoutExpired:
        url = _mermaid_live_url(syntax)
        _open_url(url)
        return False, f"mmdc timed out. Opened in mermaid.live.\nSource: {mmd_path}"
    except Exception as e:
        url = _mermaid_live_url(syntax)
        _open_url(url)
        return False, f"mmdc error: {e}. Opened in mermaid.live.\nSource: {mmd_path}"


# ── LLM → Mermaid syntax generation ─────────────────────────────────────────

DIAGRAM_SYSTEM_PROMPT = """\
You are a Mermaid diagram expert. Convert the description into valid Mermaid syntax.
Output ONLY the raw Mermaid code. No markdown fences, no explanation, no extra text.

Diagram type guide:
- Process / workflow / steps            → flowchart TD
- API calls / system interactions       → sequenceDiagram
- Database tables / relationships       → erDiagram
- Class structure / OOP                 → classDiagram
- Project timeline                      → gantt
- Topic hierarchy / brainstorm          → mindmap
- State machine                         → stateDiagram-v2
- Proportions / shares                  → pie

Rules:
- Always start with the diagram type keyword on the first line
- Quote labels that contain spaces or special characters
- Keep node IDs short (A, B, C or descriptive short words)
- Valid sequenceDiagram example:
    sequenceDiagram
        participant U as User
        participant S as Server
        U->>S: Request
        S-->>U: Response
"""

def _generate_mermaid_from_description(description: str, llm_client, retries: int = 2) -> tuple[bool, str]:
    """
    Call NIM directly WITHOUT json_object mode so we get raw Mermaid text.
    Tries Qwen first (reliable structured text), falls back to Stepfun on
    repeated timeout/failure. Retries once on timeout before giving up.
    """
    import requests as _req
    from requests.exceptions import ReadTimeout, ConnectionError

    messages = [
        {"role": "system", "content": DIAGRAM_SYSTEM_PROMPT},
        {"role": "user",   "content": description},
    ]

    attempts = [
        ("qwen/qwen3.5-122b-a10b", llm_client.qwen_api_key),
    ]
    # On the final attempt, fall back to Stepfun's key/model in case Qwen is
    # consistently timing out (e.g. NIM congestion on the 122B model)
    if hasattr(llm_client, "stepfun_api_key"):
        attempts.append(("stepfun-ai/step-3.5-flash", llm_client.stepfun_api_key))

    last_error = "Unknown error"

    for model, api_key in attempts:
        for attempt in range(retries):
            try:
                resp = _req.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.1,
                        "max_tokens": 1024,
                        "stream": False,
                        # NO response_format — raw text output only
                    },
                    timeout=180,  # generous — diagram generation can be slow
                )
                resp.raise_for_status()
                syntax = resp.json()["choices"][0]["message"]["content"] or ""
                syntax = _clean_syntax(syntax)

                if not syntax:
                    last_error = f"{model} returned empty Mermaid syntax"
                    continue

                first_line = syntax.splitlines()[0].lower()
                known_types = ["flowchart", "sequencediagram", "erdiagram", "classdiagram",
                               "gantt", "mindmap", "statediagram", "pie", "gitgraph", "journey"]
                if not any(first_line.startswith(t) for t in known_types):
                    print(f"[diagrams] Warning: unexpected first line: {first_line!r}")

                return True, syntax

            except (ReadTimeout,) as e:
                last_error = f"{model} timed out after 180s (attempt {attempt+1}/{retries})"
                print(f"[diagrams] {last_error}")
                continue
            except ConnectionError as e:
                last_error = f"{model} connection error: {e}"
                print(f"[diagrams] {last_error}")
                continue
            except Exception as e:
                last_error = f"{model} error: {e}"
                break  # non-retryable error, move to next model

    return False, f"Diagram generation failed after all attempts. Last error: {last_error}"


# ── Public tool functions ────────────────────────────────────────────────────

def generate_diagram(args: dict) -> str:
    description = (args.get("description") or "").strip()
    fmt         = args.get("format", "png").lower()  # PNG default — Discord/Telegram render it inline, SVG only downloads
    if not description:
        return "Error: 'description' argument is required"
    if fmt not in ("svg", "png"):
        fmt = "png"
    if not _llm_client:
        return "Error: LLM client not initialized"

    ok, syntax = _generate_mermaid_from_description(description, _llm_client)
    if not ok:
        return syntax

    ok, result = _render_mermaid_syntax(syntax, fmt)
    status = "Diagram rendered locally" if ok else "Diagram opened in mermaid.live (local render failed)"
    return f"{status}: {result}\n\nMermaid syntax:\n{syntax}"


def render_mermaid(args: dict) -> str:
    syntax = (args.get("syntax") or "").strip()
    fmt    = args.get("format", "svg").lower()
    if not syntax:
        return "Error: 'syntax' argument is required"
    if fmt not in ("svg", "png"):
        fmt = "svg"
    ok, result = _render_mermaid_syntax(syntax, fmt)
    status = "Rendered locally" if ok else "Opened in mermaid.live"
    return f"{status}: {result}"

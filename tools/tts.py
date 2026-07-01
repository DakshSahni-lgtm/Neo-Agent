"""
Text-to-speech tool using Piper — fully local, free, zero API cost.

Provides one agent-callable function:
  speak — converts text to a .wav voice file, ready to send via
          Discord/Telegram as a native voice message

Setup:
  1. Download piper.exe from https://github.com/rhasspy/piper/releases
     (piper_windows_amd64.zip) → extract to local-agent/piper/piper.exe
  2. Download a voice model from
     https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US
     Recommended: en_US-lessac-medium (.onnx + .onnx.json)
     → place both files in local-agent/piper/voices/

Output:
  Files saved to local-agent/outputs/speech_<timestamp>.wav
  Discord/Telegram bots auto-detect and attach .wav files as voice messages
  (same file-detection pattern used for diagrams).
"""
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR     = Path(__file__).parent.parent
PIPER_DIR    = BASE_DIR / "piper"
VOICES_DIR   = PIPER_DIR / "voices"
OUTPUTS_DIR  = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

DEFAULT_VOICE = "en_US-lessac-medium"

# Simple daily rate limit — since this is fully local/free there's no real
# cost, but a cap prevents runaway loops from generating hundreds of files.
DAILY_LIMIT_PATH = OUTPUTS_DIR / ".tts_usage.json"
DAILY_LIMIT      = 100  # generous since it's free, just a sanity guard


def _find_piper() -> Path | None:
    """Locate piper.exe — checks the bundled piper/ folder first, then PATH."""
    bundled = PIPER_DIR / "piper.exe"
    if bundled.exists():
        return bundled
    found = shutil.which("piper")
    return Path(found) if found else None


def _find_voice(voice_name: str) -> tuple[Path | None, Path | None]:
    """
    Returns (onnx_path, json_path) for a voice, or (None, None) if missing.
    Searches several common locations since Piper zip extraction sometimes
    nests voice files inside piper/espeak-ng-data/voices/ instead of
    piper/voices/ depending on how the zip was extracted.
    """
    search_dirs = [
        VOICES_DIR,
        PIPER_DIR / "espeak-ng-data" / "voices",
        PIPER_DIR,
    ]

    for d in search_dirs:
        onnx = d / f"{voice_name}.onnx"
        json_cfg = d / f"{voice_name}.onnx.json"
        if onnx.exists() and json_cfg.exists():
            return onnx, json_cfg

    # Last resort: recursive search under piper/ for the .onnx file by name
    if PIPER_DIR.exists():
        matches = list(PIPER_DIR.rglob(f"{voice_name}.onnx"))
        if matches:
            onnx = matches[0]
            json_cfg = onnx.parent / f"{onnx.name}.json"  # e.g. voice.onnx.json
            if json_cfg.exists():
                return onnx, json_cfg

    return None, None


def _check_daily_limit() -> tuple[bool, int]:
    """Returns (allowed, count_today). Resets automatically each day."""
    import json
    today = datetime.now().strftime("%Y-%m-%d")

    if DAILY_LIMIT_PATH.exists():
        try:
            data = json.loads(DAILY_LIMIT_PATH.read_text())
        except Exception:
            data = {}
    else:
        data = {}

    count = data.get(today, 0)
    if count >= DAILY_LIMIT:
        return False, count

    data = {today: count + 1}  # reset old days, only track today
    DAILY_LIMIT_PATH.write_text(json.dumps(data))
    return True, count + 1


def speak(args: dict) -> str:
    """
    Convert text to speech using local Piper TTS.
    Args:
      text  (str) — the text to speak (keep reasonably short — this is for
                    voice messages, not full document narration)
      voice (str, optional) — voice model name, default 'en_US-lessac-medium'
    """
    text  = (args.get("text") or "").strip()
    voice = args.get("voice", DEFAULT_VOICE).strip()

    if not text:
        return "Error: 'text' argument is required"

    # Strip markdown formatting that would sound bad read aloud
    clean_text = re.sub(r"[*_#`]", "", text)
    clean_text = re.sub(r"\n{2,}", ". ", clean_text)
    # Strip URLs and HTML that sound terrible when spoken
    clean_text = re.sub(r"https?://\S+", "", clean_text)
    clean_text = re.sub(r"<[^>]+>", " ", clean_text)
    clean_text = re.sub(r"#[0-9a-fA-F]{3,8}\b", "", clean_text)
    # Strip emoji and other non-ASCII symbols — Piper can't pronounce them
    # and Windows cp1252 stdin would crash on them anyway
    clean_text = re.sub(r"[^\x00-\x7F]+", " ", clean_text)
    clean_text = re.sub(r"[ \t]+", " ", clean_text)
    clean_text = clean_text.strip()

    if len(clean_text) > 2000:
        return (
            f"Error: text is too long for a voice message ({len(clean_text)} chars). "
            f"Keep it under 2000 characters — summarize first if needed."
        )

    allowed, count = _check_daily_limit()
    if not allowed:
        return f"Daily TTS limit reached ({DAILY_LIMIT}/day). Resets at midnight."

    piper = _find_piper()
    if not piper:
        return (
            "Piper not found. Download piper_windows_amd64.zip from "
            "https://github.com/rhasspy/piper/releases and extract piper.exe "
            "to local-agent/piper/piper.exe"
        )

    onnx_path, json_path = _find_voice(voice)
    if not onnx_path:
        return (
            f"Voice model '{voice}' not found in local-agent/piper/voices/. "
            f"Download {voice}.onnx and {voice}.onnx.json from "
            f"https://huggingface.co/rhasspy/piper-voices"
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path  = OUTPUTS_DIR / f"speech_{timestamp}.wav"

    try:
        # Encode as UTF-8 bytes explicitly — Windows defaults to cp1252
        # which crashes on emoji and many Unicode characters in email content
        result = subprocess.run(
            [str(piper), "--model", str(onnx_path), "--output_file", str(out_path)],
            input=clean_text.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )

        if result.returncode != 0 or not out_path.exists():
            err = (result.stderr or result.stdout).strip()[:300]
            return f"Piper TTS failed: {err}"

        return f"Voice message generated: {out_path}\n\n(usage today: {count}/{DAILY_LIMIT})"

    except subprocess.TimeoutExpired:
        return "Piper timed out after 120s — text may be too long"
    except Exception as e:
        return f"Error running Piper: {e}"

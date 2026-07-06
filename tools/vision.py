"""
Image understanding tool for the local agent — via NVIDIA NIM (free).

Model: meta/llama-3.2-90b-vision-instruct
  Strong general vision model — handles photos, screenshots, diagrams,
  charts, OCR (reading text in images), and general scene description.

Get a free API key: build.nvidia.com → search "llama-3.2-90b-vision" →
Generate API Key. Add to .env as VISION_API_KEY.

Two entry points:
  describe_image_from_bytes(image_bytes, question) — used internally by
    Discord/Telegram bots when a user sends a photo (same pattern as STT
    voice transcription — happens before the agent loop runs).
  analyze_image(args) — agent-callable tool for analyzing images already
    saved on disk (generated diagrams, downloaded Drive images, etc.)
"""
import os
import base64
from pathlib import Path

NIM_BASE_URL  = "https://integrate.api.nvidia.com/v1/chat/completions"
VISION_MODEL  = "meta/llama-3.2-90b-vision-instruct"
VISION_TIMEOUT = 60

DEFAULT_QUESTION = "Describe this image in detail. If it contains text, charts, tables, or diagrams, transcribe and explain the key information."


def _get_vision_api_key() -> str | None:
    return os.environ.get("VISION_API_KEY")


def _guess_mime(image_bytes: bytes, filename: str = "") -> str:
    """Best-effort MIME type detection from file extension or magic bytes."""
    ext = Path(filename).suffix.lower()
    ext_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
    }
    if ext in ext_map:
        return ext_map[ext]

    # Magic byte sniffing fallback
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"  # reasonable default


def describe_image_from_bytes(
    image_bytes: bytes,
    question: str = DEFAULT_QUESTION,
    filename: str = "",
) -> str:
    """
    Core vision call — sends raw image bytes + a question to the NIM vision
    model, returns the text response. Raises RuntimeError on failure.
    """
    import requests

    api_key = _get_vision_api_key()
    if not api_key:
        raise RuntimeError(
            "VISION_API_KEY not set in .env.\n"
            "Get a free key: build.nvidia.com → llama-3.2-90b-vision-instruct → Generate API Key"
        )

    mime = _guess_mime(image_bytes, filename)
    b64  = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    resp = requests.post(
        NIM_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=VISION_TIMEOUT,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    if not content:
        raise RuntimeError("Vision model returned empty response")

    return content.strip()


# ── Agent-callable tool ──────────────────────────────────────────────────────

def analyze_image(args: dict) -> str:
    """
    Analyze an image file already saved on disk (e.g. a generated diagram
    or a file downloaded from Drive). For images sent directly in chat,
    this happens automatically — no need to call this tool for those.
    Args:
      path     (str) — path to the image file on disk
      question (str, optional) — specific question to ask about the image
    """
    path     = (args.get("path") or "").strip()
    question = (args.get("question") or "").strip() or DEFAULT_QUESTION

    if not path:
        return "Error: 'path' is required"

    file_path = Path(path)
    if not file_path.exists():
        return f"Error: file not found at '{path}'"

    try:
        image_bytes = file_path.read_bytes()
        result = describe_image_from_bytes(image_bytes, question, filename=file_path.name)
        return result
    except Exception as e:
        return f"Error analyzing image: {e}"

"""
LLM client — two-model strategy via NVIDIA NIM (both free):

  1. Stepfun Step-3.5-Flash  (PRIMARY)
  2. Qwen3.5-122B-A10B       (FALLBACK)

Known NIM quirks handled here:
  - Stepfun returns null content with response_format=json_object → disabled
  - Stepfun sometimes wraps JSON in markdown fences → stripped by orchestrator
  - Qwen3.5-122B is slow on free tier → 240s timeout, ReadTimeout is retried
"""
import os
import time
import requests
from requests.exceptions import ReadTimeout, ConnectionError

NIM_BASE_URL  = "https://integrate.api.nvidia.com/v1/chat/completions"
STEPFUN_MODEL = "stepfun-ai/step-3.5-flash"
QWEN_MODEL    = "qwen/qwen3.5-122b-a10b"

# Stepfun is fast, Qwen3.5-122B is slow on free tier — separate timeouts
STEPFUN_TIMEOUT = 60
QWEN_TIMEOUT    = 240


class LLMClient:
    def __init__(
        self,
        stepfun_api_key: str | None = None,
        qwen_api_key: str | None = None,
        force_qwen: bool = False,
    ):
        self.stepfun_api_key = stepfun_api_key or os.environ.get("STEPFUN_API_KEY")
        self.qwen_api_key    = qwen_api_key    or os.environ.get("QWEN_API_KEY")
        self.force_qwen      = force_qwen

        if not self.stepfun_api_key:
            raise RuntimeError(
                "STEPFUN_API_KEY not set — add it to .env\n"
                "Get key: https://build.nvidia.com → Stepfun page → Generate API Key"
            )
        if not self.qwen_api_key:
            raise RuntimeError(
                "QWEN_API_KEY not set — add it to .env\n"
                "Get key: https://build.nvidia.com → Qwen3.5 page → Generate API Key"
            )

    def chat(self, messages: list[dict]) -> str:
        if self.force_qwen:
            print("[llm] Qwen3.5-122B (forced)")
            return self._call_qwen(messages)

        try:
            return self._call_stepfun(messages)
        except Exception as e:
            print(f"[llm] Stepfun failed ({type(e).__name__}: {e})")
            print("[llm] Falling back to Qwen3.5-122B...")
            return self._call_qwen(messages)

    # ── Stepfun ──────────────────────────────────────────────────────────────
    # No response_format — Stepfun returns null content with json_object on NIM.
    # The orchestrator already handles markdown-fence stripping and JSON parsing.

    def _call_stepfun(self, messages: list[dict], retries: int = 2) -> str:
        wait_times = [10, 20]
        for attempt in range(retries):
            try:
                resp = requests.post(
                    NIM_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {self.stepfun_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": STEPFUN_MODEL,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 1024,
                        "stream": False,
                        # NO response_format — Stepfun doesn't support it on NIM
                    },
                    timeout=STEPFUN_TIMEOUT,
                )
            except (ReadTimeout, ConnectionError) as e:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"[llm] Stepfun network error ({e}). Waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"[llm] Stepfun rate limited. Waiting {wait}s (retry {attempt+1}/{retries})...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            if content is None:
                raise RuntimeError(
                    "Stepfun returned null content — NIM may be having issues"
                )
            return content

        raise RuntimeError(f"Stepfun failed after {retries} attempts.")

    # ── Qwen3.5-122B ─────────────────────────────────────────────────────────
    # Uses json_object response_format (confirmed working on NIM).
    # Longer timeout because the 122B model is slow on the free tier.

    def _call_qwen(self, messages: list[dict], retries: int = 3) -> str:
        wait_times = [15, 30, 60]
        for attempt in range(retries):
            try:
                resp = requests.post(
                    NIM_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {self.qwen_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": QWEN_MODEL,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 1024,
                        "stream": False,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=QWEN_TIMEOUT,
                )
            except ReadTimeout:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"[llm] Qwen timed out (>{QWEN_TIMEOUT}s). Waiting {wait}s and retrying ({attempt+1}/{retries})...")
                time.sleep(wait)
                continue
            except ConnectionError as e:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"[llm] Qwen connection error ({e}). Waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                wait = wait_times[min(attempt, len(wait_times) - 1)]
                print(f"[llm] Qwen rate limited. Waiting {wait}s (retry {attempt+1}/{retries})...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if content is None:
                raise RuntimeError("Qwen returned null content")
            return content

        raise RuntimeError(f"Qwen3.5 failed after {retries} attempts (timeout={QWEN_TIMEOUT}s).")

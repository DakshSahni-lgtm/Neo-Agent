"""
Telegram bot interface for the local agent.

Wraps the same Orchestrator used by main.py — no agent logic duplicated.
Listens for messages via long polling, runs them through the agent,
sends the reply back. Verbose "thinking" output is suppressed for Telegram
(stays clean — only the final answer is sent).

Setup:
  1. Message @BotFather on Telegram, /newbot, get your token
  2. Add to .env:  TELEGRAM_BOT_TOKEN=123456789:ABC...
  3. Add to .env:  TELEGRAM_ALLOWED_USER_ID=your_telegram_user_id
     (get your ID by messaging @userinfobot on Telegram)
  4. pip install python-telegram-bot --break-system-packages
  5. python telegram_bot.py

Security:
  Only responds to TELEGRAM_ALLOWED_USER_ID — anyone else messaging the bot
  gets ignored. This is YOUR personal agent, not a public bot.
"""
import os
import re
import logging
from pathlib import Path

# Load .env
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

try:
    from telegram import Update
    from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
except ImportError:
    raise SystemExit(
        "python-telegram-bot not installed.\n"
        "Run: pip install python-telegram-bot --break-system-packages"
    )

from core.orchestrator import Orchestrator
from core.llm_client import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN: str | None = os.environ.get("TELEGRAM_BOT_TOKEN")
_allowed_user_raw = os.environ.get("TELEGRAM_ALLOWED_USER_ID")

if not BOT_TOKEN:
    raise SystemExit("TELEGRAM_BOT_TOKEN not set in .env")
if not _allowed_user_raw:
    raise SystemExit(
        "TELEGRAM_ALLOWED_USER_ID not set in .env\n"
        "Message @userinfobot on Telegram to get your numeric user ID."
    )

ALLOWED_USER_ID: int = int(_allowed_user_raw)

# One orchestrator instance persists for the bot's lifetime —
# session context (e.g. last email read) carries across messages naturally.
agent = Orchestrator(llm=LLMClient())

# Rolling conversation history — Telegram is single-user so one global list
_conversation_history: list[dict] = []
HISTORY_MAX_MESSAGES = 20

BASE_DIR = Path(__file__).parent

# Matches generated file paths (both relative "outputs/x.png" and full
# Windows paths) referenced in the agent's text answer — same pattern
# used by discord_bot.py
_FILE_PATH_PATTERN = re.compile(
    r"([A-Za-z]:[\\/][^\s]*?[\\/]outputs[\\/][\w\-.]+\.(?:svg|png|jpg|jpeg|gif|wav|mp3)"
    r"|outputs[\\/][\w\-.]+\.(?:svg|png|jpg|jpeg|gif|wav|mp3))",
    re.IGNORECASE,
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif"}
VOICE_EXTS = {".wav", ".mp3"}


def _extract_attachable_files(text: str) -> list[Path]:
    """Find file paths mentioned in the agent's answer that actually exist on disk."""
    found = []
    for match in _FILE_PATH_PATTERN.finditer(text):
        raw_path = match.group(1)
        if re.match(r"^[A-Za-z]:[\\/]", raw_path):
            full_path = Path(raw_path)
        else:
            full_path = BASE_DIR / raw_path.replace("\\", "/")
        if full_path.exists() and full_path not in found:
            found.append(full_path)
    return found


def _is_authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ALLOWED_USER_ID


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        logger.warning(f"Unauthorized access attempt from user {update.effective_user.id}")
        return

    # ── Voice message handling ───────────────────────────────────────────────
    user_text = ""
    if update.message.voice or update.message.audio:
        voice = update.message.voice or update.message.audio
        tmp_path = BASE_DIR / "outputs" / f"voice_in_{voice.file_id}.ogg"
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )
            tg_file = await context.bot.get_file(voice.file_id)
            await tg_file.download_to_drive(tmp_path)
            logger.info(f"[stt] Transcribing Telegram voice message")

            import asyncio
            loop = asyncio.get_running_loop()
            from tools.stt import transcribe
            user_text = await loop.run_in_executor(None, lambda: transcribe(tmp_path))
            logger.info(f"[stt] Transcript: {user_text}")

        except Exception as e:
            logger.error(f"[stt] Transcription failed: {e}")
            await update.message.reply_text(
                f"I received your voice message but couldn't transcribe it: {e}\n"
                "Make sure faster-whisper is installed: "
                "`pip install faster-whisper --break-system-packages`"
            )
            return
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
    else:
        user_text = update.message.text or ""

    if not user_text:
        return

    logger.info(f"You: {user_text}")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # global declaration must come before any use of _conversation_history
    global _conversation_history

    try:
        answer = agent.run(user_text, verbose=True, conversation_history=_conversation_history)
    except Exception as e:
        answer = f"Something went wrong: {e}"
        logger.error(f"Agent error: {e}")

    # Update global conversation history with this exchange
    _conversation_history = (_conversation_history + [
        {"role": "user",      "content": user_text},
        {"role": "assistant", "content": answer},
    ])[-HISTORY_MAX_MESSAGES:]

    logger.info(f"Agent: {answer}")
    await update.message.reply_text(answer)

    for file_path in _extract_attachable_files(answer):
        ext = file_path.suffix.lower()
        try:
            with open(file_path, "rb") as f:
                if ext in IMAGE_EXTS:
                    await update.message.reply_photo(photo=f)
                elif ext in VOICE_EXTS:
                    await update.message.reply_voice(voice=f)
                else:
                    await update.message.reply_document(document=f)
        except Exception as e:
            logger.error(f"Failed to send attachment {file_path}: {e}")


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "Local agent connected. Send me anything — text or voice messages. "
        "I can check your email, draft replies, generate diagrams, or just chat."
    )


def main():
    assert BOT_TOKEN is not None
    assert BOT_TOKEN is not None
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", handle_start))
    # Handle both text messages and voice/audio messages
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.VOICE | filters.AUDIO) & ~filters.COMMAND,
        handle_message
    ))

    print("[telegram] Bot starting... (Ctrl+C to stop)")
    print(f"[telegram] Listening for messages from user ID: {ALLOWED_USER_ID}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
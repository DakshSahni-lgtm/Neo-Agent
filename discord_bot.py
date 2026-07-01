"""
Discord bot interface for the local agent.

Wraps the same Orchestrator used by main.py and telegram_bot.py — no agent
logic duplicated. Responds only to DMs or messages in a specific allowed
channel from a specific allowed user, keeping this a personal agent rather
than a public bot.

Setup:
  1. Go to https://discord.com/developers/applications → New Application
  2. Bot tab → Add Bot → Reset Token → copy it
  3. Bot tab → enable "Message Content Intent" under Privileged Gateway Intents
  4. OAuth2 → URL Generator → scope: bot → permissions: Send Messages,
     Read Message History → open generated URL → add bot to your server
  5. Add to .env:
       DISCORD_BOT_TOKEN=your_token
       DISCORD_ALLOWED_USER_ID=your_discord_user_id
     (get your Discord user ID: enable Developer Mode in Settings > Advanced,
      then right-click your name > Copy User ID)
  6. pip install discord.py --break-system-packages
  7. python discord_bot.py

Usage:
  - DM the bot directly (works from anywhere, simplest setup), OR
  - @ mention the bot in any channel it's in

Security:
  Only responds to DISCORD_ALLOWED_USER_ID — all other users are ignored.
"""
import os
import re
import logging
import asyncio
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
    import discord
except ImportError:
    raise SystemExit(
        "discord.py not installed.\n"
        "Run: pip install discord.py --break-system-packages"
    )

from core.orchestrator import Orchestrator
from core.llm_client import LLMClient
from core.scheduler import AgentScheduler
from tools.scheduler_tool import set_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN: str | None = os.environ.get("DISCORD_BOT_TOKEN")
_allowed_user_raw = os.environ.get("DISCORD_ALLOWED_USER_ID")

if not BOT_TOKEN:
    raise SystemExit("DISCORD_BOT_TOKEN not set in .env")
if not _allowed_user_raw:
    raise SystemExit(
        "DISCORD_ALLOWED_USER_ID not set in .env\n"
        "Enable Developer Mode in Discord Settings > Advanced, then "
        "right-click your username > Copy User ID."
    )

ALLOWED_USER_ID: int = int(_allowed_user_raw)

DISCORD_MAX_LEN = 2000  # Discord's hard message length limit

# One orchestrator instance persists for the bot's lifetime —
# session context (e.g. last email read) carries across messages naturally.
agent = Orchestrator(llm=LLMClient())

# Proactive scheduler — set up fully in on_ready() once the event loop is running
agent_scheduler: AgentScheduler | None = None

# Rolling conversation history — keeps the last N exchanges so the agent
# remembers what was discussed earlier in the same session.
# Keyed by channel_id so DMs and server channels have separate histories.
_conversation_histories: dict[int, list[dict]] = {}
HISTORY_MAX_MESSAGES = 20  # 10 exchanges (user + assistant pairs)

intents = discord.Intents.default()
intents.message_content = True  # required to read message text
intents.dm_messages = True

client = discord.Client(intents=intents)


def _split_message(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """Discord caps messages at 2000 chars — split long replies into chunks."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split on a newline near the limit for cleaner breaks
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# Matches any path ending in .../outputs/<filename>.<ext> — handles both
# relative ("outputs/diagram.svg") and full Windows paths
# ("C:\Users\...\outputs\diagram.svg")
_FILE_PATH_PATTERN = re.compile(
    r"([A-Za-z]:[\\/][^\s]*?[\\/]outputs[\\/][\w\-.]+\.(?:svg|png|jpg|jpeg|gif|wav|mp3)"
    r"|outputs[\\/][\w\-.]+\.(?:svg|png|jpg|jpeg|gif|wav|mp3))",
    re.IGNORECASE,
)
BASE_DIR = Path(__file__).parent


def _extract_attachable_files(text: str) -> list[Path]:
    """Find file paths mentioned in the agent's answer that actually exist on disk."""
    found = []
    for match in _FILE_PATH_PATTERN.finditer(text):
        raw_path = match.group(1)
        # Absolute path (starts with drive letter) — use directly
        if re.match(r"^[A-Za-z]:[\\/]", raw_path):
            full_path = Path(raw_path)
        else:
            # Relative path like "outputs/diagram.svg" — resolve against BASE_DIR
            full_path = BASE_DIR / raw_path.replace("\\", "/")
        if full_path.exists() and full_path not in found:
            found.append(full_path)
    return found


def _transcribe_audio(audio_path: Path) -> str:
    """Transcribe audio using faster-whisper. Lazy import so bot starts without it."""
    try:
        from tools.stt import transcribe
        return transcribe(audio_path)
    except RuntimeError as e:
        raise RuntimeError(str(e))


@client.event
async def on_ready():
    print(f"[discord] Logged in as {client.user}")
    print(f"[discord] Listening for messages from user ID: {ALLOWED_USER_ID}")

    # Set up the proactive scheduler now that we have a running event loop
    # and can DM the allowed user
    global agent_scheduler
    loop = asyncio.get_running_loop()

    def send_to_discord(message: str) -> None:
        """
        Called from the scheduler's background thread — must hop back onto
        Discord's event loop safely using run_coroutine_threadsafe.
        """
        async def _send():
            try:
                user = await client.fetch_user(ALLOWED_USER_ID)
                dm = await user.create_dm()
                for chunk in _split_message(message):
                    await dm.send(chunk)
            except Exception as e:
                logger.error(f"[scheduler] Failed to send scheduled message: {e}")

        asyncio.run_coroutine_threadsafe(_send(), loop)

    try:
        agent_scheduler = AgentScheduler(agent=agent, send_callback=send_to_discord)
        agent_scheduler.start()
        agent_scheduler.load_saved_tasks()
        set_scheduler(agent_scheduler)
        print("[scheduler] Ready — agent can now schedule proactive tasks")
    except RuntimeError as e:
        print(f"[scheduler] Not available: {e}")

    print("[discord] Bot ready. (Ctrl+C to stop)")


@client.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages
    if message.author == client.user:
        return

    # Only respond to the allowed user
    if message.author.id != ALLOWED_USER_ID:
        return

    # client.user is None until login completes — should never happen inside
    # on_message (which only fires after on_ready), but guard for Pylance/safety
    bot_user = client.user
    if bot_user is None:
        return

    is_dm       = isinstance(message.channel, discord.DMChannel)
    is_mention  = bot_user in message.mentions

    # Respond to DMs always, or to @mentions in servers
    if not is_dm and not is_mention:
        return

    user_text = message.content
    # Strip the @mention text if present (e.g. "<@123456> check my inbox")
    if is_mention:
        user_text = user_text.replace(f"<@{bot_user.id}>", "").strip()

    # ── Voice message handling ───────────────────────────────────────────────
    # Discord voice messages are .ogg attachments (also catches .wav, .mp3).
    # If the message has a voice attachment, transcribe it with Whisper and
    # use that as the user's input — optionally combined with any typed text.
    audio_exts = {".ogg", ".wav", ".mp3", ".m4a", ".webm", ".mp4"}
    voice_attachments = [
        a for a in message.attachments
        if Path(a.filename).suffix.lower() in audio_exts
    ]

    if voice_attachments:
        attachment = voice_attachments[0]
        outputs_dir = BASE_DIR / "outputs"
        outputs_dir.mkdir(exist_ok=True)
        suffix = Path(attachment.filename).suffix.lower() or ".ogg"
        tmp_path = outputs_dir / f"voice_in_{attachment.id}{suffix}"

        logger.info(f"[stt] Saving voice attachment to: {tmp_path}")
        logger.info(f"[stt] Attachment: id={attachment.id} filename={attachment.filename} size={attachment.size}")

        try:
            await attachment.save(fp=str(tmp_path))

            if not tmp_path.exists():
                raise RuntimeError(f"File not saved — path does not exist after save(): {tmp_path}")

            logger.info(f"[stt] Saved OK ({tmp_path.stat().st_size} bytes), transcribing...")

            audio_path_str = str(tmp_path)
            loop = asyncio.get_running_loop()
            transcript = await loop.run_in_executor(
                None, lambda p=audio_path_str: _transcribe_audio(Path(p))
            )
            logger.info(f"[stt] Transcript: {transcript}")

            if user_text:
                user_text = f"{user_text} [voice: {transcript}]"
            else:
                user_text = transcript

        except Exception as e:
            logger.error(f"[stt] Transcription failed: {e}", exc_info=True)
            await message.channel.send(
                f"Voice transcription failed: {e}"
            )
            return
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    if not user_text:
        return

    logger.info(f"You: {user_text}")

    channel_id = message.channel.id
    history = _conversation_histories.get(channel_id, [])

    async with message.channel.typing():
        try:
            loop = asyncio.get_running_loop()
            answer = await loop.run_in_executor(
                None, lambda: agent.run(user_text, verbose=True, conversation_history=history)
            )
        except Exception as e:
            answer = f"Something went wrong: {e}"
            logger.error(f"Agent error: {e}")

    # Update conversation history with this exchange
    history = history + [
        {"role": "user",      "content": user_text},
        {"role": "assistant", "content": answer},
    ]
    _conversation_histories[channel_id] = history[-HISTORY_MAX_MESSAGES:]

    logger.info(f"Agent: {answer}")

    # If the agent's answer references a generated file (diagram, etc.),
    # attach it as a real Discord file/image instead of just printing the path.
    attachable: list[Path] = _extract_attachable_files(answer)

    chunks = _split_message(answer)
    for i, chunk in enumerate(chunks):
        is_last_chunk = (i == len(chunks) - 1)
        if is_last_chunk and attachable:
            # discord.File objects are single-use — must create fresh ones per send
            files = [discord.File(str(p), filename=p.name) for p in attachable]
            await message.channel.send(chunk, files=files)
        else:
            await message.channel.send(chunk)


def main():
    # BOT_TOKEN is guaranteed non-None here (checked at module load with
    # raise SystemExit above) — assert re-narrows it for Pylance inside this
    # function scope, since static analysis doesn't track the earlier check
    # across the module/function boundary.
    assert BOT_TOKEN is not None
    client.run(BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

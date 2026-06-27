# Local AI Agent Framework

Minimal agent loop: Stepfun Step-3.5-Flash (primary) + Qwen3.5-122B (fallback),
both free via NVIDIA NIM. Markdown-based memory that persists across sessions.

## File structure

```
local-agent/
├── agent.md          # Agent persona and operating rules (edit freely)
├── skills.md         # Auto-generated tool list (run sync_skills.py to update)
├── memory.md         # Long-term memory — agent appends facts here
├── .env              # Your NVIDIA_API_KEY goes here
├── main.py           # CLI entry point
├── sync_skills.py    # Regenerates skills.md from core/tools.py
├── test_mock.py      # Tests the loop without needing an API key
├── requirements.txt
└── core/
    ├── llm_client.py     # NIM API client (Stepfun → Qwen fallback)
    ├── orchestrator.py   # ReAct loop (think → act → observe → repeat)
    └── tools.py          # Tool registry (add Gmail/Calendar/TTS here)
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Verify the loop works (no API key needed)
python test_mock.py

# 3. Add your NVIDIA API key to .env
#    Each model needs its OWN key from its page on build.nvidia.com:
#      STEPFUN_API_KEY → https://build.nvidia.com/stepfun-ai/step-3-5-flash
#      QWEN_API_KEY    → https://build.nvidia.com/qwen/qwen3-5-122b-a10b
#    !! NEVER paste keys into chat or commit .env to git !!

# 4. Run the agent (Stepfun by default)
python main.py

# 5. Or force Qwen3.5-122B
python main.py --qwen
```

## How memory works

Stepfun (and Qwen) are stateless API models — they don't remember previous
conversations natively. This is solved at the framework level:

- `memory.md` is loaded fresh into the system prompt on EVERY turn
- The agent can call `append_memory` to save facts permanently
- On the next session, those facts are already in the prompt before the
  first message

So "no model memory" is not a real limitation here — the framework IS the memory.

## Adding tools (next phase: Gmail, Calendar, diagrams, TTS)

1. Write a function in `core/tools.py` (takes dict → returns str)
2. Register it in the `TOOLS` dict with description + args_schema
3. Run `python sync_skills.py`
4. The agent sees the new tool automatically — no other changes needed

## Text-to-speech (Piper — free, fully local)

1. Download `piper_windows_amd64.zip` from
   [github.com/rhasspy/piper/releases](https://github.com/rhasspy/piper/releases)
   → extract `piper.exe` to `local-agent/piper/piper.exe`
2. Download a voice model from
   [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US)
   (recommended: `en_US-lessac-medium`) — get both `.onnx` and `.onnx.json`
   files → place in `local-agent/piper/voices/`
3. Try it: "say hello out loud" or "send me a voice message explaining X"

Voice messages are saved as `.wav` to `outputs/` and automatically attached
as playable voice messages on Discord and Telegram — same auto-detection
pattern used for diagram images. No API cost, runs entirely on CPU, no
GPU required (leaves your RTX 5060 free for the LLM/other work).

The agent only generates voice when explicitly asked — it won't convert
every response to speech automatically (see `agent.md` Voice rules).

## Telegram bot (remote access from your phone)

The same agent — same memory.md, same tools — accessible from Telegram
instead of the terminal.

1. Message **@BotFather** on Telegram → `/newbot` → follow prompts → copy the token
2. Message **@userinfobot** on Telegram → copy your numeric user ID
3. Add both to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABC...
   TELEGRAM_ALLOWED_USER_ID=987654321
   ```
4. Install: `pip install python-telegram-bot --break-system-packages`
5. Run: `python telegram_bot.py`
6. Open your bot in Telegram and message it

The bot only responds to your `TELEGRAM_ALLOWED_USER_ID` — anyone else
messaging it is silently ignored. Thinking/step output is suppressed on
Telegram (only final answers are sent) — same `verbose=False` pattern
that any future interface (Discord, web UI) should use.

## Discord bot (alternative remote access)

Same agent, same memory, accessible via Discord — useful if Telegram is
unavailable in your region.

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. **Bot** tab → Reset Token → copy it
3. **Bot** tab → enable **Message Content Intent** under Privileged Gateway Intents
4. **OAuth2 → URL Generator** → scope `bot` → permissions: Send Messages, Read Message History → open the generated URL → add to your server
5. Get your Discord user ID: Settings → Advanced → enable Developer Mode → right-click your username → Copy User ID
6. Add both to `.env`:
   ```
   DISCORD_BOT_TOKEN=your_token
   DISCORD_ALLOWED_USER_ID=your_user_id
   ```
7. Install: `pip install discord.py --break-system-packages`
8. Run: `python discord_bot.py`
9. DM the bot directly, or @ mention it in a server channel

Only responds to `DISCORD_ALLOWED_USER_ID` — everyone else is ignored.
Long responses are automatically split into multiple messages (Discord
caps messages at 2000 characters).

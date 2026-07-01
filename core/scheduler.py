"""
Proactive scheduler for the local agent.

Runs background jobs (morning briefings, reminders, recurring checks) that
call agent.run() automatically and push the result to Discord/Telegram
without the user sending a message first.

Uses APScheduler (free, local, no external service needed).

Persistence:
  scheduled_tasks.json stores all tasks so they survive bot restarts.

Usage (from a bot):
    from core.scheduler import AgentScheduler

    scheduler = AgentScheduler(agent=agent, send_callback=my_send_fn)
    scheduler.start()
    scheduler.load_saved_tasks()

send_callback signature:
    def send_callback(message: str) -> None
    Called with the agent's response text — bot-specific implementation
    handles actually delivering it (Discord channel.send, Telegram bot.send_message).
"""
import json
import uuid
from pathlib import Path
from datetime import datetime

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

BASE_DIR   = Path(__file__).parent.parent
TASKS_PATH = BASE_DIR / "scheduled_tasks.json"


class AgentScheduler:
    def __init__(self, agent, send_callback):
        """
        Args:
          agent          the Orchestrator instance to run prompts through
          send_callback  function(message: str) -> None, delivers output
                         to the user (bot-specific: Discord/Telegram/etc.)
        """
        if not APSCHEDULER_AVAILABLE:
            raise RuntimeError(
                "apscheduler not installed.\n"
                "Run: pip install apscheduler --break-system-packages"
            )

        self.agent = agent
        self.send_callback = send_callback
        self.scheduler = BackgroundScheduler()
        self._tasks: dict[str, dict] = {}  # task_id -> task metadata

    def start(self) -> None:
        self.scheduler.start()
        print("[scheduler] Started")

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)

    # ── Task execution ───────────────────────────────────────────────────────

    def _run_task(self, task_id: str) -> None:
        """Executed by APScheduler when a job fires."""
        task = self._tasks.get(task_id)
        if not task:
            return

        prompt = task["prompt"]
        print(f"[scheduler] Running task '{task_id}': {prompt}")

        try:
            # Scheduled tasks run with no conversation history (fresh context)
            response = self.agent.run(prompt, verbose=True)
            # Prefix so the user knows this was unprompted
            message = f"🔔 Scheduled: {task.get('name', task_id)}\n\n{response}"
            self.send_callback(message)
        except Exception as e:
            print(f"[scheduler] Task '{task_id}' failed: {e}")
            try:
                self.send_callback(f"Scheduled task '{task.get('name', task_id)}' failed: {e}")
            except Exception:
                pass

    # ── Task management ──────────────────────────────────────────────────────

    def add_daily_task(self, name: str, prompt: str, hour: int, minute: int = 0) -> str:
        """Schedule a task to run every day at a specific time (24h format)."""
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id,
            "name": name,
            "prompt": prompt,
            "type": "daily",
            "hour": hour,
            "minute": minute,
        }
        self.scheduler.add_job(
            self._run_task,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=[task_id],
            id=task_id,
        )
        self._save_tasks()
        return task_id

    def add_interval_task(self, name: str, prompt: str, minutes: int) -> str:
        """Schedule a task to run every N minutes."""
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id,
            "name": name,
            "prompt": prompt,
            "type": "interval",
            "minutes": minutes,
        }
        self.scheduler.add_job(
            self._run_task,
            trigger=IntervalTrigger(minutes=minutes),
            args=[task_id],
            id=task_id,
        )
        self._save_tasks()
        return task_id

    def cancel_task(self, task_id: str) -> bool:
        if task_id not in self._tasks:
            return False
        try:
            self.scheduler.remove_job(task_id)
        except Exception:
            pass
        del self._tasks[task_id]
        self._save_tasks()
        return True

    def list_tasks(self) -> list[dict]:
        return list(self._tasks.values())

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_tasks(self) -> None:
        TASKS_PATH.write_text(json.dumps(list(self._tasks.values()), indent=2))

    def load_saved_tasks(self) -> int:
        """Load and re-register tasks from scheduled_tasks.json. Returns count loaded."""
        if not TASKS_PATH.exists():
            return 0

        try:
            saved = json.loads(TASKS_PATH.read_text())
        except Exception as e:
            print(f"[scheduler] Failed to load saved tasks: {e}")
            return 0

        count = 0
        for task in saved:
            task_id = task["id"]
            self._tasks[task_id] = task

            try:
                if task["type"] == "daily":
                    trigger = CronTrigger(hour=task["hour"], minute=task["minute"])
                elif task["type"] == "interval":
                    trigger = IntervalTrigger(minutes=task["minutes"])
                else:
                    continue

                self.scheduler.add_job(
                    self._run_task, trigger=trigger, args=[task_id], id=task_id
                )
                count += 1
            except Exception as e:
                print(f"[scheduler] Failed to restore task {task_id}: {e}")

        print(f"[scheduler] Restored {count} saved task(s)")
        return count

"""
Proactive scheduler for the local agent.

Supports three kinds of scheduled work:
  1. Recurring prompt tasks (daily/interval) — run agent.run(prompt) on a
     schedule, indefinitely, e.g. morning briefings.
  2. One-time prompt tasks — run agent.run(prompt) once at a specific
     datetime, then remove themselves, e.g. "remind me to check X at 5pm".
  3. One-time direct actions — execute a specific tool function directly
     (bypassing the LLM entirely) at a specific datetime, then remove
     themselves. Used for "send this email at 3pm" — the content is
     already confirmed, so sending later should be deterministic, not
     re-reasoned by the LLM with no memory of the original conversation.

Uses APScheduler (free, local, no external service needed).

Persistence:
  scheduled_tasks.json stores all tasks so they survive bot restarts.

send_callback signature:
    def send_callback(message: str) -> None
    Called with output text — bot-specific implementation handles delivery
    (Discord channel.send, Telegram bot.send_message).
"""
import json
import uuid
from pathlib import Path
from datetime import datetime

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.date import DateTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

BASE_DIR   = Path(__file__).parent.parent
TASKS_PATH = BASE_DIR / "scheduled_tasks.json"

# Registry of direct-action executors — populated by tools that need
# deterministic scheduled execution (e.g. tools/gmail.py registers "send_email")
_direct_action_registry: dict[str, callable] = {}


def register_direct_action(action_type: str, func: callable) -> None:
    """
    Register a function to handle a specific direct-action type.
    func signature: func(action_data: dict) -> str (result message)
    """
    _direct_action_registry[action_type] = func


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

        kind = task.get("kind", "prompt")  # default for older saved tasks
        name = task.get("name", task_id)

        try:
            if kind == "direct_action":
                action_type = task["action_type"]
                action_data = task["action_data"]
                handler = _direct_action_registry.get(action_type)

                if not handler:
                    raise RuntimeError(f"No handler registered for action type '{action_type}'")

                print(f"[scheduler] Running direct action '{task_id}': {action_type}")
                result = handler(action_data)
                message = f"⏰ Scheduled action completed: {name}\n\n{result}"

            else:
                # Standard prompt-based task — runs through the agent
                prompt = task["prompt"]
                print(f"[scheduler] Running task '{task_id}': {prompt}")
                response = self.agent.run(prompt, verbose=True)
                message = f"🔔 Scheduled: {name}\n\n{response}"

            self.send_callback(message)

        except Exception as e:
            print(f"[scheduler] Task '{task_id}' failed: {e}")
            try:
                self.send_callback(f"Scheduled task '{name}' failed: {e}")
            except Exception:
                pass

        finally:
            # One-time tasks (one_time / direct_action) remove themselves
            # after firing — recurring tasks (daily / interval) persist
            if task.get("type") in ("one_time", "direct_action"):
                self._tasks.pop(task_id, None)
                self._save_tasks()

    # ── Task management: recurring ──────────────────────────────────────────

    def add_daily_task(self, name: str, prompt: str, hour: int, minute: int = 0) -> str:
        """Schedule a task to run every day at a specific time (24h format)."""
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id, "name": name, "prompt": prompt,
            "kind": "prompt", "type": "daily", "hour": hour, "minute": minute,
        }
        self.scheduler.add_job(
            self._run_task, trigger=CronTrigger(hour=hour, minute=minute),
            args=[task_id], id=task_id,
        )
        self._save_tasks()
        return task_id

    def add_interval_task(self, name: str, prompt: str, minutes: int) -> str:
        """Schedule a task to run every N minutes."""
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id, "name": name, "prompt": prompt,
            "kind": "prompt", "type": "interval", "minutes": minutes,
        }
        self.scheduler.add_job(
            self._run_task, trigger=IntervalTrigger(minutes=minutes),
            args=[task_id], id=task_id,
        )
        self._save_tasks()
        return task_id

    # ── Task management: one-time ────────────────────────────────────────────

    def add_one_time_task(self, name: str, prompt: str, run_at: datetime) -> str:
        """
        Schedule a task to run the given prompt through the agent ONCE at a
        specific datetime, then remove itself. Use for things like "remind
        me to check the oven at 6pm" — the agent reasons about the prompt
        at that time.
        """
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id, "name": name, "prompt": prompt,
            "kind": "prompt", "type": "one_time",
            "run_at": run_at.isoformat(),
        }
        self.scheduler.add_job(
            self._run_task, trigger=DateTrigger(run_date=run_at),
            args=[task_id], id=task_id,
        )
        self._save_tasks()
        return task_id

    def add_direct_action_task(
        self, name: str, action_type: str, action_data: dict, run_at: datetime
    ) -> str:
        """
        Schedule a DETERMINISTIC action (not an agent prompt) to run once at
        a specific datetime. Use for "send this exact drafted email at 3pm" —
        the content is already confirmed, so execution should be direct and
        NOT re-reasoned by the LLM (which would have no memory of the
        original draft by the time it fires).
        """
        task_id = str(uuid.uuid4())[:8]
        self._tasks[task_id] = {
            "id": task_id, "name": name,
            "kind": "direct_action", "type": "direct_action",
            "action_type": action_type, "action_data": action_data,
            "run_at": run_at.isoformat(),
        }
        self.scheduler.add_job(
            self._run_task, trigger=DateTrigger(run_date=run_at),
            args=[task_id], id=task_id,
        )
        self._save_tasks()
        return task_id

    # ── Common management ─────────────────────────────────────────────────────

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
        now = datetime.now()

        for task in saved:
            task_id = task["id"]
            task_type = task.get("type")

            try:
                if task_type == "daily":
                    trigger = CronTrigger(hour=task["hour"], minute=task["minute"])
                elif task_type == "interval":
                    trigger = IntervalTrigger(minutes=task["minutes"])
                elif task_type in ("one_time", "direct_action"):
                    run_at = datetime.fromisoformat(task["run_at"])
                    if run_at <= now:
                        # Scheduled time already passed while bot was offline —
                        # skip silently rather than firing a stale action
                        print(f"[scheduler] Skipping past-due one-time task {task_id} (was due {run_at})")
                        continue
                    trigger = DateTrigger(run_date=run_at)
                else:
                    continue

                self._tasks[task_id] = task
                self.scheduler.add_job(
                    self._run_task, trigger=trigger, args=[task_id], id=task_id
                )
                count += 1
            except Exception as e:
                print(f"[scheduler] Failed to restore task {task_id}: {e}")

        # Persist with any skipped past-due tasks removed
        self._save_tasks()
        print(f"[scheduler] Restored {count} saved task(s)")
        return count

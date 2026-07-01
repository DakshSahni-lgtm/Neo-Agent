"""
Scheduler tool — lets the agent create, list, and cancel its own proactive
scheduled tasks (morning briefings, reminders, recurring checks).

This module doesn't own the scheduler itself — it holds a reference set by
the bot at startup (same set_scheduler() pattern as set_llm_client()).
"""

_scheduler = None  # set by the bot via set_scheduler()


def set_scheduler(scheduler) -> None:
    global _scheduler
    _scheduler = scheduler


def schedule_daily_task(args: dict) -> str:
    """
    Schedule a task to run automatically every day at a specific time.
    Args:
      name   (str) — short name for the task, e.g. "Morning briefing"
      prompt (str) — the instruction to run each time, e.g.
                     "Check my calendar for today and summarize my inbox"
      time   (str) — 24-hour time e.g. "08:00" or "17:30"
    """
    if not _scheduler:
        return "Error: scheduler not initialized"

    name   = (args.get("name") or "").strip()
    prompt = (args.get("prompt") or "").strip()
    time_s = (args.get("time") or "").strip()

    if not name:
        return "Error: 'name' is required"
    if not prompt:
        return "Error: 'prompt' is required"
    if not time_s:
        return "Error: 'time' is required (24-hour format, e.g. '08:00')"

    try:
        hour, minute = map(int, time_s.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        return f"Error: couldn't parse time '{time_s}' — use 24-hour format like '08:00' or '17:30'"

    task_id = _scheduler.add_daily_task(name, prompt, hour, minute)
    return (
        f"Scheduled daily task created!\n"
        f"Name: {name}\n"
        f"Runs at: {hour:02d}:{minute:02d} every day\n"
        f"Task ID: {task_id}\n"
        f"Prompt: {prompt}"
    )


def schedule_interval_task(args: dict) -> str:
    """
    Schedule a task to run automatically every N minutes.
    Args:
      name    (str) — short name for the task
      prompt  (str) — the instruction to run each time
      minutes (int) — how often to run, in minutes (minimum 15)
    """
    if not _scheduler:
        return "Error: scheduler not initialized"

    name    = (args.get("name") or "").strip()
    prompt  = (args.get("prompt") or "").strip()
    minutes = int(args.get("minutes", 0))

    if not name:
        return "Error: 'name' is required"
    if not prompt:
        return "Error: 'prompt' is required"
    if minutes < 15:
        return "Error: minimum interval is 15 minutes (to avoid excessive API usage)"

    task_id = _scheduler.add_interval_task(name, prompt, minutes)
    return (
        f"Scheduled recurring task created!\n"
        f"Name: {name}\n"
        f"Runs every: {minutes} minutes\n"
        f"Task ID: {task_id}\n"
        f"Prompt: {prompt}"
    )


def list_scheduled_tasks(args: dict) -> str:
    """List all currently scheduled tasks."""
    if not _scheduler:
        return "Error: scheduler not initialized"

    tasks = _scheduler.list_tasks()
    if not tasks:
        return "No scheduled tasks currently set up."

    lines = [f"Scheduled tasks ({len(tasks)}):\n"]
    for t in tasks:
        if t["type"] == "daily":
            timing = f"daily at {t['hour']:02d}:{t['minute']:02d}"
        elif t["type"] == "interval":
            timing = f"every {t['minutes']} minutes"
        else:
            timing = "unknown schedule"
        lines.append(f"• {t['name']} ({timing})\n  ID: {t['id']}\n  Prompt: {t['prompt']}")

    return "\n".join(lines)


def cancel_scheduled_task(args: dict) -> str:
    """
    Cancel a scheduled task by its ID.
    Args:
      id (str) — task ID from list_scheduled_tasks
    """
    if not _scheduler:
        return "Error: scheduler not initialized"

    task_id = (args.get("id") or "").strip()
    if not task_id:
        return "Error: 'id' is required — get it from list_scheduled_tasks"

    if _scheduler.cancel_task(task_id):
        return f"Task '{task_id}' cancelled successfully."
    return f"No task found with ID '{task_id}'. Use list_scheduled_tasks to see valid IDs."

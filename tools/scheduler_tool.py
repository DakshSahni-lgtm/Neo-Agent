"""
Scheduler tool — lets the agent create, list, and cancel its own proactive
and one-time scheduled tasks.

This module doesn't own the scheduler itself — it holds a reference set by
the bot at startup (same set_scheduler() pattern as set_llm_client()).
"""
from datetime import datetime, timedelta

_scheduler = None  # set by the bot via set_scheduler()


def set_scheduler(scheduler) -> None:
    global _scheduler
    _scheduler = scheduler

    # Register the deterministic email-send action so scheduled sends
    # don't need to go through the LLM again at execution time
    from core.scheduler import register_direct_action
    from tools.gmail import _scheduled_send_email_action
    register_direct_action("send_email", _scheduled_send_email_action)


def _parse_time_today_or_tomorrow(time_s: str) -> datetime | None:
    """
    Parse a time like '15:00' or '3pm' into a datetime — today if that time
    hasn't passed yet, otherwise tomorrow. Also accepts full datetimes like
    '2026-07-01 15:00' or 'tomorrow 3pm'.
    """
    time_s = time_s.strip()
    now = datetime.now()

    # Try full datetime formats first
    full_formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M",
    ]
    for fmt in full_formats:
        try:
            return datetime.strptime(time_s, fmt)
        except ValueError:
            continue

    # "tomorrow 3pm" / "tomorrow 15:00"
    if time_s.lower().startswith("tomorrow"):
        rest = time_s[8:].strip()
        t = _parse_bare_time(rest)
        if t:
            base = now + timedelta(days=1)
            return base.replace(hour=t[0], minute=t[1], second=0, microsecond=0)

    # Bare time — "15:00", "3pm", "3:30 pm"
    t = _parse_bare_time(time_s)
    if t:
        candidate = now.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)  # already passed today — schedule for tomorrow
        return candidate

    return None


def _parse_bare_time(time_s: str) -> tuple[int, int] | None:
    """Parse just a time-of-day string into (hour, minute), 24h format."""
    import re
    time_s = time_s.strip().lower()

    # 24-hour "15:00" or "15:30"
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return (h, mi)

    # "3pm", "3:30pm", "3 pm", "3:30 pm"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", time_s)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3)
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return (h, mi)

    return None


def schedule_one_time_task(args: dict) -> str:
    """
    Schedule a task to run through the agent ONCE at a specific time, then
    it removes itself. Use for reminders/checks that happen only once —
    e.g. "remind me to check the oven at 6pm", "check my calendar tomorrow at 9am".
    Args:
      name   (str) — short name for the task
      prompt (str) — self-contained instruction to run at that time (no
                     conversation history will be available — make it clear)
      time   (str) — when to run: '15:00', '3pm', 'tomorrow 9am', or a full
                     datetime like '2026-07-01 15:00'
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
        return "Error: 'time' is required, e.g. '15:00', '3pm', or 'tomorrow 9am'"

    run_at = _parse_time_today_or_tomorrow(time_s)
    if not run_at:
        return f"Error: couldn't parse time '{time_s}' — try '15:00', '3pm', or 'tomorrow 9am'"

    task_id = _scheduler.add_one_time_task(name, prompt, run_at)
    return (
        f"One-time task scheduled!\n"
        f"Name: {name}\n"
        f"Runs at: {run_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"Task ID: {task_id}\n"
        f"Prompt: {prompt}"
    )


def schedule_email_send(args: dict) -> str:
    """
    Schedule an ALREADY-CONFIRMED email draft to be sent at a specific time.
    The email is sent EXACTLY as given here — no re-drafting happens at send
    time. Always show the draft and get Daksh's explicit confirmation of both
    content AND timing before calling this.
    Args:
      to      (str) — recipient email address
      subject (str) — email subject
      body    (str) — full email body
      time    (str) — when to send: '15:00', '3pm', 'tomorrow 9am', or a
                      full datetime like '2026-07-01 15:00'
      reply_to_id (str, optional) — original message ID to thread correctly
    """
    if not _scheduler:
        return "Error: scheduler not initialized"

    to      = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body    = (args.get("body") or "").strip()
    time_s  = (args.get("time") or "").strip()
    reply_to_id = (args.get("reply_to_id") or "").strip()

    if not to or not subject or not body:
        return "Error: 'to', 'subject', and 'body' are all required"
    if not time_s:
        return "Error: 'time' is required, e.g. '15:00', '3pm', or 'tomorrow 9am'"

    run_at = _parse_time_today_or_tomorrow(time_s)
    if not run_at:
        return f"Error: couldn't parse time '{time_s}' — try '15:00', '3pm', or 'tomorrow 9am'"

    action_data = {"to": to, "subject": subject, "body": body}
    if reply_to_id:
        action_data["reply_to_id"] = reply_to_id

    task_id = _scheduler.add_direct_action_task(
        name=f"Send email to {to}",
        action_type="send_email",
        action_data=action_data,
        run_at=run_at,
    )

    return (
        f"Email scheduled to send at {run_at.strftime('%Y-%m-%d %H:%M')}!\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"Task ID: {task_id}\n\n"
        f"It will be sent automatically at that time — no further confirmation needed."
    )


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
        t_type = t.get("type", "unknown")

        if t_type == "daily":
            timing = f"daily at {t['hour']:02d}:{t['minute']:02d}"
            detail = f"Prompt: {t.get('prompt', '')}"
        elif t_type == "interval":
            timing = f"every {t['minutes']} minutes"
            detail = f"Prompt: {t.get('prompt', '')}"
        elif t_type == "one_time":
            run_at = t.get("run_at", "?")[:16].replace("T", " ")
            timing = f"once at {run_at}"
            detail = f"Prompt: {t.get('prompt', '')}"
        elif t_type == "direct_action":
            run_at = t.get("run_at", "?")[:16].replace("T", " ")
            timing = f"once at {run_at}"
            action_data = t.get("action_data", {})
            detail = f"Action: send email to {action_data.get('to', '?')} — {action_data.get('subject', '?')}"
        else:
            timing = "unknown schedule"
            detail = ""

        lines.append(f"• {t['name']} ({timing})\n  ID: {t['id']}\n  {detail}")

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

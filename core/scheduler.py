"""
core/scheduler.py
Production background job automation using APScheduler (Asia/Kolkata timezone).
Features:
  - Outbound Dispatcher: Scheduled Monday-Friday from 09:00 to 17:00 IST.
  - Nightly Hot Backup: Daily at 02:00 AM IST calling backup_database_online().
  - max_instances=1 to prevent duplicate job executions or database locking contention.
"""

import sys
from pathlib import Path
from typing import Optional
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.db_manager import backup_database_online
from core.sender_engine import process_outreach_queue

IST = pytz.timezone("Asia/Kolkata")
_scheduler: Optional[BackgroundScheduler] = None


def scheduled_outreach_job() -> None:
    """Dispatches a batch of cold outreach emails during active business hours."""
    print("[scheduler] Starting scheduled business-hours outreach dispatch...")
    try:
        result = process_outreach_queue(max_batch=5, pace_sleep=True)
        print(f"[scheduler] Outreach cycle finished: {result}")
    except Exception as exc:
        print(f"[scheduler] Outreach execution failed: {exc}")


def scheduled_backup_job() -> None:
    """Executes transaction-safe online SQLite backup to data/backups/."""
    print("[scheduler] Starting scheduled nightly SQLite online backup...")
    try:
        path = backup_database_online()
        print(f"[scheduler] Backup created successfully: {path}")
    except Exception as exc:
        print(f"[scheduler] Backup job failed: {exc}")


# Alias for backward compatibility
backup_sqlite_database = backup_database_online


def init_scheduler(app=None) -> BackgroundScheduler:
    """
    Initializes and starts the APScheduler background daemon.
    Configured with Asia/Kolkata timezone and max_instances=1.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=IST)

    # Cron 1: Outbound Dispatcher Monday-Friday 09:00 to 17:00 IST
    _scheduler.add_job(
        func=scheduled_outreach_job,
        trigger="cron",
        day_of_week="mon-fri",
        hour="9-17",
        minute=0,
        id="business_hours_outreach",
        name="Business-Hours Cold Outreach Dispatch (Mon-Fri 09:00-17:00 IST)",
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )

    # Cron 2: Nightly Hot SQLite Backup daily at 02:00 AM IST
    _scheduler.add_job(
        func=scheduled_backup_job,
        trigger="cron",
        hour=2,
        minute=0,
        id="nightly_sqlite_backup",
        name="Nightly Online SQLite Backup (02:00 IST)",
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )

    _scheduler.start()
    print("[scheduler] APScheduler active — 2 production cron jobs registered (Asia/Kolkata).")
    return _scheduler


def shutdown_scheduler() -> None:
    """Gracefully stops the scheduler thread pool."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[scheduler] APScheduler stopped.")


if __name__ == "__main__":
    s = init_scheduler()
    print("Scheduler running jobs:", [j.id for j in s.get_jobs()])
    shutdown_scheduler()

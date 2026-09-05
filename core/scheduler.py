"""
core/scheduler.py
Production background job manager using APScheduler (Asia/Kolkata timezone).
Manages business-hours cold email dispatching and nightly online SQLite backups.
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import DB_PATH
from core.sender_engine import process_outreach_queue

IST = pytz.timezone("Asia/Kolkata")
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

_scheduler: Optional[BackgroundScheduler] = None


def backup_sqlite_database() -> str:
    """
    Executes a non-blocking, transaction-safe online SQLite backup
    using sqlite3.Connection.backup() API to data/backups/.
    Retains rolling backups and logs execution.
    """
    print("[scheduler] Starting automated online SQLite database backup...")
    try:
        timestamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"outbound_backup_{timestamp}.db"
        target_path = BACKUP_DIR / backup_filename

        # Source connection in read-only / shared WAL mode
        source_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30.0)
        target_conn = sqlite3.connect(str(target_path))

        with source_conn:
            with target_conn:
                source_conn.backup(target_conn, pages=100, sleep=0.01)

        source_conn.close()
        target_conn.close()

        print(f"[scheduler] Backup successfully written to {target_path}")

        # Rolling retention: keep newest 7 backups, purge older ones
        existing_backups = sorted(BACKUP_DIR.glob("outbound_backup_*.db"))
        if len(existing_backups) > 7:
            for old_backup in existing_backups[:-7]:
                try:
                    old_backup.unlink()
                    print(f"[scheduler] Pruned old backup: {old_backup.name}")
                except Exception as e:
                    print(f"[scheduler] Failed to prune {old_backup.name}: {e}")

        return str(target_path)

    except Exception as exc:
        print(f"[scheduler] SQLite backup failed: {exc}")
        return ""


def run_scheduled_dispatch() -> None:
    """
    Triggers batch outreach dispatching during business hours.
    """
    print("[scheduler] Running business-hours outreach dispatch sweep...")
    try:
        result = process_outreach_queue(max_batch=5, pace_sleep=True)
        print(f"[scheduler] Dispatch sweep complete: {result}")
    except Exception as exc:
        print(f"[scheduler] Scheduled dispatch error: {exc}")


def init_scheduler(app=None) -> BackgroundScheduler:
    """
    Configures and starts the background job scheduler with timezone awareness.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone=IST)

    # Job 1: Hourly business-hours outreach dispatch (09:00 - 17:00 IST)
    _scheduler.add_job(
        func=run_scheduled_dispatch,
        trigger="cron",
        hour="9-17",
        minute=0,
        id="business_hours_outreach",
        name="Business Hours Outreach Queue (09:00-17:00 IST)",
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )

    # Job 2: Nightly online SQLite database backup (02:00 IST)
    _scheduler.add_job(
        func=backup_sqlite_database,
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
    print("[scheduler] APScheduler initialized and running with 2 core cron jobs (IST).")
    return _scheduler


def shutdown_scheduler() -> None:
    """Gracefully terminates background scheduler threads."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("[scheduler] APScheduler stopped.")


if __name__ == "__main__":
    b_path = backup_sqlite_database()
    print(f"Test backup generated: {b_path}")

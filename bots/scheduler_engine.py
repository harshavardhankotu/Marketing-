"""
Scheduler engine — APScheduler configured for Asia/Kolkata.

Scheduled jobs:
    1. Content sweep (morning 08:15 IST)     — source + compose new campaigns.
    2. Content sweep (evening 18:30 IST)      — evening publishing batch.
    3. Auto-publish sweep (every 5 minutes)   — publish due pending campaigns.
    4. Zero-cost hot database backup (daily 02:00 IST) using the native
       SQLite ``source_conn.backup(target_conn)`` API for transaction-safe
       online backups, deleting backups older than 7 days.
    5. Video trash collector (daily 03:00 IST) — delete rendered .mp4/.png
       under ``static/campaigns/`` older than 48 hours to keep disk usage flat.

The scheduler uses a thread-local flag so it is only ever started once per
process (the Flask reloader can otherwise double-start it).
"""

import os
import sys
import glob
import time
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH, BACKUP_DIR, SCHEDULER_TZ  # noqa: E402

_STARTED = False


# ─────────────────────────────────────────────────────────────────────────────
# JOB IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────
def content_sweep(sector=None):
    """
    Source products for a vertical, generate copy + video assets, and persist
    them as pending campaigns. Falls back gracefully when APIs are absent.
    """
    from scrapers.product_scraper import fetch_active_campaigns
    from generators.ai_copywriter import generate_multilingual_copy
    from generators.video_script_engine import render_video_clip, generate_video_scripts
    from db_manager import save_campaign

    if sector is None:
        sectors = ["electronics", "home_kitchen"]
    else:
        sectors = [sector]

    total_saved = 0
    for sec in sectors:
        print(f"[SCHEDULER] Content sweep -> sector: {sec}")
        try:
            products = fetch_active_campaigns(sec)
        except Exception as exc:
            print(f"[SCHEDULER] Sourcing failed for {sec}: {exc}")
            continue

        for idx, product in enumerate(products):
            try:
                product["sector"] = sec
                copies = generate_multilingual_copy(product)
                product["caption"] = copies.get("en", "")
                product["copy"] = copies

                script = generate_video_scripts(product)
                product["graphic_path"] = render_video_clip(product, script)
                product["commission"] = product.get("commission", 0.03)

                save_campaign(product, sector=sec)
                total_saved += 1
            except Exception as exc:
                print(f"[SCHEDULER] Failed to compose product {idx} in {sec}: {exc}")

    print(f"[SCHEDULER] Content sweep complete. {total_saved} campaigns persisted.")
    return total_saved


def auto_publish_sweep():
    """
    Publish every pending campaign whose publish_at timestamp has arrived.
    """
    from db_manager import _connection

    conn = _connection()
    due = []
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id FROM campaigns WHERE status = 'pending_approval' AND publish_at <= ?",
            (now,),
        ).fetchall()
        due = [r[0] for r in rows]
    finally:
        conn.close()

    from distributor import distribute_campaign

    published = 0
    for campaign_id in due:
        try:
            if distribute_campaign(campaign_id):
                published += 1
        except Exception as exc:
            print(f"[SCHEDULER] Auto-publish failed for campaign {campaign_id}: {exc}")

    print(f"[SCHEDULER] Auto-publish sweep published {published}/{len(due)} due campaigns.")
    return published


def hot_backup():
    """
    Zero-cost hot backup using SQLite's online backup API.
    Backups older than 7 days are pruned.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DB_PATH):
        print("[SCHEDULER] No database yet — skipping backup.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_path = os.path.join(BACKUP_DIR, f"campaigns_{timestamp}.db")

    source_conn = sqlite3.connect(DB_PATH, timeout=30.0)
    target_conn = None
    try:
        target_conn = sqlite3.connect(target_path, timeout=30.0)
        source_conn.backup(target_conn)
        print(f"[SCHEDULER] Hot backup written: {target_path}")
    except sqlite3.Error as exc:
        print(f"[SCHEDULER] Backup failed: {exc}")
        return None
    finally:
        if target_conn:
            target_conn.close()
        source_conn.close()

    _prune_old_backups(days=7)
    return target_path


def _prune_old_backups(days=7):
    cutoff = time_now = datetime.now() - timedelta(days=days)
    removed = 0
    for backup_file in glob.glob(os.path.join(BACKUP_DIR, "campaigns_*.db")):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(backup_file))
            if mtime < cutoff:
                os.remove(backup_file)
                removed += 1
        except OSError:
            pass
    print(f"[SCHEDULER] Pruned {removed} backup(s) older than {days} days.")


def video_trash_collector(older_than_hours=48):
    """
    Frugal disk maintenance: delete rendered campaign media (.mp4/.png) under
    ``static/campaigns/`` that is older than ``older_than_hours`` so the disk
    never fills up with stale reels/posters.
    """
    from config import CAMPAIGN_STATIC_DIR

    if not os.path.isdir(CAMPAIGN_STATIC_DIR):
        print("[SCHEDULER] No campaign media directory yet — skipping trash collector.")
        return 0

    cutoff = time.time() - (older_than_hours * 3600)
    removed = 0
    for filename in os.listdir(CAMPAIGN_STATIC_DIR):
        if not filename.lower().endswith((".mp4", ".png")):
            continue
        file_path = os.path.join(CAMPAIGN_STATIC_DIR, filename)
        try:
            if os.path.getmtime(file_path) < cutoff:
                os.remove(file_path)
                removed += 1
        except OSError:
            pass

    print(f"[SCHEDULER] Trash collector removed {removed} media file(s) older than {older_than_hours}h.")
    return removed


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────
def start(app=None):
    """Register all scheduled jobs (idempotent per process)."""
    global _STARTED
    if _STARTED:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError as exc:
        print(f"[SCHEDULER] APScheduler not installed ({exc}). Scheduling disabled.")
        return

    scheduler = BackgroundScheduler(timezone=SCHEDULER_TZ, daemon=True)
    scheduler.add_job(
        content_sweep,
        CronTrigger(hour=8, minute=15, timezone=SCHEDULER_TZ),
        id="content_sweep_morning",
        name="Morning content sweep",
        replace_existing=True,
    )
    scheduler.add_job(
        content_sweep,
        CronTrigger(hour=18, minute=30, timezone=SCHEDULER_TZ),
        id="content_sweep_evening",
        name="Evening content sweep",
        replace_existing=True,
    )
    scheduler.add_job(
        auto_publish_sweep,
        IntervalTrigger(minutes=5),
        id="auto_publish_sweep",
        name="Auto-publish sweep",
        replace_existing=True,
    )
    scheduler.add_job(
        hot_backup,
        CronTrigger(hour=2, minute=0, timezone=SCHEDULER_TZ),
        id="hot_db_backup",
        name="Hot database backup",
        replace_existing=True,
    )
    scheduler.add_job(
        video_trash_collector,
        CronTrigger(hour=3, minute=0, timezone=SCHEDULER_TZ),
        id="video_trash_collector",
        name="Video trash collector (48h media cleanup)",
        replace_existing=True,
    )

    scheduler.start()
    _STARTED = True
    print("[SCHEDULER] Scheduler started (Asia/Kolkata).")
    _sync_job_table(scheduler)


def _sync_job_table(scheduler):
    """Mirror scheduler jobs into the scheduler_jobs table for the dashboard."""
    from db_manager import _connection

    conn = _connection()
    try:
        for job in scheduler.get_jobs():
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduler_jobs
                    (job_id, job_label, job_type, schedule_expr, enabled, next_run_at, last_run_at, run_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    job.id,
                    job.name,
                    str(job.trigger),
                    str(job.trigger),
                    1,
                    str(job.next_run_time) if job.next_run_time else None,
                    str(job.next_run_time) if job.next_run_time else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_status():
    """Return scheduler + job status for the dashboard / health checks."""
    jobs = []
    from db_manager import _connection

    conn = _connection()
    try:
        rows = conn.execute("SELECT * FROM scheduler_jobs ORDER BY job_id").fetchall()
        jobs = [dict(r) for r in rows]
    finally:
        conn.close()

    return {
        "scheduler_running": _STARTED,
        "jobs": jobs,
    }


def trigger_now(job_id):
    """Manually trigger a registered job by id (admin API)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    jobs = {job.id: job for job in _get_scheduler().get_jobs()} if _STARTED else {}
    if job_id not in jobs:
        return {"status": "error", "message": f"Job '{job_id}' not registered."}
    try:
        jobs[job_id].func()
        return {"status": "success", "message": f"Job '{job_id}' executed."}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _get_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    return BackgroundScheduler()


def set_job_enabled(job_id, enabled):
    """Enable or disable a scheduler job."""
    from db_manager import _connection

    conn = _connection()
    try:
        conn.execute("UPDATE scheduler_jobs SET enabled = ? WHERE job_id = ?", (1 if enabled else 0, job_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "success", "job_id": job_id, "enabled": enabled}

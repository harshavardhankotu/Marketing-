"""
Scheduler Engine
────────────────────────────────────────────────────────────────
APScheduler-based autonomous job runner for the affiliate-commerce pipeline.

Jobs (all IST timezone):
  pipeline_morning_daily  → 08:15 IST  → ENABLED
  pipeline_evening_daily  → 18:30 IST  → DISABLED (manual activation)
  retarget_1000           → 10:00 IST  → ENABLED
  retarget_1400           → 14:00 IST  → ENABLED
  retarget_1800           → 18:00 IST  → ENABLED
  retarget_2100           → 21:00 IST  → ENABLED

Execution model:
  Jobs call the same internal service functions used by Flask API routes.
  No self-HTTP calls. No code duplication.

Lock model:
  SQLite scheduler_locks table provides belt-and-suspenders protection
  on top of APScheduler's max_instances=1.
"""

import sqlite3
import os
import json
import threading
from datetime import datetime, timedelta

import pytz

from config import DB_PATH

IST = pytz.timezone("Asia/Kolkata")

# ─── job registry ─────────────────────────────────────────────────────────────
# Each entry defines the default config for a job.
JOB_REGISTRY = [
    {
        "job_id":      "pipeline_morning_daily",
        "job_label":   "Morning Pipeline (all sectors)",
        "job_type":    "pipeline",
        "schedule_expr": "daily 08:15 IST",
        "hour": 8, "minute": 15,
        "enabled":     True,
    },
    {
        "job_id":      "pipeline_evening_daily",
        "job_label":   "Evening Pipeline (all sectors)",
        "job_type":    "pipeline",
        "schedule_expr": "daily 18:30 IST",
        "hour": 18, "minute": 30,
        "enabled":     False,   # disabled by default; enable via UI/API
    },
    {
        "job_id":      "retarget_1000",
        "job_label":   "Retargeting Sweep 10:00",
        "job_type":    "retargeting",
        "schedule_expr": "daily 10:00 IST",
        "hour": 10, "minute": 0,
        "enabled":     True,
    },
    {
        "job_id":      "retarget_1400",
        "job_label":   "Retargeting Sweep 14:00",
        "job_type":    "retargeting",
        "schedule_expr": "daily 14:00 IST",
        "hour": 14, "minute": 0,
        "enabled":     True,
    },
    {
        "job_id":      "retarget_1800",
        "job_label":   "Retargeting Sweep 18:00",
        "job_type":    "retargeting",
        "schedule_expr": "daily 18:00 IST",
        "hour": 18, "minute": 0,
        "enabled":     True,
    },
    {
        "job_id":      "retarget_2100",
        "job_label":   "Retargeting Sweep 21:00",
        "job_type":    "retargeting",
        "schedule_expr": "daily 21:00 IST",
        "hour": 21, "minute": 0,
        "enabled":     True,
    },
]

_scheduler = None          # module-level APScheduler instance
_flask_app  = None         # kept for app-context usage if needed

# ─── schema ───────────────────────────────────────────────────────────────────

def _setup_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS scheduler_jobs (
        job_id        TEXT PRIMARY KEY,
        job_label     TEXT,
        job_type      TEXT,
        schedule_expr TEXT,
        enabled       INTEGER DEFAULT 1,
        next_run_at   TIMESTAMP,
        last_run_at   TIMESTAMP,
        last_status   TEXT,
        last_error    TEXT,
        run_count     INTEGER DEFAULT 0
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS scheduler_runs (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id         TEXT NOT NULL,
        started_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at    TIMESTAMP,
        status         TEXT,
        result_summary TEXT,
        error_message  TEXT,
        duration_secs  REAL,
        FOREIGN KEY (job_id) REFERENCES scheduler_jobs (job_id)
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS scheduler_locks (
        job_id     TEXT PRIMARY KEY,
        locked_at  TIMESTAMP,
        locked_by  TEXT
    )""")

    conn.commit()
    conn.close()


def _seed_job_rows():
    """Insert default rows for any job not yet in the DB (never overwrite user changes)."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    for job in JOB_REGISTRY:
        c.execute("""
        INSERT OR IGNORE INTO scheduler_jobs
            (job_id, job_label, job_type, schedule_expr, enabled)
        VALUES (?, ?, ?, ?, ?)
        """, (
            job["job_id"], job["job_label"], job["job_type"],
            job["schedule_expr"], int(job["enabled"])
        ))
    conn.commit()
    conn.close()


# ─── lock helpers ─────────────────────────────────────────────────────────────

_LOCK_TIMEOUT_MINUTES = 30   # consider a lock stale after 30 min


def _acquire_lock(job_id: str) -> bool:
    """Try to acquire a SQLite lock.  Returns True if acquired, False if busy."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    try:
        # Clear stale locks first
        stale_cutoff = (datetime.utcnow() - timedelta(minutes=_LOCK_TIMEOUT_MINUTES)).isoformat()
        c.execute("DELETE FROM scheduler_locks WHERE job_id = ? AND locked_at < ?",
                  (job_id, stale_cutoff))

        c.execute("""
        INSERT INTO scheduler_locks (job_id, locked_at, locked_by)
        VALUES (?, ?, ?)
        """, (job_id, datetime.utcnow().isoformat(), "scheduler_engine"))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False   # another instance already holds the lock
    finally:
        conn.close()


def _release_lock(job_id: str):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("DELETE FROM scheduler_locks WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()


# ─── run recording ────────────────────────────────────────────────────────────

def _start_run(job_id: str) -> int:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("""
    INSERT INTO scheduler_runs (job_id, started_at, status)
    VALUES (?, ?, 'running')
    """, (job_id, datetime.utcnow().isoformat()))
    run_id = c.lastrowid
    c.execute("""
    UPDATE scheduler_jobs
    SET last_run_at = ?, last_status = 'running'
    WHERE job_id = ?
    """, (datetime.utcnow().isoformat(), job_id))
    conn.commit()
    conn.close()
    return run_id


def _finish_run(job_id: str, run_id: int, status: str,
                summary: str = "", error: str = "", duration: float = 0.0):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("""
    UPDATE scheduler_runs
    SET finished_at = ?, status = ?, result_summary = ?, error_message = ?, duration_secs = ?
    WHERE id = ?
    """, (datetime.utcnow().isoformat(), status, summary, error, duration, run_id))
    c.execute("""
    UPDATE scheduler_jobs
    SET last_run_at = ?, last_status = ?, last_error = ?,
        run_count = run_count + 1
    WHERE job_id = ?
    """, (datetime.utcnow().isoformat(), status, error, job_id))
    conn.commit()
    conn.close()


def _update_next_run(job_id: str, next_run_dt):
    """Cache next_run_at in scheduler_jobs for dashboard display."""
    if next_run_dt is None:
        return
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    val = next_run_dt.isoformat() if hasattr(next_run_dt, "isoformat") else str(next_run_dt)
    c.execute("UPDATE scheduler_jobs SET next_run_at = ? WHERE job_id = ?", (val, job_id))
    conn.commit()
    conn.close()


# ─── job executors ─────────────────────────────────────────────────────────────

def _record_skip_run(job_id, summary, error):
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("""
    INSERT INTO scheduler_runs (job_id, started_at, finished_at, status, result_summary)
    VALUES (?, ?, ?, 'skipped', ?)
    """, (job_id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), summary))
    c.execute("""
    UPDATE scheduler_jobs
    SET last_run_at = ?, last_status = 'skipped', last_error = ?
    WHERE job_id = ?
    """, (datetime.utcnow().isoformat(), error, job_id))
    conn.commit()
    conn.close()

def _run_preflight_checks(job_id: str) -> bool:
    """
    Perform pre-flight checks (database readiness, quota limits, circuit breakers).
    Returns True if OK to run, or False if skipped.
    """
    # 1. Dependency readiness check (Database check)
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT 1")
        conn.close()
    except Exception as exc:
        print(f"[scheduler] {job_id}: pre-flight check failed — database/dependency not ready: {exc}")
        return False

    # 2. Quota Check
    try:
        try:
            from quota_manager import check_quota
        except ImportError:
            from bots.quota_manager import check_quota
        
        # Gemini Quota check
        if check_quota("gemini") == "BLOCKED":
            print(f"[scheduler] {job_id}: pre-flight check failed — gemini daily quota exhausted")
            _record_skip_run(job_id, 'Skipped: Gemini quota exhausted', 'Gemini quota exhausted')
            return False

        # Telegram Quota check
        if check_quota("telegram") == "BLOCKED":
            print(f"[SRE_SCHEDULER] {job_id}: Telegram breaker OPEN or quota exhausted. Skipping pipeline execution.")
            _record_skip_run(job_id, 'Skipped: Telegram quota exhausted', 'Telegram quota exhausted')
            return False
            
    except Exception as exc:
        print(f"[scheduler] {job_id}: quota check crashed: {exc}")

    # 3. Circuit Breaker Check
    try:
        try:
            from circuit_breakers import get_breaker, CircuitBreakerOpenException
        except ImportError:
            from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
        
        # Gemini breaker check
        breaker = get_breaker("gemini", failure_threshold=5, cooldown_sec=60)
        breaker.check()

        # Telegram breaker check
        tel_breaker = get_breaker("telegram", failure_threshold=5, cooldown_sec=60)
        tel_breaker.check()
        
    except CircuitBreakerOpenException as exc:
        # Determine which provider tripped
        provider = "telegram" if "telegram" in str(exc).lower() else "gemini"
        if provider == "telegram":
            print(f"[SRE_SCHEDULER] {job_id}: Telegram breaker OPEN or quota exhausted. Skipping pipeline execution.")
        else:
            print(f"[scheduler] {job_id}: pre-flight check failed — {provider} circuit breaker is OPEN")
            
        _record_skip_run(job_id, f'Skipped: {provider.capitalize()} circuit breaker open', f'{provider.capitalize()} circuit breaker open')
        return False
    except Exception as exc:
        print(f"[scheduler] {job_id}: breaker check encountered error: {exc}")

    return True

def _exec_pipeline(job_id: str):
    """Execute the full sector pipeline via shared service layer."""
    if not _acquire_lock(job_id):
        print(f"[scheduler] {job_id}: locked — skipping overlapping run")
        # still record the skip
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("""
        INSERT INTO scheduler_runs (job_id, started_at, finished_at, status, result_summary)
        VALUES (?, ?, ?, 'locked', 'Skipped: another instance is running')
        """, (job_id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
        return

    if not _run_preflight_checks(job_id):
        _release_lock(job_id)
        _sync_next_run(job_id)
        return

    run_id = _start_run(job_id)
    t0 = datetime.utcnow()
    try:
        # Import the shared service layer (lazy, so app.py can import us first)
        from pipeline_service import run_all_sectors_internal
        result = run_all_sectors_internal()
        duration = (datetime.utcnow() - t0).total_seconds()
        summary = (
            f"{result.get('sectors_completed', 0)} sectors · "
            f"{result.get('total_products', 0)} products"
        )
        _finish_run(job_id, run_id, "success", summary=summary, duration=duration)
        print(f"[scheduler] {job_id}: OK — {summary} ({duration:.1f}s)")
    except Exception as exc:
        duration = (datetime.utcnow() - t0).total_seconds()
        _finish_run(job_id, run_id, "error", error=str(exc), duration=duration)
        print(f"[scheduler] {job_id}: ERROR — {exc}")
    finally:
        _release_lock(job_id)
    _sync_next_run(job_id)


def _exec_retargeting(job_id: str):
    """Execute the retargeting sweep via shared service layer."""
    if not _acquire_lock(job_id):
        print(f"[scheduler] {job_id}: locked — skipping")
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        c.execute("""
        INSERT INTO scheduler_runs (job_id, started_at, finished_at, status, result_summary)
        VALUES (?, ?, ?, 'locked', 'Skipped: another instance is running')
        """, (job_id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
        return

    if not _run_preflight_checks(job_id):
        _release_lock(job_id)
        _sync_next_run(job_id)
        return

    run_id = _start_run(job_id)
    t0 = datetime.utcnow()
    try:
        from pipeline_service import run_retargeting_internal
        plans = run_retargeting_internal()
        duration = (datetime.utcnow() - t0).total_seconds()
        summary = f"{len(plans)} retargeting campaigns generated"
        _finish_run(job_id, run_id, "success", summary=summary, duration=duration)
        print(f"[scheduler] {job_id}: OK — {summary} ({duration:.1f}s)")
    except Exception as exc:
        duration = (datetime.utcnow() - t0).total_seconds()
        _finish_run(job_id, run_id, "error", error=str(exc), duration=duration)
        print(f"[scheduler] {job_id}: ERROR — {exc}")
    finally:
        _release_lock(job_id)
    _sync_next_run(job_id)


def _sync_next_run(job_id: str):
    """Pull next_run_time from APScheduler and persist it."""
    global _scheduler
    if _scheduler is None:
        return
    job = _scheduler.get_job(job_id)
    if job and hasattr(job, "next_run_time"):
        _update_next_run(job_id, job.next_run_time)


def _run_auto_publish_sweep():
    """Sweeps for pending campaigns where publish_at <= now, and publishes them."""
    print("[scheduler] Running Auto-Publish Sweep...")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Query for campaigns pending approval where publish_at has passed
        c.execute('''
            SELECT id FROM campaigns 
            WHERE status = 'pending_approval' AND publish_at <= CURRENT_TIMESTAMP
        ''')
        rows = c.fetchall()
        conn.close()
        
        if not rows:
            print("[scheduler] No pending campaigns ready for auto-publish.")
            return
            
        import distributor
        for row in rows:
            campaign_id = row["id"]
            print(f"[scheduler] Auto-publishing campaign {campaign_id}...")
            try:
                distributor.distribute_campaign(campaign_id)
            except Exception as e:
                print(f"[scheduler] Error auto-publishing campaign {campaign_id}: {e}")
    except Exception as exc:
        print(f"[scheduler] Auto-publish sweep failed: {exc}")


def backup_sqlite_database():
    """
    Safely copies data/campaigns.db to a new folder data/backups/campaigns_backup_YYYYMMDD.db
    using SQLite's native, transaction-safe online backup API (sqlite3.Connection.backup).
    Deletes backup files older than 7 days to preserve disk space.
    """
    print("[scheduler] Starting automated database backup using SQLite online backup API...")
    source_conn = None
    target_conn = None
    try:
        from datetime import datetime, timedelta
        
        db_dir = os.path.dirname(DB_PATH)
        backup_dir = os.path.join(db_dir, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        
        # Target backup filename format: campaigns_backup_YYYYMMDD.db
        date_str = datetime.now().strftime("%Y%m%d")
        backup_filename = f"campaigns_backup_{date_str}.db"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        if os.path.exists(DB_PATH):
            source_conn = sqlite3.connect(DB_PATH, timeout=30.0)
            target_conn = sqlite3.connect(backup_path, timeout=30.0)
            with source_conn:
                source_conn.backup(target_conn)
            print(f"[scheduler] Database backup successful: {backup_path}")
        else:
            print(f"[scheduler] Warning: Database file not found at: {DB_PATH}")
            
        # Clean up backups older than 7 days
        cutoff = datetime.now() - timedelta(days=7)
        for filename in os.listdir(backup_dir):
            if filename.startswith("campaigns_backup_") and filename.endswith(".db"):
                filepath = os.path.join(backup_dir, filename)
                try:
                    date_part = filename[17:25]
                    file_date = datetime.strptime(date_part, "%Y%m%d")
                    if file_date < cutoff:
                        os.remove(filepath)
                        print(f"[scheduler] Purged old backup file: {filepath}")
                except Exception:
                    # Fallback to mtime if file naming pattern doesn't yield date
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff:
                        os.remove(filepath)
                        print(f"[scheduler] Purged old backup file (mtime fallback): {filepath}")
                        
    except Exception as exc:
        print(f"[scheduler] Database backup failed: {exc}")
    finally:
        if source_conn:
            source_conn.close()
        if target_conn:
            target_conn.close()


def cleanup_old_video_assets():
    """
    Scans the static/campaigns/ directory and deletes any render outputs (.mp4, .png) older than 48 hours.
    """
    print("[scheduler] Starting automated media cleanup sweep...")
    try:
        from datetime import datetime, timedelta
        
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        campaigns_dir = os.path.join(project_root, "static", "campaigns")
        
        if not os.path.exists(campaigns_dir):
            print(f"[scheduler] Video campaigns directory does not exist: {campaigns_dir}")
            return
            
        cutoff = datetime.now() - timedelta(hours=48)
        deleted_count = 0
        
        for filename in os.listdir(campaigns_dir):
            if filename.endswith(".mp4") or filename.endswith(".png"):
                filepath = os.path.join(campaigns_dir, filename)
                mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                if mtime < cutoff:
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        print(f"[scheduler] Deleted old render: {filepath}")
                    except Exception as e:
                        print(f"[scheduler] Failed to delete file {filepath}: {e}")
                        
        print(f"[scheduler] Video assets cleanup complete. Total files deleted: {deleted_count}")
    except Exception as exc:
        print(f"[scheduler] Video assets cleanup sweep failed: {exc}")


# ─── public API ───────────────────────────────────────────────────────────────

def start(flask_app):
    """
    Initialise APScheduler and register all jobs.
    Called once from app.py startup (reloader-safe).
    """
    global _scheduler, _flask_app
    _flask_app = flask_app

    from apscheduler.schedulers.background import BackgroundScheduler

    _setup_tables()
    _seed_job_rows()

    _scheduler = BackgroundScheduler(timezone=IST)

    # Read enabled states from DB so user changes survive restarts
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT job_id, enabled FROM scheduler_jobs")
    db_enabled = {r["job_id"]: bool(r["enabled"]) for r in c.fetchall()}
    conn.close()

    for job in JOB_REGISTRY:
        jid = job["job_id"]
        enabled = db_enabled.get(jid, job["enabled"])
        executor_fn = _exec_pipeline if job["job_type"] == "pipeline" else _exec_retargeting

        _scheduler.add_job(
            func=executor_fn,
            args=[jid],
            trigger="cron",
            id=jid,
            name=job["job_label"],
            hour=job["hour"],
            minute=job["minute"],
            timezone=IST,
            max_instances=1,    # APScheduler-level overlap guard
            coalesce=True,      # if missed fires pile up, run once only
            replace_existing=True,
        )

        # Respect DB-stored enabled state
        if not enabled:
            _scheduler.pause_job(jid)

    # Register background sweep for Auto-Publish Preview Gate (runs every 5 minutes)
    _scheduler.add_job(
        func=_run_auto_publish_sweep,
        trigger="interval",
        minutes=5,
        id="auto_publish_sweep",
        name="Auto-Publish Sweep (every 5 min)",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Register database backups daily at 02:00 AM IST
    _scheduler.add_job(
        func=backup_sqlite_database,
        trigger="cron",
        hour=2,
        minute=0,
        timezone=IST,
        id="db_backup_daily",
        name="Daily DB Backup (02:00 IST)",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Register video asset cleanup daily at 03:00 AM IST
    _scheduler.add_job(
        func=cleanup_old_video_assets,
        trigger="cron",
        hour=3,
        minute=0,
        timezone=IST,
        id="video_cleanup_daily",
        name="Daily Video Cleanup (03:00 IST)",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    _scheduler.start()
    print("[scheduler] APScheduler started — 9 jobs registered (IST)")

    # Persist initial next_run_at values
    for job in JOB_REGISTRY:
        _sync_next_run(job["job_id"])


def get_status() -> dict:
    """Return status of all 6 jobs for the API."""
    global _scheduler
    _setup_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM scheduler_jobs ORDER BY job_id")
    jobs = [dict(r) for r in c.fetchall()]
    c.execute("""
        SELECT * FROM scheduler_runs
        ORDER BY started_at DESC LIMIT 20
    """)
    recent_runs = [dict(r) for r in c.fetchall()]
    conn.close()

    # Enrich with live APScheduler state
    for job in jobs:
        jid = job["job_id"]
        if _scheduler:
            apj = _scheduler.get_job(jid)
            if apj:
                job["apscheduler_state"] = "paused" if apj.next_run_time is None else "active"
                if apj.next_run_time:
                    # Convert to IST for display
                    ist_next = apj.next_run_time.astimezone(IST)
                    job["next_run_ist"] = ist_next.strftime("%Y-%m-%d %H:%M IST")
                else:
                    job["next_run_ist"] = "paused"
            else:
                job["apscheduler_state"] = "unknown"
                job["next_run_ist"] = "—"
        else:
            job["apscheduler_state"] = "not_started"
            job["next_run_ist"] = "—"

    return {
        "scheduler_running": _scheduler is not None and _scheduler.running,
        "jobs": jobs,
        "recent_runs": recent_runs,
    }


def trigger_now(job_id: str) -> dict:
    """Manually trigger a job immediately in a background thread."""
    # Find job type
    job_meta = next((j for j in JOB_REGISTRY if j["job_id"] == job_id), None)
    if job_meta is None:
        return {"status": "error", "message": f"Unknown job_id: {job_id}"}

    executor_fn = _exec_pipeline if job_meta["job_type"] == "pipeline" else _exec_retargeting
    t = threading.Thread(target=executor_fn, args=[job_id], daemon=True)
    t.start()
    return {"status": "triggered", "job_id": job_id, "label": job_meta["job_label"]}


def set_job_enabled(job_id: str, enabled: bool) -> dict:
    """Enable or disable a job. Persists to SQLite."""
    global _scheduler
    job_meta = next((j for j in JOB_REGISTRY if j["job_id"] == job_id), None)
    if job_meta is None:
        return {"status": "error", "message": f"Unknown job_id: {job_id}"}

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("UPDATE scheduler_jobs SET enabled = ? WHERE job_id = ?",
              (int(enabled), job_id))
    conn.commit()
    conn.close()

    if _scheduler:
        if enabled:
            _scheduler.resume_job(job_id)
            _sync_next_run(job_id)
        else:
            _scheduler.pause_job(job_id)
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            c = conn.cursor()
            c.execute("UPDATE scheduler_jobs SET next_run_at = NULL WHERE job_id = ?", (job_id,))
            conn.commit()
            conn.close()

    return {
        "status": "ok",
        "job_id": job_id,
        "enabled": enabled,
    }

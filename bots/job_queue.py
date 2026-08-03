"""
Dead-letter job queue for background distribution tasks.

Every enqueued task is persisted in ``job_queue``. The worker acquires one
job at a time (atomic ``pending -> running`` transition), and on repeated
failure the job is moved to ``dead_letter_jobs`` so an operator (or the alert
engine) can review, requeue, or purge it later.
"""

import json
import sqlite3
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH  # noqa: E402


class JobQueueError(Exception):
    pass


def enqueue_job(job_type, payload=None, max_retries=3):
    """
    Persist a new background job. Returns its numeric job id.
    """
    payload = payload or {}
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO job_queue (job_type, payload, status, retry_count, max_retries)
            VALUES (?, ?, 'pending', 0, ?)
            """,
            (job_type, json.dumps(payload), int(max_retries)),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as exc:
        raise JobQueueError(f"Failed to enqueue job '{job_type}': {exc}") from exc
    finally:
        conn.close()


def acquire_next_job():
    """
    Atomically claim the next pending job (FIFO). Returns a dict or None.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, job_type, payload, retry_count, max_retries FROM job_queue "
            "WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        cursor.execute("UPDATE job_queue SET status = 'running' WHERE id = ?", (row[0],))
        conn.commit()
        return {
            "job_id": row[0],
            "job_type": row[1],
            "payload": json.loads(row[2] or "{}"),
            "retry_count": row[3],
            "max_retries": row[4],
        }
    except sqlite3.Error as exc:
        raise JobQueueError(f"Failed to acquire job: {exc}") from exc
    finally:
        conn.close()


def complete_job(job_id):
    """Mark a job as completed and remove it from the live queue."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("DELETE FROM job_queue WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def fail_job(job_id, error):
    """
    Record a failure. If retry_count exceeds max_retries, the job moves to the
    dead-letter queue; otherwise it returns to 'pending' for another attempt.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT job_type, payload, retry_count, max_retries FROM job_queue WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return
        job_type, payload, retry_count, max_retries = row

        if retry_count + 1 >= max_retries:
            # Dead-letter it.
            cursor.execute(
                "INSERT INTO dead_letter_jobs (job_type, payload, error) VALUES (?, ?, ?)",
                (job_type, payload, str(error)[:2000]),
            )
            cursor.execute("DELETE FROM job_queue WHERE id = ?", (job_id,))
            print(f"[JOB_QUEUE] Job {job_id} moved to dead-letter queue (retries exhausted).")
        else:
            cursor.execute(
                "UPDATE job_queue SET status = 'pending', retry_count = retry_count + 1, last_error = ? WHERE id = ?",
                (str(error)[:2000], job_id),
            )
            print(f"[JOB_QUEUE] Job {job_id} will be retried ({retry_count + 1}/{max_retries}).")
        conn.commit()
    finally:
        conn.close()


def recover_stale_jobs():
    """
    On startup, reset any 'running' jobs left behind by a crash back to
    'pending' so they are not lost forever.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("UPDATE job_queue SET status = 'pending' WHERE status = 'running'")
        conn.commit()
        print("[JOB_QUEUE] Recovered stale 'running' jobs to 'pending'.")
    finally:
        conn.close()


def get_queue_summary():
    """Return counts per status in the live queue."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM job_queue GROUP BY status"
        ).fetchall()
        return {r["status"]: r["count"] for r in rows} or {"pending": 0, "running": 0, "failed": 0}
    finally:
        conn.close()


def get_dead_letter_jobs(limit=50):
    """Return recent dead-letter jobs."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM dead_letter_jobs ORDER BY failed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        ]
    finally:
        conn.close()


def requeue_dead_job(job_id):
    """Move a dead-letter job back into the pending queue."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT job_type, payload FROM dead_letter_jobs WHERE id = ?", (job_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return False
        cursor.execute(
            "INSERT INTO job_queue (job_type, payload, status) VALUES (?, ?, 'pending')",
            (row[0], row[1]),
        )
        cursor.execute("DELETE FROM dead_letter_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def purge_queue():
    """Remove all jobs from the live and dead-letter queues."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("DELETE FROM job_queue")
        conn.execute("DELETE FROM dead_letter_jobs")
        conn.commit()
    finally:
        conn.close()

import sqlite3
import json
import os
import sys
import uuid
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH

def _get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

def enqueue_job(task_name, payload, max_retries=3, scheduled_at=None):
    """
    Enqueues a job into the SQL queue.
    """
    conn = _get_connection()
    job_id = str(uuid.uuid4())
    payload_str = json.dumps(payload)
    
    if not scheduled_at:
        scheduled_at = datetime.utcnow().isoformat()
    elif isinstance(scheduled_at, datetime):
        scheduled_at = scheduled_at.isoformat()
        
    created_at = datetime.utcnow().isoformat()
    
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO job_queue (job_id, task_name, payload, state, retry_count, max_retries, created_at, scheduled_at)
            VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (job_id, task_name, payload_str, max_retries, created_at, scheduled_at)
        )
        conn.commit()
        print(f"[JOB_QUEUE] Enqueued job '{job_id}' for task '{task_name}'.")
        return job_id
    except Exception as e:
        print(f"[JOB_QUEUE] Error enqueuing job: {e}")
        return None
    finally:
        conn.close()

def acquire_next_job():
    """
    Atomically fetches and locks the next ready job.
    Returns a dict of the job row, or None.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        now_str = datetime.utcnow().isoformat()
        
        # Select first available job that is scheduled for execution
        cursor.execute(
            """
            SELECT * FROM job_queue 
            WHERE state IN ('pending', 'failed', 'reprocessed') AND scheduled_at <= ?
            ORDER BY created_at ASC LIMIT 1
            """,
            (now_str,)
        )
        row = cursor.fetchone()
        if not row:
            return None
            
        job = dict(row)
        job_id = job["job_id"]
        
        # Atomically mark as running
        cursor.execute(
            "UPDATE job_queue SET state = 'running' WHERE job_id = ? AND state IN ('pending', 'failed', 'reprocessed')",
            (job_id,)
        )
        conn.commit()
        
        # Verify that we actually locked it (in case of multi-threaded race conditions)
        if cursor.rowcount > 0:
            job["state"] = "running"
            job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
            return job
        return None
    except Exception as e:
        print(f"[JOB_QUEUE] Error acquiring job: {e}")
        return None
    finally:
        conn.close()

def complete_job(job_id):
    """
    Marks the job as completed.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("UPDATE job_queue SET state = 'completed' WHERE job_id = ?", (job_id,))
        conn.commit()
        print(f"[JOB_QUEUE] Completed job '{job_id}'.")
        return True
    except Exception as e:
        print(f"[JOB_QUEUE] Error completing job: {e}")
        return False
    finally:
        conn.close()

def fail_job(job_id, error_msg, backoff_base=10):
    """
    Registers a job failure, increments retry count, and schedules backoff.
    Moves to dead letter queue if retry threshold is met.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM job_queue WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return False
            
        job = dict(row)
        retries = job["retry_count"] + 1
        max_retries = job["max_retries"]
        
        if retries > max_retries:
            # Move to Dead Letter Table
            cursor.execute(
                """
                INSERT OR REPLACE INTO dead_letter_jobs (job_id, task_name, payload, failed_at, final_error)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job["job_id"], job["task_name"], job["payload"], datetime.utcnow().isoformat(), error_msg)
            )
            # Update state in main queue to 'dead'
            cursor.execute(
                "UPDATE job_queue SET state = 'dead', last_error = ?, retry_count = ? WHERE job_id = ?",
                (error_msg, retries, job_id)
            )
            print(f"[JOB_QUEUE] Job '{job_id}' exceeded max retries. Moved to DEAD LETTER QUEUE.")
        else:
            # Exponential backoff scheduling
            delay_sec = backoff_base * (2 ** (retries - 1))
            next_run = (datetime.utcnow() + timedelta(seconds=delay_sec)).isoformat()
            cursor.execute(
                """
                UPDATE job_queue 
                SET state = 'failed', retry_count = ?, last_error = ?, scheduled_at = ?
                WHERE job_id = ?
                """,
                (retries, error_msg, next_run, job_id)
            )
            print(f"[JOB_QUEUE] Job '{job_id}' failed. Retrying (attempt {retries}/{max_retries}) in {delay_sec}s.")
            
        conn.commit()
        return True
    except Exception as e:
        print(f"[JOB_QUEUE] Error handling job failure: {e}")
        return False
    finally:
        conn.close()

def requeue_dead_job(job_id):
    """
    Rescues a job from the dead letter queue and sets it back to pending.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            print(f"[JOB_QUEUE] Job '{job_id}' not found in dead_letter_jobs.")
            return False
            
        dead_job = dict(row)
        
        # Reset main queue item
        cursor.execute(
            """
            UPDATE job_queue 
            SET state = 'pending', retry_count = 0, last_error = NULL, scheduled_at = ?
            WHERE job_id = ?
            """,
            (datetime.utcnow().isoformat(), job_id)
        )
        
        # Remove from dead-letter
        cursor.execute("DELETE FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        conn.commit()
        print(f"[JOB_QUEUE] Requeued dead job '{job_id}' back to active queue.")
        return True
    except Exception as e:
        print(f"[JOB_QUEUE] Error requeuing job: {e}")
        return False
    finally:
        conn.close()

def recover_stale_jobs():
    """
    Resets all 'running' jobs to 'failed' (or 'pending') state.
    Use on app startup to recover from server crashes/restarts.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT job_id, task_name FROM job_queue WHERE state = 'running'")
        rows = cursor.fetchall()
        
        count = 0
        for row in rows:
            job_id = row[0]
            # Call fail_job to increment retry and schedule backoff safely
            fail_job(job_id, "Server restarted / Stale lock recovered.")
            count += 1
            
        if count > 0:
            print(f"[JOB_QUEUE] Recovered {count} stale running jobs.")
        return count
    except Exception as e:
        print(f"[JOB_QUEUE] Error recovering stale jobs: {e}")
        return 0
    finally:
        conn.close()

def get_queue_summary():
    """
    Returns counts for pending, running, completed, failed, and dead.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT state, COUNT(*) FROM job_queue GROUP BY state")
        summary = {row[0]: row[1] for row in cursor.fetchall()}
        
        cursor.execute("SELECT COUNT(*) FROM dead_letter_jobs")
        summary["dead_letter"] = cursor.fetchone()[0]
        return summary
    except Exception as e:
        print(f"[JOB_QUEUE] Error reading summary: {e}")
        return {}
    finally:
        conn.close()

def get_dead_letter_jobs():
    """
    Returns all dead letter jobs as a list of dicts.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dead_letter_jobs ORDER BY failed_at DESC")
        return [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        print(f"[JOB_QUEUE] Error reading dead letter jobs: {e}")
        return []
    finally:
        conn.close()

def rerun_dead_job_manual(job_id):
    """
    Reruns a dead job manually by marking its state as 'reprocessed' and deleting it from dead letter table.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            print(f"[JOB_QUEUE] Job '{job_id}' not found in dead_letter_jobs.")
            return False
        
        # Mark as reprocessed and reset in job_queue
        cursor.execute(
            """
            UPDATE job_queue 
            SET state = 'reprocessed', retry_count = 0, last_error = NULL, scheduled_at = ?
            WHERE job_id = ?
            """,
            (datetime.utcnow().isoformat(), job_id)
        )
        
        # Delete from dead_letter_jobs
        cursor.execute("DELETE FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        conn.commit()
        print(f"[JOB_QUEUE] Manually retriggered dead job '{job_id}' with state 'reprocessed'.")
        return True
    except Exception as e:
        print(f"[JOB_QUEUE] Error manually rerunning dead job '{job_id}': {e}")
        return False
    finally:
        conn.close()

def delete_dead_job_manual(job_id):
    """
    Manually deletes a dead job by removing it from dead letter queue and marking state as 'reprocessed' in job_queue.
    """
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            print(f"[JOB_QUEUE] Job '{job_id}' not found in dead_letter_jobs.")
            return False
        
        # Mark state as reprocessed in job_queue
        cursor.execute(
            "UPDATE job_queue SET state = 'reprocessed', last_error = 'Manually deleted / reprocessed' WHERE job_id = ?",
            (job_id,)
        )
        
        # Delete from dead_letter_jobs
        cursor.execute("DELETE FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        conn.commit()
        print(f"[JOB_QUEUE] Manually deleted/reprocessed dead job '{job_id}'.")
        return True
    except Exception as e:
        print(f"[JOB_QUEUE] Error manually deleting dead job '{job_id}': {e}")
        return False
    finally:
        conn.close()

import sqlite3
import hashlib
import json
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH

class IdempotencyError(Exception):
    pass

def generate_operation_id(event_type, unique_payload):
    """
    Generates a deterministic operation/event ID based on type and payload.
    """
    if isinstance(unique_payload, (dict, list)):
        payload_str = json.dumps(unique_payload, sort_keys=True)
    else:
        payload_str = str(unique_payload)
    
    raw = f"{event_type}:{payload_str}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def check_and_lock(event_id):
    """
    Atomically checks if event_id exists. If not, locks it in processed_events.
    Returns True if successfully locked, False if it was already processed/locked.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO processed_events (event_id, processed_at) VALUES (?, ?)",
            (event_id, datetime.utcnow().isoformat())
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        print(f"[IDEMPOTENCY] Error acquiring lock for {event_id}: {e}")
        return False
    finally:
        conn.close()

def release_lock(event_id):
    """
    Removes the event_id from processed_events, effectively unlocking/releasing it.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM processed_events WHERE event_id = ?", (event_id,))
        conn.commit()
        return True
    except Exception as e:
        print(f"[IDEMPOTENCY] Error releasing lock for {event_id}: {e}")
        return False
    finally:
        conn.close()

class IdempotentLock:
    """
    Context manager to safely lock and optionally release an idempotent operation.
    """
    def __init__(self, event_id, auto_release_on_error=True):
        self.event_id = event_id
        self.auto_release_on_error = auto_release_on_error
        self.acquired = False

    def __enter__(self):
        self.acquired = check_and_lock(self.event_id)
        if not self.acquired:
            raise IdempotencyError(f"Operation with ID '{self.event_id}' has already been processed or is currently active.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None and self.auto_release_on_error and self.acquired:
            print(f"[IDEMPOTENCY] Operation {self.event_id} failed with error. Releasing lock.")
            release_lock(self.event_id)

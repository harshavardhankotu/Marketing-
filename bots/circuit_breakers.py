import sqlite3
from datetime import datetime, timedelta
import os
import sys

# Ensure bots is in path to load DB_PATH safely
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH

class CircuitBreakerOpenException(Exception):
    pass

class CircuitBreaker:
    def __init__(self, provider, failure_threshold=5, cooldown_sec=60, recovery_success_count=3):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.recovery_success_count = recovery_success_count

    def _get_connection(self):
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def get_state(self):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM circuit_breaker_state WHERE provider = ?", (self.provider,))
            row = cursor.fetchone()
            if not row:
                # Seed state
                cursor.execute("""
                INSERT OR IGNORE INTO circuit_breaker_state (provider, state, failure_count, success_count)
                VALUES (?, 'CLOSED', 0, 0)
                """, (self.provider,))
                conn.commit()
                return {"state": "CLOSED", "failure_count": 0, "success_count": 0, "last_failure_at": None, "tripped_at": None}
            return dict(row)
        finally:
            conn.close()

    def update_state(self, state, failure_count=None, success_count=None, last_failure_at=None, tripped_at=None):
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            query = "UPDATE circuit_breaker_state SET state = ?"
            params = [state]
            if failure_count is not None:
                query += ", failure_count = ?"
                params.append(failure_count)
            if success_count is not None:
                query += ", success_count = ?"
                params.append(success_count)
            if last_failure_at is not None:
                query += ", last_failure_at = ?"
                params.append(last_failure_at)
            if tripped_at is not None:
                query += ", tripped_at = ?"
                params.append(tripped_at)
            query += " WHERE provider = ?"
            params.append(self.provider)
            cursor.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def check(self):
        """
        Verify if the provider is allowed to execute.
        If OPEN and cooldown passed, transitions to HALF_OPEN.
        If OPEN and cooldown NOT passed, raises CircuitBreakerOpenException.
        """
        info = self.get_state()
        state = info.get("state", "CLOSED")
        
        if state == "OPEN":
            tripped_str = info.get("tripped_at")
            if tripped_str:
                try:
                    tripped_at = datetime.fromisoformat(tripped_str)
                except ValueError:
                    # Fallback if isoformat parsing fails
                    tripped_at = datetime.utcnow()
                elapsed = (datetime.utcnow() - tripped_at).total_seconds()
                if elapsed >= self.cooldown_sec:
                    # Cooldown expired -> Shifting to HALF_OPEN
                    print(f"[CIRCUIT_BREAKER] Provider '{self.provider}' shifted to HALF_OPEN (cooldown passed)")
                    self.update_state("HALF_OPEN", success_count=0)
                    return "HALF_OPEN"
                else:
                    raise CircuitBreakerOpenException(
                        f"Circuit breaker for provider '{self.provider}' is OPEN. "
                        f"Cooldown remaining: {int(self.cooldown_sec - elapsed)}s"
                    )
        return state

    def record_success(self):
        info = self.get_state()
        state = info.get("state", "CLOSED")
        
        if state == "HALF_OPEN":
            success_count = info.get("success_count", 0) + 1
            if success_count >= self.recovery_success_count:
                print(f"[CIRCUIT_BREAKER] Provider '{self.provider}' successfully recovered. Shifting to CLOSED.")
                self.update_state("CLOSED", failure_count=0, success_count=0)
            else:
                self.update_state("HALF_OPEN", success_count=success_count)
        elif state == "CLOSED":
            # Clear failure counts on successful normal operations
            self.update_state("CLOSED", failure_count=0, success_count=0)

    def record_failure(self):
        info = self.get_state()
        state = info.get("state", "CLOSED")
        failures = info.get("failure_count", 0) + 1
        now_str = datetime.utcnow().isoformat()
        
        if state in ("HALF_OPEN", "OPEN"):
            # Any failure in HALF_OPEN immediately trips back to OPEN and resets cooldown
            print(f"[CIRCUIT_BREAKER] Provider '{self.provider}' failed in {state}. Tripping back to OPEN.")
            self.update_state("OPEN", failure_count=failures, success_count=0, last_failure_at=now_str, tripped_at=now_str)
        else: # CLOSED
            if failures >= self.failure_threshold:
                print(f"[CIRCUIT_BREAKER] Provider '{self.provider}' exceeded failure threshold ({failures}/{self.failure_threshold}). Tripping to OPEN.")
                self.update_state("OPEN", failure_count=failures, success_count=0, last_failure_at=now_str, tripped_at=now_str)
            else:
                self.update_state("CLOSED", failure_count=failures, last_failure_at=now_str)

def get_breaker(provider, failure_threshold=5, cooldown_sec=60, recovery_success_count=3):
    return CircuitBreaker(provider, failure_threshold, cooldown_sec, recovery_success_count)

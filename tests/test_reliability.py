import sys
import os
import time
import unittest
import sqlite3
from datetime import datetime, timedelta

# Adjust python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scrapers'))

from bots.config import DB_PATH
from bots.resilience import timeout_call, call_with_retry, TimeoutException
from bots.idempotency import generate_operation_id, check_and_lock, release_lock, IdempotentLock, IdempotencyError
from bots.quota_manager import consume_quota, check_quota, reset_quota, get_quota_usage, QuotaExceededException, QUOTA_LIMITS
from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
from bots.job_queue import enqueue_job, acquire_next_job, complete_job, fail_job, requeue_dead_job, recover_stale_jobs, get_queue_summary

class TestReliabilityLayers(unittest.TestCase):
    
    def setUp(self):
        # Ensure database is configured
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Clear out jobs, dead_letter, and processed_events to start clean
        cursor.execute("DELETE FROM job_queue")
        cursor.execute("DELETE FROM dead_letter_jobs")
        cursor.execute("DELETE FROM processed_events")
        conn.commit()
        conn.close()

    def test_01_idempotency_locking(self):
        print("\n--- Running Test: Idempotency Locking ---")
        event_type = "sector_pipeline_execution"
        payload = {"sector": "smartphones", "user": "test_operator"}
        
        # Deterministic Event ID generation
        event_id = generate_operation_id(event_type, payload)
        self.assertIsNotNone(event_id)
        
        # Clear out any stale locks first
        release_lock(event_id)
        
        # Try acquiring lock
        acquired = check_and_lock(event_id)
        self.assertTrue(acquired, "First lock attempt should succeed.")
        
        # Try acquiring same lock again (duplicate check)
        duplicate_attempt = check_and_lock(event_id)
        self.assertFalse(duplicate_attempt, "Subsequent duplicate lock attempt must fail.")
        
        # Release and recheck
        released = release_lock(event_id)
        self.assertTrue(released)
        
        reacquired = check_and_lock(event_id)
        self.assertTrue(reacquired, "Lock should be re-acquirable after release.")
        release_lock(event_id)

        # Context manager testing
        with IdempotentLock(event_id) as lock:
            self.assertTrue(lock.acquired)
            # Nested duplicate lock should raise IdempotencyError
            with self.assertRaises(IdempotencyError):
                with IdempotentLock(event_id):
                    pass
        
        # Verify that lock is automatically preserved on successful exits:
        # Since it exited successfully, check_and_lock must fail because it's still locked.
        self.assertFalse(check_and_lock(event_id))
        
        # Explicitly release it to proceed with the next test
        release_lock(event_id)
        
        # But if an exception is raised inside, lock should be auto-released if specified
        try:
            with IdempotentLock(event_id, auto_release_on_error=True):
                raise ValueError("Simulated Pipeline Error")
        except ValueError:
            pass
            
        # Verify lock was auto-released on failure
        acquired_again = check_and_lock(event_id)
        self.assertTrue(acquired_again, "Lock should have been auto-released on context block exception.")
        release_lock(event_id)

    def test_02_resilience_retries(self):
        print("\n--- Running Test: Resilience Retries ---")
        
        # Mock function that fails N times and then succeeds
        state = {"calls": 0}
        def failing_function(threshold_succeed):
            state["calls"] += 1
            if state["calls"] < threshold_succeed:
                raise IOError(f"Network error on attempt {state['calls']}")
            return "SUCCESS"
            
        # Case A: Success within retries
        res = call_with_retry(failing_function, 3, max_retries=5, base_delay=0.1)
        self.assertTrue(res["success"])
        self.assertEqual(res["attempts"], 3)
        self.assertEqual(res["result"], "SUCCESS")
        
        # Case B: Exceed max retries
        state["calls"] = 0
        res_fail = call_with_retry(failing_function, 10, max_retries=3, base_delay=0.01)
        self.assertFalse(res_fail["success"])
        self.assertEqual(res_fail["attempts"], 3)
        self.assertEqual(res_fail["error_class"], "CRITICAL")
        self.assertIn("Network error", res_fail["error"])

    def test_03_resilience_timeouts(self):
        print("\n--- Running Test: Resilience Timeouts ---")
        
        def quick_func():
            return "Done"
            
        def slow_func():
            time.sleep(2.0)
            return "Finished Slow"
            
        # Quick call
        quick_res = timeout_call(1.0, quick_func)
        self.assertEqual(quick_res, "Done")
        
        # Exceeding timeout (Windows-safe ThreadPoolExecutor check)
        with self.assertRaises(TimeoutException):
            timeout_call(0.5, slow_func)

    def test_04_circuit_breakers(self):
        print("\n--- Running Test: Circuit Breakers ---")
        provider = "test_gemini_breaker"
        
        # Clean state in DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM circuit_breaker_state WHERE provider = ?", (provider,))
        conn.commit()
        conn.close()
        
        breaker = get_breaker(provider, failure_threshold=3, cooldown_sec=2, recovery_success_count=2)
        
        # 1. Closed state initially
        state = breaker.check()
        self.assertEqual(state, "CLOSED")
        
        # 2. Record failures to trigger OPEN state
        breaker.record_failure()
        breaker.record_failure()
        
        # Breaker should still be CLOSED (2 failures < threshold 3)
        self.assertEqual(breaker.check(), "CLOSED")
        
        # 3rd failure trips the breaker
        breaker.record_failure()
        
        # Breaker should now raise CircuitBreakerOpenException
        with self.assertRaises(CircuitBreakerOpenException):
            breaker.check()
            
        # 3. Cooldown check
        print("Waiting for circuit breaker cooldown (2 seconds)...")
        time.sleep(2.2)
        
        # Cooldown expired -> Shifting to HALF_OPEN
        state_half = breaker.check()
        self.assertEqual(state_half, "HALF_OPEN")
        
        # 4. Probe success and recovery
        breaker.record_success()
        self.assertEqual(breaker.check(), "HALF_OPEN")  # needs 2 successes
        
        breaker.record_success()
        # 2nd consecutive success recovers the breaker -> CLOSED
        self.assertEqual(breaker.check(), "CLOSED")
        
        # 5. Half-open failure regression
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()  # Trips again
        
        with self.assertRaises(CircuitBreakerOpenException):
            breaker.check()
            
        # Wait cooldown
        time.sleep(2.2)
        self.assertEqual(breaker.check(), "HALF_OPEN")
        
        # Any failure in HALF_OPEN immediately trips it back to OPEN without waiting
        breaker.record_failure()
        with self.assertRaises(CircuitBreakerOpenException):
            breaker.check()

    def test_05_daily_quota_tracking(self):
        print("\n--- Running Test: Quota Tracking ---")
        provider = "test_playwright_sre"
        
        # Hook custom limits for the test provider
        QUOTA_LIMITS[provider] = {
            "hard_cap": 10,
            "warning_threshold": 8
        }
        
        # Reset quota
        reset_quota(provider)
        self.assertEqual(get_quota_usage(provider), 0)
        
        # Consume below threshold
        res = consume_quota(provider, 5)
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["usage"], 5)
        self.assertEqual(check_quota(provider), "OK")
        
        # Exceed warning limit
        res_warn = consume_quota(provider, 3)
        self.assertEqual(res_warn["status"], "WARNING")
        self.assertEqual(res_warn["usage"], 8)
        self.assertEqual(check_quota(provider), "WARNING")
        
        # Try exceeding hard cap
        with self.assertRaises(QuotaExceededException):
            consume_quota(provider, 3)  # 8 + 3 = 11 > 10
            
        # Usage must remain 8
        self.assertEqual(get_quota_usage(provider), 8)
        self.assertEqual(check_quota(provider), "WARNING")
        
        # Consume exactly to hard cap
        res_block = consume_quota(provider, 2)
        self.assertEqual(res_block["status"], "BLOCKED")
        self.assertEqual(res_block["usage"], 10)
        self.assertEqual(check_quota(provider), "BLOCKED")
        
        # Future consumption blocked
        with self.assertRaises(QuotaExceededException):
            consume_quota(provider, 1)

    def test_06_job_queue_failures(self):
        print("\n--- Running Test: Job Queue & Failure Workflows ---")
        task_name = "test_pipeline_run"
        payload = {"sector": "fashion", "limit": 5}
        
        # 1. Enqueue job
        job_id = enqueue_job(task_name, payload, max_retries=1)
        self.assertIsNotNone(job_id)
        
        # 2. Acquire job
        job = acquire_next_job()
        self.assertIsNotNone(job)
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["state"], "running")
        
        # 3. Simulate failure with exponential backoff scheduling
        fail_job(job_id, "Gemini API Timeout Error", backoff_base=1)
        
        # Verify job is scheduled for a future retry
        summary = get_queue_summary()
        self.assertEqual(summary.get("failed", 0), 1)
        
        # Retrieve from DB to verify retry count incremented
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT retry_count, state FROM job_queue WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "failed")
        conn.close()
        
        # Wait a second for backoff scheduler
        time.sleep(1.2)
        
        # Acquire again (retry 1)
        job_retry = acquire_next_job()
        self.assertIsNotNone(job_retry)
        self.assertEqual(job_retry["job_id"], job_id)
        
        # Fail again (reaches max retries)
        fail_job(job_id, "Gemini API Limit Exhausted", backoff_base=1)
        
        # Must shift to DEAD LETTER QUEUE
        summary_dead = get_queue_summary()
        self.assertEqual(summary_dead.get("dead_letter", 0), 1)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()
        
        # 4. Requeue from dead letter queue
        requeued = requeue_dead_job(job_id)
        self.assertTrue(requeued)
        
        # DLQ should be empty, job state should be pending
        summary_requeued = get_queue_summary()
        self.assertEqual(summary_requeued.get("dead_letter", 0), 0)
        self.assertEqual(summary_requeued.get("pending", 0), 1)
        
        # Acquire & Complete
        job_final = acquire_next_job()
        self.assertIsNotNone(job_final)
        complete_job(job_id)
        
        summary_done = get_queue_summary()
        self.assertEqual(summary_done.get("completed", 0), 1)
        
        # 5. Startup Recovery
        # Manually seed a job with state = 'running' simulating a crash
        job_crash_id = enqueue_job("test_crash_pipeline", {}, max_retries=1)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE job_queue SET state = 'running' WHERE job_id = ?", (job_crash_id,))
        conn.commit()
        conn.close()
        
        # Recover stale jobs
        recovered_count = recover_stale_jobs()
        self.assertEqual(recovered_count, 1)
        
        # Stale job should have been failed/scheduled
        summary_recovered = get_queue_summary()
        self.assertEqual(summary_recovered.get("failed", 0), 1)
        
        # Cleanup
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM job_queue")
        cursor.execute("DELETE FROM dead_letter_jobs")
        conn.commit()
        conn.close()

if __name__ == '__main__':
    unittest.main()

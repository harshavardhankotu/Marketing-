import sys
import os
import sqlite3
import unittest
import requests
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))

from bots.config import DB_PATH
from bots.job_queue import get_dead_letter_jobs, rerun_dead_job_manual, delete_dead_job_manual

BASE_URL = "http://127.0.0.1:5000"

class TestDLQManualReview(unittest.TestCase):
    def setUp(self):
        # We want to insert custom dead letter job for testing
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        
        # Clean up existing test state to be safe
        self.cursor.execute("DELETE FROM job_queue WHERE job_id LIKE 'test-dlq-%'")
        self.cursor.execute("DELETE FROM dead_letter_jobs WHERE job_id LIKE 'test-dlq-%'")
        self.conn.commit()

    def tearDown(self):
        # Clean up test state
        self.cursor.execute("DELETE FROM job_queue WHERE job_id LIKE 'test-dlq-%'")
        self.cursor.execute("DELETE FROM dead_letter_jobs WHERE job_id LIKE 'test-dlq-%'")
        self.conn.commit()
        self.conn.close()

    def test_01_backend_rerun(self):
        print("\n--- Testing Backend DLQ Rerun Manual Integration ---")
        job_id = "test-dlq-rerun-backend"
        task_name = "test_task"
        payload = '{"test": 123}'
        
        # 1. Seed job in job_queue as 'dead' and in dead_letter_jobs
        self.cursor.execute(
            "INSERT INTO job_queue (job_id, task_name, payload, state, retry_count, max_retries, last_error) VALUES (?, ?, ?, 'dead', 3, 3, 'Fatal test error')",
            (job_id, task_name, payload)
        )
        self.cursor.execute(
            "INSERT INTO dead_letter_jobs (job_id, task_name, payload, final_error) VALUES (?, ?, ?, 'Fatal test error')",
            (job_id, task_name, payload)
        )
        self.conn.commit()

        # 2. Call rerun_dead_job_manual
        success = rerun_dead_job_manual(job_id)
        self.assertTrue(success)

        # 3. Verify changes in db
        self.cursor.execute("SELECT state, retry_count, last_error FROM job_queue WHERE job_id = ?", (job_id,))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "reprocessed")
        self.assertEqual(row[1], 0)
        self.assertIsNone(row[2])

        self.cursor.execute("SELECT COUNT(*) FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        self.assertEqual(self.cursor.fetchone()[0], 0)

    def test_02_backend_delete(self):
        print("\n--- Testing Backend DLQ Delete Manual Integration ---")
        job_id = "test-dlq-delete-backend"
        task_name = "test_task"
        payload = '{"test": 456}'
        
        # 1. Seed job in job_queue as 'dead' and in dead_letter_jobs
        self.cursor.execute(
            "INSERT INTO job_queue (job_id, task_name, payload, state, retry_count, max_retries, last_error) VALUES (?, ?, ?, 'dead', 3, 3, 'Fatal test error')",
            (job_id, task_name, payload)
        )
        self.cursor.execute(
            "INSERT INTO dead_letter_jobs (job_id, task_name, payload, final_error) VALUES (?, ?, ?, 'Fatal test error')",
            (job_id, task_name, payload)
        )
        self.conn.commit()

        # 2. Call delete_dead_job_manual
        success = delete_dead_job_manual(job_id)
        self.assertTrue(success)

        # 3. Verify changes in db
        self.cursor.execute("SELECT state, last_error FROM job_queue WHERE job_id = ?", (job_id,))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "reprocessed")
        self.assertEqual(row[1], "Manually deleted / reprocessed")

        self.cursor.execute("SELECT COUNT(*) FROM dead_letter_jobs WHERE job_id = ?", (job_id,))
        self.assertEqual(self.cursor.fetchone()[0], 0)

    def test_03_api_review_endpoints(self):
        print("\n--- Testing API /api/review_dead_letter GET & POST Integration ---")
        job_id = "test-dlq-api"
        task_name = "test_task_api"
        payload_str = '{"data": "api_test"}'
        
        # 1. Seed job in job_queue as 'dead' and in dead_letter_jobs
        self.cursor.execute(
            "INSERT INTO job_queue (job_id, task_name, payload, state, retry_count, max_retries, last_error) VALUES (?, ?, ?, 'dead', 3, 3, 'Fatal API error')",
            (job_id, task_name, payload_str)
        )
        self.cursor.execute(
            "INSERT INTO dead_letter_jobs (job_id, task_name, payload, final_error) VALUES (?, ?, ?, 'Fatal API error')",
            (job_id, task_name, payload_str)
        )
        self.conn.commit()

        # 2. Test GET endpoint
        try:
            r = requests.get(f"{BASE_URL}/api/review_dead_letter", timeout=10)
        except Exception as e:
            self.fail(f"Could not connect to live Flask server: {e}. Make sure it is running.")

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "success")
        dead_jobs = data.get("dead_letter_jobs", [])
        
        # Find our seeded job
        matched = [j for j in dead_jobs if j.get("job_id") == job_id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["task_name"], task_name)
        self.assertEqual(matched[0]["final_error"], "Fatal API error")

        # 3. Test POST endpoint rerun action
        post_data = {"job_id": job_id, "action": "rerun"}
        r_post = requests.post(f"{BASE_URL}/api/review_dead_letter", json=post_data, timeout=10)
        self.assertEqual(r_post.status_code, 200)
        res_post = r_post.json()
        self.assertEqual(res_post.get("status"), "success")
        self.assertIn("successfully marked as reprocessed and enqueued for rerun", res_post.get("message"))

        # Verify job is no longer in dead_letter_jobs
        r_get_again = requests.get(f"{BASE_URL}/api/review_dead_letter", timeout=10)
        data_again = r_get_again.json()
        self.assertEqual(len([j for j in data_again.get("dead_letter_jobs", []) if j.get("job_id") == job_id]), 0)

        # Verify state in job_queue is updated to reprocessed
        self.cursor.execute("SELECT state FROM job_queue WHERE job_id = ?", (job_id,))
        self.assertEqual(self.cursor.fetchone()[0], "reprocessed")

        # 4. Seed another job for deletion test
        job_id_del = "test-dlq-api-del"
        self.cursor.execute(
            "INSERT INTO job_queue (job_id, task_name, payload, state, retry_count, max_retries, last_error) VALUES (?, ?, ?, 'dead', 3, 3, 'Fatal API error')",
            (job_id_del, task_name, payload_str)
        )
        self.cursor.execute(
            "INSERT INTO dead_letter_jobs (job_id, task_name, payload, final_error) VALUES (?, ?, ?, 'Fatal API error')",
            (job_id_del, task_name, payload_str)
        )
        self.conn.commit()

        # Test POST endpoint delete action
        post_data_del = {"job_id": job_id_del, "action": "delete"}
        r_post_del = requests.post(f"{BASE_URL}/api/review_dead_letter", json=post_data_del, timeout=10)
        self.assertEqual(r_post_del.status_code, 200)
        res_post_del = r_post_del.json()
        self.assertEqual(res_post_del.get("status"), "success")
        self.assertIn("successfully marked as reprocessed and removed from dead letter queue", res_post_del.get("message"))

        # Verify state in job_queue is updated to reprocessed
        self.cursor.execute("SELECT state FROM job_queue WHERE job_id = ?", (job_id_del,))
        self.assertEqual(self.cursor.fetchone()[0], "reprocessed")

        # 5. Test error cases
        # Invalid action
        r_err1 = requests.post(f"{BASE_URL}/api/review_dead_letter", json={"job_id": job_id_del, "action": "invalid"}, timeout=10)
        self.assertEqual(r_err1.status_code, 400)
        self.assertEqual(r_err1.json().get("status"), "error")

        # Missing params
        r_err2 = requests.post(f"{BASE_URL}/api/review_dead_letter", json={"job_id": job_id_del}, timeout=10)
        self.assertEqual(r_err2.status_code, 400)

        # Job not found
        r_err3 = requests.post(f"{BASE_URL}/api/review_dead_letter", json={"job_id": "non-existent-id", "action": "rerun"}, timeout=10)
        self.assertEqual(r_err3.status_code, 404)

if __name__ == '__main__':
    unittest.main()

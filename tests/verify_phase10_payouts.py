import unittest
import sqlite3
import threading
import time
import sys
import os

# Ensure bots and root are in the system path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bots')))

from bots import db_manager, payout_gateway
from bots.config import DB_PATH

class TestPhase10Payouts(unittest.TestCase):
    def setUp(self):
        # Reset and setup database before each test
        db_manager.setup_database()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Reset staging test users to baseline balances
        c.execute("UPDATE user_wallets SET available_balance = 500.0 WHERE user_id = 'test_user'")
        c.execute("UPDATE user_wallets SET available_balance = 2500.0 WHERE user_id = 'guest@marketing.ai'")
        # Delete old transactions
        c.execute("DELETE FROM payout_transactions")
        conn.commit()
        conn.close()

    def test_successful_payout(self):
        # 1. Fetch baseline
        bal_before = payout_gateway.get_wallet_balance("test_user")
        self.assertEqual(bal_before, 500.0)

        # 2. Process valid payout
        res = payout_gateway.process_upi_payout("test_user", "test@okaxis", 200.0)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["amount"], 200.0)
        self.assertEqual(res["upi_id"], "test@okaxis")

        # 3. Check balance after deduction
        bal_after = payout_gateway.get_wallet_balance("test_user")
        self.assertEqual(bal_after, 300.0)

        # 4. Check ledger history
        history = payout_gateway.get_payout_history("test_user")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["payout_amount"], 200.0)
        self.assertEqual(history[0]["upi_id"], "test@okaxis")

    def test_insufficient_balance_rejection(self):
        # Request payout exceeding balance
        with self.assertRaises(ValueError) as ctx:
            payout_gateway.process_upi_payout("test_user", "test@okaxis", 600.0)
        self.assertIn("Insufficient balance", str(ctx.exception))

        # Balance must remain untouched
        bal = payout_gateway.get_wallet_balance("test_user")
        self.assertEqual(bal, 500.0)

    def test_non_positive_payout_rejection(self):
        with self.assertRaises(ValueError):
            payout_gateway.process_upi_payout("test_user", "test@okaxis", -50.0)
        with self.assertRaises(ValueError):
            payout_gateway.process_upi_payout("test_user", "test@okaxis", 0.0)

    def test_double_spend_race_condition_prevention(self):
        """
        Launches 10 concurrent threads trying to withdraw Rs.100.0 each.
        Since the starting balance is Rs.500.0, exactly 5 threads must succeed,
        and exactly 5 threads must fail with ValueError due to BEGIN IMMEDIATE isolation.
        """
        success_list = []
        error_list = []
        threads = []

        def worker():
            try:
                res = payout_gateway.process_upi_payout("test_user", "race@okicici", 100.0)
                success_list.append(res)
            except ValueError as e:
                error_list.append(e)

        # Spawn 10 concurrent requests
        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Assert exactly 5 requests succeeded and 5 failed
        print(f"\n[RACE TEST RESULTS] Successful payouts: {len(success_list)}, Insufficient balance errors: {len(error_list)}")
        self.assertEqual(len(success_list), 5)
        self.assertEqual(len(error_list), 5)

        # Final balance must be exactly 0
        bal = payout_gateway.get_wallet_balance("test_user")
        self.assertEqual(bal, 0.0)

if __name__ == '__main__':
    unittest.main()

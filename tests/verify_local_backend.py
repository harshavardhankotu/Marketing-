import os
import sys
import sqlite3
import hmac
import hashlib
import json
import re
import uuid
import requests

# Ensure bots and root are in the system path to reuse config definitions
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bots')))

try:
    from bots.config import DB_PATH
except ImportError:
    # Fallback to default if configuration layout is changed
    DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'campaigns.db')

BASE_URL = "http://127.0.0.1:5000"
TEST_USER = "guest@marketing.ai"
TEST_PASS = "guest123"

def print_separator(title):
    print("\n" + "=" * 80)
    print(f" {title} ".center(80, "="))
    print("=" * 80)

def main():
    print_separator("CPA LEAD ARBITRAGE SYSTEM LOCAL INTEGRATION TEST")
    print(f"[INFO] Target Database: {os.path.abspath(DB_PATH)}")
    print(f"[INFO] Target Flask Server: {BASE_URL}")

    # Check database file existence
    if not os.path.exists(DB_PATH):
        print(f"[FAIL] Database file not found at: {DB_PATH}")
        print("[INFO] Make sure to start the Flask server first, which initializes the database.")
        sys.exit(1)

    # =========================================================================
    # STEP 1: DATABASE INTEGRITY CHECK
    # =========================================================================
    print_separator("STEP 1: DATABASE INTEGRITY CHECK")
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cursor = conn.cursor()
        
        # Check if user_wallets table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_wallets'")
        if not cursor.fetchone():
            raise AssertionError("Table 'user_wallets' does not exist in the database.")
        print("[PASS] Table 'user_wallets' successfully verified to exist.")

        # Check if payout_transactions table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='payout_transactions'")
        if not cursor.fetchone():
            raise AssertionError("Table 'payout_transactions' does not exist in the database.")
        print("[PASS] Table 'payout_transactions' successfully verified to exist.")

        # Setup/reset baseline balance for deterministic testing
        cursor.execute("INSERT OR IGNORE INTO user_wallets (user_id, available_balance) VALUES (?, 0.0)", (TEST_USER,))
        cursor.execute("UPDATE user_wallets SET available_balance = 2500.0 WHERE user_id = ?", (TEST_USER,))
        conn.commit()
        
        # Assert initial baseline balance
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (TEST_USER,))
        balance = cursor.fetchone()[0]
        assert balance == 2500.0, f"Expected initial balance Rs. 2500.0, but got Rs. {balance:.2f}"
        print(f"[PASS] Database seed user ({TEST_USER}) verified with baseline balance: Rs. {balance:.2f}")

    except Exception as e:
        print(f"[FAIL] Database Integrity Check failed: {e}")
        if conn:
            conn.close()
        sys.exit(1)
    finally:
        if conn:
            conn.close()

    # =========================================================================
    # STEP 2: THE WEBHOOK REVENUE TEST
    # =========================================================================
    print_separator("STEP 2: WEBHOOK REVENUE POSTBACK TEST")

    # Fetch postback secret from DB
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM operator_settings WHERE key = 'postback_secret'")
        row = cursor.fetchone()
        postback_secret = row[0] if row else "default_secret_key_123"
    except Exception as e:
        print(f"[WARN] Failed to query postback_secret from DB: {e}. Defaulting to standard secret.")
        postback_secret = "default_secret_key_123"
    finally:
        if conn:
            conn.close()

    print(f"[INFO] Using postback secret for HMAC: '{postback_secret}'")

    # Generate CPA conversion payload (₹4,000 commission)
    transaction_id = f"TX-MOCK-{uuid.uuid4().hex[:8].upper()}"
    payload = {
        "product_id": "test_cpa_card_99",
        "session_id": "mock_session_abc123",
        "sale_amount": 12000.0,
        "commission_amount": 4000.0,  # 15% cashback of 4000 is 600
        "transaction_id": transaction_id,
        "network_name": "arbitrage_cpa_network"
    }
    
    raw_payload = json.dumps(payload).encode('utf-8')
    signature = hmac.new(postback_secret.encode('utf-8'), raw_payload, hashlib.sha256).hexdigest()
    
    headers = {
        "Content-Type": "application/json",
        "X-Signature": signature
    }

    try:
        postback_url = f"{BASE_URL}/postback/cpa_lead"
        print(f"[INFO] Sending POST to {postback_url} with transaction ID: {transaction_id}")
        r = requests.post(postback_url, data=raw_payload, headers=headers, timeout=10.0)
        
        if r.status_code != 200:
            print(f"[FAIL] Webhook request failed with status code {r.status_code}: {r.text}")
            sys.exit(1)
            
        res_json = r.json()
        print(f"[INFO] Webhook Server Response: {res_json}")
        assert res_json.get("status") == "success", f"Expected success status, got: {res_json}"
        print("[PASS] Webhook postback endpoint accepted payload with valid HMAC.")

        # Re-query DB to assert that exactly 15% (₹600) was credited to the user's wallet
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (TEST_USER,))
        new_balance = cursor.fetchone()[0]
        conn.close()

        expected_balance = 2500.0 + (0.15 * 4000.0) # 2500.0 + 600.0 = 3100.0
        print(f"[INFO] Re-queried DB balance: Rs. {new_balance:.2f} (Expected: Rs. {expected_balance:.2f})")
        assert abs(new_balance - expected_balance) < 0.01, f"Expected Rs. {expected_balance:.2f}, got Rs. {new_balance:.2f}"
        print(f"[PASS] Webhook signed and balance updated: exactly 15% (Rs. 600.00) successfully credited to {TEST_USER} wallet.")

    except requests.exceptions.ConnectionError:
        print(f"\n[FAIL] Connection error: Local Flask server is not running at {BASE_URL}.")
        print("[INFO] Please launch the Flask server in another terminal window first before running this test script.")
        sys.exit(1)
    except Exception as e:
        print(f"[FAIL] Webhook Revenue Test failed: {e}")
        sys.exit(1)

    # =========================================================================
    # STEP 3: THE UPI PAYOUT TEST
    # =========================================================================
    print_separator("STEP 3: UPI CASHBACK PAYOUT TEST")

    session = requests.Session()
    
    try:
        # 1. Fetch login page to extract CSRF token
        login_url = f"{BASE_URL}/login"
        print(f"[INFO] Fetching login page: {login_url}")
        r_login_page = session.get(login_url, timeout=10.0)
        
        csrf_token_login = None
        csrf_match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', r_login_page.text)
        if csrf_match:
            csrf_token_login = csrf_match.group(1)
            print(f"[INFO] Extracted login CSRF token: {csrf_token_login[:16]}...")
        else:
            print("[FAIL] Failed to extract CSRF token from login page HTML.")
            sys.exit(1)

        # 2. Login to authenticate session
        login_data = {
            "csrf_token": csrf_token_login,
            "email": TEST_USER,
            "password": TEST_PASS
        }
        print(f"[INFO] Submitting login form for email: {TEST_USER}")
        r_login_post = session.post(login_url, data=login_data, timeout=10.0)
        
        # Verify authenticated redirect or session cookie
        if r_login_post.status_code != 200 or TEST_USER not in r_login_post.text and "Dashboard" not in r_login_post.text:
            # Fetch / dashboard page to verify authenticated state
            r_dash = session.get(f"{BASE_URL}/", timeout=10.0)
            if "Logout" not in r_dash.text:
                print(f"[FAIL] Authentication failed. Response status: {r_login_post.status_code}")
                sys.exit(1)
            else:
                r_login_post = r_dash
        
        print("[PASS] User session successfully authenticated via secure Flask-Login.")

        # 3. Extract fresh API CSRF Token from response (inserted globally by app.py after_request)
        csrf_token_api = None
        api_csrf_match = re.search(r"options\.headers\['X-CSRFToken'\]\s*=\s*'([^']+)'", r_login_post.text)
        if api_csrf_match:
            csrf_token_api = api_csrf_match.group(1)
            print(f"[INFO] Extracted API CSRF token: {csrf_token_api[:16]}...")
        else:
            # Fallback: parse from index.html if we were redirected
            r_dash = session.get(f"{BASE_URL}/", timeout=10.0)
            api_csrf_match = re.search(r"options\.headers\['X-CSRFToken'\]\s*=\s*'([^']+)'", r_dash.text)
            if api_csrf_match:
                csrf_token_api = api_csrf_match.group(1)
                print(f"[INFO] Extracted API CSRF token (fallback): {csrf_token_api[:16]}...")
            else:
                print("[FAIL] Could not find injected CSRF token in the response HTML.")
                sys.exit(1)

        # 4. Request payout of ₹500
        payout_url = f"{BASE_URL}/api/payout"
        payout_payload = {
            "upi_id": "test@ybl",
            "amount": 500.0
        }
        payout_headers = {
            "Content-Type": "application/json",
            "X-CSRFToken": csrf_token_api
        }
        
        print(f"[INFO] Sending POST to {payout_url} for withdrawal of Rs. 500.00 to test@ybl")
        r_payout = session.post(payout_url, json=payout_payload, headers=payout_headers, timeout=10.0)
        
        if r_payout.status_code != 200:
            print(f"[FAIL] Payout request rejected with code {r_payout.status_code}: {r_payout.text}")
            sys.exit(1)
            
        payout_json = r_payout.json()
        print(f"[INFO] Payout API Response: {payout_json}")
        assert payout_json.get("status") == "success", f"Expected status 'success', got: {payout_json}"
        print("[PASS] Payout API accepted and successfully completed withdrawal request.")

        # 5. Query the database directly to assert wallet balance decrease and payout record existence
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check balance
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (TEST_USER,))
        balance_after_payout = cursor.fetchone()[0]
        
        # Check transaction ledger
        cursor.execute("""
            SELECT payout_id, payout_amount, upi_id, status 
            FROM payout_transactions 
            WHERE user_id = ? 
            ORDER BY id DESC LIMIT 1
        """, (TEST_USER,))
        tx_row = cursor.fetchone()
        conn.close()

        expected_wallet_balance = 3100.0 - 500.0 # 2600.0
        print(f"[INFO] Re-queried DB balance: Rs. {balance_after_payout:.2f} (Expected: Rs. {expected_wallet_balance:.2f})")
        assert abs(balance_after_payout - expected_wallet_balance) < 0.01, f"Expected wallet balance Rs. {expected_wallet_balance:.2f}, got Rs. {balance_after_payout:.2f}"
        print(f"[PASS] Database wallet balance successfully decreased by exactly Rs. 500.00.")

        assert tx_row is not None, "No payout transaction found in the database payout_transactions table."
        p_id, p_amount, p_upi, p_status = tx_row
        print(f"[INFO] Latest Transaction Ledger Entry -> TxID: {p_id}, Amount: Rs. {p_amount:.2f}, UPI: {p_upi}, Status: {p_status}")
        
        assert p_amount == 500.0, f"Expected payout amount 500.0, got {p_amount}"
        assert p_upi == "test@ybl", f"Expected UPI test@ybl, got {p_upi}"
        assert p_status == "success", f"Expected transaction status 'success', got {p_status}"
        print("[PASS] Database ledger verified: transaction recorded with status 'success' and correct metadata.")

    except Exception as e:
        print(f"[FAIL] UPI Payout Test failed: {e}")
        sys.exit(1)

    # =========================================================================
    # STEP 4: THE NEGATIVE BALANCE GUARD TEST
    # =========================================================================
    print_separator("STEP 4: NEGATIVE BALANCE GUARD TEST")

    try:
        # Request payout exceeding current balance (Rs. 50,000.0)
        excessive_amount = 50000.0
        payout_payload["amount"] = excessive_amount
        
        print(f"[INFO] Sending POST to {payout_url} for withdrawal of Rs. {excessive_amount:.2f} (Exceeds Rs. 2600.00)")
        r_excessive = session.post(payout_url, json=payout_payload, headers=payout_headers, timeout=10.0)
        
        print(f"[INFO] Server Response (Status: {r_excessive.status_code}): {r_excessive.text}")
        assert r_excessive.status_code == 400, f"Expected HTTP status code 400, got {r_excessive.status_code}"
        
        res_excessive_json = r_excessive.json()
        assert res_excessive_json.get("status") == "error", f"Expected status 'error', got: {res_excessive_json}"
        print("[PASS] Payout API rejected excessive withdrawal with HTTP status 400 and clear error body.")

        # Verify DB balance remains completely unchanged
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (TEST_USER,))
        final_balance = cursor.fetchone()[0]
        conn.close()

        print(f"[INFO] Re-queried DB balance: Rs. {final_balance:.2f} (Expected: Rs. 2600.00)")
        assert abs(final_balance - 2600.0) < 0.01, f"Expected wallet balance to remain unchanged at Rs. 2600.00, but got Rs. {final_balance:.2f}"
        print("[PASS] Database wallet balance remains completely unchanged, verifying transaction rollback integrity.")

    except Exception as e:
        print(f"[FAIL] Negative Balance Guard Test failed: {e}")
        sys.exit(1)

    print_separator("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("[SUCCESS] Database and Flask API systems are robust, secure, and production-ready!")
    print("================================================================================\n")

if __name__ == "__main__":
    main()

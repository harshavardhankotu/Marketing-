import sqlite3
import os
import uuid
from datetime import datetime
from bots.config import DB_PATH

def process_upi_payout(user_id: str, upi_id: str, amount: float) -> dict:
    """
    Processes a UPI cashback payout for a user.
    Uses a BEGIN IMMEDIATE write lock block to prevent double-spends and transaction race conditions.
    Enforces non-negative checks and guarantees connection closing.
    """
    if not user_id or not upi_id:
        raise ValueError("Invalid parameters: user_id and upi_id are required.")
    if amount <= 0:
        raise ValueError("Payout amount must be greater than zero.")

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        # Acquire write lock immediately to block concurrent writes
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()

        # Check current wallet balance
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Wallet not found for user: {user_id}")

        balance = row[0]
        if balance < amount:
            raise ValueError(f"Insufficient balance. Available: Rs.{balance:.2f}, Requested: Rs.{amount:.2f}")

        # Deduct balance (the CHECK constraint available_balance >= 0.0 acts as additional guard)
        cursor.execute("""
        UPDATE user_wallets
        SET available_balance = available_balance - ?
        WHERE user_id = ?
        """, (amount, user_id))

        # Generate a unique payout transaction ID
        payout_id = f"PAY-{uuid.uuid4().hex[:12].upper()}"

        # Insert payout transaction record
        cursor.execute("""
        INSERT INTO payout_transactions (payout_id, user_id, payout_amount, upi_id, status)
        VALUES (?, ?, ?, ?, 'success')
        """, (payout_id, user_id, amount, upi_id))

        conn.commit()

        # Check remaining operator/pool balance (guest@marketing.ai is the primary operator wallet)
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = 'guest@marketing.ai'")
        op_row = cursor.fetchone()
        op_balance = op_row[0] if op_row else 0.0
        
        if op_balance < 1000.0:
            telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
            admin_id = os.getenv("ADMIN_TELEGRAM_ID")
            if telegram_token and admin_id and telegram_token != "your_telegram_bot_token_here":
                alert_text = f"⚠️ CRITICAL: Payout Wallet Balance is Low (Rs. {op_balance:.2f}). Top up immediately to prevent failed UPI payouts."
                def send_telegram_alert():
                    try:
                        import requests
                        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                        r = requests.post(url, json={"chat_id": admin_id, "text": alert_text}, timeout=10)
                        print(f"[ALERT] Low-balance Telegram alert sent: status {r.status_code}")
                    except Exception as te:
                        print(f"[ALERT ERROR] Failed to send Telegram alert: {te}")
                
                import threading
                threading.Thread(target=send_telegram_alert, daemon=True).start()

        print(f"[PAYOUT] Successfully processed Rs.{amount} to {upi_id} for {user_id}. TxID: {payout_id}")
        return {
            "status": "success",
            "payout_id": payout_id,
            "user_id": user_id,
            "amount": amount,
            "upi_id": upi_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        conn.rollback()
        print(f"[PAYOUT ERROR] Transaction failed: {e}")
        raise e
    finally:
        conn.close()

def get_wallet_balance(user_id: str) -> float:
    """Fetches available balance for a user's wallet safely."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT available_balance FROM user_wallets WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()

def get_payout_history(user_id: str = None) -> list:
    """Fetches payout transactions history safely."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        if user_id:
            cursor.execute("""
            SELECT * FROM payout_transactions 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT 50
            """, (user_id,))
        else:
            cursor.execute("""
            SELECT * FROM payout_transactions 
            ORDER BY created_at DESC 
            LIMIT 100
            """)
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()

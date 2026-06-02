import sqlite3
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH

class QuotaExceededException(Exception):
    pass

# Hard-coded defaults which can be customized via env if desired
QUOTA_LIMITS = {
    "gemini": {
        "hard_cap": int(os.getenv("QUOTA_GEMINI_CAP", 150)),
        "warning_threshold": int(os.getenv("QUOTA_GEMINI_WARN", 120))
    },
    "telegram": {
        "hard_cap": int(os.getenv("QUOTA_TELEGRAM_CAP", 300)),
        "warning_threshold": int(os.getenv("QUOTA_TELEGRAM_WARN", 240))
    },
    "playwright": {
        "hard_cap": int(os.getenv("QUOTA_PLAYWRIGHT_CAP", 100)),
        "warning_threshold": int(os.getenv("QUOTA_PLAYWRIGHT_WARN", 80))
    },
    "twitter": {
        "hard_cap": int(os.getenv("QUOTA_TWITTER_CAP", 50)),
        "warning_threshold": int(os.getenv("QUOTA_TWITTER_WARN", 40))
    },
    "instagram": {
        "hard_cap": int(os.getenv("QUOTA_INSTAGRAM_CAP", 50)),
        "warning_threshold": int(os.getenv("QUOTA_INSTAGRAM_WARN", 40))
    },
    "whatsapp": {
        "hard_cap": int(os.getenv("QUOTA_WHATSAPP_CAP", 50)),
        "warning_threshold": int(os.getenv("QUOTA_WHATSAPP_WARN", 40))
    }
}

def _get_connection():
    return sqlite3.connect(DB_PATH, timeout=30.0)

def get_current_date_str():
    return datetime.utcnow().strftime("%Y-%m-%d")

def get_quota_usage(provider):
    """
    Returns the current usage count for the given provider for today.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        date_str = get_current_date_str()
        cursor.execute(
            "SELECT request_count FROM api_quota_usage WHERE provider = ? AND usage_date = ?",
            (provider, date_str)
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        else:
            # Seed usage_date
            cursor.execute(
                "INSERT OR IGNORE INTO api_quota_usage (provider, usage_date, request_count) VALUES (?, ?, 0)",
                (provider, date_str)
            )
            conn.commit()
            return 0
    except Exception as e:
        print(f"[QUOTA_MANAGER] Error reading quota for {provider}: {e}")
        return 0
    finally:
        conn.close()

def check_quota(provider):
    """
    Checks if the provider is currently blocked, warning, or ok.
    Returns: 'OK', 'WARNING', 'BLOCKED'
    """
    if provider not in QUOTA_LIMITS:
        return "OK"
        
    usage = get_quota_usage(provider)
    limits = QUOTA_LIMITS[provider]
    
    if usage >= limits["hard_cap"]:
        return "BLOCKED"
    elif usage >= limits["warning_threshold"]:
        return "WARNING"
    return "OK"

def consume_quota(provider, count=1):
    """
    Increments the provider's quota usage by `count`.
    Raises QuotaExceededException if it would violate the hard cap (unless forced).
    Returns a dict with: {"status": str, "usage": int, "cap": int}
    """
    if provider not in QUOTA_LIMITS:
        return {"status": "OK", "usage": 0, "cap": 999999}
        
    limits = QUOTA_LIMITS[provider]
    usage = get_quota_usage(provider)
    
    if usage + count > limits["hard_cap"]:
        # Block transaction
        raise QuotaExceededException(
            f"Daily quota limit exceeded for '{provider}'. "
            f"Current usage: {usage}/{limits['hard_cap']}. Attempted increment: {count}"
        )
        
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        date_str = get_current_date_str()
        cursor.execute(
            """
            INSERT INTO api_quota_usage (provider, usage_date, request_count)
            VALUES (?, ?, ?)
            ON CONFLICT(provider, usage_date) DO UPDATE SET request_count = request_count + ?
            """,
            (provider, date_str, count, count)
        )
        conn.commit()
    except Exception as e:
        print(f"[QUOTA_MANAGER] Error incrementing quota for {provider}: {e}")
    finally:
        conn.close()
        
    new_usage = usage + count
    status = "OK"
    if new_usage >= limits["hard_cap"]:
        status = "BLOCKED"
    elif new_usage >= limits["warning_threshold"]:
        status = "WARNING"
        
    return {
        "status": status,
        "usage": new_usage,
        "cap": limits["hard_cap"]
    }

def get_all_quotas():
    """
    Returns a summary of all active provider quotas.
    """
    res = {}
    for prov, limits in QUOTA_LIMITS.items():
        usage = get_quota_usage(prov)
        status = "OK"
        if usage >= limits["hard_cap"]:
            status = "BLOCKED"
        elif usage >= limits["warning_threshold"]:
            status = "WARNING"
            
        res[prov] = {
            "usage": usage,
            "hard_cap": limits["hard_cap"],
            "warning_threshold": limits["warning_threshold"],
            "status": status
        }
    return res

def reset_quota(provider):
    """
    Resets the quota for a given provider for today.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        date_str = get_current_date_str()
        cursor.execute(
            "UPDATE api_quota_usage SET request_count = 0 WHERE provider = ? AND usage_date = ?",
            (provider, date_str)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"[QUOTA_MANAGER] Failed to reset quota for {provider}: {e}")
        return False
    finally:
        conn.close()

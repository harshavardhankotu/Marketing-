"""
SQLite database manager for the Autonomous Affiliate Marketing Suite.

Enforces strict compliance constraints:
    * NO ``user_wallets`` table.
    * NO ``payout_transactions`` table.
    * NO ``cpa_phone_pool`` / DNI telephony infrastructure.

Every database connection is wrapped in ``try / except / finally`` so the
connection is ALWAYS closed, preventing handle leakage on Windows.
Write paths that mutate multiple rows use explicit ``BEGIN IMMEDIATE``
write-locks to avoid SQLITE_BUSY contention in WAL mode.
"""

import os
import json
import sqlite3
from datetime import datetime

try:
    from config import DB_PATH
except ImportError:  # allow `python bots/db_manager.py` to work directly
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL PRAGMA ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────────────
_orig_connect = sqlite3.connect


def _hardened_connect(*args, **kwargs):
    """Wrap sqlite3.connect so every new connection enforces foreign keys."""
    conn = _orig_connect(*args, **kwargs)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
    except sqlite3.Error:
        pass
    return conn


sqlite3.connect = _hardened_connect


def _connection(timeout=30.0):
    """Open a new WAL-mode connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA_STATEMENTS = [
    # 1. Campaigns
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  TEXT,
        title       TEXT NOT NULL,
        sector      TEXT,
        target_url  TEXT,
        price       REAL DEFAULT 0.0,
        discount    REAL DEFAULT 0.0,
        commission  REAL DEFAULT 0.0,
        caption     TEXT,
        graphic_path TEXT,
        status      TEXT DEFAULT 'pending_approval',
        publish_at  TIMESTAMP,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 2. Distribution logs
    """
    CREATE TABLE IF NOT EXISTS distribution_logs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id  INTEGER,
        channel      TEXT,
        message_id   TEXT,
        status       TEXT,
        timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (campaign_id) REFERENCES campaigns (id)
    )
    """,
    # 3. Affiliate clicks (human-only analytics)
    """
    CREATE TABLE IF NOT EXISTS affiliate_clicks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  TEXT,
        channel     TEXT,
        session_id  TEXT,
        user_agent  TEXT,
        ip_address  TEXT,
        is_bot      INTEGER DEFAULT 0,
        variant     TEXT,
        timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 4. Affiliate conversions (single-write, idempotent)
    """
    CREATE TABLE IF NOT EXISTS affiliate_conversions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id    TEXT UNIQUE,
        product_id        TEXT,
        session_id        TEXT,
        sale_amount       REAL DEFAULT 0.0,
        commission_amount REAL DEFAULT 0.0,
        status            TEXT DEFAULT 'converted',
        timestamp         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 5. System settings (key/value KV store)
    """
    CREATE TABLE IF NOT EXISTS system_settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    # 6. Operator settings
    """
    CREATE TABLE IF NOT EXISTS operator_settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    # 7. Admin users (bcrypt hashed)
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role          TEXT DEFAULT 'admin',
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 8. Stateful circuit breakers
    """
    CREATE TABLE IF NOT EXISTS circuit_breaker_state (
        provider       TEXT PRIMARY KEY,
        state          TEXT DEFAULT 'CLOSED',
        failure_count  INTEGER DEFAULT 0,
        success_count  INTEGER DEFAULT 0,
        last_failure   TIMESTAMP,
        tripped_at     TIMESTAMP
    )
    """,
    # 9. Daily API quota usage
    """
    CREATE TABLE IF NOT EXISTS api_quota_usage (
        provider      TEXT,
        usage_date    TEXT,
        request_count INTEGER DEFAULT 0,
        PRIMARY KEY (provider, usage_date)
    )
    """,
    # 10. Scheduler jobs
    """
    CREATE TABLE IF NOT EXISTS scheduler_jobs (
        job_id        TEXT PRIMARY KEY,
        job_label     TEXT,
        job_type      TEXT,
        schedule_expr TEXT,
        enabled       INTEGER DEFAULT 1,
        next_run_at   TIMESTAMP,
        last_run_at   TIMESTAMP,
        last_status   TEXT,
        last_error    TEXT,
        run_count     INTEGER DEFAULT 0
    )
    """,
    # 11. Idempotency keys (prevents double-counted conversions/webhooks)
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        transaction_id TEXT PRIMARY KEY,
        event_type     TEXT,
        processed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 12. Background job queue
    """
    CREATE TABLE IF NOT EXISTS job_queue (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type     TEXT NOT NULL,
        payload      TEXT,
        status       TEXT DEFAULT 'pending',
        retry_count  INTEGER DEFAULT 0,
        max_retries  INTEGER DEFAULT 3,
        last_error   TEXT,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 13. Dead-letter queue for failed distributions
    """
    CREATE TABLE IF NOT EXISTS dead_letter_jobs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_type    TEXT NOT NULL,
        payload     TEXT,
        error       TEXT,
        failed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 14. Price history — the free "lowest ever" engine (our own observations)
    """
    CREATE TABLE IF NOT EXISTS price_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id  TEXT,
        price       REAL,
        mrp         REAL,
        seen_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_price_history_product
        ON price_history (product_id, seen_at)
    """,
    # 15. Channel growth snapshots — free audience telemetry (Bot API counts)
    """
    CREATE TABLE IF NOT EXISTS growth_snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        channel       TEXT DEFAULT 'telegram',
        member_count  INTEGER,
        source        TEXT DEFAULT 'bot_api',
        captured_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # 16. Newsletter subscribers — double opt-in, instant unsubscribe (DPDP)
    """
    CREATE TABLE IF NOT EXISTS newsletter_subscribers (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        email            TEXT UNIQUE NOT NULL,
        token            TEXT UNIQUE NOT NULL,
        confirmed        INTEGER DEFAULT 0,
        unsubscribed_at  TIMESTAMP,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

# Seed rows for system_settings (spec section B item 5)
SYSTEM_SETTINGS_SEED = {
    "primary_routing_domain": "https://www.amazon.in",
    "auto_publish_timeout": "30",
}

# Seed rows for circuit breakers
CIRCUIT_BREAKER_PROVIDERS = ["gemini", "telegram", "amazon_paapi", "twitter", "instagram", "meta"]


def _migrate_schema(cursor):
    """Add columns introduced after the original schema was shipped.

    Uses ``ALTER TABLE ADD COLUMN`` guarded by a duplicate-column check so this
    is safe to run on both fresh and pre-existing databases.
    """
    _ALTER_STATEMENTS = [
        ("campaigns", "caption", "TEXT"),
        ("campaigns", "graphic_path", "TEXT"),
        ("campaigns", "mrp", "REAL"),
        ("campaigns", "deal_score", "REAL DEFAULT 0"),
        ("campaigns", "lowest_ever", "INTEGER DEFAULT 0"),
        ("campaigns", "variant", "TEXT"),
        ("affiliate_clicks", "session_id", "TEXT"),
    ]
    for table, column, col_type in _ALTER_STATEMENTS:
        try:
            existing = {
                row["name"]
                for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.Error as exc:
            print(f"[DB_MANAGER] schema migration skipped ({table}.{column}): {exc}")


def setup_database():
    """Create every table idempotently and seed default rows."""
    from config import ensure_directories
    ensure_directories()
    conn = _connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        cursor = conn.cursor()
        for statement in SCHEMA_STATEMENTS:
            cursor.execute(statement)

        _migrate_schema(cursor)

        # Seed system_settings
        for key, value in SYSTEM_SETTINGS_SEED.items():
            cursor.execute(
                "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
                (key, value),
            )

        # Seed circuit breakers
        for provider in CIRCUIT_BREAKER_PROVIDERS:
            cursor.execute(
                "INSERT OR IGNORE INTO circuit_breaker_state (provider, state, failure_count, success_count) "
                "VALUES (?, 'CLOSED', 0, 0)",
                (provider,),
            )

        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        print(f"[DB_MANAGER] setup_database failed: {exc}")
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────────────────────────
def seed_admin_user(username="admin", password=None):
    """Create the initial admin user if it does not exist."""
    import bcrypt

    if password is None:
        try:
            from config import ADMIN_DEFAULT_PASSWORD
            password = ADMIN_DEFAULT_PASSWORD
        except ImportError:
            password = "admin123"

    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        if not cursor.fetchone():
            hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            cursor.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                (username, hashed),
            )
            conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CAMPAIGNS
# ─────────────────────────────────────────────────────────────────────────────
def save_campaign(campaign_data, sector="electronics"):
    """
    Persist a campaign together with its distribution log entries inside a
    single ``BEGIN IMMEDIATE`` write-lock transaction.
    """
    setup_database()
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()

        timeout_mins = get_system_setting("auto_publish_timeout", "30")
        try:
            timeout_mins = int(timeout_mins)
        except (TypeError, ValueError):
            timeout_mins = 30

        from datetime import timedelta
        publish_at = (datetime.utcnow() + timedelta(minutes=timeout_mins)).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT INTO campaigns
                (product_id, title, sector, target_url, price, discount, commission,
                 caption, graphic_path, mrp, deal_score, lowest_ever, variant,
                 status, publish_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)
            """,
            (
                campaign_data.get("id") or campaign_data.get("product_id"),
                campaign_data.get("title", "Untitled"),
                sector,
                campaign_data.get("target_url") or campaign_data.get("affiliate_link", ""),
                float(campaign_data.get("price", 0) or 0),
                float(campaign_data.get("discount", 0) or 0),
                float(campaign_data.get("commission", 0) or 0),
                campaign_data.get("caption", ""),
                campaign_data.get("graphic_path", ""),
                float(campaign_data["mrp"]) if campaign_data.get("mrp") else None,
                float(campaign_data.get("deal_score", 0) or 0),
                1 if campaign_data.get("lowest_ever") else 0,
                campaign_data.get("variant") if campaign_data.get("variant") in ("A", "B") else None,
                publish_at,
            ),
        )
        campaign_id = cursor.lastrowid

        for dist in campaign_data.get("distribution", []) or []:
            cursor.execute(
                """
                INSERT INTO distribution_logs (campaign_id, channel, status, message_id)
                VALUES (?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    dist.get("channel", ""),
                    dist.get("status", ""),
                    dist.get("message_id"),
                ),
            )

        conn.commit()
        return campaign_id
    except sqlite3.Error as exc:
        conn.rollback()
        print(f"[DB_MANAGER] save_campaign failed: {exc}")
        raise
    finally:
        conn.close()


def get_campaign(campaign_id):
    """Fetch a single campaign row as a dict."""
    conn = _connection()
    try:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_campaign_status(campaign_id, status):
    """Transition a campaign to a new status."""
    conn = _connection()
    try:
        conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))
        conn.commit()
    finally:
        conn.close()


def list_campaigns(status=None, sector=None, limit=100):
    """List campaigns, optionally filtered by status / sector."""
    conn = _connection()
    try:
        query = "SELECT * FROM campaigns WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if sector:
            query += " AND sector = ?"
            params.append(sector)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def count_campaigns():
    """Return the total number of campaigns."""
    conn = _connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    finally:
        conn.close()


def has_recent_campaign(product_id, days=7):
    """
    True when a campaign for this product was already created within the last
    ``days`` days — the channel-spam guard that stops every sweep re-posting
    the same ASIN. Re-alerts bypass this when the price dropped further.
    """
    if not product_id:
        return False
    conn = _connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM campaigns
            WHERE product_id = ?
              AND status IN ('pending_approval', 'published')
              AND created_at >= datetime('now', ?)
            """,
            (product_id, f"-{int(days)} days"),
        ).fetchone()
        return bool(row[0])
    finally:
        conn.close()


def get_lowest_recorded_price(product_id):
    """Minimum price ever observed for a product, or None."""
    stats = get_price_stats(product_id)
    return stats["min"] if stats else None


def count_posts_today():
    """
    Distinct campaigns distributed today (UTC date) — the daily posting cap
    basis so the auto-publish sweep can never flood the channel.
    """
    conn = _connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT campaign_id) FROM distribution_logs
            WHERE date(timestamp) = date('now')
              AND status LIKE 'Success%'
            """
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# PRICE HISTORY — the free "lowest ever" engine
# ─────────────────────────────────────────────────────────────────────────────
def record_price(product_id, price, mrp=None):
    """Store a price observation for a product. Returns the row id."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO price_history (product_id, price, mrp) VALUES (?, ?, ?)",
            (product_id, float(price), float(mrp) if mrp else None),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_price_stats(product_id):
    """
    Return {min, max, avg, last, samples} over recorded observations,
    or None when nothing has been recorded yet.
    """
    conn = _connection()
    try:
        row = conn.execute(
            """
            SELECT MIN(price), MAX(price), AVG(price),
                   (SELECT price FROM price_history WHERE product_id = ?
                    ORDER BY id DESC LIMIT 1),
                   COUNT(*)
            FROM price_history WHERE product_id = ?
            """,
            (product_id, product_id),
        ).fetchone()
        if not row or row[4] == 0:
            return None
        return {
            "min": float(row[0]),
            "max": float(row[1]),
            "avg": float(row[2]),
            "last": float(row[3]) if row[3] is not None else None,
            "samples": int(row[4]),
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CHANNEL GROWTH SNAPSHOTS — audience telemetry
# ─────────────────────────────────────────────────────────────────────────────
def record_growth_snapshot(member_count, channel="telegram", source="bot_api"):
    """Store one member-count observation. Returns the row id."""
    conn = _connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO growth_snapshots (channel, member_count, source) VALUES (?, ?, ?)",
            (channel, int(member_count), source),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_growth_history(channel="telegram", limit=60):
    """Return recent snapshots oldest-first: [{member_count, captured_at}, ...]."""
    conn = _connection()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT member_count, captured_at FROM growth_snapshots
            WHERE channel = ? ORDER BY id DESC LIMIT ?
            """,
            (channel, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def get_growth_summary(channel="telegram"):
    """Current count + 1d/7d deltas computed from snapshots (None when empty)."""
    history = get_growth_history(channel, limit=60)
    if not history:
        return None
    current = history[-1]["member_count"]

    def _delta(days_back):
        target = len(history) - 1 - days_back * _snapshots_per_day()
        if days_back == 1:
            # Compare against the previous snapshot taken on an earlier day.
            for i in range(len(history) - 2, -1, -1):
                prev_date = str(history[i]["captured_at"])[:10]
                curr_date = str(history[-1]["captured_at"])[:10]
                if prev_date != curr_date:
                    return current - history[i]["member_count"]
            return 0
        idx = int(target)
        if idx < 0:
            idx = 0
        return current - history[idx]["member_count"]

    return {
        "current": current,
        "delta_24h": _delta(1),
        "delta_7d": _delta(7),
        "samples": len(history),
        "history": [{"date": str(h["captured_at"])[:10], "count": h["member_count"]} for h in history],
    }


def _snapshots_per_day():
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# NEWSLETTER — double opt-in subscribers (DPDP-consent friendly)
# ─────────────────────────────────────────────────────────────────────────────
_EMAIL_RE = None


def newsletter_subscribe(email):
    """
    Register/refresh an opt-in. Returns the confirm token (new or existing
    row re-tokened). Unconfirmed + previously unsubscribed rows start over.
    """
    import secrets

    email = (email or "").strip().lower()
    token = secrets.token_urlsafe(24)
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT id FROM newsletter_subscribers WHERE email = ?", (email,)
        ).fetchone()
        if row:
            cursor.execute(
                """
                UPDATE newsletter_subscribers
                SET token = ?, confirmed = 0, unsubscribed_at = NULL
                WHERE email = ?
                """,
                (token, email),
            )
        else:
            cursor.execute(
                "INSERT INTO newsletter_subscribers (email, token) VALUES (?, ?)",
                (email, token),
            )
        conn.commit()
        return token
    finally:
        conn.close()


def newsletter_confirm(token):
    """Mark a subscriber confirmed via their double-opt-in token."""
    conn = _connection()
    try:
        cur = conn.execute(
            "UPDATE newsletter_subscribers SET confirmed = 1 WHERE token = ?",
            (token,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def newsletter_unsubscribe(token):
    """Honor an unsubscribe instantly (one click, no login)."""
    from datetime import datetime as _dt

    conn = _connection()
    try:
        cur = conn.execute(
            "UPDATE newsletter_subscribers SET unsubscribed_at = CURRENT_TIMESTAMP "
            "WHERE token = ?",
            (token,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def newsletter_list_confirmed(limit=200):
    """Active recipients: confirmed, not unsubscribed (email + token for unsub links)."""
    conn = _connection()
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            """
            SELECT email, token FROM newsletter_subscribers
            WHERE confirmed = 1 AND unsubscribed_at IS NULL
            ORDER BY id LIMIT ?
            """,
            (limit,),
        ).fetchall()]
    finally:
        conn.close()


def newsletter_get_email(token):
    conn = _connection()
    try:
        row = conn.execute(
            "SELECT email FROM newsletter_subscribers WHERE token = ?", (token,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_top_published_deals(limit=8):
    """Best published campaigns for newsletter/repackage surfaces."""
    conn = _connection()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM campaigns WHERE status = 'published'
            ORDER BY deal_score DESC, created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────
def get_system_setting(key, default=""):
    """Read a single system setting value."""
    conn = _connection(timeout=5.0)
    try:
        row = conn.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_system_setting(key, value):
    """Upsert a single system setting value."""
    conn = _connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_operator_setting(key, default=""):
    """Read a single operator setting value."""
    conn = _connection(timeout=5.0)
    try:
        row = conn.execute("SELECT value FROM operator_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_operator_setting(key, value):
    """Upsert a single operator setting value."""
    conn = _connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO operator_settings (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_postback_secret():
    """Return the postback HMAC secret (operator override, else env)."""
    stored = get_operator_setting("postback_secret")
    if stored:
        return stored
    try:
        from config import POSTBACK_SECRET
        return POSTBACK_SECRET
    except ImportError:
        return "default_secret_key_123"


if __name__ == "__main__":
    setup_database()
    seed_admin_user()
    print("Database setup complete.")

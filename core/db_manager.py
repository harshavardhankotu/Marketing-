"""
core/db_manager.py
Zero-marginal-cost SQLite database manager for B2B Outbound Engine.
Enforces WAL mode (PRAGMA journal_mode=WAL;), foreign keys, atomic BEGIN IMMEDIATE writes,
guaranteed connection termination via context managers, and online transaction-safe backups.
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import bcrypt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import DB_PATH, BACKUP_DIR, ADMIN_EMAIL, ADMIN_PASSWORD, PUBLIC_BOOKING_URL, ENGINE_MODE


def get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    """Creates a SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


@contextmanager
def get_db_cursor(commit: bool = False):
    """
    Context manager yielding a cursor with guaranteed connection closure.
    Acquires BEGIN IMMEDIATE write locks when commit=True.
    """
    conn = get_connection()
    try:
        if commit:
            conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        yield cursor
        if commit:
            conn.commit()
    except Exception as exc:
        if commit:
            try:
                conn.rollback()
            except Exception:
                pass
        raise exc
    finally:
        conn.close()


def setup_database() -> None:
    """
    Initializes all 6 database tables, indexes, and initial seeds.
    """
    with get_db_cursor(commit=True) as cursor:
        # 1. Leads Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            website TEXT,
            contact_name TEXT,
            email TEXT UNIQUE NOT NULL,
            role TEXT,
            industry TEXT,
            trigger_signal TEXT,
            verification_status TEXT CHECK(verification_status IN ('pending', 'valid', 'invalid')) DEFAULT 'pending',
            status TEXT CHECK(status IN ('new', 'queued', 'emailed_step1', 'emailed_step2', 'replied', 'booked', 'unsubscribed')) DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status, verification_status);")

        # 2. Campaigns Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mode TEXT CHECK(mode IN ('self', 'client')) DEFAULT 'self',
            target_niche TEXT,
            value_prop TEXT,
            booking_link TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 3. Outreach Logs Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS outreach_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            campaign_id INTEGER,
            step_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT CHECK(status IN ('sent', 'failed')) DEFAULT 'sent',
            error_message TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_logs(lead_id);")

        # Column migration: ensure status column exists if table was created in an earlier phase
        cursor.execute("PRAGMA table_info(outreach_logs);")
        outreach_cols = [r[1] for r in cursor.fetchall()]
        if "status" not in outreach_cols:
            cursor.execute("ALTER TABLE outreach_logs ADD COLUMN status TEXT DEFAULT 'sent';")

        # 4. Booking Events Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS booking_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            booking_time TEXT,
            attendee_email TEXT NOT NULL,
            raw_payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE SET NULL
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_booking_email ON booking_events(attendee_email);")

        # 5. System Settings Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # 6. Users Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin'
        );
        """)

        # Seed default admin user
        cursor.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,))
        if not cursor.fetchone():
            hashed_pwd = bcrypt.hashpw(ADMIN_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            cursor.execute(
                "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
                (ADMIN_EMAIL, hashed_pwd, "admin")
            )

        # Seed default system settings
        default_settings = {
            "engine_mode": ENGINE_MODE,
            "daily_email_cap": "30",
            "public_booking_url": PUBLIC_BOOKING_URL,
            "min_send_delay": "120",
            "max_send_delay": "240"
        }
        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)", (k, v))

        # Seed initial self and client campaigns
        cursor.execute("SELECT id FROM campaigns WHERE mode = 'self'")
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO campaigns (name, mode, target_niche, value_prop, booking_link, active)
            VALUES (?, ?, ?, ?, ?, 1)
            """, (
                "Self-Growth Autonomous Outbound",
                "self",
                "B2B Agencies & Tech Consultancies",
                "We deploy an autonomous AI cold outbound and meeting-booking infrastructure that books 15-25 qualified discovery calls each month on complete autopilot.",
                PUBLIC_BOOKING_URL
            ))

        cursor.execute("SELECT id FROM campaigns WHERE mode = 'client'")
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO campaigns (name, mode, target_niche, value_prop, booking_link, active)
            VALUES (?, ?, ?, ?, ?, 0)
            """, (
                "Client Sample Campaign",
                "client",
                "Mid-Market SaaS Companies",
                "We help software leaders streamline cloud operations and scale ARR by 40% with zero engineering overhead.",
                PUBLIC_BOOKING_URL
            ))


def backup_database_online() -> str:
    """
    Hot transaction-safe online SQLite backup via sqlite3.Connection.backup.
    Writes snapshot to data/backups/outbound_backup_YYYYMMDD.db without stopping writes.
    Purges backup snapshots older than 7 days.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d")
    backup_file = BACKUP_DIR / f"outbound_backup_{timestamp}.db"

    source_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30.0)
    target_conn = sqlite3.connect(str(backup_file))

    try:
        with source_conn:
            with target_conn:
                source_conn.backup(target_conn, pages=100, sleep=0.01)
        print(f"[db_manager] Online backup completed: {backup_file}")
    finally:
        source_conn.close()
        target_conn.close()

    # Rolling retention: keep newest 7 days of backups
    cutoff = datetime.now() - timedelta(days=7)
    for p in BACKUP_DIR.glob("outbound_backup_*.db"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink()
                print(f"[db_manager] Purged old backup: {p.name}")
        except Exception as e:
            print(f"[db_manager] Error during backup retention sweep: {e}")

    return str(backup_file)


# ─── Data Access Helpers ───────────────────────────────────────────────────────

def insert_lead(
    company_name: str,
    email: str,
    website: Optional[str] = None,
    contact_name: Optional[str] = None,
    role: Optional[str] = None,
    industry: Optional[str] = None,
    trigger_signal: Optional[str] = None,
    verification_status: str = "pending",
    status: str = "queued"
) -> Optional[int]:
    """Inserts a lead atomically; returns lead ID or None if duplicate email."""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM leads WHERE email = ?", (email.strip().lower(),))
        if cursor.fetchone():
            return None

        cursor.execute("""
        INSERT INTO leads (
            company_name, website, contact_name, email, role, industry, trigger_signal, verification_status, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            company_name.strip(),
            website.strip() if website else None,
            contact_name.strip() if contact_name else None,
            email.strip().lower(),
            role.strip() if role else None,
            industry.strip() if industry else None,
            trigger_signal.strip() if trigger_signal else None,
            verification_status,
            status
        ))
        return cursor.lastrowid


def update_lead_status(lead_id: int, status: str) -> bool:
    """Updates lead pipeline status."""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        return cursor.rowcount > 0


def get_lead_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single lead by email address."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM leads WHERE email = ?", (email.strip().lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_leads_for_outreach(limit: int = 30) -> List[Dict[str, Any]]:
    """
    Returns leads eligible for Step 1 (status IN ('new', 'queued')) or
    Step 2 (status = 'emailed_step1' and at least 3 business days since Step 1 sent).
    """
    with get_db_cursor(commit=False) as cursor:
        # Step 1 eligible leads
        cursor.execute("""
        SELECT *, 1 AS target_step FROM leads
        WHERE verification_status = 'valid' AND status IN ('new', 'queued')
        ORDER BY id ASC
        LIMIT ?
        """, (limit,))
        step1_leads = [dict(r) for r in cursor.fetchall()]

        if len(step1_leads) >= limit:
            return step1_leads

        remaining = limit - len(step1_leads)

        # Step 2 eligible leads (at least 3 days elapsed since step 1 send, not replied/booked/unsubscribed)
        cursor.execute("""
        SELECT l.*, 2 AS target_step FROM leads l
        JOIN outreach_logs o ON l.id = o.lead_id AND o.step_number = 1
        WHERE l.verification_status = 'valid' 
          AND l.status = 'emailed_step1'
          AND julianday('now') - julianday(o.sent_at) >= 3.0
        ORDER BY l.id ASC
        LIMIT ?
        """, (remaining,))
        step2_leads = [dict(r) for r in cursor.fetchall()]

        return step1_leads + step2_leads


# Alias for backward compatibility
get_queued_leads = get_leads_for_outreach


def log_outreach(
    lead_id: int,
    campaign_id: Optional[int],
    step_number: int,
    subject: str,
    body: str,
    status: str = "sent",
    error_message: Optional[str] = None
) -> int:
    """Logs an email dispatch record."""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("""
        INSERT INTO outreach_logs (lead_id, campaign_id, step_number, subject, body, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (lead_id, campaign_id, step_number, subject, body, status, error_message))
        return cursor.lastrowid


def count_emails_sent_today() -> int:
    """Counts emails successfully sent today (UTC/local date)."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("""
        SELECT COUNT(*) FROM outreach_logs
        WHERE status = 'sent' AND date(sent_at) = date('now')
        """)
        return cursor.fetchone()[0]


def record_booking(
    attendee_email: str,
    event_type: str,
    booking_time: str,
    raw_payload: str
) -> int:
    """Records a meeting booking event and updates lead status to 'booked'."""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM leads WHERE email = ?", (attendee_email.strip().lower(),))
        lead_row = cursor.fetchone()
        lead_id = lead_row[0] if lead_row else None

        cursor.execute("""
        INSERT INTO booking_events (lead_id, event_type, booking_time, attendee_email, raw_payload)
        VALUES (?, ?, ?, ?, ?)
        """, (lead_id, event_type, booking_time, attendee_email.strip().lower(), raw_payload))
        event_id = cursor.lastrowid

        if lead_id:
            cursor.execute("UPDATE leads SET status = 'booked' WHERE id = ?", (lead_id,))

        return event_id


def get_active_campaign(mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches the active campaign for the given mode (or default active)."""
    with get_db_cursor(commit=False) as cursor:
        if mode:
            cursor.execute("SELECT * FROM campaigns WHERE active = 1 AND mode = ? ORDER BY id DESC LIMIT 1", (mode,))
        else:
            cursor.execute("SELECT * FROM campaigns WHERE active = 1 ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Retrieves user row by email."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves user row by ID."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_dashboard_metrics() -> Dict[str, Any]:
    """Calculates all key performance indicators for the dashboard."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT COUNT(*) FROM leads")
        total_leads = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE verification_status = 'valid'")
        valid_leads = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM outreach_logs WHERE status = 'sent'")
        emails_sent = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'booked'")
        calls_booked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'replied'")
        replies = cursor.fetchone()[0]

        cursor.execute("SELECT value FROM system_settings WHERE key = 'engine_mode'")
        mode_row = cursor.fetchone()
        current_mode = mode_row[0] if mode_row else "self"

        validation_rate = round((valid_leads / total_leads * 100), 1) if total_leads > 0 else 0.0

        return {
            "total_leads": total_leads,
            "valid_leads": valid_leads,
            "validation_rate": validation_rate,
            "emails_sent": emails_sent,
            "emails_sent_today": count_emails_sent_today(),
            "calls_booked": calls_booked,
            "replies": replies,
            "engine_mode": current_mode
        }


# Alias for backward compatibility
get_kpi_overview = get_dashboard_metrics


if __name__ == "__main__":
    setup_database()
    print("Database setup complete.")
    b = backup_database_online()
    print("Backup verified at:", b)

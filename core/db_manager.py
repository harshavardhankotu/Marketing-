"""
core/db_manager.py
Thread-safe, WAL-enabled SQLite database manager for Autonomous B2B Lead Generation & Call-Booking Engine.
Enforces strict foreign keys, atomic BEGIN IMMEDIATE writes, and guaranteed connection termination.
"""

import sqlite3
import sys
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional
import bcrypt

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import DB_PATH, ADMIN_EMAIL, ADMIN_PASSWORD, PUBLIC_BOOKING_URL, ENGINE_MODE


def get_connection(timeout: float = 30.0) -> sqlite3.Connection:
    """
    Creates and configures a SQLite connection with WAL mode and foreign keys enabled.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


@contextmanager
def get_db_cursor(commit: bool = False):
    """
    Context manager that yields a cursor and guarantees connection closure.
    If commit is True, begins an IMMEDIATE transaction and commits upon success.
    """
    conn = get_connection()
    try:
        if commit:
            conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        yield cursor
        if commit:
            conn.commit()
    except Exception as e:
        if commit:
            conn.rollback()
        raise e
    finally:
        conn.close()


def setup_database() -> None:
    """
    Initializes the database schema with strict integrity constraints,
    foreign keys, indices, and default seed configurations.
    """
    with get_db_cursor(commit=True) as cursor:
        # 1. Leads table
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

        # Indices for fast queries and pipeline filters
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status, verification_status);")

        # 2. Campaigns table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mode TEXT CHECK(mode IN ('self', 'client')) DEFAULT 'self',
            target_niche TEXT,
            value_prop TEXT,
            booking_link TEXT,
            step1_prompt TEXT,
            step2_prompt TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 3. Outreach Logs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS outreach_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            campaign_id INTEGER,
            step_number INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_id TEXT,
            error_message TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL
        );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_logs(lead_id);")

        # 4. Booking Events table
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

        # 5. System Settings table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # 6. Users table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin'
        );
        """)

        # Seed default admin user if absent
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
            "smtp_configured": "0"
        }
        for k, v in default_settings.items():
            cursor.execute(
                "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)",
                (k, v)
            )

        # Seed default primary campaign
        cursor.execute("SELECT id FROM campaigns WHERE name = ?", ("Default Self-Growth Campaign",))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO campaigns (name, mode, target_niche, value_prop, booking_link, step1_prompt, step2_prompt, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                "Default Self-Growth Campaign",
                "self",
                "B2B Agencies & SaaS",
                "We install autonomous AI lead generation & call booking engines that book 15-25 qualified calls/month.",
                PUBLIC_BOOKING_URL,
                "Write an ultra-concise 3-4 sentence cold email based on the company's trigger signal.",
                "Write a 2-sentence follow-up asking if solving their growth bottleneck is currently a priority.",
            ))


# ─── Lead Management Helpers ──────────────────────────────────────────────────

def insert_lead(
    company_name: str,
    email: str,
    website: Optional[str] = None,
    contact_name: Optional[str] = None,
    role: Optional[str] = None,
    industry: Optional[str] = None,
    trigger_signal: Optional[str] = None,
    verification_status: str = "pending"
) -> Optional[int]:
    """
    Inserts a newly scraped/discovered lead safely with transactional write-locks.
    Returns the lead ID if inserted, or None if the email already exists.
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("SELECT id FROM leads WHERE email = ?", (email.strip().lower(),))
        if cursor.fetchone():
            return None

        cursor.execute("""
        INSERT INTO leads (company_name, website, contact_name, email, role, industry, trigger_signal, verification_status, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """, (
            company_name.strip(),
            website.strip() if website else None,
            contact_name.strip() if contact_name else None,
            email.strip().lower(),
            role.strip() if role else None,
            industry.strip() if industry else None,
            trigger_signal.strip() if trigger_signal else None,
            verification_status
        ))
        return cursor.lastrowid


def update_lead_status(lead_id: int, status: str) -> bool:
    """
    Updates the outreach status of a lead.
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        return cursor.rowcount > 0


def update_lead_verification(lead_id: int, verification_status: str) -> bool:
    """
    Updates the email verification status of a lead ('valid', 'invalid', 'pending').
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE leads SET verification_status = ? WHERE id = ?",
            (verification_status, lead_id)
        )
        return cursor.rowcount > 0


def get_lead_by_id(lead_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieves a single lead record by ID.
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_queued_leads(limit: int = 30) -> List[Dict[str, Any]]:
    """
    Fetches leads that are verified as valid and ready for step 1 or step 2 outreach.
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("""
        SELECT * FROM leads 
        WHERE verification_status = 'valid' AND status IN ('new', 'queued', 'emailed_step1')
        ORDER BY created_at ASC
        LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


# ─── Campaign & Outreach Helpers ──────────────────────────────────────────────

def get_active_campaign(mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves the currently active outreach campaign.
    """
    with get_db_cursor(commit=False) as cursor:
        if mode:
            cursor.execute("SELECT * FROM campaigns WHERE active = 1 AND mode = ? ORDER BY id DESC LIMIT 1", (mode,))
        else:
            cursor.execute("SELECT * FROM campaigns WHERE active = 1 ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None


def log_outreach_attempt(
    lead_id: int,
    step_number: int,
    subject: str,
    body: str,
    campaign_id: Optional[int] = None,
    message_id: Optional[str] = None,
    error_message: Optional[str] = None
) -> int:
    """
    Records an email dispatch attempt in outreach_logs with atomic lock.
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("""
        INSERT INTO outreach_logs (lead_id, campaign_id, step_number, subject, body, message_id, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (lead_id, campaign_id, step_number, subject, body, message_id, error_message))
        return cursor.lastrowid


def count_emails_sent_today() -> int:
    """
    Calculates the total successful emails dispatched in the current UTC calendar day.
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("""
        SELECT COUNT(*) FROM outreach_logs
        WHERE error_message IS NULL AND date(sent_at) = date('now')
        """)
        return cursor.fetchone()[0]


# ─── Booking Gateway Helpers ──────────────────────────────────────────────────

def record_booking_event(
    attendee_email: str,
    event_type: str,
    booking_time: Optional[str] = None,
    raw_payload: Optional[str] = None
) -> int:
    """
    Processes incoming Cal.com / Calendly booking webhooks.
    Associates the attendee email with a lead if found, updates status to 'booked'.
    """
    with get_db_cursor(commit=True) as cursor:
        # Find lead if already in database
        cursor.execute("SELECT id FROM leads WHERE email = ?", (attendee_email.strip().lower(),))
        lead_row = cursor.fetchone()
        lead_id = lead_row[0] if lead_row else None

        # Insert booking record
        cursor.execute("""
        INSERT INTO booking_events (lead_id, event_type, booking_time, attendee_email, raw_payload)
        VALUES (?, ?, ?, ?, ?)
        """, (lead_id, event_type, booking_time, attendee_email.strip().lower(), raw_payload))
        booking_id = cursor.lastrowid

        # Update lead status if matched
        if lead_id:
            cursor.execute("UPDATE leads SET status = 'booked' WHERE id = ?", (lead_id,))

        return booking_id


# ─── Analytics & KPI Overview ─────────────────────────────────────────────────

def get_kpi_overview() -> Dict[str, Any]:
    """
    Computes real-time conversion and performance metrics for the dashboard.
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute("SELECT COUNT(*) FROM leads")
        total_leads = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE verification_status = 'valid'")
        valid_leads = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM outreach_logs WHERE error_message IS NULL")
        emails_sent = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'replied'")
        replied_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'booked'")
        booked_count = cursor.fetchone()[0]

        cursor.execute("SELECT value FROM system_settings WHERE key = 'engine_mode'")
        engine_mode = cursor.fetchone()
        mode_val = engine_mode[0] if engine_mode else "self"

        return {
            "total_leads": total_leads,
            "valid_leads": valid_leads,
            "emails_sent": emails_sent,
            "replies": replied_count,
            "booked_calls": booked_count,
            "engine_mode": mode_val,
            "emails_sent_today": count_emails_sent_today()
        }


if __name__ == "__main__":
    setup_database()
    print("Database schema successfully verified and initialized.")

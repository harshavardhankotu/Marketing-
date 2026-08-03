"""
Idempotency guards for webhook conversion processing.

Two layers of protection are applied to every inbound postback:

1. **Signature validation** — HMAC-SHA256 over the raw request body using the
   shared ``POSTBACK_SECRET``. Prevents forgery.
2. **Transaction idempotency** — the ``transaction_id`` is stored in the
   ``idempotency_keys`` table with a PRIMARY KEY. Duplicate deliveries from
   network retries are rejected atomically.

The exact string sent by the network is matched (canonicalized by stripping
whitespace) so identical retries are deduplicated, while genuinely distinct
transactions always pass.
"""

import hmac
import hashlib
import sqlite3
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH  # noqa: E402


class IdempotencyError(Exception):
    """Raised when an operation is a duplicate of a previously processed one."""


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """
    Validate an HMAC-SHA256 signature over the raw request body.

    Uses ``hmac.compare_digest`` to avoid timing side-channels.
    """
    if not signature or not raw_body:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def _canonical_transaction_id(transaction_id: str) -> str:
    """Normalise a transaction ID so whitespace differences don't dup-cancel."""
    return " ".join(str(transaction_id).strip().split())


def check_and_mark(transaction_id: str, event_type: str = "conversion"):
    """
    Atomically check-and-mark a transaction ID as processed.

    Returns True when the transaction is NEW (and now locked), False when it
    was already processed (idempotent duplicate).
    """
    canonical = _canonical_transaction_id(transaction_id)
    if not canonical:
        raise IdempotencyError("Empty transaction_id cannot be idempotency-guarded")

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM idempotency_keys WHERE transaction_id = ?", (canonical,))
        if cursor.fetchone():
            conn.rollback()
            return False
        cursor.execute(
            "INSERT INTO idempotency_keys (transaction_id, event_type) VALUES (?, ?)",
            (canonical, event_type),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Raced with another writer — the duplicate already committed.
        conn.rollback()
        return False
    finally:
        conn.close()


def is_processed(transaction_id: str) -> bool:
    """Return True when a transaction ID has already been processed."""
    canonical = _canonical_transaction_id(transaction_id)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        row = conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE transaction_id = ?", (canonical,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def release(transaction_id: str) -> bool:
    """Manually release an idempotency lock (used by tests / operators)."""
    canonical = _canonical_transaction_id(transaction_id)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        conn.execute("DELETE FROM idempotency_keys WHERE transaction_id = ?", (canonical,))
        conn.commit()
        return True
    finally:
        conn.close()

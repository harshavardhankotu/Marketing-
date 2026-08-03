"""
Enterprise resilience layer — daily API quota caps + stateful circuit breakers.

For each external provider we enforce:
    * a daily hard cap (``api_quota_usage`` table) and
    * a persistent circuit breaker (``circuit_breaker_state`` table) that
      trips to ``OPEN`` after a configurable consecutive-failure threshold
      (default: any 5xx family failures) and only returns to ``CLOSED`` after
      a cooldown period or manual reset.

Every external call should pass through ``breaker_call(provider, fn)`` so that
failures are recorded and OPEN circuits short-circuit before spending money
or hitting rate limits.
"""

import os
import sys
import time
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH  # noqa: E402


class QuotaExceededException(Exception):
    """Raised when a provider's daily hard cap would be exceeded."""


class CircuitBreakerOpenException(Exception):
    """Raised when a provider's circuit breaker is currently OPEN."""


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT LIMITS (overridable via environment variables)
# ─────────────────────────────────────────────────────────────────────────────
QUOTA_LIMITS = {
    "gemini": {
        "hard_cap": int(os.getenv("QUOTA_GEMINI_CAP", "150")),
        "warning_threshold": int(os.getenv("QUOTA_GEMINI_WARN", "120")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_GEMINI_COOLDOWN", "300")),
    },
    "telegram": {
        "hard_cap": int(os.getenv("QUOTA_TELEGRAM_CAP", "300")),
        "warning_threshold": int(os.getenv("QUOTA_TELEGRAM_WARN", "240")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_TELEGRAM_COOLDOWN", "300")),
    },
    "amazon_paapi": {
        "hard_cap": int(os.getenv("QUOTA_PAAPI_CAP", "7200")),
        "warning_threshold": int(os.getenv("QUOTA_PAAPI_WARN", "6500")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_PAAPI_COOLDOWN", "300")),
    },
    "twitter": {
        "hard_cap": int(os.getenv("QUOTA_TWITTER_CAP", "50")),
        "warning_threshold": int(os.getenv("QUOTA_TWITTER_WARN", "40")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_TWITTER_COOLDOWN", "300")),
    },
    "instagram": {
        "hard_cap": int(os.getenv("QUOTA_INSTAGRAM_CAP", "50")),
        "warning_threshold": int(os.getenv("QUOTA_INSTAGRAM_WARN", "40")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_INSTAGRAM_COOLDOWN", "300")),
    },
    "meta": {
        "hard_cap": int(os.getenv("QUOTA_META_CAP", "50")),
        "warning_threshold": int(os.getenv("QUOTA_META_WARN", "40")),
        "failure_threshold": 5,
        "cooldown_sec": int(os.getenv("CB_META_COOLDOWN", "300")),
    },
}


def _conn():
    return sqlite3.connect(DB_PATH, timeout=30.0)


def _today():
    return datetime.utcnow().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# QUOTA MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
def get_quota_usage(provider):
    """Return today's request count for a provider (seeding the row lazily)."""
    conn = _conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT request_count FROM api_quota_usage WHERE provider = ? AND usage_date = ?",
            (provider, _today()),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor.execute(
            "INSERT OR IGNORE INTO api_quota_usage (provider, usage_date, request_count) VALUES (?, ?, 0)",
            (provider, _today()),
        )
        conn.commit()
        return 0
    except sqlite3.Error as exc:
        print(f"[QUOTA_MANAGER] Error reading quota for {provider}: {exc}")
        return 0
    finally:
        conn.close()


def check_quota(provider):
    """Return 'OK' | 'WARNING' | 'BLOCKED' for today's quota on a provider."""
    if provider not in QUOTA_LIMITS:
        return "OK"
    usage = get_quota_usage(provider)
    limits = QUOTA_LIMITS[provider]
    if usage >= limits["hard_cap"]:
        return "BLOCKED"
    if usage >= limits["warning_threshold"]:
        return "WARNING"
    return "OK"


def consume_quota(provider, count=1, force=False):
    """
    Increment a provider's daily usage counter.
    Raises :class:`QuotaExceededException` unless ``force`` is True.
    Returns a summary dict.
    """
    if provider not in QUOTA_LIMITS:
        return {"status": "OK", "usage": 0, "cap": 10**9}

    limits = QUOTA_LIMITS[provider]
    usage = get_quota_usage(provider)
    if usage + count > limits["hard_cap"] and not force:
        raise QuotaExceededException(
            f"Daily quota limit exceeded for '{provider}'. "
            f"Usage: {usage}/{limits['hard_cap']}. Attempted increment: {count}"
        )

    conn = _conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_quota_usage (provider, usage_date, request_count)
            VALUES (?, ?, ?)
            ON CONFLICT(provider, usage_date) DO UPDATE SET request_count = request_count + ?
            """,
            (provider, _today(), count, count),
        )
        conn.commit()
    except sqlite3.Error as exc:
        print(f"[QUOTA_MANAGER] Error incrementing quota for {provider}: {exc}")
    finally:
        conn.close()

    new_usage = usage + count
    status = "OK"
    if new_usage >= limits["hard_cap"]:
        status = "BLOCKED"
    elif new_usage >= limits["warning_threshold"]:
        status = "WARNING"
    return {"status": status, "usage": new_usage, "cap": limits["hard_cap"]}


def reset_quota(provider):
    """Reset today's quota counter for a provider."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE api_quota_usage SET request_count = 0 WHERE provider = ? AND usage_date = ?",
            (provider, _today()),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        print(f"[QUOTA_MANAGER] Failed to reset quota for {provider}: {exc}")
        return False
    finally:
        conn.close()


def get_all_quotas():
    """Return a summary of every configured provider quota."""
    summary = {}
    for provider, limits in QUOTA_LIMITS.items():
        usage = get_quota_usage(provider)
        status = "OK"
        if usage >= limits["hard_cap"]:
            status = "BLOCKED"
        elif usage >= limits["warning_threshold"]:
            status = "WARNING"
        summary[provider] = {
            "usage": usage,
            "hard_cap": limits["hard_cap"],
            "warning_threshold": limits["warning_threshold"],
            "status": status,
        }
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKERS
# ─────────────────────────────────────────────────────────────────────────────
def _breaker_row(provider):
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO circuit_breaker_state (provider, state, failure_count, success_count) "
            "VALUES (?, 'CLOSED', 0, 0)",
            (provider,),
        )
        conn.commit()
        return conn.execute(
            "SELECT state, failure_count, success_count, last_failure, tripped_at "
            "FROM circuit_breaker_state WHERE provider = ?",
            (provider,),
        ).fetchone()
    finally:
        conn.close()


def _breaker_limits(provider):
    limits = QUOTA_LIMITS.get(provider, {})
    return (
        limits.get("failure_threshold", 5),
        limits.get("cooldown_sec", 300),
    )


def get_breaker_state(provider):
    """Return the current circuit breaker state dict for a provider."""
    row = _breaker_row(provider)
    return {
        "provider": provider,
        "state": row[0],
        "failure_count": row[1],
        "success_count": row[2],
        "last_failure": row[3],
        "tripped_at": row[4],
    }


def check_breaker(provider):
    """
    Raise :class:`CircuitBreakerOpenException` if the provider circuit is OPEN.
    Automatically transitions HALF_OPEN/OPEN back to CLOSED after cooldown.
    """
    state = get_breaker_state(provider)
    if state["state"] == "OPEN":
        _, cooldown = _breaker_limits(provider)
        tripped_at = state["tripped_at"]
        if tripped_at:
            try:
                tripped_dt = datetime.strptime(str(tripped_at)[:19], "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() - tripped_dt > timedelta(seconds=cooldown):
                    _set_breaker_state(provider, "CLOSED", failure_count=0)
                    print(f"[CIRCUIT_BREAKER] {provider} cooldown elapsed. Reopened to CLOSED.")
                    return
            except (ValueError, TypeError):
                pass
        raise CircuitBreakerOpenException(f"Circuit breaker OPEN for '{provider}'")
    if state["state"] == "HALF_OPEN":
        raise CircuitBreakerOpenException(f"Circuit breaker HALF_OPEN for '{provider}'")


def _set_breaker_state(provider, state, failure_count=None, success_count=None):
    conn = _conn()
    try:
        params = [state]
        if failure_count is not None:
            params.append(failure_count)
        if success_count is not None:
            params.append(success_count)
        params.append(provider)
        conn.execute(
            f"""
            UPDATE circuit_breaker_state
            SET state = ?, {', failure_count = ?' if failure_count is not None else ''}{', success_count = ?' if success_count is not None else ''},
                last_failure = CASE WHEN ? = 'OPEN' OR ? = 'HALF_OPEN' THEN CURRENT_TIMESTAMP ELSE last_failure END,
                tripped_at = CASE WHEN ? = 'OPEN' OR ? = 'HALF_OPEN' THEN CURRENT_TIMESTAMP ELSE tripped_at END
            WHERE provider = ?
            """,
            (*params, state, state, state, state, provider),
        )
        conn.commit()
    finally:
        conn.close()


def record_breaker_success(provider):
    """Record a successful call, resetting failure counts."""
    _set_breaker_state(provider, "CLOSED", failure_count=0)


def record_breaker_failure(provider):
    """
    Record a failed call. Trips the breaker to OPEN once the consecutive
    failure threshold is reached.
    """
    failure_threshold, _ = _breaker_limits(provider)
    state = get_breaker_state(provider)
    new_failures = state["failure_count"] + 1

    conn = _conn()
    try:
        new_state = "OPEN" if new_failures >= failure_threshold else state["state"]
        conn.execute(
            """
            UPDATE circuit_breaker_state
            SET state = ?, failure_count = ?, last_failure = CURRENT_TIMESTAMP,
                tripped_at = CASE WHEN ? = 'OPEN' THEN CURRENT_TIMESTAMP ELSE tripped_at END
            WHERE provider = ?
            """,
            (new_state, new_failures, new_state, provider),
        )
        conn.commit()
        if new_state == "OPEN":
            print(f"[CIRCUIT_BREAKER] {provider} TRIPPED OPEN after {new_failures} consecutive failures.")
        return new_state
    finally:
        conn.close()


def reset_breaker(provider):
    """Manually reset a circuit breaker to CLOSED with zero failures."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE circuit_breaker_state SET state = 'CLOSED', failure_count = 0, success_count = 0, tripped_at = NULL "
            "WHERE provider = ?",
            (provider,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_all_breakers():
    """Return every registered circuit breaker's state."""
    return [get_breaker_state(provider) for provider in QUOTA_LIMITS]


def is_provider_blocked(provider):
    """True when a provider is either quota-blocked or breaker-open."""
    if check_quota(provider) == "BLOCKED":
        return True
    try:
        check_breaker(provider)
        return False
    except CircuitBreakerOpenException:
        return True


def breaker_call(provider, fn, *args, **kwargs):
    """
    Execute ``fn`` behind the resilience shield.
        * short-circuits when quota BLOCKED or circuit OPEN,
        * records success/failure, tripping OPEN on 5xx-style exceptions.

    ``fn`` should raise :class:`CircuitBreakerOpenException` or any Exception
    for it to count as a failure.
    """
    if check_quota(provider) == "BLOCKED":
        raise QuotaExceededException(f"Daily quota blocked for '{provider}'")
    check_breaker(provider)
    try:
        result = fn(*args, **kwargs)
        record_breaker_success(provider)
        return result
    except CircuitBreakerOpenException:
        raise
    except QuotaExceededException:
        raise
    except Exception as exc:
        record_breaker_failure(provider)
        raise

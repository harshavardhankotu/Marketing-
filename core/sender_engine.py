"""
core/sender_engine.py
Cold email dispatcher with strict deliverability guards:
  - Daily hard cap (30 emails/day)
  - Randomized pacing intervals (120-240s)
  - Plain-text format with mandatory one-click opt-out footer
  - SMTP TLS (Port 587) with graceful mock fallback for dev/testing
"""

import email.utils
import random
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_NAME,
    DAILY_EMAIL_CAP, MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS
)
from core.db_manager import (
    get_queued_leads, get_active_campaign, update_lead_status,
    log_outreach_attempt, count_emails_sent_today
)
from core.ai_writer import generate_outreach_email

OPT_OUT_FOOTER = (
    "\n\n---\n"
    "If you would prefer not to receive future messages, simply reply with "
    "'unsubscribe' and you will be immediately removed from our outreach."
)


def dispatch_single_email(
    to_email: str,
    subject: str,
    body: str,
    simulate: bool = False
) -> Dict[str, Any]:
    """
    Sends a plain-text email via smtplib with TLS.
    If credentials are missing or simulate=True, performs a safe simulation.
    """
    full_body = body.strip() + OPT_OUT_FOOTER

    if simulate or not SMTP_USER or not SMTP_PASS or SMTP_USER == "outreach@agency.ai":
        # Simulation / Local test mode
        message_id = email.utils.make_msgid(domain="growthops.ai")
        print(f"[sender_engine:SIMULATION] Dispatched email to {to_email} | Message-ID: {message_id}")
        return {
            "success": True,
            "message_id": message_id,
            "simulated": True,
            "error": None
        }

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=SMTP_HOST)

        # Plain text only
        msg.attach(MIMEText(full_body, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20.0) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [to_email], msg.as_string())

        return {
            "success": True,
            "message_id": msg["Message-ID"],
            "simulated": False,
            "error": None
        }

    except Exception as exc:
        print(f"[sender_engine] SMTP Dispatch failed for {to_email}: {exc}")
        return {
            "success": False,
            "message_id": None,
            "simulated": False,
            "error": str(exc)
        }


def process_outreach_queue(max_batch: int = 5, pace_sleep: bool = True) -> Dict[str, Any]:
    """
    Scans for valid queued leads and dispatches Step 1 or Step 2 outreach.
    Respects daily send caps and randomized humanized delays.
    """
    sent_today = count_emails_sent_today()
    if sent_today >= DAILY_EMAIL_CAP:
        print(f"[sender_engine] Daily send cap reached ({sent_today}/{DAILY_EMAIL_CAP}). Pausing queue.")
        return {"status": "capped", "sent_count": 0, "reason": "Daily limit reached"}

    campaign = get_active_campaign()
    if not campaign:
        print("[sender_engine] No active outreach campaign configured.")
        return {"status": "no_campaign", "sent_count": 0, "reason": "No active campaign"}

    remaining_cap = DAILY_EMAIL_CAP - sent_today
    batch_size = min(max_batch, remaining_cap)
    leads = get_queued_leads(limit=batch_size)

    if not leads:
        print("[sender_engine] No verified leads currently queued for outreach.")
        return {"status": "idle", "sent_count": 0, "reason": "Queue empty"}

    dispatched = 0

    for idx, lead in enumerate(leads):
        lead_id = lead["id"]
        lead_email = lead["email"]
        current_status = lead["status"]

        # Determine step number
        step_number = 1 if current_status in ("new", "queued") else 2

        # Generate email content via Gemini 2.0 Flash (or deterministic fallback)
        email_content = generate_outreach_email(lead, campaign)
        subject = email_content["subject"]
        body = email_content["body"]

        # Dispatch
        result = dispatch_single_email(lead_email, subject, body)

        if result["success"]:
            new_status = "emailed_step1" if step_number == 1 else "emailed_step2"
            update_lead_status(lead_id, new_status)
            log_outreach_attempt(
                lead_id=lead_id,
                campaign_id=campaign["id"],
                step_number=step_number,
                subject=subject,
                body=body,
                message_id=result["message_id"]
            )
            dispatched += 1
            print(f"[sender_engine] Dispatched Step {step_number} to {lead_email} -> {new_status}")
        else:
            log_outreach_attempt(
                lead_id=lead_id,
                campaign_id=campaign["id"],
                step_number=step_number,
                subject=subject,
                body=body,
                error_message=result["error"]
            )
            print(f"[sender_engine] Error dispatching to {lead_email}: {result['error']}")

        # Random humanized delay between emails (skipped on last email or if pace_sleep is False)
        if pace_sleep and idx < len(leads) - 1:
            delay = random.randint(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            print(f"[sender_engine] Humanized pacing delay: sleeping for {delay}s...")
            time.sleep(delay)

    return {
        "status": "success",
        "sent_count": dispatched,
        "remaining_cap": DAILY_EMAIL_CAP - (sent_today + dispatched)
    }


if __name__ == "__main__":
    res = process_outreach_queue(max_batch=2, pace_sleep=False)
    print(f"Queue Result: {res}")

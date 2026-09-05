"""
core/sender_engine.py
Deliverability-optimized humanized cold email dispatcher.
Enforces:
  - Daily hard cap (30 emails/day per SMTP account)
  - Randomized pacing delay (120-240 seconds between sends)
  - Strict plain-text formatting (MIMEText 'plain') with zero tracking pixels or redirects
  - Authenticated SMTP submission via Port 587 (STARTTLS) or Port 465 (SSL)
  - Step 1 and Step 2 cadence handling
"""

import email.utils
import os
import random
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, FROM_NAME,
    DAILY_EMAIL_CAP, MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS
)
from core.db_manager import (
    get_leads_for_outreach, get_active_campaign, update_lead_status,
    log_outreach, count_emails_sent_today
)
from core.ai_writer import generate_outreach_email


def send_smtp_plain_email(
    to_email: str,
    subject: str,
    body: str,
    simulate: bool = False
) -> Dict[str, Any]:
    """
    Dispatches a single plain-text cold email via smtplib.
    Supports STARTTLS on Port 587 or SSL on Port 465.
    Falls back to safe mock simulation in test/local environments.
    """
    # Safe simulation mode if credentials are unconfigured or simulate=True
    if simulate or not SMTP_USER or not SMTP_PASS or "brevo" in SMTP_HOST and SMTP_USER == "your_smtp_user":
        msg_id = email.utils.make_msgid(domain="outbound.local")
        print(f"[sender_engine:MOCK] Dispatched email to {to_email} | Subject: '{subject}' | MsgID: {msg_id}")
        return {
            "success": True,
            "message_id": msg_id,
            "simulated": True,
            "error": None
        }

    try:
        # Construct RFC-compliant plain-text email with no HTML parts
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=SMTP_HOST)

        # Port 465 (SSL) vs Port 587 (STARTTLS)
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=25.0) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25.0) as server:
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
        print(f"[sender_engine] SMTP dispatch error to {to_email}: {exc}")
        return {
            "success": False,
            "message_id": None,
            "simulated": False,
            "error": str(exc)
        }


def process_outreach_queue(
    max_batch: int = 5,
    pace_sleep: bool = True,
    simulate_force: bool = False
) -> Dict[str, Any]:
    """
    Executes the outbound dispatch cycle:
      - Validates daily email hard cap (30/day)
      - Queries eligible Step 1 and Step 2 prospects
      - Renders AI proof-of-work copy
      - Dispatches via SMTP
      - Applies randomized pacing delay (120-240 seconds)
      - Updates lead pipeline stages
    """
    sent_today = count_emails_sent_today()
    if sent_today >= DAILY_EMAIL_CAP:
        print(f"[sender_engine] Daily limit reached ({sent_today}/{DAILY_EMAIL_CAP}). Halting queue.")
        return {
            "status": "capped",
            "sent_count": 0,
            "sent_today": sent_today,
            "daily_cap": DAILY_EMAIL_CAP,
            "reason": f"Daily hard cap of {DAILY_EMAIL_CAP} emails reached"
        }

    campaign = get_active_campaign()
    if not campaign:
        print("[sender_engine] No active campaign found.")
        return {"status": "no_campaign", "sent_count": 0, "reason": "No active campaign"}

    remaining_cap = DAILY_EMAIL_CAP - sent_today
    batch_limit = min(max_batch, remaining_cap)
    leads = get_leads_for_outreach(limit=batch_limit)

    if not leads:
        print("[sender_engine] No leads currently eligible for outreach.")
        return {"status": "idle", "sent_count": 0, "reason": "Outreach queue empty"}

    dispatched = 0
    results: List[Dict[str, Any]] = []

    for idx, lead in enumerate(leads):
        lead_id = lead["id"]
        to_email = lead["email"]
        target_step = lead.get("target_step", 1)

        # Generate personalized 4-sentence copy
        email_pack = generate_outreach_email(lead, campaign)
        subject = email_pack["subject"]
        body = email_pack["body"]

        # Dispatch via SMTP
        send_result = send_smtp_plain_email(
            to_email=to_email,
            subject=subject,
            body=body,
            simulate=simulate_force
        )

        if send_result["success"]:
            new_status = "emailed_step1" if target_step == 1 else "emailed_step2"
            update_lead_status(lead_id, new_status)
            log_outreach(
                lead_id=lead_id,
                campaign_id=campaign["id"],
                step_number=target_step,
                subject=subject,
                body=body,
                status="sent"
            )
            dispatched += 1
            results.append({
                "lead_id": lead_id,
                "email": to_email,
                "step": target_step,
                "status": "sent",
                "simulated": send_result.get("simulated", False)
            })
            print(f"[sender_engine] Sent Step {target_step} to {to_email} -> {new_status}")
        else:
            log_outreach(
                lead_id=lead_id,
                campaign_id=campaign["id"],
                step_number=target_step,
                subject=subject,
                body=body,
                status="failed",
                error_message=send_result["error"]
            )
            results.append({
                "lead_id": lead_id,
                "email": to_email,
                "step": target_step,
                "status": "failed",
                "error": send_result["error"]
            })
            print(f"[sender_engine] Failed to send Step {target_step} to {to_email}: {send_result['error']}")

        # Apply randomized human pacing delay between sends
        if pace_sleep and idx < len(leads) - 1:
            delay = random.randint(MIN_SEND_DELAY_SECONDS, MAX_SEND_DELAY_SECONDS)
            print(f"[sender_engine] Pacing sleep: waiting {delay} seconds before next send...")
            time.sleep(delay)

    return {
        "status": "success",
        "sent_count": dispatched,
        "sent_today": sent_today + dispatched,
        "remaining_cap": DAILY_EMAIL_CAP - (sent_today + dispatched),
        "dispatched_leads": results
    }


if __name__ == "__main__":
    report = process_outreach_queue(max_batch=1, pace_sleep=False, simulate_force=True)
    print("Dispatch cycle complete:", report)

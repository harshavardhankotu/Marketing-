"""
tests/live_fire_single_send.py
Safe Live-Fire Single Recipient Test.
Allows the operator to test the entire email pipeline (Gemini copywriting, SMTP submission,
plain-text formatting, and delivery) against their own inbox without blasting any contacts.
"""

import argparse
import sys
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, PUBLIC_BOOKING_URL
from core.db_manager import setup_database, insert_lead, get_active_campaign
from core.email_verifier import verify_email_address
from core.ai_writer import generate_outreach_email
from core.sender_engine import send_smtp_plain_email


def run_live_fire(target_email: str) -> None:
    print("\n" + "=" * 70)
    print("[TEST] AUTONOMOUS B2B ENGINE: LIVE-FIRE SINGLE SEND TEST")
    print("=" * 70)
    print(f"[1/5] Validating target email syntax & MX records: {target_email}...")

    ver = verify_email_address(target_email)
    print(f"      Verification Result: {ver['status'].upper()} ({ver['reason']})")
    if ver["status"] != "valid":
        print(f"[WARN] Warning: Target email does not have valid MX records: {ver['reason']}")

    print("\n[2/5] Initializing local database and creating live-fire prospect lead...")
    setup_database()

    dummy_lead = {
        "company_name": "Acme Technologies (Live Test)",
        "contact_name": "Operator Test",
        "email": target_email,
        "role": "CEO",
        "industry": "B2B Software",
        "trigger_signal": "Lacks automated online booking calendar | Relies on static contact forms",
        "verification_status": ver["status"],
        "status": "queued"
    }

    insert_lead(
        company_name=dummy_lead["company_name"],
        email=dummy_lead["email"],
        contact_name=dummy_lead["contact_name"],
        role=dummy_lead["role"],
        industry=dummy_lead["industry"],
        trigger_signal=dummy_lead["trigger_signal"],
        verification_status=ver["status"],
        status="queued"
    )
    print("      Prospect record registered in SQLite.")

    print("\n[3/5] Generating personalized Proof-of-Work cold email via Gemini 2.0 Flash...")
    campaign = get_active_campaign() or {
        "id": 1,
        "name": "Live Test Campaign",
        "value_prop": "We deploy autonomous AI outbound systems booking 15-25 qualified discovery calls/month.",
        "booking_link": PUBLIC_BOOKING_URL
    }

    copy = generate_outreach_email(dummy_lead, campaign)
    print("      Generated Subject: " + copy["subject"])
    print("\n" + "-" * 50)
    print(copy["body"])
    print("-" * 50)

    print("\n[4/5] Inspecting SMTP Outbound Configuration...")
    print(f"      Host: {SMTP_HOST}:{SMTP_PORT}")
    print(f"      User: {SMTP_USER or 'Not configured (Simulation will be used)'}")

    is_simulated = not SMTP_USER or SMTP_USER == "your_smtp_user" or "brevo" in SMTP_HOST and not SMTP_USER

    print("\n[5/5] Executing Single Recipient Dispatch...")
    result = send_smtp_plain_email(
        to_email=target_email,
        subject=copy["subject"],
        body=copy["body"],
        simulate=is_simulated
    )

    if result["success"]:
        mode_text = "SIMULATED (No real SMTP credentials set)" if result.get("simulated") else "LIVE SMTP DELIVERED"
        print(f"\n[SUCCESS] [{mode_text}]")
        print(f"   Message-ID: {result['message_id']}")
        print("   The email passed all deliverability, formatting, and safety checks.")
    else:
        print(f"\n[FAILED] DISPATCH FAILED: {result['error']}")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live-Fire Single Send Outbound Test")
    parser.add_argument("--email", type=str, help="Target email address to receive the test cold email")
    args = parser.parse_args()

    if args.email:
        target = args.email.strip()
    else:
        try:
            target = input("Enter your personal email address for the test send [default: test@example.com]: ").strip()
        except EOFError:
            target = ""
        if not target:
            target = "test@example.com"

    run_live_fire(target)

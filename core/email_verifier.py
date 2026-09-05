"""
core/email_verifier.py
High-deliverability email verification engine.
Executes syntax validation, DNS MX record query via dnspython, and disposable provider checks.
"""

import re
from typing import Dict
import dns.resolver

# Comprehensive set of common disposable / temporary email domains
DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "sharklasers.com", "yopmail.com", "trashmail.com", "getnada.com",
    "dispostable.com", "tempail.com", "throwawaymail.com", "fakeinbox.com",
    "inboxkitten.com", "temp-mail.org", "mohmal.com", "burnermail.io",
    "maildrop.cc", "crazymailing.com", "mytemp.email"
}

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def verify_email_address(email: str) -> Dict[str, str]:
    """
    Verifies an email address using a 3-step deliverability pipeline:
      1. Regex syntax validation.
      2. Disposable domain blocklist check.
      3. DNS MX record resolution via dnspython (Port 53 UDP).

    Returns:
      dict: {"status": "valid" | "invalid", "reason": str}
    """
    if not email or not isinstance(email, str):
        return {"status": "invalid", "reason": "Empty or non-string email provided"}

    clean_email = email.strip().lower()

    # Step 1: Syntax format check
    if not EMAIL_REGEX.match(clean_email):
        return {"status": "invalid", "reason": "Malformed email syntax"}

    try:
        _, domain = clean_email.rsplit("@", 1)
    except ValueError:
        return {"status": "invalid", "reason": "Invalid email parts"}

    # Step 2: Disposable domain check
    if domain in DISPOSABLE_DOMAINS:
        return {"status": "invalid", "reason": f"Domain '{domain}' is a known disposable provider"}

    # Step 3: DNS MX record query (Port 53 UDP)
    resolver = dns.resolver.Resolver()
    resolver.timeout = 5.0
    resolver.lifetime = 5.0

    try:
        mx_records = resolver.resolve(domain, "MX")
        if not mx_records:
            return {"status": "invalid", "reason": f"No MX records found for domain '{domain}'"}
        
        # Sort and ensure at least one valid exchange target
        valid_exchanges = [r.exchange.to_text().rstrip(".") for r in mx_records if r.exchange]
        if not valid_exchanges:
            return {"status": "invalid", "reason": f"MX records exist but no exchange host available for '{domain}'"}

        return {"status": "valid", "reason": f"Verified MX records ({len(valid_exchanges)} hosts found)"}

    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        # Domain does not exist or has no MX records
        return {"status": "invalid", "reason": f"Domain '{domain}' does not exist or has no mail servers"}
    except dns.resolver.Timeout:
        # Fallback: if MX lookup timed out, check if A record exists for domain
        try:
            a_records = resolver.resolve(domain, "A")
            if a_records:
                return {"status": "valid", "reason": "MX timed out, but host A record resolved"}
        except Exception:
            pass
        return {"status": "invalid", "reason": f"DNS query timed out resolving MX records for '{domain}'"}
    except Exception as exc:
        return {"status": "invalid", "reason": f"DNS resolution failed: {str(exc)}"}


if __name__ == "__main__":
    test_emails = [
        "support@google.com",
        "invalid..syntax@xyz",
        "user@tempmail.com",
        "nonexistentdomain1239847192847.org"
    ]
    for e in test_emails:
        res = verify_email_address(e)
        print(f"{e} -> {res}")

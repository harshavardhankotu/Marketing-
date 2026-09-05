"""
core/email_verifier.py
VPS Port 25 safe email deliverability verification engine.
Validates email syntax via strict regex, flags disposable email providers,
and queries domain MX records via dnspython over UDP Port 53 (never blocks or triggers cloud ISP filters).
"""

import re
from typing import Dict
import dns.resolver

# Standard blocklist of disposable / ephemeral email domain providers
DISPOSABLE_DOMAINS = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "sharklasers.com", "yopmail.com", "trashmail.com", "getnada.com",
    "dispostable.com", "tempail.com", "throwawaymail.com", "fakeinbox.com",
    "inboxkitten.com", "temp-mail.org", "mohmal.com", "burnermail.io",
    "maildrop.cc", "crazymailing.com", "mytemp.email", "fakemailgenerator.com",
    "generator.email", "nada.ltd", "emailondeck.com", "throwaway.email"
}

# Strict RFC 5322 compatible syntax regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)


def verify_email_address(email: str) -> Dict[str, str]:
    """
    Executes a 3-step deliverability validation pipeline:
      1. Regex syntax formatting check.
      2. Disposable email domain blocklist check.
      3. UDP Port 53 DNS MX record query via dnspython.

    Returns:
      dict: {"status": "valid" | "invalid", "reason": str}
    """
    if not email or not isinstance(email, str):
        return {"status": "invalid", "reason": "Empty or non-string email provided"}

    clean_email = email.strip().lower()

    # Step 1: Regex syntax check
    if not EMAIL_REGEX.match(clean_email):
        return {"status": "invalid", "reason": "Malformed email syntax format"}

    try:
        user_part, domain = clean_email.rsplit("@", 1)
    except ValueError:
        return {"status": "invalid", "reason": "Missing @ domain delimiter"}

    # Discard domain parts with consecutive dots or invalid chars
    if ".." in domain or not domain:
        return {"status": "invalid", "reason": "Malformed domain format"}

    # Step 2: Disposable domain check
    if domain in DISPOSABLE_DOMAINS:
        return {"status": "invalid", "reason": f"Domain '{domain}' is a known disposable email provider"}

    # Step 3: Domain MX record lookup via dnspython (Port 53 UDP)
    resolver = dns.resolver.Resolver()
    resolver.timeout = 5.0
    resolver.lifetime = 5.0

    try:
        answers = resolver.resolve(domain, "MX")
        if not answers:
            return {"status": "invalid", "reason": f"No MX records configured for domain '{domain}'"}

        exchanges = [r.exchange.to_text().rstrip(".") for r in answers if r.exchange]
        if not exchanges:
            return {"status": "invalid", "reason": f"No active mail exchangers listed for '{domain}'"}

        return {"status": "valid", "reason": f"Verified MX records ({len(exchanges)} hosts resolved)"}

    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return {"status": "invalid", "reason": f"Domain '{domain}' does not exist or lacks MX records"}
    except dns.resolver.Timeout:
        # Fallback check: check if A record exists for domain host
        try:
            a_answers = resolver.resolve(domain, "A")
            if a_answers:
                return {"status": "valid", "reason": "MX query timed out, but host A record resolved"}
        except Exception:
            pass
        return {"status": "invalid", "reason": f"DNS MX lookup timed out for '{domain}'"}
    except Exception as exc:
        return {"status": "invalid", "reason": f"DNS MX resolution failed: {str(exc)}"}


if __name__ == "__main__":
    tests = [
        "valid.user@gmail.com",
        "bad@syntax..com",
        "disposable@mailinator.com",
        "nonexistent9381928301.com"
    ]
    for t in tests:
        print(f"{t}: {verify_email_address(t)}")

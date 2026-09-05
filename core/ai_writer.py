"""
core/ai_writer.py
Autonomous cold email copywriting engine powered by Google Gemini 2.0 Flash SDK (google-genai).
Enforces:
  - Strictly 3 to 4 sentences in body
  - Plain-text format only (no HTML, no bold, no links except booking URL)
  - Explicit proof-of-work statement
  - Spintax variation resolution ({Hi|Hello|Hey}) to prevent mail provider fingerprinting
  - Mandatory plain-text opt-out footer ("Reply 'STOP' to unsubscribe")
"""

import os
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import GEMINI_API_KEY, PUBLIC_BOOKING_URL, FROM_NAME

OPT_OUT_FOOTER = (
    "\n\n---\n"
    "Reply 'STOP' to unsubscribe. We honor immediate removal."
)


def resolve_spintax(text: str) -> str:
    """
    Randomly resolves nested Spintax patterns formatted as {option1|option2|option3}.
    Prevents mass template fingerprinting by SpamAssassin and Google Postmaster.
    """
    pattern = re.compile(r"\{([^{}]+)\}")
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split("|")
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text


def get_deterministic_pow_copy(
    company_name: str,
    contact_name: str,
    trigger_signal: str,
    value_prop: str,
    booking_url: str
) -> Dict[str, str]:
    """
    Deterministic 4-sentence proof-of-work email fallback.
    Used when Gemini API key is unconfigured or during offline/CI testing.
    """
    first_name = contact_name.split()[0] if contact_name else "there"
    
    # Spintax greeting and subject variations
    subject_template = "{Growth bottleneck|Outbound pipeline inquiry|Quick question} regarding " + company_name
    subject = resolve_spintax(subject_template)

    greeting = resolve_spintax("{Hi|Hello|Hey}") + f" {first_name},"

    # Sentence 1: Observation citing target company and trigger signal
    s1 = f"I was researching {company_name} and noticed your current setup ({trigger_signal})."

    # Sentence 2: Proof-of-work statement
    s2 = "This email was autonomously researched, verified, and drafted by our AI outbound engine without human intervention."

    # Sentence 3: Value proposition (15-25 qualified calls/month)
    default_vp = "We install autonomous outbound systems that book 15-25 qualified pipeline calls every month on complete autopilot."
    s3 = value_prop if value_prop else default_vp

    # Sentence 4: Low-friction CTA pointing to booking URL
    s4 = f"Would you be open to a 10-minute discovery chat this Thursday? Grab a time that suits you here: {booking_url}"

    body = f"{greeting}\n\n{s1} {s2} {s3} {s4}\n\nBest regards,\n{FROM_NAME}{OPT_OUT_FOOTER}"
    return {"subject": subject, "body": body}


def generate_outreach_email(
    lead_dict: Dict[str, Any],
    campaign_dict: Dict[str, Any]
) -> Dict[str, str]:
    """
    Generates a personalized, 3-to-4 sentence plain-text outreach email
    using Gemini 2.0 Flash or deterministic proof-of-work fallback.
    """
    company_name = lead_dict.get("company_name", "your company")
    contact_name = lead_dict.get("contact_name", "there")
    trigger_signal = lead_dict.get("trigger_signal", "inbound lead friction observed")
    industry = lead_dict.get("industry", "B2B")

    value_prop = campaign_dict.get("value_prop") if campaign_dict else ""
    booking_url = (campaign_dict.get("booking_link") if campaign_dict else None) or PUBLIC_BOOKING_URL

    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

    # If API key is absent or default placeholder, use deterministic template
    if not api_key or api_key == "your_gemini_api_key_here":
        return get_deterministic_pow_copy(
            company_name=company_name,
            contact_name=contact_name,
            trigger_signal=trigger_signal,
            value_prop=value_prop,
            booking_url=booking_url
        )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        prompt = f"""
You are an expert B2B deliverability copywriter. Write a 4-sentence plain-text cold email to {contact_name} at {company_name}.

Context:
- Company: {company_name} ({industry})
- Observed Trigger Signal: {trigger_signal}
- Core Offer / Value Prop: {value_prop or 'We deploy autonomous AI outbound systems booking 15-25 qualified pipeline calls each month.'}
- Direct Booking Link: {booking_url}

STRICT CONSTRAINTS:
1. Exactly 3 to 4 sentences total in the body.
2. Plain text only. Absolutely ZERO HTML, markdown bold (**), italics (*), or bullet points.
3. Sentence 1: Observation citing {company_name} and their specific trigger signal ({trigger_signal}).
4. Sentence 2: Mandatory proof-of-work statement: "This email was autonomously researched, verified, and drafted by our AI outbound engine without human intervention."
5. Sentence 3: Value proposition stating how you book 15-25 qualified calls/month for their business.
6. Sentence 4: Low-friction CTA including the exact booking link: {booking_url}

Output Format:
Subject: <3 to 5 lowercase words>
Body:
<the exact 3-4 sentence message>
"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=250
            )
        )

        output_text = response.text.strip()
        lines = output_text.split("\n")
        subject = f"Question regarding {company_name}"
        body_lines = []
        is_body = False

        for line in lines:
            line_str = line.strip()
            if line_str.lower().startswith("subject:"):
                subject = line_str.split(":", 1)[1].strip()
            elif line_str.lower().startswith("body:"):
                is_body = True
            elif is_body or (not line_str.lower().startswith("subject:") and len(line_str) > 0):
                # Strip any stray markdown syntax
                cleaned = line_str.replace("**", "").replace("*", "").replace("#", "")
                body_lines.append(cleaned)

        body_content = "\n\n".join([l for l in body_lines if l]).strip()
        if not body_content:
            body_content = output_text.replace("**", "").replace("*", "")

        # Apply spintax greeting if not already present
        if not body_content.lower().startswith(("hi", "hello", "hey")):
            first_name = contact_name.split()[0] if contact_name else "there"
            greeting = resolve_spintax("{Hi|Hello|Hey}") + f" {first_name},\n\n"
            body_content = greeting + body_content

        # Sign-off and mandatory plain text opt-out footer
        if FROM_NAME not in body_content:
            body_content += f"\n\nBest regards,\n{FROM_NAME}"
        body_content += OPT_OUT_FOOTER

        return {
            "subject": resolve_spintax(subject),
            "body": body_content
        }

    except Exception as exc:
        print(f"[ai_writer] Gemini API call fallback: {exc}")
        return get_deterministic_pow_copy(
            company_name=company_name,
            contact_name=contact_name,
            trigger_signal=trigger_signal,
            value_prop=value_prop,
            booking_url=booking_url
        )


if __name__ == "__main__":
    lead = {
        "company_name": "Kinetic SaaS",
        "contact_name": "Marcus Vance",
        "trigger_signal": "Lacks automated online booking calendar",
        "industry": "Enterprise Software"
    }
    camp = {
        "value_prop": "We install autonomous outbound systems that book 15-25 qualified pipeline calls every month.",
        "booking_link": "https://cal.com/outbound/15min"
    }
    res = generate_outreach_email(lead, camp)
    print("SUBJECT:", res["subject"])
    print("BODY:\n" + res["body"])

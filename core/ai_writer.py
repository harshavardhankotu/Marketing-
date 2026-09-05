"""
core/ai_writer.py
Autonomous cold email copywriting engine powered by Google Gemini 2.0 Flash SDK (google-genai).
Strictly adheres to 4-sentence plain-text high-deliverability conversion architecture.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import GEMINI_API_KEY, PUBLIC_BOOKING_URL, FROM_NAME


def get_deterministic_fallback_copy(
    company_name: str,
    contact_name: str,
    trigger_signal: str,
    value_prop: str,
    booking_url: str
) -> Dict[str, str]:
    """
    Deterministic, high-converting 4-sentence plain text email fallback
    when Gemini API key is unconfigured or network is unavailable.
    """
    first_name = contact_name.split()[0] if contact_name else "there"
    subject = f"Growth bottleneck at {company_name}"
    
    # 4 strictly structured sentences
    s1 = f"Hi {first_name}, I was reviewing {company_name} and noted your current setup ({trigger_signal})."
    s2 = "This email was researched, verified, and drafted autonomously by our AI outbound engine."
    s3 = f"{value_prop if value_prop else 'We install autonomous outbound systems that book 15-25 qualified B2B sales calls every month on autopilot.'}"
    s4 = f"Would you be open to a 10-minute chat this Thursday to see how this works for {company_name}? You can grab a slot directly here: {booking_url}"

    body = f"{s1} {s2} {s3} {s4}\n\nBest regards,\n{FROM_NAME}"
    return {"subject": subject, "body": body}


def generate_outreach_email(
    lead_dict: Dict[str, Any],
    campaign_dict: Dict[str, Any]
) -> Dict[str, str]:
    """
    Generates an ultra-personalized, 3-to-4 sentence plain-text outreach email
    using Gemini 2.0 Flash.

    Structure:
      Sentence 1: Specific observation of company & trigger signal.
      Sentence 2: Proof-of-work statement ("This email was researched, verified, and drafted autonomously by our AI engine").
      Sentence 3: Specific value proposition (booking 15-25 calls/month).
      Sentence 4: Low-friction CTA pointing to PUBLIC_BOOKING_URL.

    Returns:
      dict: {"subject": str, "body": str}
    """
    company_name = lead_dict.get("company_name", "your company")
    contact_name = lead_dict.get("contact_name", "there")
    trigger_signal = lead_dict.get("trigger_signal", "inbound lead capture friction")
    industry = lead_dict.get("industry", "B2B")
    
    value_prop = campaign_dict.get("value_prop") if campaign_dict else ""
    booking_url = (campaign_dict.get("booking_link") if campaign_dict else None) or PUBLIC_BOOKING_URL
    api_key = os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY

    # If Gemini API key is not configured, return deterministic fallback
    if not api_key or api_key == "your_gemini_api_key_here":
        return get_deterministic_fallback_copy(
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
You are an elite B2B Cold Email Copywriter. Write a hyper-personalized, ultra-concise cold email to {contact_name} at {company_name}.

Company Details:
- Company Name: {company_name}
- Industry: {industry}
- Trigger Signal Observed on Website: {trigger_signal}
- Core Offer/Value Proposition: {value_prop or 'We install autonomous AI sales engines that book 15-25 qualified calls every month.'}
- Booking URL: {booking_url}

CRITICAL RULES:
1. Strictly 3 to 4 sentences total in the body.
2. Plain text only. NO markdown formatting, NO bold (**), NO italics, NO bullet points, NO HTML.
3. Sentence 1: Direct observation of {company_name} and their trigger signal ({trigger_signal}).
4. Sentence 2: Explicit proof-of-work statement: "This email was researched, verified, and drafted autonomously by our AI engine."
5. Sentence 3: State the core value proposition: books 15-25 qualified calls per month for their business.
6. Sentence 4: Low-friction call to action with the exact booking link: {booking_url}

Output Format:
Return your response in EXACTLY this format:
Subject: <compelling, lowercase 3-5 word subject line>
Body:
<the 3-4 sentence body>
"""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=300
            )
        )

        output_text = response.text.strip()
        lines = output_text.split("\n")
        subject = f"Quick question regarding {company_name}"
        body_lines = []
        is_body = False

        for line in lines:
            line_str = line.strip()
            if line_str.lower().startswith("subject:"):
                subject = line_str.split(":", 1)[1].strip()
            elif line_str.lower().startswith("body:"):
                is_body = True
            elif is_body or (not line_str.lower().startswith("subject:") and len(line_str) > 0):
                # Clean any markdown asterisks if model inserted them
                cleaned_line = line_str.replace("**", "").replace("*", "")
                body_lines.append(cleaned_line)

        body_text = "\n\n".join([l for l in body_lines if l]).strip()
        if not body_text:
            body_text = output_text.replace("**", "").replace("*", "")

        # Append professional sign-off
        if FROM_NAME not in body_text:
            body_text += f"\n\nBest,\n{FROM_NAME}"

        return {
            "subject": subject,
            "body": body_text
        }

    except Exception as exc:
        print(f"[ai_writer] Gemini API call exception ({exc}), using deterministic fallback.")
        return get_deterministic_fallback_copy(
            company_name=company_name,
            contact_name=contact_name,
            trigger_signal=trigger_signal,
            value_prop=value_prop,
            booking_url=booking_url
        )


if __name__ == "__main__":
    sample_lead = {
        "company_name": "Apex Marketing Lab",
        "contact_name": "David Miller",
        "trigger_signal": "Relies on static contact forms | No self-serve booking calendar",
        "industry": "Performance Marketing"
    }
    sample_campaign = {
        "value_prop": "We install autonomous AI sales engines that book 15-25 qualified calls every month.",
        "booking_link": "https://cal.com/growthops/demo"
    }
    res = generate_outreach_email(sample_lead, sample_campaign)
    print("=== SUBJECT ===")
    print(res["subject"])
    print("\n=== BODY ===")
    print(res["body"])

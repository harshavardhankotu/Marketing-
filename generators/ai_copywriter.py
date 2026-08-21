"""
Multilingual organic copywriter (Gemini 2.0 Flash via google-genai).

Generates compliant affiliate captions in English, Hinglish and Tamil and
applies an **ASCI Compliance Guard**: every output is audited and, when the
required disclosure is missing, the strict Indian affiliate disclaimer is
appended verbatim:

    "*Affiliate link — I may earn a commission at no extra cost to you."

Organic traffic only — the copy never promises incentives, cashback, or
spoofed proof of earnings.
"""

import os
import re
import sys
import json
import hashlib

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import GEMINI_API_KEY, GEMINI_MODEL, has_gemini  # noqa: E402
from bots.quota_manager import (  # noqa: E402
    consume_quota, check_quota, check_breaker, record_breaker_failure,
    record_breaker_success, QuotaExceededException, CircuitBreakerOpenException,
)

# The exact ASCI-compliant disclosure mandated by the spec.
ASCI_DISCLOSURE_EN = "*Affiliate link — I may earn a commission at no extra cost to you."

ASCI_DISCLOSURES = {
    "en": ASCI_DISCLOSURE_EN,
    "hi": "*Affiliate link — मुझे आपके लिए कोई अतिरिक्त लागत के बिना कमीशन मिल सकता है।",
    "ta": "*Affiliate link — உங்களுக்கு கூடுதல் செலவு இல்லாமல் எனக்கு கமிஷன் கிடைக்கலாம்.",
}

_CLIENT = None


def _get_client():
    """Lazily initialise the google-genai client."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not has_gemini():
        return None
    try:
        from google import genai
        _CLIENT = genai.Client(api_key=GEMINI_API_KEY)
    except ImportError:
        try:
            import google.generativeai as legacy  # noqa: F401
            _CLIENT = "legacy"
        except ImportError:
            _CLIENT = None
    return _CLIENT


# ─────────────────────────────────────────────────────────────────────────────
# MOCK / TEMPLATE FALLBACK (offline-safe)
# ─────────────────────────────────────────────────────────────────────────────
def _safe(product, key, default=""):
    value = product.get(key, default)
    return default if value is None else value


def _mock_copies(product):
    """Deterministic, ASCI-compliant fallback captions (offline mode)."""
    title = _safe(product, "title", "this product")
    price = _safe(product, "price", "best price")
    discount = _safe(product, "discount", "great deal")
    sector = _safe(product, "sector", "electronics")

    tagline = f"Grab the {title} at {price} — a solid {discount}% off the list price."
    en = (
        f"🛒 {tagline}\n\n"
        f"Quality you can rely on, backed by real buyer reviews.\n\n"
        f"{ASCI_DISCLOSURE_EN}\n\n"
        f"#Deals #AmazonDeals #{sector}"
    )
    hi = (
        f"🛒 {tagline}\n\n"
        f"गुणवत्ता जिस पर आप भरोसा कर सकते हैं, असली खरीदारों की समीक्षाओं के साथ।\n\n"
        f"{ASCI_DISCLOSURES['hi']}\n\n"
        f"#Deals #AmazonDeals #{sector}"
    )
    ta = (
        f"🛒 {tagline}\n\n"
        f"நீங்கள் நம்பக்கூடிய தரம், உண்மையான வாங்குபவர் விமர்சனங்களுடன்.\n\n"
        f"{ASCI_DISCLOSURES['ta']}\n\n"
        f"#Deals #AmazonDeals #{sector}"
    )
    return {"en": en, "hi": hi, "ta": ta}


# ─────────────────────────────────────────────────────────────────────────────
# ASCI COMPLIANCE GUARD
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_disclosure(text, lang):
    """Append the mandated disclosure when it is not already present."""
    required = ASCI_DISCLOSURES.get(lang, ASCI_DISCLOSURE_EN)
    if "commission" in text.lower() and "affiliate" in text.lower():
        return text
    if "#Ad" in text and "commission" in text.lower():
        return text
    return text.rstrip() + "\n\n" + required


def _audit_and_fix(copies):
    """Audit generated text in all three languages for the ASCI disclosure."""
    for lang in ("en", "hi", "ta"):
        raw = copies.get(lang, "")
        if not raw:
            raw = _mock_copies({}).get(lang, "")
        copies[lang] = _ensure_disclosure(raw, lang)
    return copies


# ─────────────────────────────────────────────────────────────────────────────
# PROVEN DEAL FORMAT — the format real Indian deals channels convert with
# ─────────────────────────────────────────────────────────────────────────────
def _inr(amount):
    """Format a number as Indian-style rupee string (1,23,456.00)."""
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    whole = int(round(amount))
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"₹{s}"


def generate_deal_post(product):
    """
    Build the high-converting deal-alert caption from scored product fields.

    Deterministic and offline-free: uses price/MRP/badge data produced by
    ``bots/deal_scorer.py`` — no generative API required. Always ends with
    the mandated ASCI disclosure.
    """
    title = _safe(product, "title", "Featured product")
    price = float(_safe(product, "price", 0) or 0)
    mrp = product.get("mrp")
    badge = product.get("badge", "")
    sector = _safe(product, "sector", "deals")

    lines = ["🔥 PRICE DROP ALERT", ""]
    if badge:
        lines += [f"⚠️ {badge} on this channel!", ""]
    lines.append(title)

    if price > 0:
        price_line = f"💰 Now {_inr(price)}"
        if mrp and float(mrp) > price:
            pct = round((float(mrp) - price) / float(mrp) * 100)
            price_line += f" ~~{_inr(mrp)}~~ ({pct}% OFF)"
        elif _safe(product, "discount"):
            price_line += f" ({int(float(product['discount']))}% OFF)"
        lines += ["", price_line]

    if product.get("is_lowest_ever"):
        lines += ["📉 Lowest price we have ever tracked!"]
    lines += ["⏳ Limited-period offer — stock moves fast."]

    lines += [
        "",
        "🛒 Check live price & grab it here 👇",
        "",
        ASCI_DISCLOSURE_EN,
        "",
        f"#Deals #{sector.replace(' ', '')} #AmazonIndia",
    ]
    text = "\n".join(lines)
    return _ensure_disclosure(text, "en")


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def generate_multilingual_copy(product):
    """
    Generate English / Hinglish / Tamil captions for a product.

    Falls back to the offline template set when Gemini is not configured,
    quota-blocked, or breaker-open.
    """
    mock = _mock_copies(product)
    client = _get_client()
    if client is None:
        return mock

    if check_quota("gemini") == "BLOCKED":
        print("[AI_COPYWRITER] Gemini quota blocked -> mock.")
        return mock
    try:
        check_breaker("gemini")
    except CircuitBreakerOpenException:
        print("[AI_COPYWRITER] Gemini circuit OPEN -> mock.")
        return mock

    prompt = _build_prompt(product)
    try:
        consume_quota("gemini")
        text = _call_gemini(client, prompt)
        parsed = _parse_json_response(text)
        if not parsed:
            return mock
        return _audit_and_fix(parsed)
    except Exception as exc:
        record_breaker_failure("gemini")
        print(f"[AI_COPYWRITER] Gemini call failed: {exc}")
        return mock

    finally:
        record_breaker_success("gemini")


def _build_prompt(product):
    title = _safe(product, "title", "this product")
    price = _safe(product, "price", "best price")
    discount = _safe(product, "discount", "great deal")
    sector = _safe(product, "sector", "electronics")
    brand = _safe(product, "brand", "")

    return f"""
You are a compliance-first affiliate copywriter for the Indian market.
Write a short, honest, organic social caption for this product on Amazon.in.

Product: {title}
Brand: {brand}
Price: ₹{price}
Discount: {discount}% off
Category: {sector}

Write the caption in EXACTLY three languages: English, Hinglish (Hindi written in Roman script), and Tamil.

Rules:
- Honest, value-focused copy. NO fake urgency, NO fabricated earnings, NO incentivized claims.
- Keep it natural and under 180 characters.
- Do NOT include any disclosure yet.

Return strict JSON with keys "en", "hi", "ta". No markdown fences.
"""


def _call_gemini(client, prompt):
    if client == "legacy":
        import google.generativeai as legacy
        model = legacy.GenerativeModel(GEMINI_MODEL)
        return model.generate_content(prompt).text.strip()
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


def _parse_json_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return {k: str(v) for k, v in data.items() if k in ("en", "hi", "ta")}
    except (json.JSONDecodeError, TypeError):
        print("[AI_COPYWRITER] Could not parse Gemini JSON response.")
        return None


def enrich_with_multilingual_copy(products):
    """Batch entry point used by the pipeline / scheduler."""
    for idx, product in enumerate(products):
        print(f"[AI_COPYWRITER] Copy {idx + 1}/{len(products)}: {_safe(product, 'title', '')[:30]}")
        copies = generate_multilingual_copy(product)
        product["copy"] = copies
        product["caption"] = copies.get("en", "")
    return products

"""
Weekly deals newsletter — double opt-in, plain smtplib, zero services.

* Recipients come ONLY from the confirmed, non-unsubscribed opt-in list
  (DPDP-friendly: consent is provable, exit is one click).
* Content is built from the top published deals with tracked /go/ links
  (channel=email) so revenue attribution flows into Channel P&L.
* When SMTP is unconfigured (dev/CI), send() fails gracefully and preview()
  still renders the full HTML — nothing silently pretends to send.
"""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from config import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, NEWSLETTER_NAME,
    )
except ImportError:
    SMTP_HOST = SMTP_PORT = SMTP_USER = SMTP_PASSWORD = None
    SMTP_FROM = "deals@example.com"
    NEWSLETTER_NAME = "Hot Deals Weekly"

ASCI_DISCLOSURE = "*Affiliate link — I may earn a commission at no extra cost to you."


def _inr(amount):
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


def _tracked_go_link(base_url, deal):
    from urllib.parse import quote, urlencode

    qs = urlencode({
        "url": deal.get("target_url") or "",
        "title": deal.get("title") or "",
        "sector": deal.get("sector") or "",
        "channel": "email",
    })
    return f"{base_url}/go/{quote(str(deal.get('product_id') or deal.get('id')))}?{qs}"


def build_html(deals, base_url=""):
    """Responsive inline-styled HTML digest of the top published deals."""
    base_url = (base_url or "").rstrip("/")
    rows = []
    for d in deals:
        badge = ""
        if d.get("lowest_ever"):
            badge = '<span style="background:#ef4444;color:#fff;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700">LOWEST EVER</span>'
        elif float(d.get("deal_score") or 0) >= 55:
            badge = '<span style="background:#f59e0b;color:#111;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700">HOT DEAL</span>'
        mrp_html = ""
        if d.get("mrp") and float(d["mrp"]) > float(d.get("price") or 0):
            mrp_html = (f'<span style="color:#94a3b8;text-decoration:line-through">'
                        f"{_inr(d['mrp'])}</span>")
        price = _inr(d.get("price") or 0)
        link = _tracked_go_link(base_url, d)
        title = str(d.get("title") or "Featured deal")
        rows.append(f"""
        <tr>
          <td style="padding:14px 18px;border-bottom:1px solid #e5e7eb;">
            <div style="font-size:12px;margin-bottom:6px">{badge}</div>
            <div style="font-size:16px;font-weight:700;color:#111827;margin-bottom:6px">{title}</div>
            <div style="font-size:15px;margin-bottom:10px">
              <strong style="color:#16a34a">{price}</strong> &nbsp;{mrp_html}
            </div>
            <a href="{link}" style="background:#16a34a;color:#ffffff;padding:9px 18px;
               border-radius:8px;text-decoration:none;font-weight:700;font-size:13px">
               View live price →</a>
          </td>
        </tr>""")

    return f"""<!DOCTYPE html>
<html><body style="margin:0;background:#f3f4f6;font-family:Segoe UI,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;background:#ffffff">
  <div style="background:#10141d;padding:26px 18px;text-align:center">
    <h1 style="color:#22c55e;margin:0;font-size:20px;letter-spacing:1px">{NEWSLETTER_NAME}</h1>
    <p style="color:#8a94a8;margin:6px 0 0;font-size:12px">Hand-verified price drops — tracked around the clock</p>
  </div>
  <table width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
  <div style="padding:18px;text-align:center;color:#6b7280;font-size:12px;line-height:1.6">
    {ASCI_DISCLOSURE}<br>
    Prices change frequently — the live store price at checkout always prevails.<br>
    You receive this because you confirmed your subscription.
    <a href="{{{{unsubscribe_url}}}}" style="color:#6b7280">Unsubscribe</a> anytime.
  </div>
</div>
</body></html>"""


def render_with_unsubscribe(html, unsubscribe_url):
    return html.replace("{{unsubscribe_url}}", unsubscribe_url)


def send_email(to_addr, subject, html_body):
    """
    Send one HTML email via configured SMTP.
    Returns (ok: bool, error: str|None). Fails loudly-but-gracefully when
    SMTP is not configured so callers never fake a successful send.
    """
    if not SMTP_HOST:
        return False, "smtp_not_configured"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=15) as server:
            try:
                server.starttls()
            except smtplib.SMTPException:
                pass  # some relays are TLS-only already
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_addr], msg.as_string())
        return True, None
    except Exception as exc:
        return False, str(exc)


if __name__ == "__main__":
    demo = [
        {"id": 1, "product_id": "B0DEMO1", "title": "Demo Fan", "price": 2599,
         "mrp": 3582, "deal_score": 88, "lowest_ever": 1,
         "target_url": "https://www.amazon.in/dp/B0DEMO1", "sector": "home_kitchen"},
    ]
    print(build_html(demo)[:400])

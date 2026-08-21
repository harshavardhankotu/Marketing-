"""
Forwardable deal card factory — 1080x1350 shareable PNG rendered with Pillow.

Zero-cost creative: no stock photos, no APIs, no MoviePy. The card uses the
proven Indian deals-channel layout (big % OFF badge, strike-through MRP,
scarcity ribbon) so subscribers forward it — forwards are the #1 organic
growth engine for deal channels.

Input is a scored product dict (see ``bots/deal_scorer.py``):
    title, price, mrp, discount_pct, deal_score, badge, is_lowest_ever
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

try:
    from config import CAMPAIGN_STATIC_DIR  # noqa: E402
except ImportError:
    CAMPAIGN_STATIC_DIR = os.path.join(PROJECT_ROOT, "static", "campaigns")

W, H = 1080, 1350

# Ledger-theme palette.
BG_TOP = (16, 20, 29)
BG_BOTTOM = (7, 9, 14)
GREEN = (34, 197, 94)
AMBER = (245, 158, 11)
RED = (239, 68, 68)
TEXT = (223, 229, 239)
DIM = (138, 148, 168)
PANEL = (26, 32, 48)
DISCLOSURE = "*Affiliate link — I may earn a commission at no extra cost to you."

_FONT_CACHE = {}


def _load_font(size):
    """Load the boldest available font supporting ₹ / em-dash; cache results."""
    key = int(size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    candidates = [
        os.path.join(PROJECT_ROOT, "assets", "Roboto-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            if path and os.path.exists(path):
                font = ImageFont.truetype(path, key)
                _FONT_CACHE[key] = font
                return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _gradient_bg():
    """Vertical ledger-dark gradient base."""
    import numpy as np

    t = np.linspace(0.0, 1.0, H, dtype=np.float64)[:, None, None]
    top = np.array(BG_TOP, dtype=np.float64)
    bottom = np.array(BG_BOTTOM, dtype=np.float64)
    arr = np.repeat(top * (1 - t) + bottom * t, W, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    return img.convert("RGBA")


def _wrap(draw, text, font, max_width):
    """Word-wrap text to fit max_width; hard-splits very long words."""
    lines = []
    for raw_line in text.split("\n"):
        words = raw_line.split(" ")
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                # Hard-split pathological single words (URLs etc).
                while draw.textlength(word, font=font) > max_width and len(word) > 12:
                    cut = max(12, int(len(word) * max_width / max(draw.textlength(word, font=font), 1)))
                    lines.append(word[:cut])
                    word = word[cut:]
                current = word
        if current:
            lines.append(current)
    return lines


def _pill(draw, xy_center, text, font, fill, pad_x=34, pad_y=16, radius=999):
    """Draw a rounded pill badge centered on xy_center; returns its bbox."""
    tw = draw.textlength(text, font=font)
    bbox = font.getbbox(text)
    th = bbox[3] - bbox[1]
    x, y = xy_center
    x0, y0 = x - tw / 2 - pad_x, y - th / 2 - pad_y
    x1, y1 = x + tw / 2 + pad_x, y + th / 2 + pad_y
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)
    draw.text((x, y), text, font=font, fill=(10, 12, 18), anchor="mm")
    return [x0, y0, x1, y1]


def render_deal_card(product, out_path=None):
    """
    Render the 1080x1350 forwardable deal card for a scored product.

    Writes under ``static/campaigns/`` and returns the WEB-relative path
    (``/static/campaigns/deal_<id>.png``) so it can be stored in the DB and
    served directly on public pages. Channel adapters resolve the real file
    via basename when posting.
    """
    os.makedirs(CAMPAIGN_STATIC_DIR, exist_ok=True)
    pid = str(product.get("id") or product.get("product_id") or "deal")
    filename = f"deal_{pid}.png"
    if not out_path:
        out_path = os.path.join(CAMPAIGN_STATIC_DIR, filename)

    img = _gradient_bg()
    draw = ImageDraw.Draw(img)

    title = str(product.get("title") or "Featured Deal")
    price = float(product.get("price", 0) or 0)
    mrp = product.get("mrp")
    discount_pct = float(product.get("discount_pct") or product.get("discount") or 0)
    badge = product.get("badge") or ""
    is_lowest = bool(product.get("is_lowest_ever"))

    # ── Header ────────────────────────────────────────────────────────────
    header_font = _load_font(52)
    draw.text((W / 2, 92), "PRICE DROP ALERT", font=header_font, fill=AMBER, anchor="mm")

    # Accent underline.
    draw.rounded_rectangle([W / 2 - 130, 132, W / 2 + 130, 140], radius=4, fill=AMBER)

    # ── Scarcity badge ────────────────────────────────────────────────────
    y_cursor = 210
    if badge:
        badge_fill = RED if is_lowest else GREEN
        label = badge if is_lowest or badge != "LOWEST EVER" else "LOWEST EVER"
        _pill(draw, (W / 2, y_cursor), label, _load_font(46), badge_fill)
        y_cursor += 110

    # ── Title (wrapped) ───────────────────────────────────────────────────
    title_font = _load_font(62)
    max_width = W - 160
    title_lines = _wrap(draw, title.upper(), title_font, max_width)[:5]
    line_h = 78
    for line in title_lines:
        draw.text((W / 2, y_cursor), line, font=title_font, fill=TEXT, anchor="mm")
        y_cursor += line_h

    # ── Price block ───────────────────────────────────────────────────────
    price_y = min(max(y_cursor + 120, 860), 1000)
    price_font = _load_font(150)
    price_text = _fmt_inr(price)
    draw.text((W / 2, price_y), price_text, font=price_font, fill=GREEN, anchor="mm")

    # MRP strike-through + % OFF pill beneath the price.
    sub_y = price_y + 105
    if mrp and float(mrp) > price:
        mrp_font = _load_font(54)
        mrp_text = _fmt_inr(float(mrp))
        mw = draw.textlength(mrp_text, font=mrp_font)
        pct_text = f"{int(round(discount_pct))}% OFF"
        total_w = mw + 60 + draw.textlength(pct_text, font=_load_font(54))
        start_x = W / 2 - total_w / 2
        draw.text((start_x, sub_y), mrp_text, font=mrp_font, fill=DIM, anchor="lm")
        bx0, by0 = start_x, sub_y - 2
        bx1, by1 = start_x + mw, sub_y + 2
        draw.line([bx0, (by0 + by1) / 2, bx1, (by0 + by1) / 2], fill=RED, width=6)
        draw.text((start_x + mw + 60, sub_y), pct_text, font=_load_font(54), fill=AMBER, anchor="lm")
    elif discount_pct > 0:
        draw.text((W / 2, sub_y), f"{int(round(discount_pct))}% OFF",
                  font=_load_font(54), fill=AMBER, anchor="mm")

    # ── Urgency line ──────────────────────────────────────────────────────
    draw.text((W / 2, 1130), "Limited-period offer — live price on Amazon",
              font=_load_font(40), fill=DIM, anchor="mm")

    # ── Disclosure footer ─────────────────────────────────────────────────
    draw.line([80, 1210, W - 80, 1210], fill=PANEL, width=3)
    disc_font = _load_font(27)
    disc_lines = _wrap(draw, DISCLOSURE, disc_font, W - 200)[:2]
    dy = 1252
    for line in disc_lines:
        draw.text((W / 2, dy), line, font=disc_font, fill=DIM, anchor="mm")
        dy += 38

    img.convert("RGB").save(out_path, "PNG", optimize=True)
    print(f"[DEAL_CARD] Rendered {out_path}")
    return f"/static/campaigns/{filename}"


def _fmt_inr(amount):
    """Indian-grouped rupee string without the glyph risk of odd fonts."""
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
    return f"\u20b9{s}"


if __name__ == "__main__":
    demo = {
        "id": "DEMO123456",
        "title": "Orient Electric Apex Swift 1200mm BLDC Ceiling Fan",
        "price": 2599.0,
        "mrp": 3582.0,
        "discount_pct": 27.4,
        "deal_score": 88.0,
        "badge": "LOWEST EVER",
        "is_lowest_ever": True,
    }
    path = render_deal_card(demo)
    print(f"Demo card -> {path}")

"""
Cross-promo channel card — a 1080x1350 shareable PNG that sells THIS channel
to other admins during shoutout/cross-promotion negotiations.

Free Pillow render in the ledger aesthetic: channel name, niche promise,
member count, cadence promise and the t.me join link. Attach it when pitching
cross-promos — admins negotiate on views/quality, so the card states them.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PIL import Image, ImageDraw  # noqa: E402

try:
    from config import STATIC_DIR  # noqa: E402
except ImportError:
    STATIC_DIR = os.path.join(PROJECT_ROOT, "static")

W, H = 1080, 1350
BG_TOP = (16, 20, 29)
BG_BOTTOM = (7, 9, 14)
GREEN = (34, 197, 94)
AMBER = (245, 158, 11)
TEXT = (223, 229, 239)
DIM = (138, 148, 168)
PANEL = (26, 32, 48)

_FONT_CACHE = {}


def _load_font(size):
    key = int(size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [
        os.path.join(PROJECT_ROOT, "assets", "Roboto-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
    ]
    for path in candidates:
        try:
            if path and os.path.exists(path):
                font = ImageFont_truetype(path, key)
                _FONT_CACHE[key] = font
                return font
        except OSError:
            continue
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def ImageFont_truetype(path, size):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def _gradient_bg():
    import numpy as np

    t = np.linspace(0.0, 1.0, H, dtype=np.float64)[:, None, None]
    top = np.array(BG_TOP, dtype=np.float64)
    bottom = np.array(BG_BOTTOM, dtype=np.float64)
    arr = np.repeat(top * (1 - t) + bottom * t, W, axis=1).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _wrap(draw, text, font, max_width):
    lines, current = [], ""
    for word in text.split(" "):
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_channel_card(channel_name="Deals Radar",
                        tagline="Hand-verified price drops — lowest-ever alerts, zero fluff.",
                        members=None,
                        cadence="5+ verified deals daily",
                        invite_url="t.me/yourdeals",
                        out_path=None):
    """Render the cross-promo card; returns the written path."""
    out_dir = os.path.join(STATIC_DIR, "campaigns")
    os.makedirs(out_dir, exist_ok=True)
    if not out_path:
        safe = "".join(c for c in channel_name.lower() if c.isalnum() or c in "-_") or "channel"
        out_path = os.path.join(out_dir, f"channel_{safe}.png")

    img = _gradient_bg()
    draw = ImageDraw.Draw(img)

    # Frame panel.
    draw.rounded_rectangle([60, 90, W - 60, H - 90], radius=42,
                           outline=PANEL, width=6,
                           fill=(20, 25, 37, 235))

    # Header.
    draw.text((W / 2, 210), "DEALS CHANNEL", font=_load_font(44), fill=AMBER, anchor="mm")
    draw.rounded_rectangle([W / 2 - 110, 244, W / 2 + 110, 252], radius=4, fill=AMBER)

    # Channel name (wrapped).
    name_font = _load_font(96)
    y = 380
    for line in _wrap(draw, channel_name.upper(), name_font, W - 240)[:3]:
        draw.text((W / 2, y), line, font=name_font, fill=TEXT, anchor="mm")
        y += 118

    # Member count pill (the number admins care about).
    y += 40
    members_label = f"{members:,} members".replace(",", ",") if isinstance(members, int) else \
        (str(members) if members else "Growing community")
    pill_font = _load_font(52)
    tw = draw.textlength(members_label, font=pill_font)
    bbox = pill_font.getbbox(members_label)
    th = bbox[3] - bbox[1]
    draw.rounded_rectangle(
        [W / 2 - tw / 2 - 46, y - th / 2 - 26, W / 2 + tw / 2 + 46, y + th / 2 + 26],
        radius=999, fill=GREEN)
    draw.text((W / 2, y), members_label, font=pill_font, fill=(10, 12, 18), anchor="mm")
    y += 130

    # Tagline + cadence promises.
    body_font = _load_font(46)
    for line in _wrap(draw, tagline, body_font, W - 260)[:4]:
        draw.text((W / 2, y), line, font=body_font, fill=DIM, anchor="mm")
        y += 62
    y += 30
    draw.text((W / 2, y), cadence, font=_load_font(50), fill=AMBER, anchor="mm")

    # Join link.
    draw.line([140, 1130, W - 140, 1130], fill=PANEL, width=4)
    draw.text((W / 2, 1200), str(invite_url), font=_load_font(64), fill=GREEN, anchor="mm")

    img.convert("RGB").save(out_path, "PNG", optimize=True)
    print(f"[CHANNEL_CARD] Rendered {out_path}")
    return out_path


if __name__ == "__main__":
    print(render_channel_card())

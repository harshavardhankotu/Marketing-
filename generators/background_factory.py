"""
Ambient background factory — procedurally generated seamless-loop web UI
backdrops inspired by cinematic AI video aesthetics.

Themes (mapped from the referenced AI-video prompts):
    * ``kelp``      — static wide-angle underwater kelp forest, amber fronds
                      swaying in slow currents under golden god-rays.
    * ``pavilion``  — cavernous bio-mineralized coastal pavilion of smooth
                      bone-white sea-limestone with organic porosity.
    * ``train``     — classic 90s-anime Japanese train carriage at golden
                      hour: interior still, countryside panning past the window.
    * ``silhouette``— double-exposure silhouette with a Pacific Northwest
                      pine forest and drifting mountain fog.

Frugal design:
    * Posters are rendered once offline with Pillow (no API, no cost) and
      cached under ``static/backgrounds/``.
    * Motion comes from lightweight CSS keyframes layered over the still
      (god-ray pulsing, horizon glide, fog drift) — no video required.
    * When MoviePy is available, ``render_loop()`` can additionally compile a
      true 10s seamless-loop MP4 (cosine zoom that returns to frame zero);
      the UI prefers the MP4 and falls back to the animated poster.
"""

import os
import sys
import math
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
import numpy as np  # noqa: E402

try:
    from config import BACKGROUND_THEME, BACKGROUND_DIR, FAST_VIDEO_RENDER  # noqa: E402
except ImportError:  # allow running as a standalone module
    BACKGROUND_THEME = "train"
    BACKGROUND_DIR = os.path.join(PROJECT_ROOT, "static", "backgrounds")
    FAST_VIDEO_RENDER = True

W, H = 1920, 1080
LOOP_SECONDS = 10
FPS = 24

THEMES = ["kelp", "pavilion", "train", "silhouette"]

# Module-level cache of generated posters, keyed by theme.
_GENERATED = set()


# ─────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL IMAGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _gradient(top, bottom):
    """Vertical linear gradient image (W x H) from ``top`` to ``bottom`` RGB."""
    t = np.linspace(0.0, 1.0, H, dtype=np.float64)[:, None, None]
    top = np.array(top, dtype=np.float64)
    bottom = np.array(bottom, dtype=np.float64)
    arr = (top * (1.0 - t) + bottom * t)
    arr = np.repeat(arr, W, axis=1)
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def _radial_glow(size, center, radius, color, strength=1.0):
    """Semi-transparent RGBA radial glow overlay (for god-rays / suns / fog)."""
    cx, cy = center
    yy, xx = np.mgrid[0:size[1], 0:size[0]]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(radius, 1)
    falloff = np.clip(1.0 - dist, 0.0, 1.0) ** 2
    r, g, b = color
    alpha = np.clip(falloff * strength, 0.0, 255.0)
    out = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    out[..., 0] = r
    out[..., 1] = g
    out[..., 2] = b
    out[..., 3] = alpha
    return Image.fromarray(out, "RGBA")


def _grain(base, amount=7):
    """Add subtle film grain so posters feel like rendered stills."""
    arr = np.asarray(base).astype(np.int16)
    noise = np.random.randint(-amount, amount + 1, arr.shape[:2])[:, :, None]
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _vignette(base, strength=0.45):
    """Darken the edges slightly (draws the eye to the center)."""
    yy, xx = np.mgrid[0:H, 0:W]
    cx, cy = W / 2, H / 2
    dist = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    mask = np.clip(dist, 0.0, 2.0)
    mask = (mask / 2.0) ** 2 * strength
    arr = np.asarray(base).astype(np.float64)
    arr = arr * (1.0 - mask[:, :, None])
    return Image.fromarray(arr.astype(np.uint8), "RGB")


# ─────────────────────────────────────────────────────────────────────────────
# THEME PAINTERS
# ─────────────────────────────────────────────────────────────────────────────
def _paint_kelp():
    """Turquoise kelp forest with golden god-rays filtering through."""
    base = _gradient((14, 138, 128), (3, 42, 46))
    base.paste(_radial_glow((W, H), (W * 0.5, 120), 520, (255, 214, 150), 0.55), (0, 0), _radial_glow((W, H), (W * 0.5, 120), 520, (255, 214, 150), 0.55))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rng = np.random.RandomState(7)
    for i in range(9):
        x_base = int(W * (0.08 + 0.84 * i / 8.0))
        sway = rng.uniform(0.18, 0.34)
        amp = rng.uniform(40, 90)
        width = rng.uniform(16, 34)
        color = (rng.randint(210, 235), rng.randint(140, 165), rng.randint(50, 80), rng.randint(95, 150))
        points = []
        for y in range(0, H, 12):
            phase = math.sin(y / H * math.pi * 2 * sway)
            x = x_base + amp * phase
            points.append((x, y))
        if len(points) > 2:
            draw.line(points, fill=color, width=int(width))

    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    base = _grain(base, 6)
    base = _vignette(base, 0.5)
    return base


def _paint_pavilion():
    """Smooth undulating bone-white bio-limestone interior."""
    base = _gradient((242, 239, 231), (205, 201, 191))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Undulating horizontal strata.
    y = H * 0.30
    while y < H:
        band = int(H * 0.05 * (0.6 + 0.4 * math.sin(y * 0.004)))
        points = [(0, y)]
        for x in range(0, W + 20, 20):
            points.append((x, y + 8 * math.sin(x * 0.006 + y * 0.01)))
        points.append((W, y + band))
        for x in range(W, -20, -20):
            points.append((x, y + band + 8 * math.sin(x * 0.006 + y * 0.01)))
        draw.polygon(points, fill=(188, 183, 172, 38))
        y += band + H * 0.035

    # Organic porosity dots.
    rng = np.random.RandomState(3)
    for _ in range(340):
        x, y = rng.randint(0, W), rng.randint(0, H)
        r = rng.randint(3, 12)
        shade = rng.randint(120, 175)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(shade, shade, shade - 8, 40))

    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    base = _grain(base, 4)
    base = _vignette(base, 0.35)
    return base


def _paint_train():
    """90s anime golden-hour: still carriage interior, countryside outside."""
    # Sky: pale blue → gold horizon.
    sky = _gradient((168, 190, 218), (255, 198, 140))
    ground = _gradient((120, 160, 100), (70, 110, 66))

    canvas = sky.convert("RGBA")
    canvas.paste(ground, (0, int(H * 0.58)))

    draw = ImageDraw.Draw(canvas)

    # Low sun.
    canvas = Image.alpha_composite(canvas, _radial_glow((W, H), (W * 0.5, int(H * 0.60)), 300, (255, 236, 190), 0.8))

    # Rolling green fields (perspective bands).
    rng = np.random.RandomState(11)
    for band in range(14):
        yy = int(H * 0.58) + band * int(H * 0.03)
        shade = (rng.randint(80, 140), rng.randint(120, 165), rng.randint(60, 100), 120)
        draw.line([(0, yy), (W, yy)], fill=shade, width=int(H * 0.028))

    # Distant wooden houses + tree silhouettes.
    for hx in range(2, 9):
        x = int(W * hx / 9.0) + rng.randint(-40, 40)
        y_base = int(H * 0.60)
        hh = rng.randint(30, 70)
        draw.polygon([(x, y_base), (x + 26, y_base - hh), (x + 52, y_base)], fill=(66, 52, 44, 200))
        draw.rectangle([x + 16, y_base - hh * 0.55, x + 36, y_base], fill=(52, 42, 36, 210))

    # Trees.
    for tx in range(0, 14):
        x = int(W * (0.02 + 0.96 * tx / 13.0))
        y_base = int(H * 0.585) + rng.randint(-8, 26)
        th = rng.randint(24, 58)
        draw.polygon([(x, y_base), (x + 30, y_base - th), (x + 60, y_base)], fill=(52, 82, 58, 170))

    # Carriage interior frame (window bars) — darker vignette border.
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rectangle([0, 0, W, int(H * 0.06)], fill=(24, 22, 26, 235))
    fd.rectangle([0, int(H * 0.94), W, H], fill=(24, 22, 26, 235))
    fd.rectangle([0, 0, int(W * 0.05), H], fill=(24, 22, 26, 235))
    fd.rectangle([int(W * 0.95), 0, W, H], fill=(24, 22, 26, 235))
    canvas = Image.alpha_composite(canvas, frame)

    base = canvas.convert("RGB")
    base = _grain(base, 6)
    return base


def _paint_silhouette():
    """Double-exposure: profile silhouette filled with a misty pine forest."""
    base = _gradient((235, 229, 214), (198, 192, 178))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Pine forest ridges (behind the silhouette).
    rng = np.random.RandomState(5)
    for ridge in range(3):
        y_base = int(H * (0.55 + ridge * 0.14))
        for tx in range(0, 34):
            x = int(W * (0.02 + 0.96 * tx / 33.0))
            th = rng.randint(50, 120) - ridge * 22
            depth = 200 - ridge * 55
            draw.polygon([(x, y_base), (x + 40, y_base - th), (x + 80, y_base)], fill=(26, 48, 42, depth))

    # Profile silhouette (head + shoulders) on the right.
    cx, cy = W * 0.72, H * 0.46
    head_r = int(H * 0.20)
    draw.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=(34, 30, 34, 255))
    shoulder = [(int(W * 0.55), H + 10), (cx - int(head_r * 0.55), cy + int(head_r * 0.45)), (cx, cy + int(head_r * 0.4))]
    draw.polygon(shoulder, fill=(34, 30, 34, 255))

    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    base = _grain(base, 5)
    base = _vignette(base, 0.4)
    return base


PAINTERS = {
    "kelp": _paint_kelp,
    "pavilion": _paint_pavilion,
    "train": _paint_train,
    "silhouette": _paint_silhouette,
}


# ─────────────────────────────────────────────────────────────────────────────
# ASSET GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_dir():
    os.makedirs(BACKGROUND_DIR, exist_ok=True)
    return BACKGROUND_DIR


def poster_path(theme):
    return os.path.join(BACKGROUND_DIR, f"{theme}.jpg")


def loop_path(theme):
    return os.path.join(BACKGROUND_DIR, f"{theme}.mp4")


def render_poster(theme, force=False):
    """Render (or refresh) a theme's poster JPEG. Returns the file path."""
    if theme not in PAINTERS:
        raise ValueError(f"Unknown background theme: {theme}")
    _ensure_dir()
    target = poster_path(theme)
    if not force and theme in _GENERATED and os.path.exists(target):
        return target
    painter = PAINTERS[theme]
    img = painter()
    img.save(target, "JPEG", quality=88)
    _GENERATED.add(theme)
    return target


def render_loop(theme, force=False):
    """
    Compile a true seamless-loop MP4 from the poster using MoviePy (optional).

    Uses a cosine zoom that returns to its starting scale at exactly 10s, so
    the last frame blends seamlessly back into the first. Temp files are
    always cleaned up in ``finally`` (Windows file-lock safety).

    Returns the MP4 path, or ``None`` when MoviePy is unavailable / disabled.
    """
    if FAST_VIDEO_RENDER:
        return None
    try:
        from moviepy.editor import ImageSequenceClip  # noqa: F401
        import numpy as _np
    except ImportError:
        print("[BACKGROUND] MoviePy unavailable — poster-only fallback.")
        return None

    poster = render_poster(theme, force=force)
    _ensure_dir()
    target = loop_path(theme)
    if not force and os.path.exists(target):
        return target

    frames = []
    steps = LOOP_SECONDS * FPS
    img = Image.open(poster).convert("RGB")
    for i in range(steps):
        t = i / max(steps - 1, 1)
        scale = 1.0 + 0.03 * math.cos(2 * math.pi * t)
        size = (int(W * scale), int(H * scale))
        f = img.resize(size, Image.BILINEAR)
        left = (size[0] - W) // 2
        top = (size[1] - H) // 2
        f = f.crop((left, top, left + W, top + H))
        frames.append(_np.asarray(f))

    tmp_path = os.path.join(tempfile.gettempdir(), f"bg_{theme}_tmp.mp4")
    try:
        clip = ImageSequenceClip(frames, fps=FPS)
        clip.write_videofile(
            tmp_path,
            fps=FPS,
            codec="libx264",
            preset="ultrafast",
            audio=False,
            logger=None,
            verbose=False,
        )
        clip.close()
        os.replace(tmp_path, target)
        return target
    except Exception as exc:
        print(f"[BACKGROUND] MP4 render failed ({exc}) — poster fallback.")
        return None
    finally:
        for p in (tmp_path,):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


def ensure_backgrounds(themes=None, force=False):
    """
    Generate posters for every theme (and the loop MP4 for the active theme).

    Cheap and idempotent — safe to call at app startup.
    """
    themes = themes or THEMES
    for theme in themes:
        try:
            render_poster(theme, force=force)
        except Exception as exc:
            print(f"[BACKGROUND] Poster render failed for '{theme}': {exc}")
    _ensure_dir()
    return {theme: poster_path(theme) for theme in themes}


def list_themes():
    """Return available themes with asset presence."""
    out = []
    for theme in THEMES:
        poster = os.path.exists(poster_path(theme))
        loop = os.path.exists(loop_path(theme))
        out.append({"theme": theme, "poster": poster, "loop": loop})
    return out


def _active_theme():
    try:
        import db_manager
        stored = db_manager.get_operator_setting("background_theme", "")
        if stored in THEMES:
            return stored
    except Exception:
        pass
    return BACKGROUND_THEME if BACKGROUND_THEME in THEMES else "train"


def set_active_theme(theme):
    """Persist the active ambient theme (admin API)."""
    if theme not in THEMES:
        raise ValueError(f"Unknown background theme: {theme}")
    import db_manager
    db_manager.set_operator_setting("background_theme", theme)
    return theme


def get_background_for_ui(theme=None):
    """Return the UI context dict for the ambient background layer."""
    theme = theme or _active_theme()
    if theme not in PAINTERS:
        theme = "train"
    try:
        render_poster(theme)
    except Exception:
        pass
    poster = os.path.join(BACKGROUND_DIR, f"{theme}.jpg")
    loop = os.path.join(BACKGROUND_DIR, f"{theme}.mp4")
    has_video = os.path.exists(loop)
    return {
        "theme": theme,
        "has_video": has_video,
        "video_url": f"/static/backgrounds/{theme}.mp4" if has_video else "",
        "poster_url": f"/static/backgrounds/{theme}.jpg",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render ambient UI background assets.")
    parser.add_argument("--theme", choices=THEMES, default=None, help="Render a single theme.")
    parser.add_argument("--loop", action="store_true", help="Also compile the seamless-loop MP4.")
    args = parser.parse_args()

    if args.loop:
        os.environ["FAST_VIDEO_RENDER"] = "False"
        try:
            from config import FAST_VIDEO_RENDER as _dummy  # noqa: F401
        except ImportError:
            pass

    themes = [args.theme] if args.theme else THEMES
    ensure_backgrounds(themes=themes, force=True)
    for theme in themes:
        print(f"[BACKGROUND] poster -> {poster_path(theme)}")
        if args.loop:
            path = render_loop(theme, force=True)
            print(f"[BACKGROUND] loop    -> {path or 'poster fallback'}")

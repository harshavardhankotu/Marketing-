"""
Vertical Video Factory — 9:16 HD reels.

Compiles vertical (1080x1920) H.264 MP4 videos using MoviePy, gTTS and Pillow:

    * Pillow frame loop (10 fps) with a dynamic Ken Burns zoom (0.90 → 1.02)
      over high-contrast product graphics.
    * Price / discount / CTA overlays.
    * ``FAST_VIDEO_RENDER=True`` bypasses audio encoding and renders a 1-fps
      mock clip (for CI / testing).

Resource safety: rendering is wrapped in ``try / finally`` and explicitly
closes ``audio_clip``, ``video``, and removes the temporary ``temp_mp3`` so no
file handles or temp files leak on Windows.
"""

import os
import sys
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import CAMPAIGN_STATIC_DIR, FAST_VIDEO_RENDER  # noqa: E402


def _mock_script(product):
    title = product.get("title", "this deal")
    discount = product.get("discount", "20")
    price = product.get("price", "best price")
    return {
        "hook": f"Check out the {title} before the discount ends!",
        "body": f"A solid {discount}% off at ₹{price} — honest value, no gimmicks.",
        "cta": "Tap the link to see real buyer reviews. *Affiliate link — I may earn a commission at no extra cost to you.",
        "visual_cues": "0s: Product hero. 5s: Price stamp. 12s: CTA button.",
    }


def generate_video_scripts(product):
    """
    Produce a voiceover script dict (hook/body/cta). Uses a deterministic
    offline template by default; a Gemini-backed variant can be wired in
    without changing the render pipeline.
    """
    return _mock_script(product)


def _render_poster(product, script):
    """
    Render the high-contrast product poster graphic with price/discount/CTA
    overlays. Returns the poster file path.
    """
    from PIL import Image, ImageDraw, ImageFont

    os.makedirs(CAMPAIGN_STATIC_DIR, exist_ok=True)
    campaign_id = str(product.get("id", "unknown"))
    poster_path = os.path.join(CAMPAIGN_STATIC_DIR, f"poster_{campaign_id}.png")

    canvas = Image.new("RGB", (1080, 1920), (16, 16, 22))
    draw = ImageDraw.Draw(canvas)

    try:
        font_large = ImageFont.truetype("arialbd.ttf", 96)
        font_mid = ImageFont.truetype("arial.ttf", 60)
        font_small = ImageFont.truetype("arial.ttf", 44)
    except Exception:
        font_large = ImageFont.load_default()
        font_mid = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Accent bands.
    draw.rectangle([0, 0, 1080, 24], fill=(245, 158, 11))
    draw.rectangle([0, 1896, 1080, 1920], fill=(34, 197, 94))

    title = str(product.get("title", "Great deal"))[:48]
    price = product.get("price", "—")
    discount = product.get("discount", "0")

    draw.text((60, 200), title, font=font_large, fill=(255, 255, 255))
    draw.text((60, 420), f"₹{price}", font=font_large, fill=(245, 158, 11))
    draw.text((60, 600), f"{discount}% OFF", font=font_mid, fill=(34, 197, 94))
    draw.text((60, 760), "Limited-time deal on Amazon.in", font=font_mid, fill=(200, 200, 210))

    # CTA panel.
    draw.rectangle([60, 1560, 1020, 1720], fill=(34, 197, 94), outline=(22, 163, 74), width=3)
    draw.text((120, 1610), "SHOP NOW  →", font=font_mid, fill=(255, 255, 255))

    # Disclosure strip.
    disclosure = "*Affiliate link — I may earn a commission at no extra cost to you."
    draw.text((60, 1800), disclosure, font=font_small, fill=(160, 160, 170))

    canvas.save(poster_path, "PNG")
    return poster_path


def render_video_clip(product, script):
    """
    Render a 9:16 vertical video and/or a poster graphic.

    Returns the file path of the primary asset (video when MoviePy is
    available, otherwise the Pillow poster) so the distributor can attach it.
    """
    campaign_id = str(product.get("id", "unknown"))
    print(f"[VIDEO-ENGINE] Rendering asset for campaign {campaign_id}...")

    # Poster always renders (Pillow only).
    poster_path = _render_poster(product, script)

    if FAST_VIDEO_RENDER:
        print("[VIDEO-ENGINE] FAST_VIDEO_RENDER active — returning poster asset only.")
        return poster_path

    try:
        import moviepy  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        print("[VIDEO-ENGINE] moviepy not installed — returning poster asset.")
        return poster_path

    temp_mp3 = None
    audio_clip = None
    video = None
    output_file = os.path.join(CAMPAIGN_STATIC_DIR, f"video_{campaign_id}.mp4")

    try:
        from gtts import gTTS
        from PIL import Image
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        from moviepy.audio.io.AudioFileClip import AudioFileClip

        # 1. Voiceover via gTTS.
        hook = script.get("hook", "")
        body = script.get("body", "")
        cta = script.get("cta", "")
        voiceover_text = f"{hook}. {body}. {cta}"
        try:
            temp_mp3 = os.path.join(tempfile.gettempdir(), f"voiceover_{campaign_id}.mp3")
            gTTS(text=voiceover_text, lang="en", slow=False).save(temp_mp3)
            audio_clip = AudioFileClip(temp_mp3)
            duration = int(max(8, min(15, audio_clip.duration)))
        except Exception as exc:
            print(f"[VIDEO-ENGINE] TTS failed ({exc}) — rendering silent clip.")
            duration = 10

        # 2. Pillow frame loop with Ken Burns zoom (0.90 → 1.02).
        fps = 10
        total_frames = fps * duration
        frames = []
        base = Image.open(poster_path)

        for i in range(total_frames):
            t = i / total_frames
            scale = 0.90 + 0.12 * t  # ends at 1.02
            w = int(1080 * scale)
            h = int(1920 * scale)
            scaled = base.resize((w, h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (1080, 1920), (16, 16, 22))
            x = (1080 - w) // 2
            y = (1920 - h) // 2
            canvas.paste(scaled, (x, y))
            frames.append(numpy.array(canvas))

        # 3. Compile clip.
        video = ImageSequenceClip(frames, fps=fps)
        if audio_clip:
            try:
                if hasattr(video, "with_audio"):
                    video = video.with_audio(audio_clip)
                else:
                    video = video.set_audio(audio_clip)
            except Exception as exc:
                print(f"[VIDEO-ENGINE] Audio attach failed ({exc}) — skipping audio.")

        video.write_videofile(
            output_file, codec="libx264", audio_codec="aac", fps=fps, logger=None
        )
        print(f"[VIDEO-ENGINE] Rendered video: {output_file}")
        return output_file
    except Exception as exc:
        print(f"[VIDEO-ENGINE] Video compilation failed ({exc}) — returning poster.")
        return poster_path
    finally:
        try:
            if audio_clip:
                audio_clip.close()
        except Exception:
            pass
        try:
            if video:
                video.close()
        except Exception:
            pass
        try:
            if temp_mp3 and os.path.exists(temp_mp3):
                os.remove(temp_mp3)
        except Exception:
            pass

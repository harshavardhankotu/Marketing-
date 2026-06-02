import os
import json
import tempfile
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini using the new google-genai SDK
api_key = os.getenv("GEMINI_API_KEY")
client = None

if api_key:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except ImportError:
        try:
            import google.generativeai as genai_old
            genai_old.configure(api_key=api_key)
            client = "legacy"
        except ImportError:
            pass

def generate_video_scripts(product):
    title = product.get('title', 'Service Offer')
    price = product.get('price', 'N/A')
    discount = product.get('discount', 40)
    why = product.get('value_explanation', {}).get('why_this_product', '')
    
    mock_script = {
        "hook": "Stop throwing away ₹15,000 every year on bloated premiums!",
        "body": f"This high-yield {title} campaign can save you up to {discount}% on your monthly overheads instantly. Simple quote, zero obligation.",
        "cta": "Get your free quote now! Link in bio. *Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad",
        "visual_cues": "0s: Close up of savings metric. 5s: Zoom into zero cost badge. 15s: Intense green GET FREE QUOTE NOW button."
    }
    
    # Import reliability services
    from bots.resilience import call_with_retry, timeout_call
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    if not client:
        return mock_script

    # 1. Quota Check
    if check_quota("gemini") == "BLOCKED":
        print("[VIDEO_SCRIPT] Daily quota blocked for 'gemini'. Returning mock script.")
        return mock_script

    # 2. Circuit Breaker Check
    breaker = get_breaker("gemini", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"[VIDEO_SCRIPT] Circuit Breaker open for 'gemini'. Returning mock script. Details: {e}")
        return mock_script

    prompt = f"""
    Write a 30-second high-conversion viral video script (Reels/Shorts/TikTok) for this high-ticket CPA lead generation offer.
    
    Campaign: {title}
    Vertical Payout: ₹{price}
    Key Optimization Feature: Save up to {discount}%
    Key Value Proposition: {why}
    
    Rules:
    - Write high-impact financial pain-point and cost-optimization hooks rather than physical product reviews (e.g. "Stop throwing away ₹15,000 every year...").
    - The copy MUST explicitly embed the required FTC marketing disclosure at the end of the CTA: "*Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad".
    
    Format:
    - Hook: A 3-second attention-grabbing opener.
    - Body: 20 seconds of punchy benefits.
    - CTA: 7-second clear call to action with disclosure.
    - Visual Cues: Brief descriptions of what should happen on screen.
    
    Output strictly as a JSON object with keys: "hook", "body", "cta", "visual_cues".
    Do not wrap in markdown code blocks.
    """
    
    def make_api_call():
        consume_quota("gemini")
        if client == "legacy":
            import google.generativeai as genai_old
            model = genai_old.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content(prompt)
            return response.text.strip()
        else:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            return response.text.strip()

    def make_api_call_with_timeout():
        return timeout_call(10, make_api_call)

    # 3. Call with Retry & Timeout
    retry_res = call_with_retry(
        make_api_call_with_timeout,
        max_retries=3,
        base_delay=1.0,
        max_delay=5.0,
        provider="gemini"
    )

    if not retry_res["success"]:
        # Record failure to circuit breaker
        breaker.record_failure()
        print(f"[VIDEO_SCRIPT] Gemini API execution failed: {retry_res['error']}. Returning mock script.")
        return mock_script

    # Record success to circuit breaker
    breaker.record_success()
    text = retry_res["result"]

    try:
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        parsed = json.loads(text)
        # Ensure FTC disclosure is attached
        disclosure = "*Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad"
        if "cta" in parsed and "#Ad" not in parsed["cta"]:
            parsed["cta"] = parsed["cta"].strip() + " " + disclosure
        return parsed
    except Exception as e:
        print(f"Error parsing video script JSON: {e}")
        return mock_script

def render_video_clip(product, script):
    """
    Renders a 15-second 9:16 vertical MP4 video for the CPA campaign.
    Saves it to static/campaigns/video_{campaign_id}.mp4.
    Applies Ken Burns typographic zoom over large financial metrics and green CTA panels.
    """
    campaign_id = product.get('id', 'unknown')
    print(f"    [VIDEO-RENDER] Starting video render for campaign {campaign_id}...")
    
    # 1. Output file path
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    output_dir = os.path.join(base_dir, 'static', 'campaigns')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"video_{campaign_id}.mp4")
    
    # 2. Get voiceover text from script
    hook = script.get("hook", "Check out this deal!")
    body = script.get("body", "")
    cta = script.get("cta", "Link in bio to apply!")
    voiceover_text = f"{hook}. {body}. {cta}"
    
    temp_mp3 = None
    video = None
    
    try:
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
        
        fast_mode = os.getenv("FAST_VIDEO_RENDER") == "True"
        
        if fast_mode:
            print("      [VIDEO-RENDER] FAST_VIDEO_RENDER active. Compiling 1-second 1-fps mock video.")
            duration = 1
            fps = 1
            audio_clip = None
        else:
            from gtts import gTTS
            from moviepy.audio.io.AudioFileClip import AudioFileClip
            
            # 3. Generate Audio via gTTS
            print("      [VIDEO-RENDER] Generating voiceover TTS MP3...")
            tts = gTTS(text=voiceover_text, lang='en', slow=False)
            temp_dir = tempfile.gettempdir()
            temp_mp3 = os.path.join(temp_dir, f"voiceover_{campaign_id}.mp3")
            tts.save(temp_mp3)
            
            # Determine duration from audio file or default to 15 seconds
            audio_clip = AudioFileClip(temp_mp3)
            audio_duration = audio_clip.duration
            duration = int(max(10, min(15, audio_duration))) # clamp between 10 and 15 seconds
            fps = 10
            print(f"      [VIDEO-RENDER] Voiceover duration: {audio_duration:.2f}s (clamped to {duration}s)")
        
        # 4. Pillow Typographic design parameters
        fps = 10
        total_frames = fps * duration
        frames = []
        
        print(f"      [VIDEO-RENDER] Rendering {total_frames} typography frames frame-by-frame...")
        
        # Select fonts
        font_large = None
        font_hook = None
        font_cta = None
        for font_name in ["Inter-Bold.ttf", "arialbd.ttf", "arial.ttf"]:
            try:
                font_large = ImageFont.truetype(font_name, 80)
                font_hook = ImageFont.truetype(font_name, 52)
                font_cta = ImageFont.truetype(font_name, 48)
                break
            except Exception:
                pass
        if not font_large:
            font_large = ImageFont.load_default()
            font_hook = ImageFont.load_default()
            font_cta = ImageFont.load_default()
            
        discount = product.get('discount', 40)
        payout = product.get('price', '3,200')
        brand = product.get('brand', 'CPA Partner')
        title = product.get('title', 'CPA Offer')
        
        # Create base image for typography
        base_img = Image.new("RGB", (1080, 1080), (18, 18, 22))
        draw_base = ImageDraw.Draw(base_img)
        
        # Draw background elements
        draw_base.rectangle([50, 50, 1030, 1030], outline=(40, 40, 50), width=3)
        
        # Draw vertical name/brand
        draw_base.text((100, 150), brand.upper(), font=font_hook, fill=(34, 197, 94)) # Green accent brand
        
        # Draw big financial metric
        draw_base.text((100, 350), f"SAVINGS: {discount}%", font=font_large, fill=(245, 158, 11)) # Amber metric
        draw_base.text((100, 500), f"PAYOUT: ₹{payout}", font=font_large, fill=(255, 255, 255))
        
        # Draw short value explainer text
        explainer_txt = product.get('description', '')
        if len(explainer_txt) > 80:
            explainer_txt = explainer_txt[:77] + "..."
        draw_base.text((100, 750), explainer_txt, font=font_cta, fill=(156, 163, 175))
        
        # DNI Pool Lookup: allocate tracking number & extension pin for high-visibility overlay
        from bots.distributor import allocate_tracking_voice_vector
        voice_vector = allocate_tracking_voice_vector(campaign_id)
        tracking_number = voice_vector.get("tracking_number", "+18005550199")
        extension_pin = voice_vector.get("extension_pin", "999")

        # 5. Compile frame loop (applying Ken Burns Zoom and Bottom CTA box)
        for i in range(total_frames):
            # Canvas 1080x1920 (9:16 vertical video)
            canvas = Image.new("RGB", (1080, 1920), (12, 12, 16))
            draw = ImageDraw.Draw(canvas)
            
            # Subtle accent lines
            draw.rectangle([0, 0, 1080, 20], fill=(245, 158, 11)) # Amber top
            draw.rectangle([0, 1900, 1080, 1920], fill=(34, 197, 94)) # Green bottom
            
            # Ken Burns effect: Zoom the base typography image over time
            t = i / total_frames
            scale = 0.95 + 0.10 * t
            
            new_w = int(base_img.width * scale)
            new_h = int(base_img.height * scale)
            scaled_img = base_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Paste scaled image in the center
            x = (1080 - new_w) // 2
            y = (1920 - new_h) // 2 - 100
            canvas.paste(scaled_img, (x, y))
            
            # Overlay Hook text panel at the top
            draw.rectangle([60, 120, 1020, 320], fill=(26, 26, 31, 230), outline=(40, 40, 50), width=2)
            hook_text = hook
            if len(hook_text) > 40:
                hook_text = hook_text[:37] + "..."
            draw.text((100, 180), hook_text, font=font_hook, fill=(241, 241, 244))
            
            # Overlay DNI high-visibility voice vector panel right above the CTA button
            draw.rectangle([80, 1400, 1000, 1550], fill=(26, 26, 31, 240), outline=(245, 158, 11), width=2)
            voice_overlay = f"Call Now: {tracking_number} Pin: {extension_pin}"
            draw.text((120, 1445), voice_overlay, font=font_hook, fill=(245, 158, 11))

            # Overlay an intense green-accented GET FREE QUOTE NOW CTA button at the bottom
            draw.rectangle([80, 1600, 1000, 1780], fill=(34, 197, 94, 255), outline=(22, 163, 74), width=3)
            # Inner border for a premium feel
            draw.rectangle([90, 1610, 990, 1770], outline=(255, 255, 255, 100), width=2)
            draw.text((180, 1660), "⚡ GET FREE QUOTE NOW", font=font_hook, fill=(255, 255, 255))
            
            # Dynamic amber progress bar at the very bottom
            progress_w = int((i / total_frames) * 1080)
            draw.rectangle([0, 1890, progress_w, 1895], fill=(245, 158, 11))
            
            frames.append(np.array(canvas))
            
        # 6. Build MoviePy Clip
        print("      [VIDEO-RENDER] Compiling video clip from frames sequence...")
        video = ImageSequenceClip(frames, fps=fps)
        
        # Sync audio and write file (compatible with moviepy v1 and v2)
        if not fast_mode and audio_clip:
            print("      [VIDEO-RENDER] Attaching voiceover audio clip...")
            if hasattr(video, 'with_audio'):
                video = video.with_audio(audio_clip.with_duration(duration))
            else:
                video = video.set_audio(audio_clip.set_duration(duration))
        
        print(f"      [VIDEO-RENDER] Exporting high-definition MP4 to: {output_file}...")
        video.write_videofile(
            output_file,
            codec="libx264",
            audio_codec="aac",
            fps=fps,
            logger=None
        )
        
        print(f"    [VIDEO-RENDER] Success! Compiled: video_{campaign_id}.mp4 ({duration}s)")
        
    except Exception as e:
        print(f"    [VIDEO-RENDER] ERROR: Video compilation failed: {e}")
    finally:
        try:
            if 'audio_clip' in locals() and audio_clip:
                audio_clip.close()
                print("      [VIDEO-RENDER] Closed audio clip handle.")
        except Exception as audio_err:
            print(f"      [VIDEO-RENDER] Failed to close audio clip: {audio_err}")
            
        try:
            if video:
                video.close()
                print("      [VIDEO-RENDER] Closed video clip handle.")
        except Exception as video_err:
            print(f"      [VIDEO-RENDER] Failed to close video clip: {video_err}")
            
        try:
            if temp_mp3 and os.path.exists(temp_mp3):
                os.remove(temp_mp3)
                print("      [VIDEO-RENDER] Successfully cleaned up temporary voiceover MP3.")
        except Exception as clean_err:
            print(f"      [VIDEO-RENDER] Failed to delete temporary voiceover MP3: {clean_err}")

def run_script_generation():
    print("Generating short-form video scripts and rendering mp4 reels...")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    input_path = os.path.join(base_dir, 'data', 'output', 'post_data.json')
    output_path = os.path.join(base_dir, 'data', 'output', 'video_scripts.json')
    
    if not os.path.exists(input_path):
        print(f"Input file {input_path} not found.")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        products = json.load(f)

    scripts = []
    for product in products:
        pid = product.get('id', 'unknown')
        print(f"  Creating script and video for {product.get('title', pid)[:30]}...")
        script = generate_video_scripts(product)
        scripts.append({
            "product_id": pid,
            "title": product.get('title'),
            "script": script
        })
        # Render the dynamic vertical video reel
        render_video_clip(product, script)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(scripts, f, indent=4, ensure_ascii=False)
    print(f"Saved {len(scripts)} scripts and reels to {output_path}")

if __name__ == "__main__":
    run_script_generation()

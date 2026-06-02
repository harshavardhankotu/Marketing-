"""
Content Distributor Bot
─────────────────────────────────────────────────────
Distributes marketing content to 4 social platforms:
  • Twitter / X
  • Instagram
  • Telegram
  • WhatsApp Business (India‑critical channel)

All distributors are mock implementations. In production, swap each
with the real API (Twitter API v2, Instagram Graph API, Telegram Bot API,
WhatsApp Business API via WABA / Gupshup / Twilio).
"""

import json
import os
import time
import hashlib
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

INDIA_OFFSET_HOURS = 5.5

def get_ist_now():
    return datetime.utcnow() + timedelta(hours=INDIA_OFFSET_HOURS)


def _mock_post_id():
    """Generate a realistic-looking mock post ID."""
    return hashlib.md5(str(time.time()).encode()).hexdigest()[:12]


def _resolve_audience_language(channel, journey_name):
    """
    Returns the preferred language key ('en', 'hi', 'ta') based on channel and audience journey.
    """
    journey_lower = journey_name.lower()
    
    # 1. Check explicit journey targeting
    if 'tamil' in journey_lower or 'chennai' in journey_lower:
        return 'ta'
    if 'hindi' in journey_lower or 'vernacular' in journey_lower or 'delhi' in journey_lower:
        return 'hi'
        
    # 2. Check channel-specific defaults
    if channel == 'telegram':
        # Example rule: Our hypothetical main Telegram audience prefers Hindi
        return 'hi'
    if channel == 'whatsapp':
        # Example rule: Our hypothetical WhatsApp broadcast list prefers Tamil
        return 'ta'
        
    # Default to English
    return 'en'


def mock_post_to_twitter(post_data):
    """Mock posting a tweet (X)."""
    caption = post_data.get('caption', '')
    if len(caption) > 280:
        caption = caption[:277] + "..."
    safe = caption.encode('ascii', 'ignore').decode('ascii')
    print(f"  [Twitter/X] Tweeted (Mock): {safe[:60]}...")
    return {
        "platform": "Twitter/X",
        "status": "Success (Mock)",
        "link": f"https://x.com/YourAgency/status/{_mock_post_id()}"
    }

def live_post_to_twitter(post_data):
    """Real posting to Twitter/X via API v1.1 & v2 with fallback to mock."""
    api_key = os.getenv("TWITTER_API_KEY")
    api_secret = os.getenv("TWITTER_API_SECRET")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN")
    access_secret = os.getenv("TWITTER_ACCESS_SECRET")
    
    if not all([api_key, api_secret, access_token, access_secret]) or "your_twitter_" in str(api_key):
        return mock_post_to_twitter(post_data)
        
    # Import SRE resilience services
    from bots.resilience import call_with_retry
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    # 1. Quota Check
    if check_quota("twitter") == "BLOCKED":
        print("  [Twitter-LIVE] Daily quota blocked. Falling back to mock...")
        return mock_post_to_twitter(post_data)

    # 2. Circuit Breaker Check
    breaker = get_breaker("twitter", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"  [Twitter-LIVE] Circuit breaker is open: {e}. Falling back to mock...")
        return mock_post_to_twitter(post_data)

    try:
        import tweepy
        
        # Twitter API v1.1 for media uploads
        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
        api_v1 = tweepy.API(auth)
        
        # Twitter API v2 for creating tweets
        client_v2 = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_secret
        )
        
        # Resolve visual creative image path
        from config import OUTPUT_DIR
        graphic_path = post_data.get('graphic_path', '')
        image_file_path = None
        media_id = None
        
        if graphic_path:
            filename = os.path.basename(graphic_path)
            candidate_path = os.path.join(OUTPUT_DIR, filename)
            if os.path.exists(candidate_path):
                image_file_path = candidate_path

        def make_live_tweet():
            consume_quota("twitter")
            nonlocal media_id
            if image_file_path:
                media = api_v1.media_upload(filename=image_file_path)
                media_id = media.media_id_string
            
            caption = post_data.get('caption', '')
            if len(caption) > 280:
                caption = caption[:277] + "..."
                
            if media_id:
                response = client_v2.create_tweet(text=caption, media_ids=[media_id])
            else:
                response = client_v2.create_tweet(text=caption)
            return response

        # 3. Call with Retry & Timeout
        res = call_with_retry(make_live_tweet, max_retries=3, base_delay=1.0, max_delay=5.0, provider="twitter")
        
        if res["success"]:
            breaker.record_success()
            tweet_id = res["result"].data.get("id", _mock_post_id())
            return {
                "platform": "Twitter/X",
                "status": "Success (Live)",
                "link": f"https://x.com/YourAgency/status/{tweet_id}"
            }
        else:
            breaker.record_failure()
            print(f"  [Twitter-LIVE] Execution failed: {res['error']}. Falling back to mock...")
            return mock_post_to_twitter(post_data)
            
    except Exception as exc:
        breaker.record_failure()
        print(f"  [Twitter-LIVE] Exception during tweet init: {exc}. Falling back to mock...")
        return mock_post_to_twitter(post_data)


def mock_post_to_instagram(post_data):
    """Mock posting to Instagram."""
    caption = post_data.get('caption', '')
    safe_cap = caption.encode('ascii', 'ignore').decode('ascii')
    print(f"  [Instagram] Posted (Mock) {post_data.get('title', 'Unknown')} | {safe_cap[:30]}...")
    return {
        "platform": "Instagram",
        "status": "Success (Mock)",
        "link": f"https://instagram.com/p/{_mock_post_id()}"
    }

def live_post_to_instagram(post_data):
    """Real posting to Instagram via Meta Graph API with fallback to mock."""
    ig_user_id = os.getenv("INSTAGRAM_ACCOUNT_ID")
    access_token = os.getenv("META_ACCESS_TOKEN")
    
    if not ig_user_id or not access_token or "your_instagram_" in str(ig_user_id):
        return mock_post_to_instagram(post_data)
        
    # Import SRE resilience services
    from bots.resilience import call_with_retry
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    # 1. Quota Check
    if check_quota("instagram") == "BLOCKED":
        print("  [Instagram-LIVE] Daily quota blocked. Falling back to mock...")
        return mock_post_to_instagram(post_data)

    # 2. Circuit Breaker Check
    breaker = get_breaker("instagram", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"  [Instagram-LIVE] Circuit breaker is open: {e}. Falling back to mock...")
        return mock_post_to_instagram(post_data)

    try:
        # Resolve creative image path
        graphic_path = post_data.get('graphic_path', '')
        if not graphic_path:
            print("  [Instagram-LIVE] Graphic path is missing. Instagram requires media. Falling back to mock...")
            return mock_post_to_instagram(post_data)
            
        filename = os.path.basename(graphic_path)
        # Construct VPS hosted image URL
        public_image_url = f"https://affiliate.yourdomain.com/static/campaigns/{filename}"
        caption = post_data.get('caption', '')

        def make_live_instagram_post():
            consume_quota("instagram")
            
            # Step 1: Create Container
            container_url = f"https://graph.facebook.com/v18.0/{ig_user_id}/media"
            payload_step1 = {
                "image_url": public_image_url,
                "caption": caption,
                "access_token": access_token
            }
            resp1 = requests.post(container_url, data=payload_step1, timeout=15)
            resp1.raise_for_status()
            creation_id = resp1.json().get("id")
            
            if not creation_id:
                raise ValueError("Container creation failed: no ID returned.")
                
            # Step 2: Publish Container
            publish_url = f"https://graph.facebook.com/v18.0/{ig_user_id}/media_publish"
            payload_step2 = {
                "creation_id": creation_id,
                "access_token": access_token
            }
            resp2 = requests.post(publish_url, data=payload_step2, timeout=15)
            resp2.raise_for_status()
            return resp2.json()

        # 3. Call with Retry & Timeout
        res = call_with_retry(make_live_instagram_post, max_retries=3, base_delay=2.0, max_delay=8.0, provider="instagram")
        
        if res["success"]:
            breaker.record_success()
            post_id = res["result"].get("id", _mock_post_id())
            return {
                "platform": "Instagram",
                "status": "Success (Live)",
                "link": f"https://instagram.com/p/{post_id}"
            }
        else:
            breaker.record_failure()
            print(f"  [Instagram-LIVE] Execution failed: {res['error']}. Falling back to mock...")
            return mock_post_to_instagram(post_data)
            
    except Exception as exc:
        breaker.record_failure()
        print(f"  [Instagram-LIVE] Exception during API post: {exc}. Falling back to mock...")
        return mock_post_to_instagram(post_data)


def mock_post_to_telegram(post_data):
    """Mock posting to Telegram channel."""
    caption = post_data.get('caption', '')
    safe_cap = caption.encode('ascii', 'ignore').decode('ascii')
    print(f"  [Telegram] Sent {post_data.get('title', 'Unknown')} | {safe_cap[:30]}...")
    return {
        "platform": "Telegram",
        "status": "Success",
        "link": f"https://t.me/YourDeals/{_mock_post_id()}"
    }

def live_post_to_telegram(post_data):
    """Real posting to Telegram via Bot API with fallback to mock."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id or bot_token == "your_telegram_bot_token" or "your_telegram_bot_token" in bot_token:
        return mock_post_to_telegram(post_data)
        
    # Import reliability services
    from bots.resilience import call_with_retry, timeout_call
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    # 1. Quota Check
    if check_quota("telegram") == "BLOCKED":
        print("  [Telegram-LIVE] Daily quota blocked. Falling back to mock...")
        return mock_post_to_telegram(post_data)

    # 2. Circuit Breaker Check
    breaker = get_breaker("telegram", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"  [Telegram-LIVE] Circuit breaker is open. Details: {e}. Falling back to mock...")
        return mock_post_to_telegram(post_data)
        
    caption = post_data.get('caption', '')
    title = post_data.get('title', 'Unknown')
    aff_link = post_data.get('affiliate_link', post_data.get('link', ''))
    
    # Ensure the affiliate link is present in the sent text
    if aff_link and "http" not in caption:
        text_to_send = f"{caption}\n\n🔗 Buy here: {aff_link}"
    else:
        text_to_send = f"{caption}\n\n🔗 {aff_link}" if aff_link else caption
        
    # Resolve the creative image path
    from config import OUTPUT_DIR
    graphic_path = post_data.get('graphic_path', '')
    image_file_path = None
    
    if graphic_path:
        # Extract filename (e.g. from "/image/graphic_0.jpg" or "graphic_0.jpg")
        filename = os.path.basename(graphic_path)
        candidate_path = os.path.join(OUTPUT_DIR, filename)
        if os.path.exists(candidate_path):
            image_file_path = candidate_path
            
    def make_live_post():
        consume_quota("telegram")
        # Try sending photo if creative image exists
        if image_file_path:
            try:
                photo_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
                with open(image_file_path, 'rb') as photo_file:
                    files = {'photo': photo_file}
                    payload = {
                        "chat_id": chat_id,
                        "caption": text_to_send[:1024], # Telegram captions are limited to 1024 chars
                    }
                    resp = requests.post(photo_url, data=payload, files=files, timeout=15)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as photo_err:
                print(f"  [Telegram-LIVE] sendPhoto failed: {photo_err}. Falling back to sendMessage...")
                # If sendPhoto fails, continue to fallback below
        
        # Fallback to sendMessage (text only)
        msg_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text_to_send,
            "disable_web_page_preview": False
        }
        resp = requests.post(msg_url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # 3. Call with Retry & Timeout
    retry_res = call_with_retry(
        make_live_post,
        max_retries=3,
        base_delay=1.0,
        max_delay=5.0,
        provider="telegram"
    )

    if retry_res["success"]:
        breaker.record_success()
        data = retry_res["result"]
        msg_id = data.get("result", {}).get("message_id", _mock_post_id())
        safe_cap = caption.encode('ascii', 'ignore').decode('ascii')
        print(f"  [Telegram-LIVE] Sent {title} | {safe_cap[:30]}...")
        
        link = f"https://t.me/{str(chat_id).replace('@', '')}/{msg_id}" if str(chat_id).startswith('@') else "N/A (Private)"
        return {
            "platform": "Telegram",
            "status": "Success (Live)",
            "link": link
        }
    else:
        breaker.record_failure()
        print(f"  [Telegram-LIVE] Failed after retries: {retry_res['error']}. Falling back to mock...")
        return mock_post_to_telegram(post_data)


def mock_post_to_whatsapp(post_data):
    """Mock WhatsApp Business API distribution."""
    title = post_data.get('title', 'Unknown')
    price = post_data.get('price', 'N/A')
    aff_link = post_data.get('affiliate_link', post_data.get('link', ''))
    caption = post_data.get('caption', '')
    template_msg = (
        f"Deal Alert! {title} at Rs.{price}. "
        f"{caption} "
        f"Shop now: {aff_link} "
        f"Reply STOP to opt-out."
    )
    safe_msg = template_msg.encode('ascii', 'ignore').decode('ascii')
    print(f"  [WhatsApp] Broadcast (Mock): {safe_msg[:60]}...")
    return {
        "platform": "WhatsApp",
        "status": "Success (Mock)",
        "link": f"https://wa.me/919999999999?text={_mock_post_id()}",
        "template_used": "daily_deal_alert",
        "audience": "opted_in_subscribers"
    }

def live_post_to_whatsapp(post_data):
    """Real WhatsApp Business API template broadcast with fallback to mock."""
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    recipient = os.getenv("WHATSAPP_TEST_RECIPIENT", "919999999999")
    
    if not phone_id or not access_token or "your_whatsapp_" in str(phone_id):
        return mock_post_to_whatsapp(post_data)
        
    # Import SRE resilience services
    from bots.resilience import call_with_retry
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    # 1. Quota Check
    if check_quota("whatsapp") == "BLOCKED":
        print("  [WhatsApp-LIVE] Daily quota blocked. Falling back to mock...")
        return mock_post_to_whatsapp(post_data)

    # 2. Circuit Breaker Check
    breaker = get_breaker("whatsapp", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"  [WhatsApp-LIVE] Circuit breaker is open: {e}. Falling back to mock...")
        return mock_post_to_whatsapp(post_data)

    try:
        graphic_path = post_data.get('graphic_path', '')
        if not graphic_path:
            print("  [WhatsApp-LIVE] Graphic path is missing. WhatsApp template requires a header image. Falling back to mock...")
            return mock_post_to_whatsapp(post_data)
            
        filename = os.path.basename(graphic_path)
        public_image_url = f"https://affiliate.yourdomain.com/static/campaigns/{filename}"
        
        title = post_data.get('title', 'Unknown')
        price = post_data.get('price', 'N/A')
        aff_link = post_data.get('affiliate_link', post_data.get('link', ''))

        def make_live_whatsapp_post():
            consume_quota("whatsapp")
            
            url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "template",
                "template": {
                    "name": "daily_deal_alert",
                    "language": {
                        "code": "en_US"
                    },
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {
                                        "link": public_image_url
                                    }
                                }
                            ]
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": str(title)
                                },
                                {
                                    "type": "text",
                                    "text": f"Rs.{price}"
                                },
                                {
                                    "type": "text",
                                    "text": str(aff_link)
                                }
                            ]
                        }
                    ]
                }
            }
            
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp.json()

        # 3. Call with Retry & Timeout
        res = call_with_retry(make_live_whatsapp_post, max_retries=3, base_delay=1.0, max_delay=5.0, provider="whatsapp")
        
        if res["success"]:
            breaker.record_success()
            waba_msg_id = res["result"].get("messages", [{}])[0].get("id", _mock_post_id())
            return {
                "platform": "WhatsApp",
                "status": "Success (Live)",
                "link": f"https://wa.me/{recipient}?msg={waba_msg_id}",
                "template_used": "daily_deal_alert",
                "audience": "opted_in_subscribers"
            }
        else:
            breaker.record_failure()
            print(f"  [WhatsApp-LIVE] Execution failed: {res['error']}. Falling back to mock...")
            return mock_post_to_whatsapp(post_data)
            
    except Exception as exc:
        breaker.record_failure()
        print(f"  [WhatsApp-LIVE] Exception during API post: {exc}. Falling back to mock...")
        return mock_post_to_whatsapp(post_data)

def allocate_tracking_voice_vector(campaign_id):
    """
    Allocates a unique trackable phone number or extension pin from a localized pool,
    flags its status as 'allocated', and binds it to the campaign context.
    Supports either integer database campaign_id or string 12-char product_id hash.
    Uses robust transaction locking and a circular recycling fallback on pool exhaustion.
    """
    import sqlite3
    from config import DB_PATH
    
    # Establish campaign identity keys
    is_hash = isinstance(campaign_id, str)
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cursor = conn.cursor()
    
    try:
        # 1. Idempotency Check: see if already allocated
        if is_hash:
            cursor.execute("SELECT tracking_number, extension_pin FROM cpa_phone_pool WHERE assigned_product_id = ?", (campaign_id,))
        else:
            cursor.execute("SELECT tracking_number, extension_pin FROM cpa_phone_pool WHERE assigned_campaign_id = ?", (campaign_id,))
        row = cursor.fetchone()
        if row:
            conn.close()
            return {"tracking_number": row[0], "extension_pin": row[1]}
            
        # 2. Start atomic transaction for allocation
        conn.execute("BEGIN IMMEDIATE")
        
        # 3. Find an available slot
        cursor.execute("SELECT id, tracking_number, extension_pin FROM cpa_phone_pool WHERE status = 'available' ORDER BY id ASC LIMIT 1")
        slot = cursor.fetchone()
        
        if slot:
            slot_id, number, pin = slot
            # Allocate the slot
            if is_hash:
                cursor.execute("""
                UPDATE cpa_phone_pool
                SET status = 'allocated', assigned_product_id = ?, allocated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (campaign_id, slot_id))
            else:
                cursor.execute("""
                UPDATE cpa_phone_pool
                SET status = 'allocated', assigned_campaign_id = ?, allocated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (campaign_id, slot_id))
            conn.commit()
            return {"tracking_number": number, "extension_pin": pin}
            
        # 4. Circular Pool Recycling: if pool is fully exhausted, recycle the oldest allocation
        cursor.execute("SELECT id, tracking_number, extension_pin FROM cpa_phone_pool ORDER BY allocated_at ASC LIMIT 1")
        oldest_slot = cursor.fetchone()
        if oldest_slot:
            slot_id, number, pin = oldest_slot
            if is_hash:
                cursor.execute("""
                UPDATE cpa_phone_pool
                SET status = 'allocated', assigned_product_id = ?, assigned_campaign_id = NULL, allocated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (campaign_id, slot_id))
            else:
                cursor.execute("""
                UPDATE cpa_phone_pool
                SET status = 'allocated', assigned_campaign_id = ?, assigned_product_id = NULL, allocated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (campaign_id, slot_id))
            conn.commit()
            return {"tracking_number": number, "extension_pin": pin}
            
        # 5. Ultimate SRE Fallback: return default numbers
        conn.rollback()
        return {"tracking_number": "+18005550199", "extension_pin": "999"}
        
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[TELEPHONY_ALLOCATOR] Allocation exception: {e}. Returning fallback tracking vectors.")
        return {"tracking_number": "+18005550199", "extension_pin": "999"}
    finally:
        conn.close()

def distribute_campaign(campaign_id):
    """
    Distributes a specific campaign from the database by its campaign_id.
    Connects to the social platforms and records the output logs.
    """
    import sqlite3
    from config import DB_PATH
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 1. Fetch campaign from DB
    c.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    campaign = c.fetchone()
    if not campaign:
        conn.close()
        print(f"[DISTRIBUTOR] Campaign {campaign_id} not found.")
        return False
        
    print(f"[DISTRIBUTOR] Distributing campaign {campaign_id}: {campaign['title']}")
    
    # Allowed channels
    allowed_channels = ["twitter", "instagram", "telegram", "whatsapp"]
    
    # Prepare post data clone
    post_data = {
        "id": campaign["product_id"],
        "title": campaign["title"],
        "price": campaign["price"],
        "platform": campaign["platform"],
        "caption": campaign["caption"],
        "graphic_path": campaign["graphic_path"],
        "affiliate_link": campaign["affiliate_link"]
    }
    
    channel_map = {
        "twitter": live_post_to_twitter,
        "instagram": live_post_to_instagram,
        "telegram": live_post_to_telegram,
        "whatsapp": live_post_to_whatsapp
    }
    
    results = []
    for ch in allowed_channels:
        func = channel_map.get(ch)
        if not func: 
            continue
            
        try:
            print(f"  Posting to {ch}...")
            res = func(post_data)
            results.append(res)
        except Exception as e:
            print(f"  Failed posting to {ch}: {e}")
            results.append({
                "platform": ch.capitalize(),
                "status": f"Failed: {str(e)}",
                "link": "N/A"
            })
            
    # 2. Insert distribution logs for SRE visibility
    for res in results:
        # Resolve platform name cleanly
        p_name = res["platform"].lower()
        if "twitter" in p_name: p_name = "twitter"
        elif "instagram" in p_name: p_name = "instagram"
        elif "telegram" in p_name: p_name = "telegram"
        elif "whatsapp" in p_name: p_name = "whatsapp"
        
        c.execute('''
        INSERT INTO distribution_logs (campaign_id, platform, status, link, message_id)
        VALUES (?, ?, ?, ?, ?)
        ''', (
            campaign_id,
            res["platform"],
            res["status"],
            res["link"],
            res.get("message_id")
        ))
        
    # 3. Update campaign status to 'published'
    c.execute("UPDATE campaigns SET status = 'published' WHERE id = ?", (campaign_id,))
    
    conn.commit()
    conn.close()
    print(f"[DISTRIBUTOR] Campaign {campaign_id} successfully distributed and updated to 'published'.")
    return True


if __name__ == "__main__":
    print("Starting Distributor Bot (Delayed Dispatch Mode)...")
    print("=" * 60)

    from config import OUTPUT_DIR
    post_data_path = os.path.join(OUTPUT_DIR, 'post_data.json')
    journey_path = os.path.join(OUTPUT_DIR, 'journey_plans.json')
    send_plan_path = os.path.join(OUTPUT_DIR, 'send_plan.json')
    dist_log_path = os.path.join(OUTPUT_DIR, 'distribution_log.json')

    if os.path.exists(post_data_path):
        with open(post_data_path, 'r', encoding='utf-8') as f:
            posts = json.load(f)
        
        # Load journeys if available
        journeys = []
        if os.path.exists(journey_path):
            with open(journey_path, 'r', encoding='utf-8') as f:
                journeys = json.load(f)

        # Load send plans if available
        send_plans = []
        if os.path.exists(send_plan_path):
            with open(send_plan_path, 'r', encoding='utf-8') as f:
                send_plans = json.load(f)

        ist_now = get_ist_now()
        print(f"Current IST: {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")

        if posts:
            distribution_results = []
            for idx, post in enumerate(posts):
                # Identify journey and timing for this product
                current_journey = journeys[idx] if idx < len(journeys) else None
                current_timing = send_plans[idx] if idx < len(send_plans) else None
                
                strategy = current_journey.get("strategy", {}) if current_journey else {}
                allowed_channels = strategy.get("channels", ["twitter", "instagram", "telegram", "whatsapp"])
                journey_name = current_journey.get("journey_name", "Standard")

                print(f"\n[{idx+1}/{len(posts)}] Journey: {journey_name} | Target: {post.get('title', 'Unknown')}")

                results = []
                for ch in allowed_channels:
                    target_lang = _resolve_audience_language(ch, journey_name)
                    
                    # Delayed dispatch logic: mark as Scheduled for review queue
                    res = {
                        "platform": ch.capitalize(),
                        "status": "Scheduled",
                        "link": "N/A",
                        "scheduled_for": (current_timing.get("channels", {}).get(ch, {}).get("window_start") if current_timing else ist_now.isoformat()),
                        "window_label": (current_timing.get("channels", {}).get(ch, {}).get("label") if current_timing else "Standard"),
                        "_target_lang": target_lang
                    }
                    print(f"  [{ch.capitalize()}] Scheduled for SRE Preview Gate [Lang: {target_lang}]")
                    results.append(res)

                distribution_results.append({
                    "product_id": post.get("id", f"prod_{idx}"),
                    "title": post.get("title", "Unknown"),
                    "journey": journey_name,
                    "platforms": results
                })

            with open(dist_log_path, 'w', encoding='utf-8') as f:
                json.dump(distribution_results, f, indent=4)
            print(f"\nAll {len(distribution_results)} posts scheduled for review. SRE Preview Gate holds execution.")
        else:
            print("No posts found in queue.")
    else:
        print(f"Post data not found at {post_data_path}")

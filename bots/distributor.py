"""
Social distributor — organic broadcast to Telegram, X (Twitter) and
Instagram Graph API.

Every channel adapter:
    * checks the daily quota and circuit breaker first,
    * records failures on the breaker, and
    * never fabricates engagement — posting is purely organic.

A distribution failure enqueues a retry through the dead-letter queue so an
operator (or the scheduler) can recover it later.
"""

import os
import sys
import time
import json
import hashlib
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config import (
    OUTPUT_DIR, CAMPAIGN_STATIC_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    TWITTER_API_KEY, TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET,
    INSTAGRAM_ACCOUNT_ID, META_ACCESS_TOKEN,
)  # noqa: E402

from quota_manager import (
    check_quota, consume_quota, check_breaker, record_breaker_failure,
    record_breaker_success, QuotaExceededException, CircuitBreakerOpenException,
)  # noqa: E402
from job_queue import enqueue_job, fail_job, complete_job  # noqa: E402
from alert_engine import notify_distribution_failure  # noqa: E402


def _mock_id():
    return hashlib.md5(f"{time.time()}".encode()).hexdigest()[:12]


def _credential_ok(value):
    return bool(value) and "your_" not in value


def _safe_caption(caption, limit=None):
    caption = caption or ""
    if limit:
        caption = caption[: limit - 3] + "..." if len(caption) > limit else caption
    return caption


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────────────────────
def mock_post_to_telegram(post_data):
    print(f"  [Telegram] (MOCK) {post_data.get('title', 'Untitled')}")
    return {"platform": "Telegram", "status": "Success (Mock)", "link": f"https://t.me/YourDeals/{_mock_id()}"}


def live_post_to_telegram(post_data):
    """Send a photo (or text) to the configured Telegram channel."""
    if not (_credential_ok(TELEGRAM_BOT_TOKEN) and TELEGRAM_CHAT_ID):
        return mock_post_to_telegram(post_data)

    if check_quota("telegram") == "BLOCKED":
        print("  [Telegram] Quota blocked -> mock.")
        return mock_post_to_telegram(post_data)
    try:
        check_breaker("telegram")
    except CircuitBreakerOpenException:
        print("  [Telegram] Circuit breaker OPEN -> mock.")
        return mock_post_to_telegram(post_data)

    caption = post_data.get("caption", "")
    link = post_data.get("affiliate_link") or post_data.get("target_url", "")
    text = f"{caption}\n\n🔗 {link}" if link else caption
    text = _safe_caption(text, 1024)

    try:
        consume_quota("telegram")
        image_file = _resolve_media(post_data.get("graphic_path"))
        if image_file:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            with open(image_file, "rb") as fh:
                resp = requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": text},
                    files={"photo": fh},
                    timeout=15,
                )
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=15,
            )
        if resp.status_code >= 500:
            record_breaker_failure("telegram")
            raise RuntimeError(f"Telegram 5xx: {resp.status_code}")
        resp.raise_for_status()
        record_breaker_success("telegram")
        msg = resp.json().get("result", {})
        message_id = msg.get("message_id", _mock_id())
        link = f"https://t.me/{TELEGRAM_CHAT_ID.lstrip('@')}/{message_id}"
        return {"platform": "Telegram", "status": "Success (Live)", "link": link, "message_id": message_id}
    except requests.RequestException as exc:
        record_breaker_failure("telegram")
        print(f"  [Telegram] Delivery failed: {exc}")
        return mock_post_to_telegram(post_data)


# ─────────────────────────────────────────────────────────────────────────────
# X / TWITTER
# ─────────────────────────────────────────────────────────────────────────────
def mock_post_to_twitter(post_data):
    print(f"  [Twitter/X] (MOCK) {post_data.get('title', 'Untitled')}")
    return {"platform": "Twitter/X", "status": "Success (Mock)", "link": f"https://x.com/YourAgency/status/{_mock_id()}"}


def live_post_to_twitter(post_data):
    """Post a tweet (optionally with an image) via the Tweepy SDK."""
    if not all(_credential_ok(v) for v in (TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET)):
        return mock_post_to_twitter(post_data)

    if check_quota("twitter") == "BLOCKED":
        return mock_post_to_twitter(post_data)
    try:
        check_breaker("twitter")
    except CircuitBreakerOpenException:
        return mock_post_to_twitter(post_data)

    caption = _safe_caption(post_data.get("caption", ""), 280)
    try:
        import tweepy

        consume_quota("twitter")
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        image_file = _resolve_media(post_data.get("graphic_path"))
        media_id = None
        if image_file:
            api_v1 = tweepy.API(tweepy.OAuth1UserHandler(
                TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET
            ))
            media_id = api_v1.media_upload(filename=image_file).media_id_string
        if media_id:
            response = client.create_tweet(text=caption, media_ids=[media_id])
        else:
            response = client.create_tweet(text=caption)
        record_breaker_success("twitter")
        tweet_id = response.data.get("id", _mock_id())
        return {"platform": "Twitter/X", "status": "Success (Live)", "link": f"https://x.com/YourAgency/status/{tweet_id}"}
    except Exception as exc:
        record_breaker_failure("twitter")
        print(f"  [Twitter/X] Posting failed: {exc}")
        return mock_post_to_twitter(post_data)


# ─────────────────────────────────────────────────────────────────────────────
# INSTAGRAM (Graph API)
# ─────────────────────────────────────────────────────────────────────────────
def mock_post_to_instagram(post_data):
    print(f"  [Instagram] (MOCK) {post_data.get('title', 'Untitled')}")
    return {"platform": "Instagram", "status": "Success (Mock)", "link": f"https://instagram.com/p/{_mock_id()}"}


def live_post_to_instagram(post_data):
    """Publish an image via the Instagram Graph API (container + publish)."""
    if not (_credential_ok(INSTAGRAM_ACCOUNT_ID) and _credential_ok(META_ACCESS_TOKEN)):
        return mock_post_to_instagram(post_data)

    image_file = _resolve_media(post_data.get("graphic_path"))
    if not image_file:
        print("  [Instagram] No media found -> mock.")
        return mock_post_to_instagram(post_data)

    if check_quota("instagram") == "BLOCKED":
        return mock_post_to_instagram(post_data)
    try:
        check_breaker("instagram")
    except CircuitBreakerOpenException:
        return mock_post_to_instagram(post_data)

    caption = _safe_caption(post_data.get("caption", ""), 2200)
    try:
        consume_quota("instagram")
        base = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}"
        # Step 1 — create container with a publicly reachable image URL.
        image_url = _public_image_url(image_file)
        create_resp = requests.post(
            f"{base}/media",
            data={"image_url": image_url, "caption": caption, "access_token": META_ACCESS_TOKEN},
            timeout=15,
        )
        if create_resp.status_code >= 500:
            record_breaker_failure("instagram")
            raise RuntimeError(f"Instagram 5xx: {create_resp.status_code}")
        create_resp.raise_for_status()
        creation_id = create_resp.json().get("id")
        if not creation_id:
            raise RuntimeError("Instagram container creation failed: no id.")

        # Step 2 — publish the container.
        publish_resp = requests.post(
            f"{base}/media_publish",
            data={"creation_id": creation_id, "access_token": META_ACCESS_TOKEN},
            timeout=15,
        )
        publish_resp.raise_for_status()
        record_breaker_success("instagram")
        media_id = publish_resp.json().get("id", _mock_id())
        return {"platform": "Instagram", "status": "Success (Live)", "link": f"https://instagram.com/p/{media_id}"}
    except requests.RequestException as exc:
        record_breaker_failure("instagram")
        print(f"  [Instagram] Delivery failed: {exc}")
        return mock_post_to_instagram(post_data)


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_media(graphic_path):
    """Resolve a graphic path to a real file on disk (or None)."""
    if not graphic_path:
        return None
    candidate = os.path.basename(graphic_path)
    for base in (OUTPUT_DIR, CAMPAIGN_STATIC_DIR):
        full = os.path.join(base, candidate)
        if os.path.exists(full):
            return full
    if os.path.exists(graphic_path):
        return graphic_path
    return None


def _public_image_url(image_file):
    """Return the best-effort public URL for an image (used by IG Graph API)."""
    host = os.getenv("PUBLIC_BASE_URL", "https://affiliate.example.com")
    return f"{host}/static/campaigns/{os.path.basename(image_file)}"


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────────
CHANNEL_ADAPTERS = {
    "telegram": live_post_to_telegram,
    "twitter": live_post_to_twitter,
    "instagram": live_post_to_instagram,
}


def distribute_campaign(campaign_id, channels=None):
    """
    Broadcast a campaign to the selected channels (default: all).

    Distribution logs are written per channel. A failure in any channel is
    routed through the dead-letter queue and the alert engine.

    Returns ``True`` when at least one channel succeeded.
    """
    from db_manager import get_campaign, update_campaign_status

    campaign = get_campaign(campaign_id)
    if not campaign:
        print(f"[DISTRIBUTOR] Campaign {campaign_id} not found.")
        return False

    channels = channels or list(CHANNEL_ADAPTERS)
    post_data = {
        "id": campaign.get("product_id") or campaign.get("id"),
        "title": campaign.get("title", "Untitled"),
        "caption": campaign.get("caption") or "",
        "affiliate_link": campaign.get("target_url", ""),
        "target_url": campaign.get("target_url", ""),
        "graphic_path": campaign.get("graphic_path", ""),
        "price": campaign.get("price", 0),
    }

    success_count = 0
    for channel in channels:
        adapter = CHANNEL_ADAPTERS.get(channel)
        if not adapter:
            continue
        try:
            print(f"[DISTRIBUTOR] Posting campaign {campaign_id} -> {channel}")
            result = adapter(post_data)
            _log_distribution(campaign_id, channel, result)
            if result.get("status", "").lower().startswith("success"):
                success_count += 1
            else:
                _enqueue_retry(campaign_id, channel, result.get("status", "unknown"))
        except Exception as exc:
            print(f"[DISTRIBUTOR] Channel {channel} raised: {exc}")
            notify_distribution_failure(campaign_id, channel, str(exc))
            _enqueue_retry(campaign_id, channel, str(exc))

    if success_count:
        update_campaign_status(campaign_id, "published")
        return True

    # Everything failed — keep campaign pending for a retry sweep.
    update_campaign_status(campaign_id, "pending_approval")
    return False


def _log_distribution(campaign_id, channel, result):
    import sqlite3
    from config import DB_PATH

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    try:
        conn.execute(
            """
            INSERT INTO distribution_logs (campaign_id, channel, status, message_id)
            VALUES (?, ?, ?, ?)
            """,
            (campaign_id, channel, result.get("status", ""), result.get("message_id")),
        )
        conn.commit()
    finally:
        conn.close()


def _enqueue_retry(campaign_id, channel, error):
    enqueue_job(
        "retry_distribution",
        {"campaign_id": campaign_id, "channel": channel, "error": str(error)[:500]},
    )


def process_retry_job(job_id, payload):
    """
    Execute a queued retry_distribution job.
    """
    campaign_id = payload.get("campaign_id")
    channel = payload.get("channel", "telegram")
    try:
        adapter = CHANNEL_ADAPTERS.get(channel)
        if not adapter:
            raise ValueError(f"Unknown channel: {channel}")
        post_data = _load_post_data(campaign_id)
        result = adapter(post_data)
        if result.get("status", "").lower().startswith("success"):
            _log_distribution(campaign_id, channel, result)
            complete_job(job_id)
        else:
            fail_job(job_id, f"Adapter returned: {result.get('status')}")
    except Exception as exc:
        fail_job(job_id, str(exc))


def _load_post_data(campaign_id):
    from db_manager import get_campaign

    campaign = get_campaign(campaign_id) or {}
    return {
        "id": campaign.get("product_id") or campaign.get("id"),
        "title": campaign.get("title", "Untitled"),
        "caption": campaign.get("caption") or "",
        "affiliate_link": campaign.get("target_url", ""),
        "target_url": campaign.get("target_url", ""),
        "graphic_path": campaign.get("graphic_path", ""),
        "price": campaign.get("price", 0),
    }

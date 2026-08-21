"""
Automated monthly spot-check — the claim-substantiation duty, automated.

Every cycle it verifies up to 5 published deals against their LIVE store
price using Amazon PA-API v5 GetItems (the sanctioned programmatic channel;
no scraping). Clean verifications auto-log the compliance cycle; only real
mismatches surface for human eyes. Without PA-API credentials it returns an
honest 'manual' plan instead of pretending.

State lives in operator_settings:
    spot_checked_ids   [campaign ids already verified this cycle]
    spot_check_log     [{at, method, results:[…]}, …]  (last 24 kept)
    last_mismatches    [{campaign_id, recorded, live, delta_pct}, …]
system_settings:
    last_spot_check_at ISO date set automatically on a completed cycle
"""

import os
import sys
import json
import hmac as _hmac
import hashlib as _hashlib
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))

from config import (
    AMAZON_ASSOCIATE_TAG, AMAZON_PAAPI_ACCESS_KEY, AMAZON_PAAPI_SECRET_KEY,
    AMAZON_PAAPI_HOST, AMAZON_PAAPI_REGION,
)

CYCLE_SIZE = 5
TOLERANCE_PCT = 15.0  # deal is "ok" if live price is within ±15% of posted


def _paapi_configured():
    return bool(AMAZON_PAAPI_ACCESS_KEY) and bool(AMAZON_PAAPI_SECRET_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# STATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _get_json_setting(key, default):
    import db_manager
    try:
        return json.loads(db_manager.get_operator_setting(key, default) or default)
    except (ValueError, TypeError):
        return json.loads(default)


def _put_json_setting(key, value):
    import db_manager
    db_manager.set_operator_setting(key, json.dumps(value))


def pick_sample(limit=CYCLE_SIZE):
    """Oldest published deals not yet verified in this cycle."""
    import db_manager
    done = set(_get_json_setting("spot_checked_ids", "[]"))
    conn = db_manager._connection()
    try:
        rows = conn.execute(
            """
            SELECT id, product_id, title, price FROM campaigns
            WHERE status='published'
            ORDER BY created_at ASC LIMIT 400
            """
        ).fetchall()
    finally:
        conn.close()
    sample = []
    for r in rows:
        if r[0] in done:
            continue
        sample.append({"id": r[0], "product_id": r[1], "title": r[2], "recorded_price": float(r[3] or 0)})
        if len(sample) >= limit:
            break
    return sample


# ─────────────────────────────────────────────────────────────────────────────
# LIVE PRICE VIA PA-API v5 (POST SigV4)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_live_prices(asins):
    """
    Return {asin: float_price} via PA-API GetItems.
    Empty dict when unconfigured or every call failed — callers treat that
    as 'manual review required', never as verification.
    """
    if not _paapi_configured() or not asins:
        return {}
    try:
        import requests
    except ImportError:
        return {}

    payload = {
        "ItemIds": list(asins),
        "Resources": ["Offers.Listings.Price", "Offers.Listings.Availability"],
        "PartnerTag": AMAZON_ASSOCIATE_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.in",
    }
    body = json.dumps(payload)
    amz_date = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    date_stamp = datetime.utcnow().strftime("%Y%m%d")
    target = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"
    service = "ProductAdvertisingAPI"

    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{AMAZON_PAAPI_HOST}\nx-amz-date:{amz_date}\nx-amz-target:{target}\n"
    signed = "content-type;host;x-amz-date;x-amz-target"
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, signed, _hashlib.sha256(body.encode()).hexdigest(),
    ])
    scope = f"{date_stamp}/{AMAZON_PAAPI_REGION}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        _hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    def hm(k, m):
        return _hmac.new(k, m.encode(), _hashlib.sha256).digest()

    k = hm(("AWS4" + AMAZON_PAAPI_SECRET_KEY).encode(), date_stamp)
    k = hm(k, AMAZON_PAAPI_REGION)
    k = hm(k, service)
    sig = _hmac.new(hm(k, "aws4_request"), string_to_sign.encode(), _hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Amz-Date": amz_date,
        "X-Amz-Target": target,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={AMAZON_PAAPI_ACCESS_KEY}/{scope}, "
                          f"SignedHeaders={signed}, Signature={sig}"),
    }
    prices = {}
    try:
        resp = requests.post(f"https://{AMAZON_PAAPI_HOST}/paapi5/getitems",
                             data=body.encode(), headers=headers, timeout=15)
        resp.raise_for_status()
        for item in resp.json().get("ItemsResult", {}).get("Items", []):
            offer = item.get("Offers", {}).get("Listings", [{}])[0].get("Price", {})
            amount = offer.get("Amount")
            if amount is not None:
                prices[item.get("ASIN")] = float(amount)
    except Exception as exc:
        print(f"[SPOT_CHECK] PA-API lookup failed: {exc}")
    return prices


# ─────────────────────────────────────────────────────────────────────────────
# CYCLE RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def run_cycle(force=False):
    """
    Verify the current sample. Returns dict:
      {method, verified, mismatches[], completed, remaining}
    Completes the cycle (and stamps last_spot_check_at) once CYCLE_SIZE
    deals are verified in this cycle. Never raises.
    """
    import db_manager

    sample = pick_sample()
    if not sample:
        # everything already verified this cycle -> complete immediately
        if force:
            _complete_cycle([], "paapi" if _paapi_configured() else "none")
        return {"method": "paapi" if _paapi_configured() else "manual",
                "verified": 0, "mismatches": [], "completed": True, "remaining": 0}

    asin_map = {}
    for s in sample:
        asin_map.setdefault(s["product_id"], []).append(s)

    live = fetch_live_prices(list(asin_map.keys()))
    method = "paapi" if live else "manual"

    if not live:
        # Honest stop: hand deep links back for human review.
        return {
            "method": "manual",
            "verified": 0,
            "completed": False,
            "remaining": len(sample),
            "review_urls": [
                f"https://www.amazon.in/dp/{s['product_id']}" for s in sample[:CYCLE_SIZE]
            ],
        }

    results, mismatches = [], []
    checked_ids = _get_json_setting("spot_checked_ids", "[]")
    for s in sample:
        live_price = live.get(s["product_id"])
        if live_price is None or s["recorded_price"] <= 0:
            continue
        delta_pct = round((live_price - s["recorded_price"]) / s["recorded_price"] * 100.0, 2)
        ok = abs(delta_pct) <= TOLERANCE_PCT or delta_pct < 0  # cheaper is always fine
        entry = {"campaign_id": s["id"], "title": s["title"][:60],
                 "recorded": s["recorded_price"], "live": live_price,
                 "delta_pct": delta_pct, "ok": ok}
        results.append(entry)
        checked_ids.append(s["id"])
        if not ok:
            mismatches.append(entry)

    _put_json_setting("spot_checked_ids", checked_ids)
    if results:
        log = _get_json_setting("spot_check_log", "[]")
        log.append({"at": datetime.utcnow().isoformat(), "method": method, "results": results})
        _put_json_setting("spot_check_log", log[-24:])
        db_manager.set_operator_setting("last_mismatches",
                                        json.dumps(mismatches[-20:]))

    remaining_after = len(pick_sample())
    completed = False
    if results and remaining_after == 0:
        _complete_cycle(mismatches, method)
        completed = True

    return {"method": method, "verified": len(results), "mismatches": mismatches,
            "completed": completed, "remaining": remaining_after}


def _complete_cycle(mismatches, method):
    import db_manager
    db_manager.set_system_setting("last_spot_check_at", datetime.utcnow().date().isoformat())
    _put_json_setting("spot_checked_ids", [])
    print(f"[SPOT_CHECK] Cycle complete ({method}); "
          f"{len(mismatches)} mismatch(es) flagged.")


def status():
    """Dashboard-facing snapshot."""
    import db_manager
    last = db_manager.get_system_setting("last_spot_check_at", "")
    days_since = None
    if last:
        try:
            from datetime import date as _d
            days_since = (_d.fromisoformat(str(last)[:10]) - _d.fromisoformat(datetime.utcnow().date().isoformat())).days * -1
        except ValueError:
            pass
    from config import SPOT_CHECK_DAYS
    pending = pick_sample()
    mismatches = _get_json_setting("last_mismatches", "[]")
    return {
        "due": days_since is None or days_since > SPOT_CHECK_DAYS,
        "days_since": days_since,
        "cadence_days": SPOT_CHECK_DAYS,
        "pending_count": len(pending),
        "pending_urls": [f"https://www.amazon.in/dp/{p['product_id']}" for p in pending[:5]],
        "auto_mode_available": _paapi_configured(),
        "mismatches": mismatches[-5:],
    }

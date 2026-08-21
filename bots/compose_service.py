"""
Campaign composition service — the SINGLE source of truth for business rules
applied between "product sourced" and "campaign persisted".

Both entry points MUST route through here so they can never drift:
    * scheduler_engine.content_sweep   (automated IST sweeps)
    * app.py /api/run_pipeline         (operator-triggered runs)

Rules enforced, in order:
    1. Deal scoring (price history, lowest-ever, deal_score)
    2. Quality gate      — drop deals below MIN_DEAL_SCORE
    3. De-duplication    — skip products alerted within DEDUPE_DAYS,
                           EXCEPT genuine further-drop re-alerts
                           (price below prior min by REALERT_DROP_PCT),
                           re-badged "FURTHER DROP"
    4. A/B bandit wiring — every creative gets a variant + hook so click
                           attribution closes the learning loop
    5. Composition       — proven deal-format caption + forwardable card
    6. Persistence       — pending_approval, publish_at per auto-publish timeout
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.join(PROJECT_ROOT, 'bots') not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, 'bots'))


def compose_campaigns(products, sector):
    """
    Apply all business rules to freshly-sourced ``products`` for one
    ``sector`` and persist survivors. Returns a stats dict:

        {saved, low_score_skipped, duplicate_skipped,
         top_badge, top_score, variants: {"A": n, "B": n}}
    """
    from generators.ai_copywriter import generate_deal_post
    from generators.deal_card import render_deal_card
    from bots.deal_scorer import score_deal, rank_deals
    from bots.link_adapter import build_link
    from bots import ab_engine
    from db_manager import (
        save_campaign, has_recent_campaign, get_lowest_recorded_price,
    )
    import config

    scored = rank_deals([score_deal(p) for p in products])

    saved_count = 0
    low_score = 0
    duplicates = 0
    variants = {"A": 0, "B": 0}
    top_badge = ""
    top_score = 0.0

    for idx, product in enumerate(scored):
        pid = product.get("id") or product.get("product_id")
        try:
            # ── 1+2. Quality gate ────────────────────────────────────────
            score = float(product.get("deal_score", 0) or 0)
            if score < config.MIN_DEAL_SCORE:
                low_score += 1
                continue

            # ── 3. Dedupe with further-drop override ─────────────────────
            price = float(product.get("price", 0) or 0)
            prev_min = get_lowest_recorded_price(pid) if pid else None
            deeper_drop = (
                prev_min is not None and price > 0
                and (prev_min - price) / prev_min * 100.0 >= config.REALERT_DROP_PCT
            )
            if pid and has_recent_campaign(pid, days=config.DEDUPE_DAYS) and not deeper_drop:
                duplicates += 1
                continue
            if deeper_drop:
                product["badge"] = "FURTHER DROP"
                product["is_lowest_ever"] = True

            # ── 4. Bandit variant wiring (closes the ML loop) ────────────
            selection = ab_engine.select_variant(pid or sector)
            variant = selection["variant"]
            product["variant"] = variant
            variants[variant] += 1
            hook_text = selection.get("hook") or ""

            # ── 5. Composition ───────────────────────────────────────────
            product["sector"] = sector
            product["commission"] = product.get("commission", 0.03)
            # Multi-network monetization: attach the right affiliate ID for
            # whichever store this deal came from (amazon/flipkart/myntra).
            converted_url, store = build_link(
                product.get("target_url") or product.get("affiliate_link") or "")
            if converted_url:
                product["target_url"] = converted_url
                product["affiliate_link"] = converted_url
                product["store"] = store or "unknown"
            caption = generate_deal_post(product)
            if hook_text:
                caption = f"{hook_text}\n\n{caption}"
            product["caption"] = caption
            product["graphic_path"] = render_deal_card(product)

            # ── 6. Persistence ───────────────────────────────────────────
            save_campaign(product, sector=sector)
            if saved_count == 0:
                top_badge = product.get("badge", "")
                top_score = score
            saved_count += 1
            badge = product.get("badge") or "scored"
            print(f"[COMPOSE] Saved [{badge} {score} var={variant}] "
                  f"{str(product.get('title', ''))[:50]}")
        except Exception as exc:
            print(f"[COMPOSE] Failed to compose product {idx} ({pid}) "
                  f"in {sector}: {exc}")

    return {
        "saved": saved_count,
        "low_score_skipped": low_score,
        "duplicate_skipped": duplicates,
        "top_badge": top_badge,
        "top_score": top_score,
        "variants": variants,
    }

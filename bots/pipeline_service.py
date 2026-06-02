"""
Pipeline Service Layer
────────────────────────────────────────────────────────────────
Shared internal service functions called by BOTH:
  - Flask API routes  (app.py)
  - Scheduler jobs    (scheduler_engine.py)

No code duplication. No self-HTTP calls.

Public API:
  run_all_sectors_internal()   → dict  (mirrors /api/run_all response body)
  run_retargeting_internal()   → list  (list of retargeting plan dicts)
"""

import subprocess
import os
import sys
import json
from datetime import datetime

# Ensure bots + scrapers are importable (mirrors app.py path inserts)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _sub in ("bots", "scrapers"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ─── sector pipeline ─────────────────────────────────────────────────────────

def _run_single_sector(sector: str) -> dict:
    """
    Execute the full 14-step pipeline for one sector.
    This is the canonical implementation — identical to what app.py/_run_single_sector does.
    Returns the structured result dict.
    """
    from bots.idempotency import check_and_lock, release_lock
    event_id = f"pipeline_run:{sector}"
    if not check_and_lock(event_id):
        msg = f"Pipeline execution for sector '{sector}' is already in progress or has a stale lock."
        print(f"  [PIPELINE_SERVICE] {msg}")
        raise RuntimeError(msg)

    start_time = datetime.utcnow()

    steps = [
        ("Scraping products",          [sys.executable, "product_scraper.py", sector], "scrapers"),
        ("Competitor Price Watch",     [sys.executable, "competitor_watch.py"],         "bots"),
        ("Market Analysis",            [sys.executable, "market_analyzer.py"],          "bots"),
        ("Segmenting products",        [sys.executable, "segmentation_engine.py"],      "bots"),
        ("Mapping journeys",           [sys.executable, "journey_engine.py"],           "bots"),
        ("Optimizing send windows",    [sys.executable, "send_time_optimizer.py"],      "bots"),
        ("Revenue ranking",            [sys.executable, "revenue_ranker.py"],           "bots"),
        ("Buyer-fit engine",           [sys.executable, "buyer_fit_engine.py"],         "bots"),
        ("Value explainer",            [sys.executable, "value_explainer.py"],          "bots"),
        ("Detecting alerts",           [sys.executable, "alert_engine.py"],             "bots"),
        ("Generating recommendations", [sys.executable, "recommendation_engine.py"],   "bots"),
        ("A/B variant generation",     [sys.executable, "ab_engine.py"],               "bots"),
        ("Generating graphics",        [sys.executable, "design_engine.py"],           "generators"),
        ("Generating ad copy",         [sys.executable, "ai_copywriter.py"],           "generators"),
        ("Video Script Creation",      [sys.executable, "video_script_engine.py"],      "generators"),
        ("Newsletter Creation",        [sys.executable, "email_newsletter.py"],        "generators"),
        ("Creating affiliate links",   [sys.executable, "affiliate_linker.py"],        "scrapers"),
        ("Distributing",               [sys.executable, "distributor.py"],             "bots"),
        ("Running analytics",          [sys.executable, "analytics_engine.py"],        "bots"),
    ]
    
    try:
        # ── steps 1-19: subprocess pipeline ─────────────────────────────────────
        total_steps = len(steps)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([
            _ROOT,
            os.path.join(_ROOT, "bots"),
            os.path.join(_ROOT, "scrapers"),
            env.get("PYTHONPATH", "")
        ]).strip(os.pathsep)
        
        for i, (label, cmd, cwd_dir) in enumerate(steps, 1):
            cwd = os.path.join(_ROOT, cwd_dir)
            print(f"  [{i}/{total_steps}] {label} ({sector})...")
            # Windows-safe timeout of 120s per step
            subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, timeout=120, env=env)
    finally:
        release_lock(event_id)

    def _load(rel):
        from config import OUTPUT_DIR
        p = os.path.join(OUTPUT_DIR, rel)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    elapsed = (datetime.utcnow() - start_time).total_seconds()

    # ── read outputs ──────────────────────────────────────────────────────────
    posts              = _load("post_data.json") or []
    analytics          = _load("analytics_log.json") or []
    market_report      = _load("market_analysis.json") or {}
    segmented_products = _load("segmented_products.json") or []
    journey_plans      = _load("journey_plans.json") or []
    send_plans         = _load("send_plan.json") or []
    ranked_products    = _load("ranked_products.json") or []
    alerts_list        = _load("alerts.json") or []
    recs_list          = _load("recommendations.json") or []
    ab_variants        = _load("ab_variants.json") or []
    comp_analysis      = _load("competitor_analysis.json") or []
    v_scripts          = _load("video_scripts.json") or []

    # Post-analytics enrichment
    segment_summary = {}
    try:
        import segmentation_engine
        segmented_products = segmentation_engine.enrich_with_performance(
            segmented_products, analytics
        )
        segment_summary = segmentation_engine.get_segment_summary(segmented_products)
    except Exception as e:
        print(f"Performance enrichment skipped: {e}")

    alerts_by_id = {}
    for a in alerts_list:
        alerts_by_id.setdefault(a.get("product_id", ""), []).append(a)

    recs_by_id = {r.get("product_id", ""): r.get("related_products", []) for r in recs_list}
    ab_by_id   = {v.get("product_id", ""): v for v in ab_variants}
    comp_by_id = {c.get("product_id", ""): c.get("analysis", {}) for c in comp_analysis}
    script_by_id = {s.get("product_id", ""): s.get("script", {}) for s in v_scripts}

    combined_products = []
    for idx, post in enumerate(posts):
        seg_data    = segmented_products[idx].get("segments", {}) if idx < len(segmented_products) else {}
        journey_data = journey_plans[idx] if idx < len(journey_plans) else {}
        timing_data  = send_plans[idx] if idx < len(send_plans) else {}

        rev_data = {}
        if idx < len(ranked_products):
            rp = ranked_products[idx]
            rev_data = {
                "revenue_score":     rp.get("revenue_score", 0),
                "revenue_reason":    rp.get("revenue_reason", ""),
                "revenue_breakdown": rp.get("revenue_breakdown", {}),
                "est_commission_pct": rp.get("_est_commission_pct", 0),
            }

        pid = post.get("id", f"prod_{idx}")
        product_data = {
            "id":            pid,
            "title":         post.get("title", "Unknown"),
            "price":         post.get("price", "N/A"),
            "platform":      post.get("platform", "Unknown"),
            "image_url":     post.get("image_url", ""),
            "link":          post.get("link", ""),
            "rating":        post.get("rating", "N/A"),
            "caption":       post.get("caption", ""),
            "graphic_path":  f"/image/graphic_{idx}.jpg",
            "affiliate_link": post.get("affiliate_link", post.get("link", "")),
            "brand":         post.get("brand", ""),
            "discount":      post.get("discount", 0),
            "stock":         post.get("stock", 0),
            "description":   post.get("description", ""),
            "sector":        sector,
            "segments":      seg_data,
            "journey":       journey_data,
            "timing":        timing_data,
            "revenue":       rev_data,
            "alerts":        alerts_by_id.get(pid, []),
            "related_products": recs_by_id.get(pid, []),
            "ab":            ab_by_id.get(pid, {}),
            "competitor_analysis": comp_by_id.get(pid, {}),
            "video_script":  script_by_id.get(pid, {}),
        }

        if idx < len(analytics):
            product_data["distribution"]   = analytics[idx].get("platforms", [])
            product_data["total_metrics"]  = analytics[idx].get("total_metrics", {})
        else:
            product_data["distribution"]   = []
            product_data["total_metrics"]  = {"total_views": 0, "total_clicks": 0, "overall_ctr": "0%"}

        # ── merge buyer_fit and value_explanation from explained_products ──
        # In the new sequence, explained_products.json contains the final enriched data
        explained = _load("explained_products.json") or []
        rp_match = next((rp for rp in explained if rp.get("id") == pid), {})
        product_data["buyer_fit"]          = rp_match.get("buyer_fit", {})
        product_data["value_explanation"]  = rp_match.get("value_explanation", {})
        
        product_data["copy"] = post.get("copy", {})

        combined_products.append(product_data)

    combined_products.sort(key=lambda x: x.get("buyer_fit", {}).get("final_rank_score", 0), reverse=True)

    # Save to DB
    try:
        import db_manager
        for product in combined_products:
            db_manager.save_campaign(product, sector)
    except Exception as e:
        print(f"Error saving to DB: {e}")

    # Get display name
    try:
        sys.path.insert(0, os.path.join(_ROOT, "scrapers"))
        from product_scraper import get_sector_display_name
        display_name = get_sector_display_name(sector)
    except Exception:
        display_name = sector

    return {
        "sector":          sector,
        "sector_display":  display_name,
        "data":            combined_products,
        "market_analysis": market_report,
        "segment_summary": segment_summary,
        "alerts":          alerts_list,
        "pipeline_time":   round(elapsed, 1),
    }


def run_all_sectors_internal() -> dict:
    """
    Run the 14-step pipeline for ALL sectors sequentially.
    Called by: Flask /api/run_all  AND  scheduler pipeline jobs.
    """
    try:
        sys.path.insert(0, os.path.join(_ROOT, "scrapers"))
        from product_scraper import SECTOR_CONFIG
        all_sectors = list(SECTOR_CONFIG.keys())
    except Exception as e:
        raise RuntimeError(f"Could not load SECTOR_CONFIG: {e}")

    # Load active sectors from system settings
    try:
        import db_manager
        active_sectors_json = db_manager.get_system_setting("active_sectors", "{}")
        active_sectors = json.loads(active_sectors_json)
    except Exception:
        active_sectors = {}

    batch_start = datetime.utcnow()
    results, errors = [], []

    for sector in all_sectors:
        # Dynamic Settings check: skip if deactivated by operator
        if not active_sectors.get(sector, True):
            print(f"BATCH: sector '{sector}' is deactivated in Operator Settings. Skipping.")
            continue
        try:
            print(f"\n{'='*60}\nBATCH: sector '{sector}' ({len(results)+1}/{len(all_sectors)})\n{'='*60}")
            result = _run_single_sector(sector)
            results.append(result)
        except Exception as e:
            errors.append({"sector": sector, "error": str(e)})
            print(f"BATCH ERROR on '{sector}': {e}")

    batch_elapsed = (datetime.utcnow() - batch_start).total_seconds()
    total_products = sum(len(r["data"]) for r in results)

    return {
        "status":             "success",
        "batch":              True,
        "sectors_completed":  len(results),
        "sectors_failed":     len(errors),
        "total_products":     total_products,
        "results":            results,
        "errors":             errors,
        "batch_time":         round(batch_elapsed, 1),
        "run_at":             datetime.utcnow().isoformat(),
    }


# ─── retargeting ─────────────────────────────────────────────────────────────

def run_retargeting_internal() -> list:
    """
    Execute the retargeting sweep.
    Called by: Flask /api/run_retargeting  AND  scheduler retargeting jobs.
    """
    import retargeting_engine
    return retargeting_engine.generate_retargeting_campaigns(hours_ago_min=0)

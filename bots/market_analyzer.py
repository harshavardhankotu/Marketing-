"""
Market Analysis Engine
─────────────────────────────────────────────────────
Analyses the scraped product data for a given sector and produces
a structured market report covering:
  • Price distribution  (min / max / avg / median)
  • Discount landscape  (avg discount, best deals)
  • Brand concentration (market share breakdown)
  • Stock health        (low-stock alerts)
  • Rating overview     (quality index)
  • Top recommendations (best value picks)

The report is saved to  data/output/market_analysis.json  and surfaced
on the dashboard.
"""

import json
import os
import statistics
from datetime import datetime


def analyse_market(products, sector_display=""):
    """
    Takes a list of product dicts and returns a rich market-analysis dict.
    """
    if not products:
        return {"error": "No products to analyse"}

    # ── helpers ──────────────────────────────────────────────────────
    prices = [int(p["price"]) for p in products if p.get("price", "0").isdigit()]
    ratings = [float(p["rating"]) for p in products if _is_float(p.get("rating", ""))]
    discounts = [p.get("discount", 0) for p in products]
    stocks = [p.get("stock", 0) for p in products]

    brands = {}
    for p in products:
        b = p.get("brand", "Unknown")
        brands[b] = brands.get(b, 0) + 1

    # ── price analysis ──────────────────────────────────────────────
    price_analysis = {}
    if prices:
        price_analysis = {
            "min": min(prices),
            "max": max(prices),
            "avg": round(statistics.mean(prices)),
            "median": round(statistics.median(prices)),
            "range": max(prices) - min(prices),
            "budget_count": sum(1 for p in prices if p < statistics.median(prices)),
            "premium_count": sum(1 for p in prices if p >= statistics.median(prices)),
        }

    # ── discount analysis ───────────────────────────────────────────
    discount_analysis = {}
    if discounts:
        discount_analysis = {
            "avg_discount": round(statistics.mean(discounts), 1),
            "max_discount": round(max(discounts), 1),
            "products_with_discount": sum(1 for d in discounts if d > 0),
            "best_deals": [],
        }
        # top 3 deals by discount
        sorted_by_discount = sorted(products, key=lambda p: p.get("discount", 0), reverse=True)
        for p in sorted_by_discount[:3]:
            discount_analysis["best_deals"].append({
                "title": p["title"],
                "discount": round(p.get("discount", 0), 1),
                "price": p["price"],
                "original_est": round(int(p["price"]) / (1 - p.get("discount", 0) / 100)) if p.get("discount", 0) > 0 else int(p["price"]),
            })

    # ── brand analysis ──────────────────────────────────────────────
    brand_analysis = {
        "total_brands": len(brands),
        "brand_breakdown": [
            {"brand": b, "products": c, "share": round(c / len(products) * 100, 1)}
            for b, c in sorted(brands.items(), key=lambda x: x[1], reverse=True)
        ],
        "dominant_brand": max(brands, key=brands.get) if brands else "N/A",
    }

    # ── rating analysis ─────────────────────────────────────────────
    rating_analysis = {}
    if ratings:
        rating_analysis = {
            "avg_rating": round(statistics.mean(ratings), 2),
            "highest_rated": max(ratings),
            "lowest_rated": min(ratings),
            "above_4_stars": sum(1 for r in ratings if r >= 4.0),
            "quality_index": _quality_label(statistics.mean(ratings)),
        }

    # ── stock analysis ──────────────────────────────────────────────
    low_stock = [p for p in products if p.get("stock", 0) < 20]
    stock_analysis = {
        "avg_stock": round(statistics.mean(stocks)) if stocks else 0,
        "low_stock_count": len(low_stock),
        "low_stock_items": [{"title": p["title"], "stock": p.get("stock", 0)} for p in low_stock[:5]],
    }

    # ── top picks (best value = high rating + high discount + decent stock) ──
    for p in products:
        r = float(p["rating"]) if _is_float(p.get("rating", "")) else 0
        d = p.get("discount", 0)
        s = min(p.get("stock", 0), 50) / 50  # normalise
        p["_score"] = round(r * 0.5 + d * 0.3 + s * 20, 2)

    top_picks = sorted(products, key=lambda p: p.get("_score", 0), reverse=True)[:5]
    recommendations = [
        {
            "title": p["title"],
            "brand": p.get("brand", "Unknown"),
            "price": p["price"],
            "rating": p["rating"],
            "discount": round(p.get("discount", 0), 1),
            "score": p.get("_score", 0),
            "reason": _pick_reason(p),
        }
        for p in top_picks
    ]

    # clean up temp score
    for p in products:
        p.pop("_score", None)

    # ── assemble report ─────────────────────────────────────────────
    report = {
        "sector": sector_display,
        "generated_at": datetime.utcnow().isoformat(),
        "total_products": len(products),
        "price_analysis": price_analysis,
        "discount_analysis": discount_analysis,
        "brand_analysis": brand_analysis,
        "rating_analysis": rating_analysis,
        "stock_analysis": stock_analysis,
        "top_recommendations": recommendations,
        "market_summary": _build_summary(
            sector_display, len(products), price_analysis,
            discount_analysis, rating_analysis, brand_analysis
        ),
    }
    return report


# ── private helpers ──────────────────────────────────────────────────

def _is_float(s):
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _quality_label(avg):
    if avg >= 4.5:
        return "Excellent"
    if avg >= 4.0:
        return "Very Good"
    if avg >= 3.5:
        return "Good"
    if avg >= 3.0:
        return "Average"
    return "Below Average"


def _pick_reason(p):
    r = float(p["rating"]) if _is_float(p.get("rating", "")) else 0
    d = p.get("discount", 0)
    if r >= 4.5 and d >= 15:
        return "Top rated with massive discount"
    if r >= 4.0:
        return "Highly rated product"
    if d >= 15:
        return "Best discount in category"
    if p.get("stock", 0) < 15:
        return "Limited stock — high demand"
    return "Strong overall value"


def _build_summary(sector, count, price, discount, rating, brand):
    parts = [f"Market snapshot for {sector}: {count} products analysed."]
    if price:
        parts.append(
            f"Prices range ₹{price['min']} – ₹{price['max']} "
            f"(avg ₹{price['avg']}, median ₹{price['median']})."
        )
    if discount:
        parts.append(
            f"Average discount is {discount['avg_discount']}%, "
            f"with {discount['products_with_discount']} products on deal."
        )
    if rating:
        parts.append(
            f"Quality index: {rating['quality_index']} "
            f"(avg {rating['avg_rating']}★, "
            f"{rating['above_4_stars']} products above 4★)."
        )
    if brand:
        parts.append(
            f"{brand['total_brands']} brands present; "
            f"dominant brand: {brand['dominant_brand']}."
        )
    return " ".join(parts)


# ── standalone runner ────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running Market Analysis Engine...")

    from config import PROJECT_ROOT, OUTPUT_DIR
    products_path = os.path.join(PROJECT_ROOT, "data", "trending_products.json")
    output_path = os.path.join(OUTPUT_DIR, "market_analysis.json")

    if os.path.exists(products_path):
        with open(products_path, "r", encoding="utf-8") as f:
            products = json.load(f)

        sector = products[0].get("sector", "Unknown") if products else "Unknown"
        # try to resolve display name
        try:
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scrapers'))
            from product_scraper import get_sector_display_name
            display = get_sector_display_name(sector)
        except Exception:
            display = sector

        report = analyse_market(products, display)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)

        print(f"Market analysis complete: {report['total_products']} products")
        # Use ASCII-safe print to avoid Windows cp1252 encoding errors with currency symbols
        safe_summary = report['market_summary'].encode('ascii', 'replace').decode('ascii')
        print(f"Summary: {safe_summary}")
    else:
        print(f"Data file not found: {products_path}")

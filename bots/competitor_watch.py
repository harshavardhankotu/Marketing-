import os
import json
import random

def simulate_competitor_check(product):
    title = product.get('title', 'Product')
    price_str = str(product.get('price', '0')).replace(',', '').replace('₹', '').strip()
    try:
        our_price = float(price_str)
    except ValueError:
        our_price = 0

    competitors = [
        {"name": "Flipkart", "price_multiplier": random.uniform(0.95, 1.1)},
        {"name": "Reliance Digital", "price_multiplier": random.uniform(0.98, 1.15)},
        {"name": "Croma", "price_multiplier": random.uniform(1.0, 1.2)}
    ]

    results = []
    is_cheapest = True
    for comp in competitors:
        comp_price = round(our_price * comp['price_multiplier'])
        if comp_price < our_price:
            is_cheapest = False
        results.append({
            "competitor": comp['name'],
            "price": comp_price,
            "difference": comp_price - our_price
        })

    return {
        "is_cheapest": is_cheapest,
        "competitiveness_score": 95 if is_cheapest else 70,
        "market_avg": round(sum(r['price'] for r in results) / len(results)) if results else 0,
        "comparisons": results,
        "verdict": "Best Price in Market" if is_cheapest else "Competitive"
    }

def run_competitor_watch():
    print("Running Competitor Price Watch (Simulation)...")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # Prioritize step 1 output: trending_products.json at data/
    input_path = os.path.join(base_dir, 'data', 'trending_products.json')
    output_path = os.path.join(base_dir, 'data', 'output', 'competitor_analysis.json')
    
    # Fallback paths for backward compatibility / migration shim
    fallbacks = [
        os.path.join(base_dir, 'data', 'output', 'post_data.json'),
        os.path.join(base_dir, 'data', 'output', 'scraped_products.json')
    ]
    
    products = None
    loaded_path = None
    
    # Try primary path first
    if os.path.exists(input_path):
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                products = json.load(f)
            loaded_path = input_path
        except Exception as e:
            print(f"ERROR: Failed to load primary file {input_path}: {e}")
            
    # Try fallbacks if primary didn't load
    if not products:
        for fb in fallbacks:
            if os.path.exists(fb):
                try:
                    with open(fb, 'r', encoding='utf-8') as f:
                        products = json.load(f)
                    loaded_path = fb
                    print(f"Migration fallback: Loaded data from {fb}")
                    break
                except Exception as e:
                    print(f"ERROR: Failed to load fallback file {fb}: {e}")
                    
    # If still no products or file missing, handle gracefully
    if not products:
        print("ERROR: No product data found for competitor analysis. Writing empty output.")
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
        except Exception as e:
            print(f"ERROR: Failed to write empty competitor analysis file: {e}")
        return

    print(f"Loaded {len(products)} products from {loaded_path} for competitor watch.")
    analysis = []
    for product in products:
        pid = product.get('id', 'unknown')
        print(f"  Checking competition for {product.get('title', pid)[:30]}...")
        result = simulate_competitor_check(product)
        analysis.append({
            "product_id": pid,
            "analysis": result
        })

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, indent=4, ensure_ascii=False)
        print(f"Saved competitor analysis for {len(analysis)} products to {output_path}")
    except Exception as e:
        print(f"ERROR: Failed to write competitor analysis output: {e}")

if __name__ == "__main__":
    run_competitor_watch()


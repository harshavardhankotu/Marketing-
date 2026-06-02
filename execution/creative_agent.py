import json
import os

def run_creative():
    print("[CREATIVE] Generating asset briefs and copy variations...")
    input_file = '../data/researched_products.json'
    output_file = '../data/creative_assets.json'
    
    if not os.path.exists(input_file):
        print("[ERROR] Research data missing.")
        return False
        
    with open(input_file, 'r') as f:
        products = json.load(f)
        
    for product in products:
        # Generate creative angles based on the research data
        product['creative_brief'] = {
            "image_prompt": f"High quality cinematic product shot of {product['product_type']}, bright studio lighting, minimalist background, 4k.",
            "headlines": [
                f"Upgrade your style with this {product['product_type']}",
                f"Trending on {product['inferred_platform']}: Get it before it's gone!",
                f"Premium {product['product_type']} at an unbeatable price."
            ],
            "primary_copy": f"We just found the highest rated {product['product_type']} trending right now. With competitor prices soaring, grabbing this for ₹{product['estimated_price']} on {product['inferred_platform']} is a no-brainer. Click the link to secure yours.",
            "status": "Assets Generated"
        }
        
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(products, f, indent=4)
        
    print(f"[CREATIVE] Generated copy and briefs for {len(products)} products.")
    return True

if __name__ == "__main__":
    run_creative()

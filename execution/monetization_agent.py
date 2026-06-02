import json
import os
import csv

def run_monetization():
    print("[MONEY] Formatting final outputs and tracking sheets...")
    input_file = '../data/creative_assets.json'
    output_sheet = '../data/final_campaign_sheet.csv'
    
    if not os.path.exists(input_file):
        print("[ERROR] Creative data missing.")
        return False
        
    with open(input_file, 'r') as f:
        products = json.load(f)
        
    # Write to a CSV simulating Google Sheets write-back
    headers = [
        'Campaign ID', 'Platform', 'Product Type', 'Price', 'Trend Score', 
        'Best Headline', 'Social Copy', 'Affiliate Link'
    ]
    
    with open(output_sheet, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        
        for p in products:
            writer.writerow([
                p['id'],
                p['inferred_platform'],
                p['product_type'],
                f"₹{p['estimated_price']}",
                p['trend_score'],
                p['creative_brief']['headlines'][0],
                p['creative_brief']['primary_copy'],
                p['original_url'] # The user provided EarnKaro links, so they are already monetized!
            ])
            
    print(f"[MONEY] Wrote {len(products)} campaign rows to {output_sheet}.")
    return True

if __name__ == "__main__":
    run_monetization()

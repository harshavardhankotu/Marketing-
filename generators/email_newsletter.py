import os
import json
from datetime import datetime

def generate_newsletter_html(top_products):
    today = datetime.now().strftime("%B %d, %Y")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background: #f4f4f4; }}
            .container {{ max-width: 600px; margin: 20px auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.1); }}
            .header {{ background: #6366f1; color: #fff; padding: 30px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; letter-spacing: -0.5px; }}
            .date {{ font-size: 14px; opacity: 0.8; margin-top: 5px; }}
            .content {{ padding: 20px; }}
            .intro {{ margin-bottom: 25px; font-size: 16px; color: #666; }}
            .deal-card {{ border: 1px solid #eee; border-radius: 10px; margin-bottom: 20px; padding: 15px; display: flex; align-items: center; gap: 15px; }}
            .deal-img {{ width: 100px; height: 100px; object-fit: cover; border-radius: 6px; background: #f9f9f9; }}
            .deal-info {{ flex: 1; }}
            .deal-title {{ font-weight: 700; font-size: 16px; margin: 0 0 5px 0; color: #1a1a1f; }}
            .deal-price {{ color: #6366f1; font-weight: 800; font-size: 18px; margin-bottom: 5px; }}
            .deal-badge {{ background: #22c55e20; color: #22c55e; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
            .btn {{ display: block; background: #6366f1; color: #fff; text-decoration: none; padding: 12px; text-align: center; border-radius: 6px; font-weight: 700; margin-top: 10px; }}
            .footer {{ background: #f9f9f9; padding: 20px; text-align: center; font-size: 12px; color: #999; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Daily Deals Roundup</h1>
                <div class="date">{today}</div>
            </div>
            <div class="content">
                <p class="intro">Hand-picked top-performing deals across all sectors, analyzed by our AI for maximum value.</p>
    """
    
    for p in top_products:
        pid = p.get('id', 'N/A')
        title = p.get('title', 'Product')
        price = p.get('price', 'N/A')
        img = p.get('image_url', '')
        link = p.get('affiliate_link', p.get('link', '#'))
        verdict = p.get('competitor_analysis', {}).get('verdict', 'Top Deal')
        
        html += f"""
                <div class="deal-card">
                    <img src="{img}" class="deal-img" alt="{title}">
                    <div class="deal-info">
                        <div class="deal-badge">{verdict}</div>
                        <h3 class="deal-title">{title}</h3>
                        <div class="deal-price">₹{price}</div>
                        <a href="{link}" class="btn">View Deal →</a>
                    </div>
                </div>
        """
        
    html += """
            </div>
            <div class="footer">
                &copy; 2026 Marketing Automation Engine. All rights reserved.<br>
                You are receiving this because you opted in to our daily deals.
            </div>
        </div>
    </body>
    </html>
    """
    return html

def run_newsletter_generation():
    print("Generating Daily Newsletter...")
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    input_path = os.path.join(base_dir, 'data', 'output', 'post_data.json')
    output_path = os.path.join(base_dir, 'data', 'output', 'newsletter.html')
    
    if not os.path.exists(input_path):
        print("No post data found for newsletter.")
        return

    with open(input_path, 'r', encoding='utf-8') as f:
        products = json.load(f)

    # Pick top 5 products based on revenue score if available, else just first 5
    products.sort(key=lambda x: x.get('revenue', {}).get('revenue_score', 0), reverse=True)
    top_5 = products[:5]

    html_content = generate_newsletter_html(top_5)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Newsletter generated at {output_path}")

if __name__ == "__main__":
    run_newsletter_generation()

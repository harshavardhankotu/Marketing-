import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from playwright.sync_api import sync_playwright
import re

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def clean_url(url):
    return url.strip()

def main():
    input_file = os.path.join(PROJECT_ROOT, "data", "input_links.txt")
    if not os.path.exists(input_file):
        print(f"File not found: {input_file}")
        return
        
    with open(input_file, 'r') as f:
        links = [line.strip() for line in f if line.strip()]
        
    print(f"Loaded {len(links)} links.")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        for idx, url in enumerate(links):
            print(f"\n[{idx+1}/{len(links)}] Sourcing: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                
                landed = page.url
                title = page.locator("meta[property='og:title']").get_attribute("content")
                if not title:
                    title_loc = page.locator("h1").first
                    title = title_loc.inner_text() if title_loc.count() > 0 else ""
                title = title.strip() if title else ""
                
                print(f"  Landed: {landed}")
                print(f"  Title: {title}")
                
                # Check for cashbackUrl in script if landed on linkredirect.in
                if "linkredirect.in" in landed:
                    html = page.content()
                    match = re.search(r'var cashbackUrl\s*=\s*"([^"]+)"', html)
                    if match:
                        cb_url = match.group(1)
                        print(f"  Found cashbackUrl: {cb_url}")
                        
                        # Parse the 'url' parameter from cb_url
                        from urllib.parse import urlparse, parse_qs
                        parsed = urlparse(cb_url)
                        queries = parse_qs(parsed.query)
                        if 'url' in queries:
                            real_dest = queries['url'][0]
                            print(f"  Extracted Real Destination: {real_dest}")
                            
            except Exception as e:
                print(f"  Error: {e}")
                
        browser.close()

if __name__ == '__main__':
    main()

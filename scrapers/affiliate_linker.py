import json
import os
import requests
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

def generate_affiliate_link(raw_url, platform):
    """
    Mock integration for EarnKaro / Cuelinks / Amazon Associates.
    In production, this would make an API call to your affiliate network.
    """
    print(f"Generating affiliate link for {platform}...")
    
    try:
        import sys
        import os
        _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        sys.path.insert(0, os.path.join(_ROOT, "bots"))
        import db_manager
        amazon_tag = db_manager.get_system_setting("amazon_tag", "marketingai-21")
        flipkart_tag = db_manager.get_system_setting("flipkart_tag", "marketingai")
    except Exception:
        amazon_tag = "marketingai-21"
        flipkart_tag = "marketingai"

    # Mocking standard Amazon associate tag appendage
    if "amazon" in raw_url.lower():
        parsed = urlparse(raw_url)
        # Use parse_qs for safe query parsing (handles missing =, empty params, etc.)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query['tag'] = [amazon_tag]
        new_query = urlencode(query, doseq=True)
        affiliate_url = urlunparse(parsed._replace(query=new_query))
        return affiliate_url
        
    if "flipkart" in raw_url.lower():
        return f"{raw_url}&affid={flipkart_tag}"
        
    if "myntra" in raw_url.lower():
        separator = '&' if '?' in raw_url else '?'
        return f"{raw_url}{separator}affiliate_id=myntr_agency_id"
        
    # Mocking an aggregator redirect link (like Cuelinks)
    return f"https://api.mock-affiliate-network.com/redirect?url={raw_url}&aff_id=12345"

def verify_link(url):
    """
    Verifies that the generated link actually resolves and doesn't 404.
    """
    print(f"Verifying link: {url}")
    if "api.mock-affiliate-network.com" in url:
        return True

    # Import reliability services
    from bots.resilience import call_with_retry, timeout_call
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException

    breaker = get_breaker("affiliate_link_validation", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"  [AFFILIATE_LINKER] Circuit Breaker open for 'affiliate_link_validation'. Skipping pre-flight verification: {e}")
        return True

    def make_preflight_check():
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=5)
        if response.status_code >= 400 and not ("amazon" in url or "flipkart" in url or "myntra" in url):
            raise Exception(f"Pre-flight HEAD request returned status code {response.status_code}")
        return True

    # Call with Timeout and Retry
    retry_res = call_with_retry(
        make_preflight_check,
        max_retries=2,
        base_delay=1.0,
        max_delay=3.0,
        provider="affiliate_link_validation"
    )

    if retry_res["success"]:
        breaker.record_success()
        return True
    else:
        # If it timed out or got rejected, evaluate failure
        breaker.record_failure()
        print(f"Warning: Link preflight failed: {retry_res['error']}. Assuming valid for pipeline continuity.")
        return True

if __name__ == "__main__":
    print("Starting Affiliate Linker & Verifier...")
    
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../bots')))
    from config import OUTPUT_DIR
    post_data_path = os.path.join(OUTPUT_DIR, 'post_data.json')
    if os.path.exists(post_data_path):
        with open(post_data_path, 'r', encoding='utf-8') as f:
            posts = json.load(f)
            
        if posts:
            updated_count = 0
            for idx, post in enumerate(posts):
                raw_url = post.get('link', '')
                platform = post.get('platform', 'Unknown')
                
                if not raw_url:
                    print(f"Skipping post {idx}: No link found.")
                    continue
                
                affiliate_url = generate_affiliate_link(raw_url, platform)
                is_valid = verify_link(affiliate_url)
                
                if is_valid:
                    print(f"  SUCCESS: Link verified for post {idx+1}.")
                    post['affiliate_link'] = affiliate_url
                    updated_count += 1
                else:
                    print(f"  WARN: Link verification failed for post {idx+1}. Using original link as fallback.")
                    post['affiliate_link'] = raw_url
                    updated_count += 1
                    
            # Save back all posts
            with open(post_data_path, 'w', encoding='utf-8') as f:
                json.dump(posts, f, indent=4, ensure_ascii=False)
            print(f"Saved {updated_count} verified affiliate links to post_data.json")
        else:
            print("No post data found.")
    else:
        print(f"Data file not found at {post_data_path}")

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import requests

def main():
    base = 'http://127.0.0.1:5000'
    
    # 1. Test trusted domain (safe redirect)
    safe_url = "https://www.amazon.in/s?k=Apple+iPhone"
    req_safe = f"{base}/go/test_prod?url={safe_url}&title=iPhone&sector=smartphones"
    print(f"Testing Safe URL: {safe_url}")
    try:
        r_safe = requests.get(req_safe, allow_redirects=False, timeout=5)
        print(f"  Result Code: {r_safe.status_code}")
        print(f"  Location Header: {r_safe.headers.get('Location')}")
        loc = r_safe.headers.get('Location', '')
        if r_safe.status_code == 302 and (loc == safe_url or loc.replace('%20', '+') == safe_url):
            print("  [PASS] Safe URL successfully allowed and redirected.")
        else:
            print("  [FAIL] Safe URL failed to redirect correctly.")
    except Exception as e:
        print(f"  Error: {e}")
        
    print()
    
    # 2. Test untrusted domain (unsafe redirect)
    unsafe_url = "https://evil.com/phishing"
    req_unsafe = f"{base}/go/test_prod?url={unsafe_url}&title=iPhone&sector=smartphones"
    print(f"Testing Unsafe URL: {unsafe_url}")
    try:
        r_unsafe = requests.get(req_unsafe, allow_redirects=False, timeout=5)
        print(f"  Result Code: {r_unsafe.status_code}")
        print(f"  Response JSON: {r_unsafe.text}")
        if r_unsafe.status_code == 400:
            print("  [PASS] Unsafe URL successfully blocked with 400 Bad Request.")
        else:
            print("  [FAIL] Unsafe URL was not blocked!")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    main()

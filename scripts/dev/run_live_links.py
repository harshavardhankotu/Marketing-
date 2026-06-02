import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import requests
import json

def main():
    url = "http://127.0.0.1:5000/api/run_pipeline"
    payload = {"sector": "live_links"}
    headers = {"Content-Type": "application/json"}
    
    print("Triggering the 'live_links' pipeline...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=300)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            res_data = response.json()
            print("Pipeline successfully completed!")
            print(f"Products completed: {len(res_data.get('data', []))}")
            # Print title of products
            for idx, p in enumerate(res_data.get('data', [])):
                print(f"  [{idx+1}] {p.get('title')} (Price: {p.get('price')}) on {p.get('platform')}")
        else:
            print("Error response from server:")
            print(response.text)
    except Exception as e:
        print(f"Failed to connect to Flask server: {e}")

if __name__ == "__main__":
    main()

import os
import sys
import json
from PIL import Image

# Ensure the root path is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../generators')))

from generators.design_engine import create_social_graphic

def run_test():
    print("Testing Design Engine with font download blocked (simulating no-network)...")
    
    sample_product = {
        "id": "test_prod_123",
        "title": "Super Fast Slim Laptop Pro",
        "price": "54999",
        "platform": "Tech Store",
        "image_url": "https://invalid.url/image.png" # Will fail download, triggering fallback badge
    }
    
    from bots.config import OUTPUT_DIR
    output_path = os.path.join(OUTPUT_DIR, 'test_fallback_graphic.jpg')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Run creation with block_network=True
    create_social_graphic(sample_product, output_path, block_network=True)
    
    # Optical / structural checks
    if os.path.exists(output_path):
        size_bytes = os.path.getsize(output_path)
        img = Image.open(output_path)
        width, height = img.size
        print(f"SUCCESS: Generated image path: {output_path}")
        print(f"Image properties: Dimensions = {width}x{height}, File size = {size_bytes} bytes")
        
        # Basic optical checks
        assert width == 1080 and height == 1080, "Dimensions must be 1080x1080 square"
        assert size_bytes > 15000, f"Saved file size too small ({size_bytes} bytes), might be empty or corrupt!"
        print("Optical checks PASSED: Dimensions and file size verified.")
    else:
        print("FAILED: Output file was not created!")
        sys.exit(1)

if __name__ == "__main__":
    run_test()

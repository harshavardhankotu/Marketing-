import json
import os
import requests
import platform
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def download_image(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGBA")
    except Exception as e:
        print(f"Failed to download image: {e}")
        return None

def download_font(url, save_path):
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    if not os.path.exists(save_path):
        try:
            print(f"Downloading font from {url}...")
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                f.write(response.content)
            print("Font downloaded successfully.")
        except Exception as e:
            print(f"Failed to download font: {e}")

def get_safe_font(font_size, preferred_bold=True):
    """
    Attempts to locate a readable TTF font in a robust fallback chain:
    1. Bundled local font in the workspace (assets/Roboto-Bold.ttf or assets/fonts/...)
    2. Common system fonts based on OS (Windows system fonts first, then MacOS, then Linux)
    3. Safe default system font or default PIL font
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    # 1. Try local bundled fonts in workspace
    local_paths = [
        os.path.join(base_dir, 'assets', 'Roboto-Bold.ttf'),
        os.path.join(base_dir, 'assets', 'fonts', 'Roboto-Bold.ttf'),
        os.path.join(base_dir, 'assets', 'fonts', 'Roboto-Regular.ttf'),
    ]
    
    for path in local_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, font_size)
            except Exception:
                pass
                
    # 2. Try common system fonts based on OS
    system_fonts = []
    current_os = platform.system().lower()
    
    if "windows" in current_os:
        win_dir = os.environ.get('WINDIR', 'C:\\Windows')
        if preferred_bold:
            system_fonts.extend([
                os.path.join(win_dir, 'Fonts', 'arialbd.ttf'), # Arial Bold
                os.path.join(win_dir, 'Fonts', 'tahomabd.ttf'), # Tahoma Bold
                os.path.join(win_dir, 'Fonts', 'calibrib.ttf'), # Calibri Bold
            ])
        system_fonts.extend([
            os.path.join(win_dir, 'Fonts', 'arial.ttf'),
            os.path.join(win_dir, 'Fonts', 'tahoma.ttf'),
            os.path.join(win_dir, 'Fonts', 'calibri.ttf'),
        ])
    elif "darwin" in current_os:
        system_fonts.extend([
            '/Library/Fonts/Arial Bold.ttf',
            '/Library/Fonts/Arial.ttf',
            '/System/Library/Fonts/Helvetica.dfont',
        ])
    else: # Linux / other
        system_fonts.extend([
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
            '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        ])
        
    # Standard fallbacks that Pillow might find in default lookup paths
    system_fonts.extend([
        'arial.ttf',
        'DejaVuSans.ttf',
        'Tahoma.ttf',
        'Helvetica.ttf'
    ])
    
    for font_name in system_fonts:
        try:
            return ImageFont.truetype(font_name, font_size)
        except Exception:
            pass
            
    # 3. Fallback to default
    print("WARNING: All TTF fonts failed to load. Falling back to default ImageFont.")
    try:
        # Try to load default with size if supported in newer Pillow
        return ImageFont.load_default(size=font_size)
    except Exception:
        return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    """Properly wrap text using actual text measurement instead of character estimation."""
    words = text.split()
    lines = []
    current_line = ""
    
    for word in words:
        test_line = f"{current_line} {word}".strip()
        try:
            bbox = draw.textbbox((0, 0), test_line, font=font)
            text_width = bbox[2] - bbox[0]
        except Exception:
            # Fallback for old PIL versions
            text_width = draw.textsize(test_line, font=font)[0]
            
        if text_width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    
    if current_line:
        lines.append(current_line)
    
    return "\n".join(lines)

def create_social_graphic(product, output_path, block_network=False):
    # Dimensions for Instagram/Twitter Square
    WIDTH, HEIGHT = 1080, 1080
    
    # 1. Create Background (Modern Dark Theme)
    background = Image.new('RGB', (WIDTH, HEIGHT), color=(18, 18, 18))
    draw = ImageDraw.Draw(background)
    
    # Draw a subtle gradient or shape (e.g., an accent circle)
    accent_color = (71, 85, 105) # Slate gray accent
    draw.ellipse((-200, -200, 600, 600), fill=(25, 25, 25))
    draw.ellipse((WIDTH-400, HEIGHT-400, WIDTH+200, HEIGHT+200), fill=(25, 25, 25))

    # 2. Add Product Image
    product_img = None
    if not block_network:
        product_img = download_image(product['image_url'])
        
    if product_img:
        # Resize image maintaining aspect ratio
        product_img.thumbnail((600, 600), Image.Resampling.LANCZOS)
        
        # Calculate center position
        img_w, img_h = product_img.size
        offset = ((WIDTH - img_w) // 2, (HEIGHT - img_h) // 2 - 100) # Slightly above center
        
        # Paste with alpha channel onto RGB background
        background.paste(product_img, offset, product_img)
    else:
        # Visual badge fallback overlay if no image/network
        draw.rounded_rectangle([240, 240, 840, 540], fill=(30, 30, 30), outline=(50, 50, 50), width=4, radius=20)
        draw.text((WIDTH//2, 390), "IMAGE OFFLINE", anchor="mm", fill=(100, 100, 100))

    # 3. Add Text
    # Setup Fonts using robust fallback chain
    title_font = get_safe_font(48, preferred_bold=True)
    price_font = get_safe_font(64, preferred_bold=True)
    brand_font = get_safe_font(32, preferred_bold=False)

    # Wrap title text properly using pixel measurements
    max_text_width = WIDTH - 100  # 50px padding each side
    wrapped_title = wrap_text(draw, product['title'], title_font, max_text_width)

    # Determine if we are using the tiny default font (which has no proper scaling in older PIL)
    is_default_font = False
    try:
        # Detect tiny default font by checking bbox height of standard string
        bbox = draw.textbbox((0, 0), "TEST", font=title_font)
        height = bbox[3] - bbox[1]
        if height < 15: # Tiny default font
            is_default_font = True
    except Exception:
        # Fallback check
        try:
            if title_font.getname()[0] == 'CourierNewPSMT' or 'default' in str(type(title_font)).lower():
                is_default_font = True
        except Exception:
            is_default_font = True

    if is_default_font:
        print("Using default font visual overlay enhancement to preserve readability at 1080p scale.")
        # Create a text badge using a smaller canvas and scaling it up to look clean and highly readable
        # This acts as the visual overlay requested for the price/CTA/title to ensure maximum readability
        title_y = HEIGHT - 350
        
        # Draw readable badge background for title & details
        draw.rounded_rectangle([40, title_y - 20, WIDTH - 40, HEIGHT - 40], fill=(25, 25, 25), outline=accent_color, width=3, radius=15)
        
        # Draw texts using large block letters (drawn onto small canvas and upscaled)
        # Create small overlay image
        overlay = Image.new('RGBA', (WIDTH // 4, 80), color=(0, 0, 0, 0))
        o_draw = ImageDraw.Draw(overlay)
        o_draw.text((10, 10), product['title'][:25], font=title_font, fill=(255, 255, 255))
        o_draw.text((10, 30), f"Rs. {product['price']}", font=price_font, fill=(255, 255, 255))
        o_draw.text((10, 50), f"On {product['platform']}", font=brand_font, fill=(255, 255, 0))
        
        # Upscale overlay image using NEAREST to make it sharp and blocky
        overlay_large = overlay.resize((WIDTH - 120, 240), Image.Resampling.NEAREST)
        background.paste(overlay_large, (60, title_y), overlay_large)
    else:
        # Draw Title
        title_y = HEIGHT - 300
        draw.multiline_text((50, title_y), wrapped_title, font=title_font, fill=(255, 255, 255), spacing=10)
        
        # Calculate dynamic price position based on title height
        title_bbox = draw.multiline_textbbox((50, title_y), wrapped_title, font=title_font, spacing=10)
        price_y = title_bbox[3] + 20
        
        # Draw Price
        price_text = f"₹ {product['price']}"
        # Draw a highlighted box for price
        price_bbox = draw.textbbox((50, price_y), price_text, font=price_font)
        draw.rounded_rectangle([price_bbox[0]-10, price_bbox[1]-10, price_bbox[2]+10, price_bbox[3]+10], fill=accent_color, radius=10)
        draw.text((50, price_y), price_text, font=price_font, fill=(255, 255, 255))
        
        # Draw Platform tag
        platform_text = f"On {product['platform']}"
        platform_bbox = draw.textbbox((0, 0), platform_text, font=brand_font)
        platform_width = platform_bbox[2] - platform_bbox[0]
        draw.text((WIDTH - platform_width - 50, 50), platform_text, font=brand_font, fill=(150, 150, 150))
    
    # Save
    background.save(output_path, quality=95)
    print(f"Saved graphic to {output_path}")

if __name__ == "__main__":
    print("Starting Design Engine...")
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    assets_dir = os.path.join(base_dir, 'assets')
    output_dir = os.path.join(base_dir, 'data', 'output')
    
    # Ensure dirs exist
    os.makedirs(assets_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    
    # Download standard font to assets dir
    font_path = os.path.join(assets_dir, 'Roboto-Bold.ttf')
    download_font("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Bold.ttf", font_path)
    
    # Load products
    products_path = os.path.join(base_dir, 'data', 'trending_products.json')
    if os.path.exists(products_path):
        with open(products_path, 'r', encoding='utf-8') as f:
            products = json.load(f)
            
        if products:
            for idx, product in enumerate(products):
                print(f"\nGenerating graphic {idx+1}/{len(products)}: {product['title']}...")
                output_file = os.path.join(output_dir, f"graphic_{idx}.jpg")
                create_social_graphic(product, output_file)
            print(f"\nGenerated {len(products)} graphics.")
        else:
            print("No products found in json.")
    else:
        print(f"Data file not found at {products_path}")

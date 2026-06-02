import os
import json
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini using the new google-genai SDK
api_key = os.getenv("GEMINI_API_KEY")
client = None

if api_key:
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except ImportError:
        # Fallback to deprecated SDK if new one isn't available
        try:
            import google.generativeai as genai_old
            genai_old.configure(api_key=api_key)
            client = "legacy"
        except ImportError:
            print("Warning: No Gemini SDK found. Using mock captions.")

SECTOR_TEMPLATES = {
    "auto_insurance": {
        "en": "🚗 Stop throwing away ₹15,000 every year on bloated car insurance premiums! Compare top auto rates in 2 minutes on {platform} and save up to {discount}% on coverages. {why}",
        "hi": "🚗 हर साल कार इंश्योरेंस के फालतू ₹15,000 भरना बंद करें! {platform} पर तुरंत मुफ्त कार इंश्योरेंस कोट पाएं और {discount}% तक बचाएं। {why}",
        "ta": "🚗 வீணாக கார் இன்சூரன்ஸில் ஆண்டுக்கு ₹15,000 வரை வீணடிப்பதை நிறுத்துங்கள்! {platform} இல் உடனே இலவச கார் இன்சூரன்ஸ் கோட் பெறுங்கள், {discount}% வரை சேமியுங்கள். {why}"
    },
    "health_insurance": {
        "en": "🏥 Medical bills shouldn't drain your life savings! Secure premium health coverages on {platform} for as low as ₹2,500/month. Free physician network checks. {why}",
        "hi": "🏥 मेडिकल बिल आपके जीवन की बचत को खत्म नहीं करने चाहिए! {platform} पर सिर्फ ₹2,500/माह में स्वास्थ्य बीमा प्राप्त करें और {discount}% की बचत करें। {why}",
        "ta": "🏥 மருத்துவக் கட்டணங்கள் உங்கள் சேமிப்பை அழிக்க விடாதீர்கள்! {platform} இல் மாதம் வெறும் ₹2,500 முதல் பிரீமியம் ஹெல்த் இன்சூரன்ஸ் பெறுங்கள், {discount}% வரை சேமியுங்கள். {why}"
    },
    "debt_relief": {
        "en": "💸 Trapped under ₹5,00,000+ credit card or loan debt? Consolidate and cut interest rates by up to {discount}% with {platform}'s free debt relief advisory. {why}",
        "hi": "💸 ₹5,00,000+ के कर्ज के जाल में फंसे हैं? {platform} के मुफ्त ऋण राहत कार्यक्रम से ब्याज दरों को {discount}% तक कम करें। {why}",
        "ta": "💸 ₹5,00,000-க்கு மேல் கடனில் தவிக்கிறீர்களா? {platform} இன் இலவச கடன் நிவாரண ஆலோசனை மூலம் வட்டி விகிதத்தை {discount}% வரை குறைக்கவும். {why}"
    },
    "solar_energy": {
        "en": "☀️ Slash your electricity bill by {discount}%+ with zero down home solar transition quotes on {platform}. Claim up to 30% government tax refund credits! {why}",
        "hi": "☀️ बिजली का बिल आधा करें! {platform} पर जीरो डाउन होम सोलर पैनल कोट पाएं और {discount}% तक बिजली बिल बचाकर 30% सरकारी टैक्स रिफंड का लाभ उठाएं। {why}",
        "ta": "☀️ மின் கட்டணத்தை {discount}% வரை குறையுங்கள்! {platform} இல் பூஜ்ஜிய முன்பணத்துடன் இலவச சோலார் பேனல் கோட் பெறுங்கள். {why}"
    },
    "home_security": {
        "en": "🔒 Protect what matters most with premium property protection systems on {platform}. Get zero upfront equipment monitoring quotes and save up to {discount}% on home insurance! {why}",
        "hi": "🔒 अपने परिवार की सुरक्षा के लिए {platform} पर प्रीमियम होम सिक्योरिटी अलार्म कोट पाएं और कार/होम इंश्योरेंस प्रीमियम में {discount}% तक बचाएं! {why}",
        "ta": "🔒 உங்கள் வீட்டைப் பாதுகாப்பாக வைத்திருங்கள்! {platform} இல் இலவச ஹோம் செக்யூரிட்டி கோட் பெற்று இன்சூரன்ஸ் பிரீமியத்தில் {discount}% வரை சேமியுங்கள்! {why}"
    },
    "live_links": {
        "en": "🔗 Real-time savings alert! Slash your monthly overheads instantly on {platform} by up to {discount}%. Claim this high-ticket quotation tunnel before limits expire! {why}",
        "hi": "🔗 रियल-टाइम बचत अलर्ट! {platform} पर तुरंत मासिक खर्चों को {discount}% तक कम करें। सीमाओं की समाप्ति से पहले लाभ उठाएं! {why}",
        "ta": "🔗 ரியல்-டைம் சேமிப்பு அலர்ட்! {platform} இல் உடனே உங்கள் மாதாந்திர செலவுகளை {discount}% வரை குறையுங்கள். உடனே விண்ணப்பியுங்கள்! {why}"
    }
}

def generate_multilingual_copy(product):
    platform = product.get('platform', 'CPA Network')
    title = product.get('title', 'Service Campaign')
    price = product.get('price', 'N/A')
    discount = product.get('discount', 40)
    
    bf = product.get('buyer_fit', {})
    labels = ", ".join(bf.get('label_display', [])) if bf.get('label_display') else "High Payout"
    
    vex = product.get('value_explanation', {})
    why = vex.get('why_this_product', '')
    tradeoffs = vex.get('tradeoffs', '')
    
    sector = product.get('sector', 'auto_insurance').lower()
    if sector not in SECTOR_TEMPLATES:
        sector = "auto_insurance"
        
    tmpl_en = SECTOR_TEMPLATES[sector]["en"].format(title=title, price=price, platform=platform, labels=labels, why=why, discount=discount)
    tmpl_hi = SECTOR_TEMPLATES[sector]["hi"].format(title=title, price=price, platform=platform, labels=labels, why=why, discount=discount)
    tmpl_ta = SECTOR_TEMPLATES[sector]["ta"].format(title=title, price=price, platform=platform, labels=labels, why=why, discount=discount)
    
    mock_en = (
        f"🚨 {tmpl_en}\n\n"
        f"🔗 Get Quote / Call Now: Link in Bio!\n\n"
        f"*Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad\n\n"
        f"#savings #CPA #{sector} #{platform.replace(' ', '')} #freequote"
    )
    mock_hi = (
        f"🚨 {tmpl_hi}\n\n"
        f"🔗 कोट पाने / कॉल करने के लिए लिंक बायो में है!\n\n"
        f"*डिस्क्लोज़र: पेड पार्टनर। क्वालिफाइड कोट पर कमीशन कमाता हूँ। #Ad\n\n"
        f"#savings #CPA #{sector} #{platform.replace(' ', '')} #freequote"
    )
    mock_ta = (
        f"🚨 {tmpl_ta}\n\n"
        f"🔗 கோட் பெற / அழைக்க லிங்க் பயோவில் உள்ளது!\n\n"
        f"*வெளிப்படுத்தல்: கட்டண கூட்டாளர். தகுதி வாய்ந்த கோட் சமர்ப்பிப்புகளுக்கு கமிஷன் பெறுகிறார். #Ad\n\n"
        f"#savings #CPA #{sector} #{platform.replace(' ', '')} #freequote"
    )
    
    # Import reliability services
    from bots.resilience import call_with_retry, timeout_call
    from bots.circuit_breakers import get_breaker, CircuitBreakerOpenException
    from bots.quota_manager import check_quota, consume_quota, QuotaExceededException

    if not client:
        return {'en': mock_en, 'hi': mock_hi, 'ta': mock_ta}

    # 1. Quota Check
    if check_quota("gemini") == "BLOCKED":
        print("[AI_COPYWRITER] Daily quota blocked for 'gemini'. Returning mock fallbacks.")
        return {'en': mock_en, 'hi': mock_hi, 'ta': mock_ta}

    # 2. Circuit Breaker Check
    breaker = get_breaker("gemini", failure_threshold=5, cooldown_sec=60)
    try:
        breaker.check()
    except CircuitBreakerOpenException as e:
        print(f"[AI_COPYWRITER] Circuit Breaker open for 'gemini'. Returning mock fallbacks. Details: {e}")
        return {'en': mock_en, 'hi': mock_hi, 'ta': mock_ta}

    prompt = f"""
    You are an expert lead-acquisition copywriter specializing in high-ticket CPA (Cost Per Action) marketing campaigns.
    Write highly engaging, problem-focused, and cost-saving captions for the following CPA service offer in 3 languages: English, Hindi, and Tamil.
    
    Offer details:
    - Campaign Title: {title}
    - Vertical Sector: {sector}
    - Estimated Lead Payout: ₹{price}
    - Cost Savings Percentage: {discount}%
    - Source Platform: {platform}
    - Target Demographics: {labels}
    - Key Pain Point Resolution: {why}
    
    Rules for ALL languages:
    - Write deep pain-point and cost-optimization hooks rather than physical product reviews (e.g. "Stop wasting ₹15,000 every year...").
    - Keep captions natural, punchy, and persuasive.
    - Keep names and pricing numbers exactly as they are.
    - Include a clear call to action to submit a quote or call free now.
    - You MUST explicitly append this required FTC marketing disclosure to the very end of the text:
      - For English: "*Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad"
      - For Hindi: "*डिस्क्लोज़र: पेड पार्टनर। क्वालिफाइड कोट पर कमीशन कमाता हूँ। #Ad"
      - For Tamil: "*வெளிப்படுத்தல்: கட்டண கூட்டாளர். தகுதி வாய்ந்த கோட் சமர்ப்பிப்புகளுக்கு கமிஷன் பெறுகிறார். #Ad"
    
    Output strictly as a JSON object with keys: "en", "hi", "ta". 
    Do not wrap in markdown code blocks. Just return valid JSON.
    """
    
    def make_api_call():
        consume_quota("gemini")
        if client == "legacy":
            import google.generativeai as genai_old
            model = genai_old.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content(prompt)
            return response.text.strip()
        else:
            response = client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            return response.text.strip()

    def make_api_call_with_timeout():
        return timeout_call(10, make_api_call)

    # 3. Call with Retry & Timeout
    retry_res = call_with_retry(
        make_api_call_with_timeout,
        max_retries=3,
        base_delay=1.0,
        max_delay=5.0,
        provider="gemini"
    )

    if not retry_res["success"]:
        # Record failure to circuit breaker
        breaker.record_failure()
        print(f"[AI_COPYWRITER] Gemini API execution failed: {retry_res['error']}. Returning mock fallbacks.")
        return {'en': mock_en, 'hi': mock_hi, 'ta': mock_ta}

    # Record success to circuit breaker
    breaker.record_success()
    text = retry_res["result"]

    disclosures = {
        'en': "*Disclosure: Paid partner. Earns commission on qualified quote submissions. #Ad",
        'hi': "*डिस्क्लोज़र: पेड पार्टनर। क्वालिफाइड कोट पर कमीशन कमाता हूँ। #Ad",
        'ta': "*வெளிப்படுத்தல்: கட்டண கூட்டாளர். தகுதி வாய்ந்த கோட் சமர்ப்பிப்புகளுக்கு கமிஷன் பெறுகிறார். #Ad"
    }

    try:
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        parsed = json.loads(text)
        if "en" in parsed and "hi" in parsed and "ta" in parsed:
            for lang in ['en', 'hi', 'ta']:
                val = parsed[lang]
                if "#Ad" not in val and "disclosure" not in val.lower() and "commission" not in val.lower():
                    parsed[lang] = val.strip() + "\n\n" + disclosures[lang]
            return parsed
        else:
            parsed = {'en': text, 'hi': mock_hi, 'ta': mock_ta}
            for lang in ['en', 'hi', 'ta']:
                val = parsed[lang]
                if "#Ad" not in val and "disclosure" not in val.lower() and "commission" not in val.lower():
                    parsed[lang] = val.strip() + "\n\n" + disclosures[lang]
            return parsed
    except Exception as e:
        print(f"Error parsing multilingual caption JSON: {e}")
        return {'en': mock_en, 'hi': mock_hi, 'ta': mock_ta}

def enrich_with_multilingual_copy(products):
    """Entry point for pipeline_service to generate copy in-process."""
    for idx, product in enumerate(products):
        print(f"  Generating copy ({idx+1}/{len(products)}): {product.get('title', '')[:30]}...")
        copies = generate_multilingual_copy(product)
        product['copy'] = copies
        # Preserve backward compatibility for UI
        product['caption'] = copies.get('en', '')
    return products

if __name__ == "__main__":
    print("Starting AI Copywriter...")
    
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../bots')))
    from config import OUTPUT_DIR

    products_path = os.path.join(OUTPUT_DIR, 'explained_products.json')
    if not os.path.exists(products_path):
        products_path = os.path.join(OUTPUT_DIR, 'ranked_products.json')
        
    if os.path.exists(products_path):
        with open(products_path, 'r', encoding='utf-8') as f:
            products = json.load(f)
            
        if products:
            output_path = os.path.join(OUTPUT_DIR, 'post_data.json')
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            post_data = []
            for idx, product in enumerate(products):
                print(f"Generating copy for product {idx+1}/{len(products)}: {product.get('title', 'Product')}...")
                copies = generate_multilingual_copy(product)
                print(f"  Copy generated successfully.")
                
                product['copy'] = copies
                product['caption'] = copies.get('en', '')
                product['graphic_path'] = f'/image/graphic_{idx}.jpg'
                if 'id' not in product:
                    product['id'] = f'prod_{idx}'
                post_data.append(product)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(post_data, f, indent=4, ensure_ascii=False)
            print(f"Saved {len(post_data)} posts to {output_path}")
        else:
            print("No products found.")
    else:
        print(f"Data file not found at {products_path}")

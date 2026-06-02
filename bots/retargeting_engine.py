import sqlite3
import os
import json
import random
from datetime import datetime, timedelta

from config import DB_PATH, OUTPUT_DIR
OUTPUT_PATH = os.path.join(OUTPUT_DIR, 'retargeting_plans.json')

def setup_retargeting_tables():
    """Create retargeting logs table if it doesn't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    
    c.execute('''
    CREATE TABLE IF NOT EXISTS retargeting_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        click_id INTEGER NOT NULL,
        product_id TEXT NOT NULL,
        session_id TEXT,
        strategy TEXT,
        retargeted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (click_id) REFERENCES affiliate_clicks (id)
    )
    ''')
    
    conn.commit()
    conn.close()

def generate_retargeting_campaigns(hours_ago_min=1, hours_ago_max=48):
    """
    Find clicks that have not converted and haven't been retargeted yet.
    Generate a multilingual retargeting plan.
    """
    import db_manager
    db_manager.setup_database()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Calculate time windows
    time_max = (datetime.utcnow() - timedelta(hours=hours_ago_min)).strftime('%Y-%m-%d %H:%M:%S')
    time_min = (datetime.utcnow() - timedelta(hours=hours_ago_max)).strftime('%Y-%m-%d %H:%M:%S')
    
    # We join with affiliate_conversions to find those still 'pending'
    query = '''
    SELECT c.id as click_id, c.product_id, c.product_title, c.sector, 
           c.channel, c.affiliate_link, c.session_id, c.clicked_at,
           c.revenue_score, c.est_commission_pct
    FROM affiliate_clicks c
    JOIN affiliate_conversions conv ON conv.click_id = c.id
    LEFT JOIN retargeting_logs r ON r.click_id = c.id
    WHERE conv.status = 'pending_conversion'
      AND c.is_bot = 0
      AND c.clicked_at BETWEEN ? AND ?
      AND r.id IS NULL
      AND (
          SELECT COUNT(*) FROM retargeting_logs rl
          WHERE rl.session_id = c.session_id AND rl.product_id = c.product_id
      ) < 2
      AND NOT EXISTS (
          SELECT 1 FROM retargeting_suppression s
          WHERE s.session_id = c.session_id AND s.product_id = c.product_id
      )
    ORDER BY c.est_commission_pct DESC, c.revenue_score DESC
    '''
    
    c.execute(query, (time_min, time_max))
    candidates = [dict(row) for row in c.fetchall()]
    
    retargeting_plans = []
    
    # Multilingual strategies
    STRATEGIES = [
        {
            "name": "urgency_reminder",
            "messages": {
                "en": "🚨 Last chance! The deal you viewed is expiring soon.",
                "hi": "🚨 आखिरी मौका! आपकी देखी हुई डील जल्द ही खत्म हो रही है।",
                "ta": "🚨 கடைசி வாய்ப்பு! நீங்கள் பார்த்த டீல் விரைவில் முடிவடைகிறது."
            }
        },
        {
            "name": "price_drop",
            "messages": {
                "en": "📉 Good news! We noticed a price drop on this item.",
                "hi": "📉 खुशखबरी! इस आइटम की कीमत गिर गई है।",
                "ta": "📉 நற்செய்தி! இந்த பொருளின் விலை குறைந்துள்ளது."
            }
        },
        {
            "name": "social_proof",
            "messages": {
                "en": "🔥 Trending now: 50+ people bought this today.",
                "hi": "🔥 ट्रेंडिंग: आज 50 से ज्यादा लोगों ने इसे खरीदा है।",
                "ta": "🔥 ட்ரெண்டிங்: இன்று 50-க்கும் மேற்பட்டோர் இதை வாங்கியுள்ளனர்."
            }
        }
    ]
    
    from distributor import _resolve_audience_language

    for candidate in candidates:
        chosen_strategy = random.choice(STRATEGIES)
        
        # Log the retargeting action to prevent spam
        c.execute('''
        INSERT INTO retargeting_logs (click_id, product_id, session_id, strategy)
        VALUES (?, ?, ?, ?)
        ''', (candidate['click_id'], candidate['product_id'], candidate['session_id'], chosen_strategy['name']))
        
        # Resolve preferred language based on the channel they clicked from
        target_lang = _resolve_audience_language(candidate['channel'], candidate['sector'])
        
        retargeting_plans.append({
            "click_id": candidate['click_id'],
            "product_id": candidate['product_id'],
            "product_title": candidate['product_title'],
            "sector": candidate['sector'],
            "original_channel": candidate['channel'],
            "session_id": candidate['session_id'],
            "affiliate_link": candidate['affiliate_link'],
            "revenue_score": candidate['revenue_score'],
            "est_commission": candidate['est_commission_pct'],
            "strategy": chosen_strategy['name'],
            "preferred_lang": target_lang,
            "message": chosen_strategy['messages'].get(target_lang, chosen_strategy['messages']['en']),
            "all_messages": chosen_strategy['messages'],
            "urgency_level": "High" if candidate['est_commission_pct'] > 5 else "Medium"
        })
        
    conn.commit()
    conn.close()
    
    # Save output
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(retargeting_plans, f, indent=4, ensure_ascii=False)
        
    return retargeting_plans

def get_retargeting_stats():
    """Return stats for the dashboard."""
    setup_retargeting_tables()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) as total FROM retargeting_logs')
    total = dict(c.fetchone()).get('total', 0)
    
    c.execute('SELECT strategy, COUNT(*) as count FROM retargeting_logs GROUP BY strategy')
    by_strategy = [dict(r) for r in c.fetchall()]
    
    c.execute('''
        SELECT r.strategy, r.retargeted_at, c.product_title, c.sector 
        FROM retargeting_logs r 
        JOIN affiliate_clicks c ON r.click_id = c.id
        ORDER BY r.retargeted_at DESC LIMIT 10
    ''')
    recent = [dict(r) for r in c.fetchall()]
    
    conn.close()
    return {
        "total_retargeted": total,
        "by_strategy": by_strategy,
        "recent_logs": recent
    }

if __name__ == "__main__":
    plans = generate_retargeting_campaigns(hours_ago_min=0, hours_ago_max=48)
    print(f"Generated {len(plans)} retargeting campaigns.")

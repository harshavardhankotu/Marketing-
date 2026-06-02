import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import sqlite3
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

def check_files():
    files = [
        'app.py', 'requirements.txt',
        'bots/scheduler_engine.py', 'bots/pipeline_service.py',
        'bots/buyer_fit_engine.py', 'bots/value_explainer.py',
        'bots/revenue_ranker.py', 'bots/segmentation_engine.py',
        'bots/recommendation_engine.py', 'bots/market_analyzer.py',
        'bots/alert_engine.py', 'bots/ab_engine.py',
        'bots/affiliate_tracker.py', 'bots/retargeting_engine.py',
        'bots/db_manager.py', 'scrapers/product_scraper.py',
        'templates/index.html', 'templates/history.html'
    ]
    res = {}
    for f in files:
        full_path = os.path.join(PROJECT_ROOT, f)
        res[f] = os.path.exists(full_path)
    return res

def check_db():
    from bots.config import DB_PATH
    if not os.path.exists(DB_PATH):
        return {'error': 'DB not found'}
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in c.fetchall()]
    
    table_counts = {}
    for t in tables:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        table_counts[t] = c.fetchone()[0]
    conn.close()
    return table_counts

def check_routes():
    base = 'http://127.0.0.1:5000'
    routes = {
        'GET /': ('GET', '/'),
        'GET /history': ('GET', '/history'),
        'GET /api/history': ('GET', '/api/history'),
        'GET /api/clicks': ('GET', '/api/clicks'),
        'GET /api/retargeting_stats': ('GET', '/api/retargeting_stats'),
        'GET /api/scheduler_status': ('GET', '/api/scheduler_status'),
        'GET /api/scheduler_config': ('GET', '/api/scheduler_config'),
        'POST /api/run_retargeting': ('POST', '/api/run_retargeting'),
        'GET /go/test_product': ('GET', '/go/test_product')
    }
    
    res = {}
    for name, (method, path) in routes.items():
        try:
            if method == 'GET':
                r = requests.get(base + path, timeout=5)
            else:
                r = requests.post(base + path, json={}, timeout=10)
            res[name] = r.status_code
        except Exception as e:
            res[name] = str(e)
    return res

print('FILES:', check_files())
print('DB:', check_db())
print('ROUTES:', check_routes())

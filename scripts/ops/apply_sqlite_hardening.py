import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

files_to_update = [
    'bots/ab_engine.py',
    'bots/affiliate_tracker.py',
    'bots/alert_engine.py',
    'bots/retargeting_engine.py',
    'bots/scheduler_engine.py',
    'tests/qa_verify.py'
]

def harden_file(filename):
    filepath = os.path.join(PROJECT_ROOT, filename)
    if not os.path.exists(filepath):
        print(f"Skipping: {filepath} (Not found)")
        return
        
    print(f"Hardening SQLite connect in {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Standardize connect calls
    modified = content.replace('sqlite3.connect(DB_PATH)', 'sqlite3.connect(DB_PATH, timeout=30.0)')
    modified = modified.replace('sqlite3.connect(db_path)', 'sqlite3.connect(db_path, timeout=30.0)')
    
    if modified != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(modified)
        print(f"Successfully updated {filepath}")
    else:
        print(f"No changes needed for {filepath}")

for f in files_to_update:
    harden_file(f)

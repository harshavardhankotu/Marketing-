import json
import os
import random

def run_analytics():
    print("Running Analytics Engine...")
    from config import OUTPUT_DIR
    dist_log_path = os.path.join(OUTPUT_DIR, 'distribution_log.json')
    analytics_log_path = os.path.join(OUTPUT_DIR, 'analytics_log.json')
    
    if os.path.exists(dist_log_path):
        with open(dist_log_path, 'r', encoding='utf-8') as f:
            logs = json.load(f)
            
        for log in logs:
            total_views = 0
            total_clicks = 0
            for plat in log.get('platforms', []):
                # Simulate metrics based on platform
                views = random.randint(150, 1500)
                # CTR between 1% and 5%
                clicks = int(views * random.uniform(0.01, 0.05))
                
                plat['metrics'] = {
                    "views": views,
                    "clicks": clicks,
                    "ctr": f"{(clicks/views)*100:.1f}%" if views > 0 else "0%"
                }
                total_views += views
                total_clicks += clicks
                
            log['total_metrics'] = {
                "total_views": total_views,
                "total_clicks": total_clicks,
                "overall_ctr": f"{(total_clicks/total_views)*100:.1f}%" if total_views > 0 else "0%"
            }
            
        with open(analytics_log_path, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=4)
        print(f"Analytics updated for {len(logs)} campaigns.")
    else:
        print("No distribution log found to analyze.")

if __name__ == "__main__":
    run_analytics()

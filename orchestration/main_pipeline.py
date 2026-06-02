import sys
import os

# Add parent dir to path so we can import execution modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.research_agent import run_research
from execution.creative_agent import run_creative
from execution.monetization_agent import run_monetization

def orchestrate():
    print("="*50)
    print("[ROCKET] ANTIGRAVITY MARKETING ORCHESTRATOR STARTING")
    print("="*50)
    
    # Step 1: Research
    print("\n[Orchestrator] Spawning Research Agent...")
    if not run_research():
        print("[X] Pipeline halted at Research phase.")
        return
        
    # Step 2: Creative
    print("\n[Orchestrator] Spawning Creative Agent...")
    if not run_creative():
        print("[X] Pipeline halted at Creative phase.")
        return
        
    # Step 3: Monetization
    print("\n[Orchestrator] Spawning Monetization Agent...")
    if not run_monetization():
        print("[X] Pipeline halted at Monetization phase.")
        return
        
    print("\n" + "="*50)
    print("[SUCCESS] PIPELINE COMPLETE. Campaign sheet is ready for review.")
    print("="*50)

if __name__ == "__main__":
    # Ensure run from the correct directory so relative paths in execution scripts work
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir('execution')
    orchestrate()

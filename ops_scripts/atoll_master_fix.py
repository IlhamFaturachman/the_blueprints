#!/usr/bin/env python3
import subprocess
import time
import json
import os

def run(cmd):
    try:
        return subprocess.check_output(cmd, shell=True).decode().strip()
    except:
        return ""

print("Stopping service...")
run("systemctl stop blueprints")
time.sleep(2)

print("Killing survivors...")
run("pkill -9 -f 'market_discovery.py'")
run("fuser -k 8082/tcp")
run("rm -f /tmp/the_blueprints.pid")

print("Resurrecting NYC...")
state_path = '/opt/the_blueprints/logs/paper_positions_5usd.json'
if os.path.exists(state_path):
    with open(state_path, 'r') as f:
        state = json.load(f)
    
    nyc = None
    new_history = []
    for pos in state.get('history', []):
        if pos.get('city') == 'new york city' and pos.get('status') == 'closed':
            nyc = pos
        else:
            new_history.append(pos)
    
    if nyc:
        nyc['status'] = 'open'
        nyc['target_strategy'] = 'hold_until_resolve'
        for field in ['closed_at', 'close_reason', 'exit_price', 'exit_value', 'realized_pnl_usd', 'realized_roi_pct']:
            if field in nyc: del nyc[field]
        
        state['history'] = new_history
        # Ensure it's not already in positions
        if not any(p.get('city') == 'new york city' and p.get('status') == 'open' for p in state['positions']):
            state['positions'].append(nyc)
            print("NYC added to positions.")
        
        with open(state_path, 'w') as f:
            json.dump(state, f, indent=2)
        print("State written.")

print("Starting service...")
run("systemctl start blueprints")
print("Done. Check logs at /opt/the_blueprints/logs/paper_loop.out")

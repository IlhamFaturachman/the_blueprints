import json
import os

path = '/opt/the_blueprints/logs/paper_positions_5usd.json'
with open(path, 'r') as f:
    state = json.load(f)

# Find NYC in history
nyc = None
new_history = []
for pos in state.get('history', []):
    if pos.get('city') == 'new york city' and pos.get('status') == 'closed':
        nyc = pos
        print(f"Found NYC in history: {pos.get('token_id')}")
    else:
        new_history.append(pos)

if nyc:
    # Resurrect
    nyc['status'] = 'open'
    nyc['target_strategy'] = 'hold_until_resolve'
    # Clean up exit fields
    for field in ['closed_at', 'close_reason', 'exit_price', 'exit_value', 'realized_pnl_usd', 'realized_roi_pct']:
        if field in nyc:
            del nyc[field]
    
    state['history'] = new_history
    state['positions'].append(nyc)
    
    # Save back
    with open(path, 'w') as f:
        json.dump(state, f, indent=2)
    print("NYC Resurrected successfully!")
else:
    print("NYC not found in history.")

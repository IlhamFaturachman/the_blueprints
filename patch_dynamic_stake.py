with open("market_discovery.py", "r") as f:
    src = f.read()

replacement = """def run_paper_trading_cycle(
    min_price=None,
    max_price=None,
    stake_usd=PAPER_STAKE_USD,
    state_path=PAPER_STATE_FILE,
    force_aggressive_scan=False,
):
    \"\"\"Run one paper-trading cycle: discover, manage exits, open new positions.\"\"\"
    try:
        state = load_paper_state(state_path)
        realized_pnl = float(state.get("realized_pnl", 0.0))
        # Initial fund = max_positions (5) * initial_stake (1) = 5
        # If the user started with different config, we assume 5.0 base.
        import os
        base_wallet = float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0
        current_wallet = base_wallet + realized_pnl
        if current_wallet >= 10.0:
            stake_usd = float(int(current_wallet / 5.0))
    except Exception:
        pass

    return _run_paper_trading_cycle_impl(
"""

import re
src = re.sub(r'def run_paper_trading_cycle\([\s\S]*?return _run_paper_trading_cycle_impl\(', replacement, src)

with open("market_discovery.py", "w") as f:
    f.write(src)
print("dynamic stake patched")

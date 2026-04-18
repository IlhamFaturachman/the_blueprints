# Live-Like Paper Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make paper trading simulate real Polymarket live execution — accurate fees, real CLOB depth, bid-only exit pricing — so paper PnL is a reliable predictor of live PnL.

**Architecture:** Three surgical changes: (1) fee model added to entry + exit calculations, (2) depth check re-enabled for paper mode, (3) exit price evaluation locked to bid only. No new files — all changes in existing modules.

**Tech Stack:** Python, `market_discovery_internal/` modules, Polymarket CLOB API (already wired)

**Polymarket Fee Reference (from official docs):**
- Formula: `fee = C × feeRate × p × (1-p)` — C = shares, p = price
- Weather category taker fee rate: `0.05`
- Only takers pay fees (we always take liquidity → always pay fee)
- Fee applied BOTH at entry (buy) and exit (sell)
- Example: $1 stake @ $0.06 → C=16.67 shares → fee = 16.67×0.05×0.06×0.94 = **$0.047** (4.7%!)

---

## Files Modified

- **Modify:** `market_discovery_internal/config.py` — add fee rate + raise min stake floor
- **Modify:** `market_discovery_internal/pricing.py` — add `calculate_taker_fee()` helper
- **Modify:** `market_discovery_internal/cycles.py` — 4 targeted changes (depth bypass removal, entry fee, exit fee, exit price alignment)

---

## Task 1: Add Fee Config + Raise Min Stake Floor

**Files:**
- Modify: `market_discovery_internal/config.py`

Find the existing `MIN_STAKE_THRESHOLD` and `MAX_ACCEPTABLE_SLIPPAGE` lines. They look like:
```python
MAX_ACCEPTABLE_SLIPPAGE = float(os.getenv("MAX_ACCEPTABLE_SLIPPAGE", "0.05"))
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "0.50"))
MARKET_MAX_SPREAD_GATE = float(os.getenv("MARKET_MAX_SPREAD_GATE", "0.12"))
```

- [ ] **Step 1: Add fee rate config right after MARKET_MAX_SPREAD_GATE**

```python
# Polymarket taker fee rate per category (formula: C × rate × p × (1-p))
# Weather = 0.05, Sports = 0.03, Crypto = 0.072 (we trade Weather only)
POLYMARKET_TAKER_FEE_RATE = float(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
```

- [ ] **Step 2: Raise MIN_STAKE_THRESHOLD default from 0.50 to 1.0**

Change the existing line:
```python
# BEFORE:
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "0.50"))

# AFTER:
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "1.00"))
```

Reason: Polymarket practical minimum is ~$1 to avoid dust orders and ensure fee math is meaningful.

- [ ] **Step 3: Verify config loads**
```bash
cd /Users/macairm12020/Documents/Blueprints/the_blueprints
python3 -c "from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE, MIN_STAKE_THRESHOLD; print(POLYMARKET_TAKER_FEE_RATE, MIN_STAKE_THRESHOLD)"
```
Expected output: `0.05 1.0`

- [ ] **Step 4: Commit**
```bash
git add market_discovery_internal/config.py
git commit -m "feat: add Polymarket taker fee rate config, raise min stake to $1"
```

---

## Task 2: Add `calculate_taker_fee()` in pricing.py

**Files:**
- Modify: `market_discovery_internal/pricing.py`

- [ ] **Step 1: Add function at the bottom of pricing.py (before any existing module-level code at end of file)**

```python
def calculate_taker_fee(quantity: float, price: float, fee_rate: float = 0.05) -> float:
    """
    Compute Polymarket taker fee for a trade.
    Formula from docs: fee = C × feeRate × p × (1-p)
    C = quantity (shares), p = price (0-1 range)
    """
    if quantity <= 0 or price <= 0 or price >= 1.0:
        return 0.0
    return round(quantity * fee_rate * price * (1.0 - price), 6)
```

- [ ] **Step 2: Verify function works**
```bash
python3 -c "
from market_discovery_internal.pricing import calculate_taker_fee
# $1 stake @ $0.06: should be ~$0.047
fee = calculate_taker_fee(quantity=16.67, price=0.06, fee_rate=0.05)
print(f'Fee at 0.06: \${fee:.4f}')  # expect ~0.047
# $1 stake @ $0.50: should be ~$0.0125
fee2 = calculate_taker_fee(quantity=2.0, price=0.50, fee_rate=0.05)
print(f'Fee at 0.50: \${fee2:.4f}')  # expect ~0.025
"
```

- [ ] **Step 3: Commit**
```bash
git add market_discovery_internal/pricing.py
git commit -m "feat: add calculate_taker_fee() — Polymarket C×rate×p×(1-p) formula"
```

---

## Task 3: Remove Paper Mode Depth Bypass

**Files:**
- Modify: `market_discovery_internal/cycles.py` — around line 1574

Current code (the bypass to remove):
```python
        if is_paper_trading:
            # Bypass CLOB depth check for paper mode to avoid weather market pricing anomalies
            dynamic_stake = float(stake_usd)
        else:
            dynamic_stake = calculate_depth_adjusted_stake(token_id, stake_usd, max_slippage_pct=MAX_ACCEPTABLE_SLIPPAGE)
```

- [ ] **Step 1: Replace the bypass block with direct depth check**

```python
        # [LIVE-LIKE] Always check real CLOB depth — no paper bypass.
        # The original "weather anomaly" was a dict/list format bug (now fixed in pricing.py).
        dynamic_stake = calculate_depth_adjusted_stake(token_id, stake_usd, max_slippage_pct=MAX_ACCEPTABLE_SLIPPAGE)
```

- [ ] **Step 2: Verify the import line above it is still present**

The lines just before should still have:
```python
        from market_discovery_internal.config import MAX_ACCEPTABLE_SLIPPAGE, MIN_STAKE_THRESHOLD
        from market_discovery_internal.pricing import calculate_depth_adjusted_stake
```
These stay as-is. Only the `if is_paper_trading:` block is replaced.

- [ ] **Step 3: Verify no syntax error**
```bash
python3 -c "from market_discovery_internal.cycles import run_paper_trading_cycle; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**
```bash
git add market_discovery_internal/cycles.py
git commit -m "fix: remove paper mode CLOB depth bypass — was dict/list bug, now fixed"
```

---

## Task 4: Add Entry Fee to `build_paper_position`

**Files:**
- Modify: `market_discovery_internal/cycles.py` — `build_paper_position` function (~line 1085)

Current calculation:
```python
    effective_price = round(entry_price * 1.01, 4)
    quantity = round(float(stake_usd) / effective_price, 6)
    cost_basis = round(quantity * effective_price, 4)
```

- [ ] **Step 1: Add fee import at top of function and calculate entry fee**

Replace the calculation block:
```python
    from market_discovery_internal.pricing import calculate_taker_fee
    from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE

    effective_price = round(entry_price * 1.01, 4)
    quantity = round(float(stake_usd) / effective_price, 6)
    cost_basis = round(quantity * effective_price, 4)
    entry_fee_usd = calculate_taker_fee(quantity, effective_price, POLYMARKET_TAKER_FEE_RATE)
    total_entry_cost = round(cost_basis + entry_fee_usd, 4)
```

- [ ] **Step 2: Add the new fields to the returned position dict**

In the `position = { ... }` dict, find the `"cost_basis": cost_basis,` line and update it:
```python
        "cost_basis": total_entry_cost,      # total cash out (shares + fee) — used for cash debit
        "shares_cost": cost_basis,           # pure share cost (quantity × price)
        "entry_fee_usd": entry_fee_usd,      # taker fee paid on entry
```

**Why `cost_basis = total_entry_cost`:** The rest of the system (CB wallet calc, cash accounting, PnL) uses `cost_basis` as "what you paid." Making it the total cost means all downstream math is automatically correct — no other files need changes.

- [ ] **Step 3: Verify build_paper_position returns correct fields**
```bash
python3 -c "
from market_discovery_internal.cycles import build_paper_position
pos = build_paper_position({'token_id':'test','city':'london','direction':'YES',
    'threshold':30,'unit':'C','date':'2026-04-20','entry_price':0.06,
    'yes_price':0.06,'strategy':'swing'}, stake_usd=1.0)
print(f'cost_basis: {pos[\"cost_basis\"]}')       # expect ~1.047 (1.0 + fee)
print(f'entry_fee_usd: {pos[\"entry_fee_usd\"]}') # expect ~0.047
print(f'shares_cost: {pos[\"shares_cost\"]}')     # expect ~1.0
print(f'quantity: {pos[\"quantity\"]}')           # expect ~16.5 shares
"
```

- [ ] **Step 4: Commit**
```bash
git add market_discovery_internal/cycles.py
git commit -m "feat: add entry taker fee to build_paper_position cost_basis"
```

---

## Task 5: Add Exit Fee to `close_paper_position`

**Files:**
- Modify: `market_discovery_internal/cycles.py` — `close_paper_position` function (~line 1300)

Current calculation (around line 1303-1306):
```python
    price = float(exit_price)
    exit_value = round(price * float(closed["quantity"]), 4)
    pnl_usd = round(exit_value - float(closed["cost_basis"]), 4)
    roi_pct = round((pnl_usd / float(closed["cost_basis"])) * 100, 4) if closed["cost_basis"] else 0.0
```

- [ ] **Step 1: Add exit fee calculation**

Replace the calculation block:
```python
    from market_discovery_internal.pricing import calculate_taker_fee
    from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE

    price = float(exit_price)
    quantity = float(closed["quantity"])
    exit_value = round(price * quantity, 4)
    exit_fee_usd = calculate_taker_fee(quantity, price, POLYMARKET_TAKER_FEE_RATE)
    net_exit_value = round(exit_value - exit_fee_usd, 4)
    pnl_usd = round(net_exit_value - float(closed["cost_basis"]), 4)
    roi_pct = round((pnl_usd / float(closed["cost_basis"])) * 100, 4) if closed["cost_basis"] else 0.0
```

- [ ] **Step 2: Add exit fee fields to the `closed.update({...})` dict**

Find the `closed.update({...})` block (around line 1314). Add these fields inside it:
```python
            "exit_value": exit_value,       # gross exit value (before fee)
            "exit_fee_usd": exit_fee_usd,   # taker fee paid on exit
            "net_exit_value": net_exit_value,  # cash actually received
            "realized_pnl_usd": pnl_usd,
            "realized_roi_pct": roi_pct,
```

(Replace the existing `exit_value`, `realized_pnl_usd`, `realized_roi_pct` lines with these.)

- [ ] **Step 3: Verify PnL math for a round-trip**
```bash
python3 -c "
from market_discovery_internal.cycles import build_paper_position, close_paper_position
from datetime import datetime, timezone

pos = build_paper_position({'token_id':'test','city':'london','direction':'YES',
    'threshold':30,'unit':'C','date':'2026-04-20','entry_price':0.06,
    'yes_price':0.06,'strategy':'swing'}, stake_usd=1.0)
print(f'Entry: cost_basis={pos[\"cost_basis\"]:.4f}, fee={pos[\"entry_fee_usd\"]:.4f}')

# Close at same price (should be negative due to double fee)
closed = close_paper_position(pos, 0.06, 'test', datetime.now(timezone.utc))
print(f'Exit: net_exit={closed[\"net_exit_value\"]:.4f}, exit_fee={closed[\"exit_fee_usd\"]:.4f}')
print(f'PnL at flat price: {closed[\"realized_pnl_usd\"]:.4f}')  # expect negative (fee cost)

# Close at stop loss (e.g. 0.048 = 80% of 0.06)
closed2 = close_paper_position(pos, 0.048, 'stop_loss', datetime.now(timezone.utc))
print(f'PnL at SL: {closed2[\"realized_pnl_usd\"]:.4f}')  # expect ~-0.35 total
"
```

- [ ] **Step 4: Commit**
```bash
git add market_discovery_internal/cycles.py
git commit -m "feat: add exit taker fee to close_paper_position — net_exit_value = exit_value - fee"
```

---

## Task 6: Align Main Cycle Exit Price to Bid-Only

**Files:**
- Modify: `market_discovery_internal/cycles.py` — position monitoring loop (~line 584-598)

Current logic (uses mid-price when both bid+ask are "sane"):
```python
        ask_sane_limit = max(entry_price * 3.0, 0.60)

        if bid_val > 0 and ask_val > 0 and ask_val <= ask_sane_limit:
            # Both sides are reasonable — use mid-price
            current_yes_price = (bid_val + ask_val) / 2.0
        elif bid_val > 0:
            current_yes_price = bid_val
        elif ask_val > 0 and ask_val <= ask_sane_limit:
            current_yes_price = ask_val
        else:
            ...
```

- [ ] **Step 1: Replace mid-price with bid-only for live-like exit evaluation**

Replace the entire `if bid_val > 0 and ask_val > 0 ...` block with:
```python
        # [LIVE-LIKE] Exit evaluation always uses bid (what you'd receive selling on CLOB).
        # Mid-price was optimistic and not representative of real execution price.
        # WS exit path already uses bid — align main cycle to match.
        ask_sane_limit = max(entry_price * 3.0, 0.60)

        if bid_val > 0:
            current_yes_price = bid_val
        elif ask_val > 0 and ask_val <= ask_sane_limit:
            # No bid available — fall back to sane ask as reference only
            current_yes_price = ask_val
        else:
            last_price = position.get("last_price")
            if last_price is not None:
                try:
                    current_yes_price = float(last_price)
                except (TypeError, ValueError):
                    current_yes_price = 0.0
            else:
                current_yes_price = 0.0
```

Note: keep all the existing bid sanity checks ABOVE this block (the `bid_val <= 0.01 and ask_val >= 0.98` and `bid_val <= 0.01 and entry_price > 0.05` checks). Only replace the final `if/elif/else` price selection.

- [ ] **Step 2: Verify import still works**
```bash
python3 -c "from market_discovery_internal.cycles import run_paper_trading_cycle; print('OK')"
```

- [ ] **Step 3: Commit**
```bash
git add market_discovery_internal/cycles.py
git commit -m "fix: exit price always uses bid — remove mid-price for live-like simulation"
```

---

## Task 7: Deploy to VPS and Verify

- [ ] **Step 1: Push all commits to GitHub**
```bash
git push origin master
```

- [ ] **Step 2: Pull on VPS and restart**
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "cd /opt/the_blueprints && git pull origin master && systemctl restart blueprints.service && echo OK"
```

- [ ] **Step 3: Reset state to fresh (since fee accounting changed)**
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "sqlite3 /opt/the_blueprints/logs/blueprints_master.db \"
DELETE FROM active_positions;
DELETE FROM trade_history;
DELETE FROM cycle_metrics;
UPDATE portfolio_summary SET base_wallet=5.0, cash=5.0, total_pnl=0.0, updated_at=datetime('now');
\""
```

Also reset JSON mirror:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "echo '{\"positions\":[],\"history\":[],\"cycle_journal\":[],\"meta\":{\"base_wallet\":5.0,\"cash\":5.0,\"current_wallet\":5.0,\"acceptance_metrics_rolling\":{\"closed_realized_pnl_total_usd\":0.0}}}' > /opt/the_blueprints/logs/paper_positions_5usd.json"
```

- [ ] **Step 4: Wait for first cycle and verify in DB**
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "sleep 120 && \
  sqlite3 /opt/the_blueprints/logs/blueprints_master.db \
  \"SELECT timestamp, json_extract(data_json,'$.entry_gate_reason') as gate, \
    json_extract(data_json,'$.counts.opportunities') as opps \
    FROM cycle_metrics ORDER BY id DESC LIMIT 1;\""
```

Expected: gate=`active`, service running clean.

- [ ] **Step 5: When first position opens, verify fee fields present**
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "sqlite3 /opt/the_blueprints/logs/blueprints_master.db \
  \"SELECT json_extract(raw_json,'$.entry_fee_usd'), json_extract(raw_json,'$.cost_basis'), \
    json_extract(raw_json,'$.shares_cost') FROM active_positions LIMIT 1;\""
```

Expected: `entry_fee_usd` non-zero, `cost_basis > shares_cost`.

- [ ] **Step 6: When first position closes, verify exit fee + PnL in trade_history**
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "sqlite3 /opt/the_blueprints/logs/blueprints_master.db \
  \"SELECT json_extract(raw_json,'$.exit_fee_usd'), json_extract(raw_json,'$.net_exit_value'), \
    pnl_usd FROM trade_history ORDER BY id DESC LIMIT 1;\""
```

Expected: `exit_fee_usd` non-zero, `pnl_usd` reflects real cost including both fees.

---

## What "Live-Like" Means After This Plan

| Aspect | Before | After |
|---|---|---|
| Entry price | best_ask + 1% slippage | same ✓ |
| Entry fee | ❌ not charged | ✅ `C×0.05×p×(1-p)` deducted |
| CLOB depth check | ❌ bypassed for paper | ✅ real depth enforced |
| Min order size | $0.50 floor | ✅ $1.00 floor |
| Exit price (main cycle) | mid-price (optimistic) | ✅ bid-only (conservative) |
| Exit price (WS) | bid ✓ | bid ✓ (already correct) |
| Exit fee | ❌ not charged | ✅ `C×0.05×p×(1-p)` deducted |

**PnL accuracy:** After this plan, paper PnL should be within ~1-2% of live PnL for equivalent trades. The remaining gap is partial fill simulation (not planned — too complex, low priority for $5 paper run).

---

## What Is Still NOT Live-Like (Out of Scope)

- **Partial fill simulation** — live orders at low liquidity might partially fill. Currently: instant full fill.
- **Order resting / GTC simulation** — live orders rest on book. Currently: instant match assumed.
- **Price impact on large orders** — depth check limits stake but doesn't model price impact for multi-level fills.
- **Gas / on-chain settlement** — not relevant for paper simulation.

These are acceptable gaps for paper trading validation. They become relevant only when implementing the actual live bridge.

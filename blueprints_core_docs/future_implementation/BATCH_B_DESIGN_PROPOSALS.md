# Batch B Design Proposals

**Date:** 21 April 2026
**Status:** Awaiting review and approval before implementation
**Prerequisite:** Batch A (7 changes) must be deployed and verified first

---

## Change 1d: Smart-Skip 2x Take-Profit for Cheap Entries

### Problem

For cheap entries (entry < ~$0.38), the 2x TP fires at $0.60-$0.76, selling the position
well below the $0.90 sniper. Example: $0.30 entry → 2x target = $0.60 → sold at $0.60.
But the sniper at $0.90 would yield +$0.60 profit instead of +$0.30.

### Proposed Solution (3 Parts)

#### Part 1: evaluate_hybrid_exit (cycles.py ~line 1626-1635)

Add a condition before the TP sell:

```python
if price >= target_price and strategy == "swing":
    # Smart skip: if 2x target is far below sniper, let sniper handle it.
    # Threshold: target_price < SNIPER_TAKE_PROFIT_PRICE * 0.85 (~$0.765)
    # This means entries below ~$0.3825 skip the 2x TP.
    if target_price < SNIPER_TAKE_PROFIT_PRICE * 0.85:
        # Mark that smart-skip is active for profit protection tracking
        position["_smart_skip_active"] = True
        position["_smart_skip_peak"] = max(
            float(position.get("_smart_skip_peak", 0.0)),
            float(price)
        )
        # Fall through to late-window checks or await_target
    else:
        return {"action": "sell", "reason": "take_profit_100pct", ...}
```

#### Part 2: ws_price_watcher.py (line 559) — CRITICAL

The WS watcher has an independent TP check that runs between main cycles (sub-second).
If not also modified, it will fire the 2x TP and bypass the smart-skip.

```python
# Current code (line 559):
if bid_price >= target and strategy == "swing":
    reason = "take_profit_100pct"

# Proposed change:
if bid_price >= target and strategy == "swing":
    # Smart skip: don't TP if target is far below sniper
    from market_discovery_internal.config import SNIPER_TAKE_PROFIT_PRICE
    if target < SNIPER_TAKE_PROFIT_PRICE * 0.85:
        pass  # Let main cycle sniper handle it
    else:
        reason = "take_profit_100pct"
```

Note: `SNIPER_TAKE_PROFIT_PRICE` must be added to the ws_price_watcher.py imports.

#### Part 3: Profit Protection Stop (NEW exit condition)

After skipping the 2x TP, if price reverses, the existing trailing stop requires
`price <= entry_price` to fire — meaning you'd lose the entire gain before any exit.

Add a new exit condition in evaluate_hybrid_exit, checked AFTER trailing stop
and BEFORE the 2x TP check:

```python
# Profit protection for smart-skip positions
if position.get("_smart_skip_active"):
    _skip_peak = float(position.get("_smart_skip_peak", 0.0))
    _entry = float(position.get("entry_price", 0.0))
    if _skip_peak > _entry and _entry > 0:
        # Protect at least 50% of peak-to-entry gain
        _gain = _skip_peak - _entry
        _protection_floor = _entry + _gain * 0.50
        if price <= _protection_floor:
            return {
                "action": "sell",
                "reason": "smart_skip_profit_protection",
                "confidence_score": confidence_score,
                "peak_price": round(_skip_peak, 4),
                "protection_floor": round(_protection_floor, 4),
            }
```

Example: Entry $0.30, peak $0.60, protection floor = $0.30 + ($0.30 * 0.50) = $0.45.
If price drops to $0.45, exit with +50% profit instead of waiting for full reversion.

### Files to Modify

| File | Lines | Change |
|------|-------|--------|
| cycles.py | ~1626-1635 | Add smart-skip condition |
| cycles.py | ~1585 (before trailing stop) | Add profit protection check |
| ws_price_watcher.py | 559 | Add same smart-skip condition |
| ws_price_watcher.py | imports | Add SNIPER_TAKE_PROFIT_PRICE |
| config.py | (optional) | Add SMART_SKIP_ENABLED flag |

### Tests to Add

1. `test_smart_skip_holds_cheap_entry_past_2x` — entry $0.30, price $0.60, expect hold
2. `test_smart_skip_does_not_affect_expensive_entry` — entry $0.45, price $0.90, expect TP
3. `test_smart_skip_profit_protection_fires` — entry $0.30, peak $0.60, price $0.45, expect sell
4. `test_ws_watcher_smart_skip` — verify WS watcher also skips

### Pros

- Cheap entries ($0.15-$0.38) held to $0.90 sniper instead of 2x TP
- Profit protection prevents total gain evaporation
- WS watcher consistency prevents bypass
- Config-gated for safety

### Cons

- Adds complexity to exit logic (new exit condition + new position fields)
- Requires tracking `_smart_skip_active` and `_smart_skip_peak` in position dict
- Profit protection floor (50% of peak gain) is somewhat arbitrary
- WS watcher modification runs in a separate process — needs careful testing

### Risk Level: MEDIUM

The WS watcher modification is the highest-risk part. It runs in a separate process
and handles real-time price updates. Must be tested with the full paper trading loop.

---

## Change 2d: Time-Decay Edge Scaling

### Problem

A trade at 17h-to-resolve has the same minimum edge requirement (0.10 from .env) as a
trade at 5h-to-resolve, despite higher forecast uncertainty and opportunity cost.

### Proposed Solution

Scale the minimum edge requirement with time-to-resolve:
`effective_min_edge = base_min_edge * sqrt(hours / base_hours)`

#### New Config (config.py)

```python
# Time-decay edge scaling: demand higher edge for longer-duration trades.
# Formula: effective_min_edge = base_edge * sqrt(hours / TIME_DECAY_BASE_HOURS)
TIME_DECAY_EDGE_ENABLED = _env_bool("TIME_DECAY_EDGE_ENABLED", True)
TIME_DECAY_BASE_HOURS = float(os.getenv("TIME_DECAY_BASE_HOURS", "6.0"))
```

#### Scaling Table (base_edge from .env = 0.10)

| Hours to Resolve | Decay Factor | Effective Min Edge |
|------------------|-------------|-------------------|
| 4h               | 0.82        | 8.2%              |
| 6h (baseline)    | 1.00        | 10.0%             |
| 10h              | 1.29        | 12.9%             |
| 14h              | 1.53        | 15.3%             |
| 18h              | 1.73        | 17.3%             |

#### Apply in filter_enriched_opportunities (discovery.py ~line 115-118)

```python
# After regime gate lookup:
edge_gate = float(gates.get("min_edge", min_edge))

# Apply time-decay scaling
if TIME_DECAY_EDGE_ENABLED:
    _hours = float(m.get("hours_until_resolve") or TIME_DECAY_BASE_HOURS)
    _decay = math.sqrt(max(_hours, 1.0) / TIME_DECAY_BASE_HOURS)
    edge_gate = round(edge_gate * _decay, 4)
```

#### Apply in filter_opportunities (discovery.py ~line 185)

Same logic for the non-enriched filter path.

#### Apply in backtest_runner.py (line 128)

Same logic for consistency with backtesting.

### Interaction with Regime Gates

The regime gates set per-market min_edge values (0.18/0.22/0.28). Time-decay
multiplies these as well:

| Regime | Base Edge | At 4h | At 6h | At 12h | At 18h |
|--------|-----------|-------|-------|--------|--------|
| Good   | 0.18      | 0.15  | 0.18  | 0.25   | 0.31   |
| Neutral| 0.22      | 0.18  | 0.22  | 0.31   | 0.38   |
| Stress | 0.28      | 0.23  | 0.28  | 0.40   | 0.48   |

This is directionally correct: stress + long duration = very high bar.

### Files to Modify

| File | Lines | Change |
|------|-------|--------|
| config.py | (new) | Add TIME_DECAY_EDGE_ENABLED, TIME_DECAY_BASE_HOURS |
| discovery.py | ~115-118 | Apply decay after gate lookup |
| discovery.py | ~185 | Apply decay in filter_opportunities |
| discovery.py | imports | Add math, TIME_DECAY_* imports |
| backtest_runner.py | 128 | Apply same decay |

### Test Considerations

Tests in test_filter.py pass explicit `min_edge` values AND test markets have
`hours_until_resolve=18`. If decay is applied to these, thresholds change.

Options:
1. Make decay only apply when `TIME_DECAY_EDGE_ENABLED` is True (default True in
   production, can be set False in test env)
2. Update test assertions to account for decay
3. Only apply decay to the default min_edge, not explicit overrides

Recommended: Option 1 — add `TIME_DECAY_EDGE_ENABLED=false` to test fixtures.

### Pros

- Better capital allocation (demand more edge for longer trades)
- Consistent with regime gates (multiplicative, not additive)
- Config-gated (can be disabled via env var)
- Reduces exposure to long-duration forecast uncertainty

### Cons

- May reduce trade volume at longer horizons (fewer trades pass the filter)
- Adds complexity to filtering logic
- Needs careful test handling to avoid breaking existing tests

### Risk Level: LOW-MEDIUM

The config gate provides a safety switch. The math is simple and deterministic.
Main risk is test breakage if not handled carefully.

---

## Implementation Order

If both are approved:
1. Implement 2d (time-decay) first — simpler, lower risk
2. Implement 1d (smart-skip TP) second — more complex, needs WS watcher changes
3. Run full test suite after each
4. Test with paper trading loop before deploying to VPS

# Tier 2 Audit — Affects Trading Decisions

**Status:** COMPLETE  
**Date:** 23 April 2026, 19:40 WIB  
**Method:** ANALYSIS_WORKFLOW.md Step 1 + Step 3b  
**Lines Audited:** ~800 lines across 8 areas

---

## Grand Summary

| Area | Lines | New Bugs | Highest Severity |
|------|-------|----------|-----------------|
| T2-1: Bayesian calibration | 26 | 1 | MEDIUM |
| T2-2: Regime scoring | 51 | 0 | LOW (cosmetic) |
| T2-3: NOAA override | 45 | 1 CRITICAL + 1 HIGH | **CRITICAL** |
| T2-4: Ensemble merge | 15 | 1 | HIGH |
| T2-5: Auto-tuner | 71 | 0 | LOW |
| T2-6: Flash Crash Shield | 142 | 1 CRITICAL | **CRITICAL** |
| T2-7: METAR fetch | 73 | 2 | MEDIUM |
| T2-8: Ensemble fetch | 101 | 1 | MEDIUM |
| **Total** | **~524** | **7 actionable** | **2 CRITICAL** |

---

## CRITICAL Bugs Found (2)

### T2-3-CRIT: "Below" NOAA Confirm for Max-Temp Markets Is Logically Flawed

| Field | Value |
|-------|-------|
| **File** | `pricing.py:518-521` |
| **Severity** | CRITICAL |

**Problem:** For a market asking "Will daily HIGH be below 25°C?", if current METAR reads 24.9°C, the override sets probability to 95%. But the daily high hasn't peaked yet — temperature could easily rise above 25°C later in the day. The current reading being below threshold does NOT confirm the daily high will stay below it.

**Proposed Fix:**
```python
# In calculate_edge(), lines 518-521, change:
elif _direction == "below":
    if noaa_temp < threshold:
        raw_prob = max(raw_prob, 0.95)
# To:
elif _direction == "below":
    # Only confirm "below" if very late in the day (daily high likely already recorded)
    # AND current temp is well below threshold (margin for afternoon warming)
    if noaa_temp < threshold - 2.0 and hours_left <= 2.0:
        raw_prob = max(raw_prob, 0.95)
```

**Side Effects:** SAFE — only makes the confirm MORE conservative. No impact on "above" or "exact" markets.

**Live-Trading:** CRITICAL fix — without this, bot could buy "below" contracts at 95% confidence when daily high hasn't peaked.

---

### T2-6-CRIT: Flash Crash L1 Deadlock Blocks Max-Delay Safety Net

| Field | Value |
|-------|-------|
| **File** | `ws_price_watcher.py:458+466` |
| **Severity** | CRITICAL |

**Problem:** When a genuine >40% crash occurs and persists, L1's `continue` fires before the max-delay timer (`_sl_first_below_at`) is ever set. L2 counter never increments, so L1 escape hatch never triggers. The position is trapped indefinitely — the safety net (P5-FIX) designed to prevent this is architecturally bypassed.

**Proposed Fix:**
```python
# Move _sl_first_below_at tracking ABOVE L1 check.
# Before the L1 spike detector (around line 435), add:
if bid_price <= stop:
    if token_id not in _sl_first_below_at:
        _sl_first_below_at[token_id] = time.time()
    elif time.time() - _sl_first_below_at[token_id] > SL_MAX_DELAY_SECONDS:
        # Force-close regardless of flash crash shield
        reason = "stop_loss"
        # (proceed to close logic, bypassing L1/L2/L3/L4)
```

**Side Effects:** LOW RISK — only affects the extreme case where L1 blocks for >600 seconds. Normal flash crash detection is unaffected.

**Live-Trading:** CRITICAL — without this, a genuine crash could trap a position indefinitely.

---

## HIGH Bugs Found (2)

### T2-3-HIGH: "Above" NOAA Confirm Has No Margin

| Field | Value |
|-------|-------|
| **File** | `pricing.py:509-512` |
| **Severity** | HIGH |

**Problem:** A METAR reading of 30.01°C triggers 95% confidence for a 30°C threshold. METAR accuracy is ±0.5°C, and the resolution source may differ from METAR.

**Proposed Fix:** Add minimum margin: `if noaa_temp > threshold + 0.5:`

---

### T2-4-HIGH: NOAA Override Gets Diluted by Downstream Pipeline

| Field | Value |
|-------|-------|
| **File** | `pricing.py:558-573` (interaction with 492-537) |
| **Severity** | HIGH |

**Problem:** NOAA sets `raw_prob = 0.95` (confirmed by real-time observation), but calibration may pull it to 0.80, then ensemble may pull it to 0.70. The real-time ground truth gets overridden by model averages.

**Proposed Fix:** If NOAA override fired, skip calibration and ensemble merge for that market. Or apply NOAA override AFTER ensemble merge.

---

## MEDIUM Bugs Found (5)

| # | File:Line | What | Impact |
|---|----------|------|--------|
| M-T2-1 | `pricing.py:255` | Calibration can catastrophically override correct model prob with sparse data | Could skip profitable trades |
| M-T2-7a | `forecasting.py:59-60` | Non-200 METAR responses not cached — repeated API hammering | Rate limit risk |
| M-T2-7b | `forecasting.py:79-80` | Silent `pass` on METAR time parse failure — stale data used | Bad NOAA override |
| M-T2-8a | `forecasting.py:209-214` | Asymmetric boundary: "above" uses `>`, "below" uses `<=` | 1-2% probability bias |
| M-T2-8b | `forecasting.py:199` | 5-member minimum too low — 20% probability resolution | Coarse ensemble estimates |

---

## LOW Bugs Found (5)

| # | File:Line | What |
|---|----------|------|
| L-T2-1 | `pricing.py:255` | Shrinkage prior uses raw_prob (slow convergence) |
| L-T2-2a | `pricing.py:296` | No clamping on evidence_score input |
| L-T2-2b | `pricing.py:287` | 24h volume unreliable depth proxy |
| L-T2-4 | `pricing.py:563` | No clamping on ensemble_prob before merge |
| L-T2-8 | `forecasting.py:235` | Semantic column mismatch (spread saved as precip) |

---

## Priority Fix Order

1. **T2-3-CRIT** — "Below" NOAA confirm logic (CRITICAL — could cause immediate capital loss)
2. **T2-6-CRIT** — Flash Crash L1 deadlock (CRITICAL — position trapped indefinitely)
3. **T2-3-HIGH** — "Above" NOAA confirm margin (HIGH — false 95% confidence)
4. **T2-4-HIGH** — NOAA dilution by pipeline (HIGH — ground truth overridden)
5. **M-T2-7b** — METAR stale data silent pass (MEDIUM — bad override data)
6. **M-T2-8a** — Ensemble boundary asymmetry (MEDIUM — systematic bias)

---

*Audit conducted following ANALYSIS_WORKFLOW.md with Step 3b critical self-questioning on every function. All bugs verified against actual source code with real number edge cases.*

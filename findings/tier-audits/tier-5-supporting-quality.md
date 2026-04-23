# Tier 5 Audit — Supporting & Quality

**Status:** COMPLETE  
**Date:** 23 April 2026, 20:30 WIB  
**Method:** ANALYSIS_WORKFLOW.md Step 1 + Step 3b  
**Lines Audited:** ~1400 lines across 7 areas

---

## Grand Summary

| Area | Lines | New Bugs | Highest Severity |
|------|-------|----------|-----------------|
| T5-1: diagnostics.py | 193 | 0 | CLEAN |
| T5-2: output.py | 549 | 1 | MEDIUM |
| T5-3: backtest_runner.py | 304 | 2 | **HIGH** |
| T5-4: log_rotator.py | 78 | 2 | MEDIUM |
| T5-5: Telegram templates | 180 | 0 | CLEAN |
| T5-6: profit attribution | 51 | 2 | MEDIUM |
| T5-7: daily_monitor.py | 104 | 4 | **HIGH** |
| **Total** | **~1459** | **11** | **2 HIGH** |

---

## HIGH Bugs Found (2)

### T5-3-HIGH: Backtest Has Lookahead Bias — Results Unreliable

| Field | Value |
|-------|-------|
| **File** | `backtest_runner.py:86-106` |
| **Severity** | HIGH |

**Problem:** `_fetch_historical_forecast()` fetches the *actual observed* max temperature (Open-Meteo historical archive), not a forecast that would have been available before the event. The backtest uses ground-truth weather data that wouldn't have been available at entry time, inflating win rate.

**Impact:** Backtest win rate and PnL are systematically overstated. Strategy tuning based on these results is built on flawed data.

**Proposed Fix:** Label results as "perfect-information backtest" or use forecast archive API if available.

---

### T5-7-HIGH: Daily Monitor Crashes on Missing Dict Keys

| Field | Value |
|-------|-------|
| **File** | `daily_monitor.py:65,82` |
| **Severity** | HIGH |

**Problem:** Direct `[]` key access without `.get()`: `report["rolling_acceptance_metrics"]` and `report['anomaly_counters']['current_zero_opportunity_streak']`. If state structure changes or is empty, `KeyError` crashes the script silently — no Telegram alert sent.

**Proposed Fix:** Use `.get()` with defaults throughout.

---

## MEDIUM Bugs Found (5)

| # | File:Line | What | Impact |
|---|----------|------|--------|
| M-T5-2 | `output.py:260` | Print function re-reads state from disk (side-effect in formatter) | Silent failure, unexpected I/O |
| M-T5-4a | `log_rotator.py:28-35` | Race between rename and recreate — log lines lost | Small window, but real |
| M-T5-4b | `log_rotator.py:52` | `os.path.dirname("")` returns empty string — crash | Edge case |
| M-T5-6 | `cycles.py:2800` | `_sorted_desc` actually sorts ascending — misleading name | Confusing reports |
| M-T5-7 | `daily_monitor.py:27` | Uses local time instead of UTC for day boundary | Wrong anomaly counts |

---

## LOW Bugs Found (4)

| # | File:Line | What |
|---|----------|------|
| L-T5-3 | `backtest_runner.py:202` | Entry price uses terminal price, not realistic entry |
| L-T5-6 | `cycles.py:2769` | DB error returns empty dict, indistinguishable from "no data" |
| L-T5-7a | `daily_monitor.py:23` | No file locking on shared state (mitigated by atomic replace) |
| L-T5-4 | `log_rotator.py:52` | dirname empty string edge case |

---

## CLEAN Areas (2)

- **T5-1: diagnostics.py** — Well-structured pure functions, all `.get()` with defaults. No bugs.
- **T5-5: Telegram templates** — `SafeFormatter` handles missing placeholders gracefully, HTML escaping prevents injection. No bugs.

---

## Proposed Changes Summary

| Priority | Bug | Fix | Risk |
|----------|-----|-----|------|
| 1 | T5-7-HIGH | Replace `[]` with `.get()` in daily_monitor.py | SAFE |
| 2 | T5-3-HIGH | Label backtest as "perfect-information" | SAFE (documentation) |
| 3 | M-T5-4a | Use copy-truncate instead of rename-create for log rotation | LOW RISK |
| 4 | M-T5-7 | Use `datetime.now(timezone.utc)` in daily_monitor | SAFE |

---

*Audit conducted following ANALYSIS_WORKFLOW.md. Clean areas confirmed with Step 3b self-questioning.*

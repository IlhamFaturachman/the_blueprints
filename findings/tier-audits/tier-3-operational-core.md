# Tier 3 Audit — Operational Core

**Status:** COMPLETE  
**Date:** 23 April 2026, 20:00 WIB  
**Method:** ANALYSIS_WORKFLOW.md Step 1 + Step 3b  
**Lines Audited:** ~1200 lines across 6 areas

---

## Grand Summary

| Area | Lines | New Bugs | Highest Severity |
|------|-------|----------|-----------------|
| T3-1: cli.py main loop | 207 | 1 MEDIUM | MEDIUM |
| T3-2: reporting.py metrics | 525 | 0 | LOW (cosmetic) |
| T3-3: enrich_discovery_markets | 192 | 2 | HIGH |
| T3-4: fetch_markets | 67 | 1 HIGH | HIGH |
| T3-5: fetch_with_retry | 60 | 2 | MEDIUM |
| T3-6: confidence score | 42 | 1 HIGH | HIGH |
| **Total** | **~1093** | **7 actionable** | **3 HIGH** |

---

## HIGH Bugs Found (3)

### T3-6-HIGH: Confidence Score Defaults Missing Prob to 1.0

| Field | Value |
|-------|-------|
| **File** | `cycles.py:1968-1969` |
| **Severity** | HIGH |

**Problem:** When `entry_model_prob` is `None`, `base_prob` defaults to `1.0`. This gives maximum confidence (score ~1.0) to positions with missing probability data, preventing exits.

**Proposed Fix:**
```python
# Change default from 1.0 to 0.5 (uncertain):
base_prob = float(position.get("entry_model_prob") or 0.5)
```

**Side Effects:** SAFE — only affects positions with missing prob data. Existing positions with valid prob are unaffected.

---

### T3-4-HIGH: `sys.exit(0)` in Inspect Mode Kills Process

| Field | Value |
|-------|-------|
| **File** | `discovery.py:58` |
| **Severity** | HIGH |

**Problem:** `sys.exit(0)` in inspect mode bypasses all cleanup. `SystemExit` is not caught by `except Exception` in the main loop. If inspect mode is accidentally enabled, the entire process dies without closing WS connections, cancelling orders, or saving state.

**Proposed Fix:**
```python
# Replace sys.exit(0) with return:
return {"markets_raw": markets, "parsed": candidates, ...}
```

---

### T3-3-HIGH: Silent Market Drops from `calculate_edge_fn` Returning None

| Field | Value |
|-------|-------|
| **File** | `cycles.py:247-248` |
| **Severity** | HIGH |

**Problem:** When `calculate_edge_fn` returns `None`, the market is silently dropped — no logging, no counter, no visibility. Valid markets with forecast data but edge calculation issues are lost.

**Proposed Fix:** Add logging: `logger.debug("[ENRICH] Edge calculation returned None for %s — skipping", city)`

---

## MEDIUM Bugs Found (5)

| # | File:Line | What | Impact |
|---|----------|------|--------|
| M-T3-1 | `cli.py:58` (via discovery.py) | `sys.exit(0)` in inspect mode — see HIGH above | Process death |
| M-T3-3 | `cycles.py:178-185` | Evidence built BEFORE null-check on `forecast_temp` | Wasted computation, potential exception |
| M-T3-5a | `utils.py:157-166` | 429 handler doesn't respect `Retry-After` header | Suboptimal retry timing |
| M-T3-5b | `utils.py:164` | Final 429 attempt sleeps 120s then raises without retrying | Wastes 2 minutes |
| M-T3-6 | `cycles.py:1992` | Non-late-window edge uses stale entry prob, not current | Edge component may be inaccurate |

---

## LOW Bugs Found (7)

| # | File:Line | What |
|---|----------|------|
| L-T3-1a | `cli.py:79` | Hardcoded 30s cool-off not configurable |
| L-T3-1b | `cli.py:114` | Stale fallback `min()` is no-op when interval < 120s |
| L-T3-1c | `cli.py:132` | `since_last` used outside its `if` scope (fragile) |
| L-T3-2 | `reporting.py:33` | Entry candidates only counts 2 bucket types |
| L-T3-3 | `cycles.py:204` | Import inside loop (style) |
| L-T3-4 | `discovery.py:68` | Off-by-one: fetches max_pages+1 total pages |
| L-T3-5 | `utils.py:174-176` | `max_retries=0` silently returns None |

---

## Proposed Changes Summary

| Priority | Bug | Fix | Risk |
|----------|-----|-----|------|
| 1 | T3-6-HIGH | Change `base_prob` default from `1.0` to `0.5` | SAFE |
| 2 | T3-4-HIGH | Replace `sys.exit(0)` with return in inspect mode | SAFE |
| 3 | T3-3-HIGH | Add logging when `calculate_edge_fn` returns None | SAFE |
| 4 | M-T3-3 | Move `forecast_temp is None` check before evidence build | SAFE |
| 5 | M-T3-5b | Skip sleep on final 429 attempt | SAFE |

---

*Audit conducted following ANALYSIS_WORKFLOW.md with Step 3b critical self-questioning. All edge cases tested with real numbers.*

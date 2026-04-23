# Tier 6 Audit — Partially Audited Gaps

**Status:** COMPLETE  
**Date:** 23 April 2026, 20:45 WIB  
**Method:** ANALYSIS_WORKFLOW.md Step 1 + Step 3b  
**Lines Audited:** ~400 lines across 5 areas

---

## Grand Summary

| Area | Lines | New Bugs | Highest Severity |
|------|-------|----------|-----------------|
| T6-1: ICAO station mappings | 52 | 2 | LOW |
| T6-2: Regex patterns | 90 | 2 | **HIGH** |
| T6-3: Per-region sigma | 50 | 2 | MEDIUM |
| T6-4: Negative cache + consensus | 60 | 2 | MEDIUM |
| T6-5: Market implied prob | 49 | 1 HIGH + 1 LOW | **HIGH** |
| **Total** | **~301** | **9** | **2 HIGH** |

---

## HIGH Bugs Found (2)

### T6-2-HIGH: `str(None).upper()` Treats Unitless Thresholds as Celsius

| Field | Value |
|-------|-------|
| **File** | `pricing.py:73` |
| **Severity** | HIGH |

**Problem:** In `_compute_market_implied_prob()`, when `THRESHOLD_RE` matches a number without a unit suffix (e.g., "be 75 or above"), `match.group(2)` returns `None`. `str(None).upper()` produces `"NONE"`, which doesn't match `"F"`, so the value is treated as Celsius. For US markets where the value is actually Fahrenheit (75°F), the threshold becomes 75°C — wildly wrong.

**Impact:** Distorted `market_implied_expected_temp_c` and bracket distributions for unitless US markets. Affects entry decisions when market-implied probability is used.

**Proposed Fix:**
```python
unit_raw = match.group(2)
unit = unit_raw.upper() if unit_raw else None
if unit is None:
    # Infer unit from value magnitude (same heuristic as parsing.py)
    unit = "F" if threshold_val >= 60 else "C"
```

---

### T6-5-HIGH: Same Bug — `str(None)` in Market Implied Prob

Same as T6-2-HIGH above. The bug is in `pricing.py:73` which is part of the T6-5 area.

---

## MEDIUM Bugs Found (4)

| # | File:Line | What | Impact |
|---|----------|------|--------|
| M-T6-3a | `config.py:602-604` | 13 of 31 cities unclassified — fall to default sigma 1.5°C | Paris, Milan, Madrid, Houston, Dallas etc. get wrong sigma |
| M-T6-3b | `pricing.py:321-341` | No Southern Hemisphere season inversion | Buenos Aires/Sao Paulo get inverted seasonal multipliers |
| M-T6-4a | `forecasting.py:607` | Module-level negative cache persists across cycles (5-min TTL) | Transient API failure blocks city for 5 minutes |
| M-T6-2 | `config.py:194` | "equal or exceed" misclassified as "exact" direction | Wrong probability model for compound phrases |

---

## LOW Bugs Found (5)

| # | File:Line | What |
|---|----------|------|
| L-T6-1a | `config.py:165` | `\bla\b` case-insensitive matches French "la" |
| L-T6-1b | `config.py:157` | `\bhk\b` case-insensitive redundant with case-sensitive |
| L-T6-4 | `forecasting.py:638` | Negative cache hits inflate cache hit stats |
| L-T6-5 | `pricing.py:89` | Potential token_id type mismatch (str vs int) |
| L-T6-2 | `config.py:191` | RANGE_THRESHOLD_PATTERN doesn't anchor unit |

---

## Proposed Changes Summary

| Priority | Bug | Fix | Risk |
|----------|-----|-----|------|
| 1 | T6-2/5-HIGH | Handle `None` unit in `_compute_market_implied_prob` | SAFE |
| 2 | M-T6-3a | Add Paris, Milan, Madrid, Houston, Dallas, etc. to FOUR_SEASON_CITIES | SAFE |
| 3 | M-T6-3b | Add Southern Hemisphere month offset in `_get_city_sigma()` | LOW RISK |
| 4 | M-T6-4a | Reduce negative cache TTL to 120s or clear at cycle start | SAFE |

---

*Audit conducted following ANALYSIS_WORKFLOW.md. All ICAO codes cross-referenced. Regex patterns tested with edge case inputs.*

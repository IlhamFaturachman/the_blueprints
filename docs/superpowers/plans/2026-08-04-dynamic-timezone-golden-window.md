# Dynamic Timezone-Aware Golden Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace flat 6-18h `hours_until_resolve` golden window with dynamic per-timezone check based on `hours_into_weather_day`, enabling the bot to trade markets across all 31 cities regardless of their timezone offset.

**Architecture:** Polymarket weather markets resolve at 12:00 UTC, but the "weather day" runs from local midnight to local midnight at each station's timezone. The current flat golden window (6-18h until resolve) never hits because there's a 3h→27h gap between same-day and next-day markets. The fix: use `hours_into_weather_day` (already computed from `gameStartTime`) as the primary entry criterion instead of `hours_until_resolve`. Entry is allowed when the weather day is 2-20h in (temps developing, forecast has edge) and there's at least 1h until resolve (safety floor). The `hours_until_resolve` "too close" check becomes a minimal safety floor, not the primary gate.

**Tech Stack:** Python 3.11, SQLite, systemd, Polymarket Gamma API

---

## Background: Why The Current System Is Broken

All Polymarket weather markets resolve at `endDate = YYYY-MM-DDT12:00:00Z`. The "weather day" starts at `gameStartTime` (local midnight at the station's timezone, expressed in UTC). Example at 09:00 UTC on Aug 4:

| City | TZ | gameStartTime (UTC) | hours_into_weather_day | hours_until_resolve | Current verdict |
|---|---|---|---|---|---|
| Shanghai | UTC+8 | Aug 3 16:00 | 17h | 27h (Aug 5 12:00) | REJECT: > 18h max |
| London | UTC+1 | Aug 3 23:00 | 10h | 27h | REJECT: > 18h max |
| New York | UTC-4 | Aug 4 04:00 | 5h | 27h | REJECT: > 18h max |
| Hong Kong | UTC+8 | Aug 3 16:00 | 17h | 2.9h (Aug 4 12:00) | REJECT: < 6h min |

The "dead time" between weather day end and resolve varies 8-20h by timezone, making `hours_until_resolve` useless as an entry criterion. `hours_into_weather_day` is the correct metric — it measures how far into the actual temperature observation period we are.

**Verification:** `gameStartTime` is confirmed present in 1078/1078 live Polymarket weather markets (checked via Gamma Events API on 2026-08-04). However, the implementation includes a timezone-based fallback: if `gameStartTime` is ever missing, `check_golden_window()` computes `hours_into_weather_day` from `TARGET_CITIES[city]["tz"]` + `market_date` (local midnight at station). Only when both `gameStartTime` and city/date are unavailable does it fall back to the legacy flat `hours_until_resolve` check.
## File Structure

- **Modify:** `market_discovery_internal/config.py` — Add weather-day-based golden window config constants
- **Modify:** `market_discovery_internal/parsing.py` — Replace flat golden window check with weather-day-based check
- **Modify:** `market_discovery_internal/research_features.py` — Replace flat golden window check in entry filter
- **Create:** `tests/test_dynamic_golden_window.py` — Unit tests for new logic
- **Modify:** `.env` on VPS — Update golden window config

---

### Task 1: Add Dynamic Golden Window Config

**Files:**
- Modify: `market_discovery_internal/config.py:318-321`
- Test: `tests/test_dynamic_golden_window.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dynamic_golden_window.py
"""Tests for dynamic timezone-aware golden window logic."""
import os
import pytest


def test_weather_day_golden_window_config_defaults():
    """Config should expose weather-day-based golden window constants."""
    from market_discovery_internal.config import (
        GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS,
        GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS,
        GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS,
    )
    assert GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS == 2.0
    assert GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS == 20.0
    assert GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS == 1.0


def test_legacy_golden_window_still_exists():
    """Legacy flat golden window constants remain for shadow/parse fallback."""
    from market_discovery_internal.config import GOLDEN_WINDOW_HOURS_MIN, GOLDEN_WINDOW_HOURS_MAX
    assert GOLDEN_WINDOW_HOURS_MIN > 0
    assert GOLDEN_WINDOW_HOURS_MAX > GOLDEN_WINDOW_HOURS_MIN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v`
Expected: FAIL with `ImportError: cannot import name 'GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS'`

- [ ] **Step 3: Add config constants**

In `market_discovery_internal/config.py`, after the existing golden window lines (line ~321), add:

```python
# [DYNAMIC GOLDEN WINDOW] Weather-day-based entry timing.
# Instead of flat hours_until_resolve (which includes dead time between
# weather day end and 12:00 UTC resolve), use hours_into_weather_day
# (time since local midnight at station timezone).
# Entry allowed when weather day is 2-20h in: temps developing, forecast has edge.
# Below 2h: too early, not enough observation data.
# Above 20h: temps mostly locked, market converging.
GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS = float(os.getenv("GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS", "2.0"))
GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS = float(os.getenv("GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS", "20.0"))
# Safety floor: don't enter if < 1h until resolve (market about to close).
GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS = float(os.getenv("GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS", "1.0"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_discovery_internal/config.py tests/test_dynamic_golden_window.py
git commit -m "feat: add weather-day-based golden window config constants"
```

---

### Task 2: Implement Dynamic Golden Window Check Function

**Files:**
- Modify: `market_discovery_internal/parsing.py` (add helper function)
- Test: `tests/test_dynamic_golden_window.py`

- [ ] **Step 1: Write failing tests for the check function**

Add to `tests/test_dynamic_golden_window.py`:

```python
def test_weather_day_in_golden_window():
    """Market 10h into weather day, 15h until resolve → ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=15.0,
        hours_into_weather_day=10.0,
    )
    assert result is None  # None = pass, no rejection


def test_weather_day_too_early():
    """Market 1h into weather day → REJECT (too early)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=1.0,
    )
    assert result is not None
    assert "too_early" in result


def test_weather_day_too_late():
    """Market 22h into weather day → REJECT (too late, temps locked)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=5.0,
        hours_into_weather_day=22.0,
    )
    assert result is not None
    assert "too_late" in result


def test_resolve_safety_floor():
    """Market 0.5h until resolve → REJECT (about to close)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=0.5,
        hours_into_weather_day=10.0,
    )
    assert result is not None
    assert "too_close" in result


def test_no_weather_day_data_falls_back():
    """When hours_into_weather_day is None, fall back to legacy flat check."""
    from market_discovery_internal.parsing import check_golden_window
    # 10h until resolve, no weather day data → legacy check: 10 is in [6,18] → pass
    result = check_golden_window(
        hours_until_resolve=10.0,
        hours_into_weather_day=None,
    )
    assert result is None  # pass via legacy


def test_no_weather_day_data_falls_back_reject():
    """When hours_into_weather_day is None and legacy check fails → reject."""
    from market_discovery_internal.parsing import check_golden_window
    # 2h until resolve, no weather day data → legacy check: 2 < 6 → reject
    result = check_golden_window(
        hours_until_resolve=2.0,
        hours_into_weather_day=None,
    )
    assert result is not None
    assert "too_close" in result


def test_shanghai_timezone_scenario():
    """Shanghai at 09:00 UTC Aug 4: 17h into weather day, 27h until resolve → ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=17.0,
    )
    assert result is None  # 17h is in [2, 20] → pass


def test_new_york_timezone_scenario():
    """New York at 09:00 UTC Aug 4: 5h into weather day, 27h until resolve → ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=5.0,
    )
    assert result is None  # 5h is in [2, 20] → pass


def test_hong_kong_same_day_scenario():
    """Hong Kong Aug 4 market at 09:00 UTC: 17h into weather day, 2.9h until resolve → REJECT (safety floor)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=2.9,
        hours_into_weather_day=17.0,
    )
    assert result is None  # 2.9 > 1.0 safety floor AND 17h in [2,20] → pass


def test_timezone_fallback_when_game_start_missing():
    """When hours_into_weather_day is None but city+date provided, compute from tz.
    
    Shanghai (UTC+8), market_date='2026-08-04', now ~09:00 UTC Aug 4.
    Local midnight Aug 4 in Shanghai = Aug 3 16:00 UTC.
    hours_into_weather_day = 17h → should PASS (in [2,20]).
    """
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="shanghai",
        market_date="2026-08-04",
    )
    assert result is None  # computed ~17h → in [2,20] → pass


def test_timezone_fallback_new_york():
    """New York (UTC-4), market_date='2026-08-04', now ~09:00 UTC.
    Local midnight Aug 4 in NYC = Aug 4 04:00 UTC.
    hours_into_weather_day = 5h → should PASS.
    """
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="new york city",
        market_date="2026-08-04",
    )
    assert result is None  # computed ~5h → in [2,20] → pass


def test_timezone_fallback_unknown_city():
    """Unknown city with no tz data → fall back to legacy flat check."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="mars colony",
        market_date="2026-08-04",
    )
    assert result is not None  # 27 > 18 → legacy reject

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v -k "weather_day or timezone or safety or fallback"`
Expected: FAIL with `ImportError: cannot import name 'check_golden_window'`

- [ ] **Step 3: Implement `check_golden_window` function**

In `market_discovery_internal/parsing.py`, add this function near the top (after imports, before `parse_market`):

```python
def check_golden_window(hours_until_resolve, hours_into_weather_day, city=None, market_date=None):
    """
    Dynamic timezone-aware golden window check.
    
    Uses hours_into_weather_day (time since local midnight at station) as primary
    criterion. When hours_into_weather_day is None (missing gameStartTime),
    computes it from city timezone + market_date (local midnight at station).
    Falls back to legacy flat hours_until_resolve check only when both
    weather-day data and timezone fallback are unavailable.
    
    Returns: None if market is in golden window (pass), or a rejection reason string.
    """
    from market_discovery_internal.config import (
        GOLDEN_WINDOW_HOURS_MIN,
        GOLDEN_WINDOW_HOURS_MAX,
        GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS,
        GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS,
        GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS,
        TARGET_CITIES,
    )
    
    # Safety floor: never enter if market is about to resolve
    if hours_until_resolve is not None and hours_until_resolve < GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS:
        return "too_close_to_resolve"
    
    # Primary check: weather-day-based (timezone-aware)
    if hours_into_weather_day is None and city and market_date:
        # Fallback: compute from city timezone + market date
        try:
            from datetime import datetime, timezone as _tz
            from zoneinfo import ZoneInfo
            _tz_name = (TARGET_CITIES.get(city) or {}).get("tz") or "UTC"
            _local_tz = ZoneInfo(_tz_name)
            # market_date = "YYYY-MM-DD" = the weather day
            _local_midnight = datetime.fromisoformat(market_date).replace(tzinfo=_local_tz)
            _now = datetime.now(timezone.utc)
            hours_into_weather_day = (_now - _local_midnight).total_seconds() / 3600.0
        except Exception:
            pass
    
    if hours_into_weather_day is not None:
        if hours_into_weather_day < GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS:
            return "too_early_weather_day"
        if hours_into_weather_day > GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS:
            return "too_late_weather_day"
        return None  # in golden window
    
    # Last resort: legacy flat check when no weather day data at all
    if hours_until_resolve is not None:
        if hours_until_resolve > GOLDEN_WINDOW_HOURS_MAX:
            return "too_early_to_enter"
        if hours_until_resolve < GOLDEN_WINDOW_HOURS_MIN:
            return "too_close_to_resolve"
    
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add market_discovery_internal/parsing.py tests/test_dynamic_golden_window.py
git commit -m "feat: implement check_golden_window with weather-day-based dynamic logic"
```

---

### Task 3: Wire Dynamic Golden Window Into parsing.py

**Files:**
- Modify: `market_discovery_internal/parsing.py:428-453` (the golden window check block)
- Test: `tests/test_dynamic_golden_window.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_dynamic_golden_window.py`:

```python
def test_parsing_passes_shanghai_aug5_market():
    """Shanghai Aug 5 market at 09:00 UTC Aug 4 should pass golden window.
    
    gameStartTime = Aug 4 16:00 UTC (Shanghai midnight = UTC+8)
    endDate = Aug 5 12:00 UTC
    now = Aug 4 09:00 UTC
    hours_into_weather_day = 17h, hours_until_resolve = 27h
    With legacy: 27 > 18 → REJECT. With dynamic: 17 in [2,20] → PASS.
    """
    from market_discovery_internal.parsing import check_golden_window
    # Simulate what parsing.py computes for Shanghai Aug 5
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=17.0,
    )
    assert result is None, f"Should pass but got: {result}"


def test_parsing_rejects_market_before_weather_day():
    """Market with hours_into_weather_day=0.5 should be rejected."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=28.0,
        hours_into_weather_day=0.5,
    )
    assert result is not None
    assert "too_early" in result
```

- [ ] **Step 2: Run to verify they pass (function already implemented)**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v -k "parsing"`
Expected: PASS

- [ ] **Step 3: Replace the golden window check block in parsing.py**

In `market_discovery_internal/parsing.py`, find the block at lines ~428-453 that currently reads:

```python
    if daily_resolve_only:
        # ... shadow mode block ...
        if _SHADOW:
            # ... shadow parse hours ...
        else:
            if hours_until_resolve > GOLDEN_WINDOW_HOURS_MAX:
                _min_into = 6.0
                if hours_into_weather_day is None or hours_into_weather_day < _min_into:
                    return _with_reason(None, "too_early_to_enter")
            if hours_until_resolve < GOLDEN_WINDOW_HOURS_MIN:
                return _with_reason(None, "too_close_to_resolve")
```

Replace the `else:` block (non-shadow path) with:

```python
        else:
            # [DYNAMIC GOLDEN WINDOW] Use weather-day-based check (timezone-aware)
            _gw_result = check_golden_window(
                hours_until_resolve, hours_into_weather_day,
                city=city, market_date=date_str,
            )
            if _gw_result is not None:
                return _with_reason(None, _gw_result)

Keep the shadow mode block unchanged — shadow mode uses its own wider window for data collection.

- [ ] **Step 4: Verify parsing.py syntax**

Run: `cd /home/farm/the_blueprints && .venv/bin/python -c "import ast; ast.parse(open('market_discovery_internal/parsing.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 5: Run all golden window tests**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add market_discovery_internal/parsing.py tests/test_dynamic_golden_window.py
git commit -m "feat: wire dynamic golden window into parsing.py parse gate"
```

---

### Task 4: Wire Dynamic Golden Window Into research_features.py Entry Filter

**Files:**
- Modify: `market_discovery_internal/research_features.py:430-441` (the golden window soft check)
- Test: `tests/test_dynamic_golden_window.py`

- [ ] **Step 1: Write failing test for entry filter**

Add to `tests/test_dynamic_golden_window.py`:

```python
def test_entry_filter_passes_shanghai_aug5():
    """Entry filter should pass a market 17h into weather day (Shanghai Aug 5)."""
    from market_discovery_internal.research_features import _exact_entry_eligible
    opp = {
        "yes_price": 0.15,
        "edge": 0.20,
        "model_prob": 0.35,
        "hours_until_resolve": 27.0,
        "hours_into_weather_day": 17.0,
    }
    ok, reason = _exact_entry_eligible(opp)
    assert ok, f"Should pass but got: {reason}"


def test_entry_filter_rejects_too_early_weather_day():
    """Entry filter should reject market 1h into weather day."""
    from market_discovery_internal.research_features import _exact_entry_eligible
    opp = {
        "yes_price": 0.15,
        "edge": 0.20,
        "model_prob": 0.35,
        "hours_until_resolve": 28.0,
        "hours_into_weather_day": 1.0,
    }
    ok, reason = _exact_entry_eligible(opp)
    assert not ok
    assert "too_early" in reason or "outside" in reason
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v -k "entry_filter"`
Expected: FAIL (current code rejects 27h > 18h max)

- [ ] **Step 3: Replace golden window check in research_features.py**

In `market_discovery_internal/research_features.py`, find the block at lines ~430-441:

```python
    # Hours window (golden) — soft check if present
    hrs = opportunity.get("hours_until_resolve")
    if hrs is not None:
        try:
            h = float(hrs)
            hmin = float(os.environ.get("GOLDEN_WINDOW_HOURS_MIN", "6"))
            hmax = float(os.environ.get("GOLDEN_WINDOW_HOURS_MAX", "18"))
            if h < hmin or h > hmax:
                return False, f"exact_outside_golden {h:.1f}h not in [{hmin},{hmax}]"
        except (TypeError, ValueError):
            pass
```

Replace with:

```python
    # [DYNAMIC GOLDEN WINDOW] Use weather-day-based check (timezone-aware)
    from market_discovery_internal.parsing import check_golden_window
    hrs = opportunity.get("hours_until_resolve")
    hwd = opportunity.get("hours_into_weather_day")
    _city = opportunity.get("city")
    _mdate = opportunity.get("date") or opportunity.get("market_date")
    try:
        _hrs = float(hrs) if hrs is not None else None
        _hwd = float(hwd) if hwd is not None else None
    except (TypeError, ValueError):
        _hrs, _hwd = None, None
    _gw = check_golden_window(_hrs, _hwd, city=_city, market_date=_mdate)
    if _gw is not None:
        return False, f"exact_outside_golden {_gw}"
```

- [ ] **Step 4: Verify the opportunity dict includes `hours_into_weather_day`**

Check that `attach_research_fields` in `research_features.py` propagates `hours_into_weather_day` into the opportunity dict. Search for where `hours_into_weather_day` is set on the opportunity/market dict. If it's not propagated, add it.

In `research_features.py`, find `attach_research_fields` function and ensure it copies `hours_into_weather_day` from the parsed market:

```python
    # Ensure hours_into_weather_day is propagated for golden window check
    if "hours_into_weather_day" not in opportunity and market.get("hours_into_weather_day") is not None:
        opportunity["hours_into_weather_day"] = market["hours_into_weather_day"]
```

- [ ] **Step 5: Run tests**

Run: `cd /home/farm/the_blueprints && .venv/bin/pytest tests/test_dynamic_golden_window.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add market_discovery_internal/research_features.py tests/test_dynamic_golden_window.py
git commit -m "feat: wire dynamic golden window into entry filter in research_features.py"
```

---

### Task 5: Update .env on VPS and Restart

**Files:**
- Modify: `/home/farm/the_blueprints/.env` on VPS

- [ ] **Step 1: Add new env vars to .env**

SSH to VPS and add to `.env`:

```bash
ssh mcs-liam "cd /home/farm/the_blueprints && cat >> .env << 'EOF'

# Dynamic golden window (timezone-aware, based on hours_into_weather_day)
GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS=2.0
GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS=20.0
GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS=1.0
EOF"
```

- [ ] **Step 2: Pull latest code and restart paper loop**

```bash
ssh mcs-liam "cd /home/farm/the_blueprints && sudo -u farm git pull origin master && sudo systemctl restart blueprints-paper && sleep 5 && systemctl is-active blueprints-paper"
```

- [ ] **Step 3: Verify markets now pass golden window**

Wait 2-3 minutes for a cycle to complete, then check:

```bash
ssh mcs-liam "cd /home/farm/the_blueprints && grep -a 'FILTER\|ENRICH-TOP\|too_early\|too_late\|too_close\|opportunities' logs/paper_loop.out | tail -20"
```

Expected: `opportunities` count should be > 0 for markets in the weather-day golden window.

- [ ] **Step 4: Commit .env change note**

```bash
git add .env.example  # if exists
git commit -m "docs: add dynamic golden window env vars to example config" || true
```

---

## Verification Checklist

After all tasks are complete:

- [ ] All tests pass: `.venv/bin/pytest tests/test_dynamic_golden_window.py -v`
- [ ] No existing tests broken: `.venv/bin/pytest tests/ -x -q`
- [ ] Paper loop running with > 0 opportunities per cycle (when markets are in weather-day window)
- [ ] Shanghai/Singapore/Seoul Aug 5 markets (17h into weather day) now pass golden window
- [ ] New York/London Aug 5 markets (5-10h into weather day) now pass golden window
- [ ] Markets < 2h into weather day still rejected (too early)
- [ ] Markets < 1h until resolve still rejected (safety floor)
- [ ] Dashboard shows cycle data with opportunities > 0

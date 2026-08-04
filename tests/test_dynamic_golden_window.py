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


def test_weather_day_in_golden_window():
    """Market 10h into weather day, 15h until resolve -> ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=15.0,
        hours_into_weather_day=10.0,
    )
    assert result is None  # None = pass, no rejection


def test_weather_day_too_early():
    """Market 1h into weather day -> REJECT (too early)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=1.0,
    )
    assert result is not None
    assert "too_early" in result


def test_weather_day_too_late():
    """Market 22h into weather day -> REJECT (too late, temps locked)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=5.0,
        hours_into_weather_day=22.0,
    )
    assert result is not None
    assert "too_late" in result


def test_resolve_safety_floor():
    """Market 0.5h until resolve -> REJECT (about to close)."""
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
    # 10h until resolve, no weather day data -> legacy check: 10 is in [6,18] -> pass
    # But .env has MIN=6.0, MAX=18.0 — 10 is in range
    result = check_golden_window(
        hours_until_resolve=10.0,
        hours_into_weather_day=None,
    )
    assert result is None  # pass via legacy


def test_no_weather_day_data_falls_back_reject():
    """When hours_into_weather_day is None and legacy check fails -> reject."""
    from market_discovery_internal.parsing import check_golden_window
    # 2h until resolve, no weather day data -> legacy check: 2 < 6 -> reject
    result = check_golden_window(
        hours_until_resolve=2.0,
        hours_into_weather_day=None,
    )
    assert result is not None
    assert "too_close" in result


def test_shanghai_timezone_scenario():
    """Shanghai at 09:00 UTC Aug 4: 17h into weather day, 27h until resolve -> ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=17.0,
    )
    assert result is None  # 17h is in [2, 20] -> pass


def test_new_york_timezone_scenario():
    """New York at 09:00 UTC Aug 4: 5h into weather day, 27h until resolve -> ALLOW."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=5.0,
    )
    assert result is None  # 5h is in [2, 20] -> pass


def test_hong_kong_same_day_scenario():
    """Hong Kong Aug 4 market at 09:00 UTC: 17h into weather day, 2.9h until resolve -> PASS (safety floor is 1h)."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=2.9,
        hours_into_weather_day=17.0,
    )
    assert result is None  # 2.9 > 1.0 safety floor AND 17h in [2,20] -> pass


def test_timezone_fallback_when_game_start_missing():
    """When hours_into_weather_day is None but city+date provided, compute from tz.
    
    Shanghai (UTC+8), market_date='2026-08-04', now ~09:00 UTC Aug 4.
    Local midnight Aug 4 in Shanghai = Aug 3 16:00 UTC.
    hours_into_weather_day = 17h -> should PASS (in [2,20]).
    """
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="shanghai",
        market_date="2026-08-04",
    )
    assert result is None  # computed ~17h -> in [2,20] -> pass


def test_timezone_fallback_new_york():
    """New York (UTC-4), market_date='2026-08-04', now ~09:00 UTC.
    Local midnight Aug 4 in NYC = Aug 4 04:00 UTC.
    hours_into_weather_day = 5h -> should PASS.
    """
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="new york city",
        market_date="2026-08-04",
    )
    assert result is None  # computed ~5h -> in [2,20] -> pass


def test_timezone_fallback_unknown_city():
    """Unknown city with no tz data -> fall back to legacy flat check."""
    from market_discovery_internal.parsing import check_golden_window
    result = check_golden_window(
        hours_until_resolve=27.0,
        hours_into_weather_day=None,
        city="mars colony",
        market_date="2026-08-04",
    )
    assert result is not None  # 27 > 18 -> legacy reject

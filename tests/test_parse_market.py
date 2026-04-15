import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from market_discovery import parse_market


def make_raw(question, prices=None, clob_token_ids=None, end_date=None):
    """Helper: build a minimal raw market dict."""
    if end_date is None:
        end_date = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return {
        "question": question,
        "outcomePrices": json.dumps(prices or ["0.28", "0.72"]),
        "clobTokenIds": json.dumps(clob_token_ids or ["0xabc123", "0xdef456"]),
        "endDate": end_date,
    }


# --- City matching ---

def test_parses_new_york_full_name():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F on April 15?"))
    assert result["city"] == "new york city"


def test_parses_nyc_abbreviation():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will NYC reach 75°F on April 15?"))
    assert result["city"] == "new york city"


def test_parses_hong_kong():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will Hong Kong exceed 30°C on April 15?"))
    assert result["city"] == "hong kong"


def test_non_target_city_returns_none_silently():
    # Tokyo is not in target list — should return None without logging
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(make_raw("Will Tokyo reach 25°C on April 15?"))
    assert result is None
    mock_log.assert_not_called()


# --- Threshold and unit extraction ---

def test_extracts_fahrenheit_threshold():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York exceed 75°F on April 15?"))
    assert result["threshold"] == 75.0
    assert result["unit"] == "F"


def test_extracts_celsius_threshold():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will Paris reach 30°C on April 15?"))
    assert result["threshold"] == 30.0
    assert result["unit"] == "C"


def test_no_temperature_logs_unmatched_and_returns_none():
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(make_raw("Will it rain in New York on April 15?"))
    assert result is None
    mock_log.assert_called_once()


# --- Direction extraction ---

def test_default_direction_is_above():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York exceed 75°F on April 15?"))
    assert result["direction"] == "above"


def test_below_direction_detected():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York drop below 60°F on April 15?"))
    assert result["direction"] == "below"


def test_under_direction_detected():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York stay under 65°F on April 15?"))
    assert result["direction"] == "below"


def test_exact_direction_detected():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York be exactly 70°F on April 15?"))
    assert result["direction"] == "exact"


def test_city_can_be_detected_from_structured_fields():
    raw = make_raw("Will it hit 70°F on April 15?")
    raw["description"] = "Weather contract for New York City"
    with patch("market_discovery._log_unmatched"):
        result = parse_market(raw)
    assert result["city"] == "new york city"


def test_threshold_can_fallback_to_slug():
    raw = make_raw("Will New York weather event resolve tomorrow?")
    raw["slug"] = "new-york-above-75f-apr-15"
    with patch("market_discovery._log_unmatched"):
        result = parse_market(raw)
    assert result["threshold"] == 75.0
    assert result["unit"] == "F"


def test_non_weather_city_market_skips_without_logging():
    raw = make_raw("Will New York Knicks win tonight?")
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(raw)
    assert result is None
    mock_log.assert_not_called()


# --- Price and token extraction ---

def test_extracts_yes_price():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", prices=["0.32", "0.68"]))
    assert result["yes_price"] == pytest.approx(0.32)


def test_extracts_token_id():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", clob_token_ids=["0xdeadbeef", "0xcafe1234"]))
    assert result["token_id"] == "0xdeadbeef"


def test_missing_outcome_prices_logs_and_returns_none():
    raw = make_raw("Will New York hit 80°F?")
    raw["outcomePrices"] = None
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(raw)
    assert result is None
    mock_log.assert_called_once()


# --- Date and resolution ---

def test_extracts_date_from_end_date():
    # Use a date within 72h window (48h from now for safety)
    future_date = (datetime.now(timezone.utc) + timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    expected_date = future_date[:10]  # Extract YYYY-MM-DD portion
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date=future_date))
    assert result["date"] == expected_date


def test_market_outside_72h_returns_none():
    """Markets resolving more than 3 days out are outside forecast window."""
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date="2030-01-01T12:00:00Z"))
    assert result is None


def test_daily_mode_same_day_market_passes():
    now_utc = datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc)
    with patch("market_discovery._log_unmatched"):
        result = parse_market(
            make_raw("Will New York hit 80°F?", end_date="2026-04-14T10:30:00Z"),
            now_utc=now_utc,
            daily_resolve_only=True,
            daily_min_hours_to_resolve=0,
        )
    assert result is not None
    assert result["date"] == "2026-04-14"


def test_daily_mode_next_day_market_rejected_with_reason():
    now_utc = datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc)
    with patch("market_discovery._log_unmatched"):
        parsed, reason = parse_market(
            make_raw("Will New York hit 80°F?", end_date="2026-04-15T12:00:00Z"),
            now_utc=now_utc,
            daily_resolve_only=True,
            daily_min_hours_to_resolve=6,
            return_skip_reason=True,
        )
    assert parsed is None
    assert reason == "daily_date_mismatch"


def test_daily_mode_market_below_min_hours_rejected_with_reason():
    now_utc = datetime(2026, 4, 14, 8, 0, tzinfo=timezone.utc)
    with patch("market_discovery._log_unmatched"):
        parsed, reason = parse_market(
            make_raw("Will New York hit 80°F?", end_date="2026-04-14T10:30:00Z"),
            now_utc=now_utc,
            daily_resolve_only=True,
            daily_min_hours_to_resolve=6,
            return_skip_reason=True,
        )
    assert parsed is None
    assert reason == "daily_min_hours_not_met"

import json
import pytest
from unittest.mock import patch
from market_discovery import parse_market


def make_raw(question, prices=None, tokens=None, end_date=None):
    """Helper: build a minimal raw market dict."""
    return {
        "question": question,
        "outcomePrices": json.dumps(prices or ["0.28", "0.72"]),
        "tokens": tokens or [{"tokenId": "0xabc123"}],
        "endDate": end_date or "2026-04-15T12:00:00Z",
    }


# --- City matching ---

def test_parses_new_york_full_name():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F on April 15?"))
    assert result["city"] == "new york"


def test_parses_nyc_abbreviation():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will NYC reach 75°F on April 15?"))
    assert result["city"] == "new york"


def test_parses_hong_kong():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will Hong Kong exceed 30°C on April 15?"))
    assert result["city"] == "hong kong"


def test_non_target_city_returns_none_silently():
    # Paris is not in target list — should return None without logging
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(make_raw("Will Paris reach 25°C on April 15?"))
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
        result = parse_market(make_raw("Will Tokyo reach 30°C on April 15?"))
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


# --- Price and token extraction ---

def test_extracts_yes_price():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", prices=["0.32", "0.68"]))
    assert result["yes_price"] == pytest.approx(0.32)


def test_extracts_token_id():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", tokens=[{"tokenId": "0xdeadbeef"}]))
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
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date="2026-04-15T18:00:00Z"))
    assert result["date"] == "2026-04-15"


def test_market_outside_72h_returns_none():
    """Markets resolving more than 3 days out are outside forecast window."""
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date="2030-01-01T12:00:00Z"))
    assert result is None

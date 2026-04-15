import pytest
from unittest.mock import patch
from market_discovery import fetch_forecast


MOCK_FORECAST = {
    "daily": {
        "time": ["2026-04-13", "2026-04-14", "2026-04-15"],
        "temperature_2m_max": [20.5, 22.1, 18.3],
    }
}


def test_returns_temp_for_requested_date():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york city", "2026-04-14")
    assert result == 22.1


def test_returns_first_day_temp():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("chicago", "2026-04-13")
    assert result == 20.5


def test_returns_none_for_unknown_city():
    """City not in TARGET_CITIES — no API call should be made."""
    with patch("market_discovery.fetch_with_retry") as mock_fetch:
        result = fetch_forecast("tokyo", "2026-04-14")
    assert result is None
    mock_fetch.assert_not_called()


def test_returns_none_when_date_not_in_forecast():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york city", "2026-04-20")
    assert result is None


def test_returns_none_on_network_failure():
    with patch("market_discovery.fetch_with_retry", side_effect=Exception("timeout")):
        result = fetch_forecast("new york city", "2026-04-14")
    assert result is None


def test_sends_correct_coordinates_for_paris():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("paris", "2026-04-13")
    call_params = mock_fetch.call_args[1]["params"]
    assert call_params["latitude"] == 48.8566
    assert call_params["longitude"] == 2.3522


def test_requests_3_forecast_days():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("london", "2026-04-13")
    call_params = mock_fetch.call_args[1]["params"]
    assert call_params["forecast_days"] == 3
    assert call_params["daily"] == "temperature_2m_max"

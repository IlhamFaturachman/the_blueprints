import pytest
from unittest.mock import patch, MagicMock
from market_discovery import fetch_forecast


MOCK_FORECAST = {
    "daily": {
        "time": ["2026-04-13", "2026-04-14", "2026-04-15"],
        "temperature_2m_max": [20.5, 22.1, 18.3],
    }
}


def _mock_db():
    """Create a mock db that returns None for all cache lookups."""
    mock = MagicMock()
    mock.get_cached_forecast.return_value = None
    mock.save_cached_forecast.return_value = None
    mock.get_weather.return_value = None
    mock.save_weather.return_value = None
    return mock


def test_returns_temp_for_requested_date():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york city", "2026-04-14")
    assert result is not None
    assert float(result) == pytest.approx(22.1, abs=0.5)


def test_returns_first_day_temp():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("chicago", "2026-04-13")
    assert result is not None
    assert float(result) == pytest.approx(20.5, abs=0.5)


def test_returns_none_for_unknown_city():
    """City not in TARGET_CITIES — no API call should be made."""
    with patch("market_discovery_internal.forecasting.fetch_with_retry") as mock_fetch:
        result = fetch_forecast("tokyo", "2026-04-14")
    assert result is None
    mock_fetch.assert_not_called()


def test_returns_none_when_date_not_in_forecast():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york city", "2026-04-20")
    # Open-Meteo won't have this date, wttr.in also mocked with same data
    # Both sources return None for this date → result is None
    assert result is None


def test_returns_none_on_network_failure():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", side_effect=Exception("timeout")):
        result = fetch_forecast("new york city", "2026-04-14")
    assert result is None


def test_sends_correct_coordinates_for_paris():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("paris", "2026-04-13")
    # fetch_with_retry is called multiple times (Open-Meteo, wttr.in, NOAA)
    # Find the Open-Meteo call (has latitude/longitude params)
    open_meteo_calls = [
        call for call in mock_fetch.call_args_list
        if call.kwargs.get("params") and "latitude" in call.kwargs.get("params", {})
    ]
    assert open_meteo_calls
    call_params = open_meteo_calls[0].kwargs["params"]
    assert call_params["latitude"] == 49.0097
    assert call_params["longitude"] == 2.5479


def test_requests_3_forecast_days():
    with patch("market_discovery_internal.forecasting.db", _mock_db()), \
         patch("market_discovery_internal.forecasting.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("london", "2026-04-13")
    open_meteo_calls = [
        call for call in mock_fetch.call_args_list
        if call.kwargs.get("params") and "latitude" in call.kwargs.get("params", {})
    ]
    assert open_meteo_calls
    call_params = open_meteo_calls[0].kwargs["params"]
    assert call_params["forecast_days"] == 3
    assert call_params["daily"] == "temperature_2m_max"

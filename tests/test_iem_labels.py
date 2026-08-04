# tests/test_iem_labels.py
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _reset_iem_cache():
    """Clear the module-level IEM cache between tests for isolation."""
    import market_discovery_internal.iem_labels as _mod
    _mod._IEM_CACHE.clear()
    yield
    _mod._IEM_CACHE.clear()


def test_fetch_iem_daily_max_returns_celsius():
    from market_discovery_internal.iem_labels import fetch_iem_daily_max
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"station": "KDFW", "max_temp_f": 95, "day": "2026-08-05"}]}
    with patch("market_discovery_internal.iem_labels.requests.get", return_value=mock_resp):
        result = fetch_iem_daily_max("KDFW", "2026-08-05")
    assert result is not None
    assert abs(result - 35.0) < 0.01  # 95F = 35C


def test_fetch_iem_returns_none_on_error():
    from market_discovery_internal.iem_labels import fetch_iem_daily_max
    with patch("market_discovery_internal.iem_labels.requests.get", side_effect=Exception("timeout")):
        result = fetch_iem_daily_max("KDFW", "2026-08-05")
    assert result is None


def test_fetch_iem_returns_none_on_404():
    from market_discovery_internal.iem_labels import fetch_iem_daily_max
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("market_discovery_internal.iem_labels.requests.get", return_value=mock_resp):
        result = fetch_iem_daily_max("KDFW", "2026-08-05")
    assert result is None


def test_fetch_iem_parses_fahrenheit():
    from market_discovery_internal.iem_labels import fetch_iem_daily_max
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"station": "KJFK", "max_temp_f": 32, "day": "2026-08-05"}]}
    with patch("market_discovery_internal.iem_labels.requests.get", return_value=mock_resp):
        result = fetch_iem_daily_max("KJFK", "2026-08-05")
    assert result is not None
    assert abs(result - 0.0) < 0.01  # 32F = 0C

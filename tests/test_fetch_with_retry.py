import pytest
import requests
from unittest.mock import patch, MagicMock
from market_discovery import fetch_with_retry


def _mock_success(data):
    """Helper: create a mock response that returns data as JSON."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


def _mock_failure():
    """Helper: create a mock response that raises RequestException."""
    resp = MagicMock()
    resp.status_code = 500
    resp.raise_for_status.side_effect = requests.RequestException("timeout")
    return resp


def test_succeeds_on_first_try():
    with patch("market_discovery_internal.utils.requests.get", return_value=_mock_success({"data": "ok"})):
        result = fetch_with_retry("http://example.com")
    assert result == {"data": "ok"}


def test_retries_on_failure_then_succeeds():
    with patch("market_discovery_internal.utils.requests.get", side_effect=[_mock_failure(), _mock_success({"data": "ok"})]):
        with patch("market_discovery_internal.utils.time.sleep"):
            result = fetch_with_retry("http://example.com")
    assert result == {"data": "ok"}


def test_raises_after_max_retries():
    with patch("market_discovery_internal.utils.requests.get", return_value=_mock_failure()):
        with patch("market_discovery_internal.utils.time.sleep"):
            with pytest.raises(requests.RequestException):
                fetch_with_retry("http://example.com", max_retries=3)


def test_exponential_backoff_timing():
    """Verify sleep durations: 1s before retry 2, 2s before retry 3."""
    with patch("market_discovery_internal.utils.requests.get", return_value=_mock_failure()):
        with patch("market_discovery_internal.utils.time.sleep") as mock_sleep:
            with pytest.raises(requests.RequestException):
                fetch_with_retry("http://example.com", max_retries=3)
    sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
    assert sleep_calls == [1, 2]


def test_passes_params_to_get():
    params = {"tag": "weather", "active": "true"}
    with patch("market_discovery_internal.utils.requests.get", return_value=_mock_success([])) as mock_get:
        fetch_with_retry("http://example.com", params=params)
    mock_get.assert_called_once_with("http://example.com", params=params, headers=None, timeout=10)

import pytest
from unittest.mock import patch
from market_discovery import fetch_markets


SAMPLE_MARKETS = [
    {"question": "Will New York hit 80°F?", "active": True},
    {"question": "Will Chicago hit 75°F?", "active": True},
]


def test_returns_list_from_array_response():
    with patch("market_discovery.fetch_with_retry", return_value=SAMPLE_MARKETS):
        result = fetch_markets()
    assert isinstance(result, list)
    assert len(result) == 2


def test_returns_list_from_dict_response():
    """Gamma API sometimes wraps results in {"markets": [...]}."""
    with patch("market_discovery.fetch_with_retry", return_value={"markets": SAMPLE_MARKETS}):
        result = fetch_markets()
    assert len(result) == 2


def test_exits_on_fetch_failure():
    with patch("market_discovery.fetch_with_retry", side_effect=Exception("API down")):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets()
    assert exc_info.value.code == 1


def test_inspect_mode_exits_after_printing(capsys):
    with patch("market_discovery.fetch_with_retry", return_value=SAMPLE_MARKETS):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets(inspect=True)
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "INSPECT MODE" in captured.out


def test_fetch_uses_correct_params():
    with patch("market_discovery.fetch_with_retry", return_value=[]) as mock_fetch:
        fetch_markets()
    call_kwargs = mock_fetch.call_args
    params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
    assert params.get("tag") == "weather"
    assert str(params.get("active")).lower() == "true"

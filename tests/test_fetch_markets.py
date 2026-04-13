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


def test_filters_to_temperature_candidates_only():
    raw = [
        {"question": "Will New York hit 80°F tomorrow?", "active": True},
        {"question": "Will New York Knicks win tonight?", "active": True},
    ]
    with patch("market_discovery.fetch_with_retry", return_value=raw):
        result = fetch_markets()
    assert len(result) == 1
    assert "80" in result[0]["question"]


def test_scans_next_offset_when_first_page_has_no_candidates():
    first_page = [{"question": "Will team A win?", "active": True}]
    second_page = [{"question": "Will Chicago exceed 75°F this week?", "active": True}]

    with patch("market_discovery.fetch_with_retry", side_effect=[first_page, second_page]) as mock_fetch:
        result = fetch_markets()

    assert len(result) == 1
    assert "Chicago" in result[0]["question"]
    assert mock_fetch.call_count == 2
    second_call_params = mock_fetch.call_args_list[1][1]["params"]
    assert second_call_params["offset"] == 100


def test_returns_first_page_if_no_candidates_found():
    first_page = [{"question": "Will candidate X win election?", "active": True}]

    with patch("market_discovery.fetch_with_retry", side_effect=[first_page, []]):
        result = fetch_markets()

    assert result == first_page


def test_returns_scanned_pages_when_no_candidates_in_all_pages():
    first_page = [{"id": "1", "question": "Will candidate X win election?", "active": True}]
    second_page = [{"id": "2", "question": "Will candidate Y win election?", "active": True}]
    third_page = [{"id": "3", "question": "Will candidate Z win election?", "active": True}]

    with patch("market_discovery.fetch_with_retry", side_effect=[first_page, second_page, third_page]):
        result = fetch_markets()

    ids = [m["id"] for m in result]
    assert ids == ["1", "2", "3"]


def test_aggressive_scan_falls_back_to_broad_active_query():
    weather_page = [{"question": "Will candidate X win election?", "active": True}]
    weather_offset_page = []
    broad_page = [{"question": "Will Chicago exceed 75F on April 14?", "active": True}]

    with patch(
        "market_discovery.fetch_with_retry",
        side_effect=[weather_page, weather_offset_page, broad_page],
    ) as mock_fetch:
        result = fetch_markets(aggressive_scan=True)

    assert len(result) == 1
    assert "Chicago" in result[0]["question"]

    first_params = mock_fetch.call_args_list[0][1]["params"]
    third_params = mock_fetch.call_args_list[2][1]["params"]
    assert first_params.get("tag") == "weather"
    assert "tag" not in third_params

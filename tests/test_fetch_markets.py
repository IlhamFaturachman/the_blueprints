import pytest
from unittest.mock import patch
from market_discovery import fetch_markets, GAMMA_EVENTS_API


SAMPLE_MARKETS = [
    {"question": "Will New York hit 80°F?", "active": True},
    {"question": "Will Chicago hit 75°F?", "active": True},
]

# Events API wraps markets inside events
SAMPLE_EVENTS = [{"title": "Weather event", "markets": SAMPLE_MARKETS}]
EVENTS_RESPONSE = {"data": SAMPLE_EVENTS}


def test_returns_list_from_events_response():
    """Events API returns {"data": [events]} where each event contains markets."""
    with patch("market_discovery.fetch_with_retry", return_value=EVENTS_RESPONSE):
        result = fetch_markets()
    assert isinstance(result, list)
    assert len(result) == 2


def test_returns_list_from_plain_list_events_response():
    """Events API may return a plain list of events."""
    with patch("market_discovery.fetch_with_retry", return_value=SAMPLE_EVENTS):
        result = fetch_markets()
    assert len(result) == 2


def test_exits_on_fetch_failure():
    with patch("market_discovery.fetch_with_retry", side_effect=Exception("API down")):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets()
    assert exc_info.value.code == 1


def test_inspect_mode_exits_after_printing(capsys):
    with patch("market_discovery.fetch_with_retry", return_value=EVENTS_RESPONSE):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets(inspect=True)
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "INSPECT MODE" in captured.out


def test_fetch_uses_events_endpoint_with_tag_slug():
    """Must call the events/pagination endpoint with tag_slug=weather."""
    with patch("market_discovery.fetch_with_retry", return_value=EVENTS_RESPONSE) as mock_fetch:
        fetch_markets()
    url = mock_fetch.call_args[0][0]
    params = mock_fetch.call_args[1]["params"]
    assert "events/pagination" in url
    assert params.get("tag_slug") == "weather"
    assert str(params.get("active")).lower() == "true"
    assert str(params.get("closed")).lower() == "false"


def test_filters_to_temperature_candidates_only():
    events = [{"markets": [
        {"question": "Will New York hit 80°F tomorrow?", "active": True},
        {"question": "Will New York Knicks win tonight?", "active": True},
    ]}]
    with patch("market_discovery.fetch_with_retry", return_value={"data": events}):
        result = fetch_markets()
    assert len(result) == 1
    assert "80" in result[0]["question"]


def test_scans_next_offset_when_first_page_has_no_candidates():
    first_response = {"data": [{"markets": [{"question": "Will team A win?", "active": True}]}]}
    second_response = {"data": [{"markets": [{"question": "Will Chicago exceed 75°F this week?", "active": True}]}]}

    with patch("market_discovery.fetch_with_retry", side_effect=[first_response, second_response]) as mock_fetch:
        result = fetch_markets()

    assert len(result) == 1
    assert "Chicago" in result[0]["question"]
    assert mock_fetch.call_count == 2
    second_call_params = mock_fetch.call_args_list[1][1]["params"]
    assert second_call_params["offset"] == 200


def test_returns_flattened_markets_when_no_candidates_found():
    market1 = {"question": "Will candidate X win election?", "active": True}
    first_response = {"data": [{"markets": [market1]}]}

    with patch("market_discovery.fetch_with_retry", side_effect=[first_response, {"data": []}]):
        result = fetch_markets()

    assert result == [market1]


def test_returns_all_scanned_markets_when_no_candidates_in_all_pages():
    mk1 = {"id": "1", "question": "Will candidate X win election?", "active": True}
    mk2 = {"id": "2", "question": "Will candidate Y win election?", "active": True}
    mk3 = {"id": "3", "question": "Will candidate Z win election?", "active": True}

    r1 = {"data": [{"markets": [mk1]}]}
    r2 = {"data": [{"markets": [mk2]}]}
    r3 = {"data": [{"markets": [mk3]}]}

    with patch("market_discovery.fetch_with_retry", side_effect=[r1, r2, r3]):
        result = fetch_markets()

    ids = [m["id"] for m in result]
    assert ids == ["1", "2", "3"]

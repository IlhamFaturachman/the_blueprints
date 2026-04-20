import pytest
from unittest.mock import patch, MagicMock
from market_discovery_internal.discovery import fetch_markets
from market_discovery_internal.config import GAMMA_EVENTS_API


def _temp_market(question, token_id="0xabc"):
    """Build a market dict that passes _is_temperature_market_candidate.
    
    The candidate check requires: weather_context + threshold + city.
    We add a description with 'temperature' to ensure weather context matches.
    """
    return {
        "question": question,
        "description": "Daily high temperature forecast market",
        "active": True,
        "clobTokenIds": f'["{token_id}", "{token_id}b"]',
    }


SAMPLE_MARKETS = [
    _temp_market("Will New York hit 80°F?", "0x1"),
    _temp_market("Will Chicago hit 75°F?", "0x2"),
]

# Events API wraps markets inside events
SAMPLE_EVENTS = [{"title": "Weather event", "markets": SAMPLE_MARKETS}]
EVENTS_RESPONSE = {"data": SAMPLE_EVENTS}


def test_returns_list_from_events_response():
    """Events API returns {"data": [events]} where each event contains markets."""
    with patch("market_discovery_internal.discovery.fetch_with_retry", return_value=EVENTS_RESPONSE), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()
    assert isinstance(result, list)
    assert len(result) == 2


def test_returns_list_from_plain_list_events_response():
    """Events API may return a plain list of events."""
    with patch("market_discovery_internal.discovery.fetch_with_retry", return_value=SAMPLE_EVENTS), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()
    assert len(result) == 2


def test_raises_on_fetch_failure():
    """fetch_markets now raises RuntimeError instead of SystemExit."""
    with patch("market_discovery_internal.discovery.fetch_with_retry", side_effect=Exception("API down")):
        with pytest.raises(RuntimeError):
            fetch_markets()


def test_inspect_mode_exits_after_printing(capsys):
    with patch("market_discovery_internal.discovery.fetch_with_retry", return_value=EVENTS_RESPONSE):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets(inspect=True)
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "INSPECT MODE" in captured.out


def test_fetch_uses_events_endpoint_with_tag_slug():
    """Must call the events endpoint with tag_slug=weather."""
    with patch("market_discovery_internal.discovery.fetch_with_retry", return_value=EVENTS_RESPONSE) as mock_fetch, \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        fetch_markets()
    # First call is the initial fetch
    first_call = mock_fetch.call_args_list[0]
    url = first_call[0][0]
    params = first_call[1]["params"]
    assert "events" in url.lower() or GAMMA_EVENTS_API in url
    assert params.get("tag_slug") == "weather"
    assert str(params.get("active")).lower() == "true"
    assert str(params.get("closed")).lower() == "false"


def test_filters_to_temperature_candidates_only():
    events = [{"markets": [
        _temp_market("Will New York hit 80°F tomorrow?", "0x1"),
        {"question": "Will New York Knicks win tonight?", "active": True},
    ]}]
    with patch("market_discovery_internal.discovery.fetch_with_retry", return_value={"data": events}), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()
    assert len(result) == 1
    assert "80" in result[0]["question"]


def test_scans_next_offset_when_first_page_has_no_candidates():
    first_response = {"data": [{"markets": [{"question": "Will team A win?", "active": True}]}]}
    second_response = {"data": [{"markets": [
        _temp_market("Will Chicago exceed 75°F this week?", "0xchi")
    ]}]}
    # Need enough responses for pagination (initial + pages)
    with patch("market_discovery_internal.discovery.fetch_with_retry",
               side_effect=[first_response, second_response, {"data": []}]), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()

    assert len(result) == 1
    assert "Chicago" in result[0]["question"]


def test_returns_empty_when_no_candidates_found():
    """When no temperature candidates found across pages, returns empty list."""
    market1 = {"question": "Will candidate X win election?", "active": True}
    first_response = {"data": [{"markets": [market1]}]}

    with patch("market_discovery_internal.discovery.fetch_with_retry",
               side_effect=[first_response, {"data": []}]), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()

    assert result == []


def test_returns_all_candidates_across_pages():
    mk1 = _temp_market("Will New York hit 80°F?", "0x1")
    mk2 = _temp_market("Will Chicago hit 75°F?", "0x2")
    mk3 = _temp_market("Will London hit 20°C?", "0x3")

    r1 = {"data": [{"markets": [mk1]}]}
    r2 = {"data": [{"markets": [mk2]}]}
    r3 = {"data": [{"markets": [mk3]}]}

    with patch("market_discovery_internal.discovery.fetch_with_retry",
               side_effect=[r1, r2, r3, {"data": []}]), \
         patch("market_discovery_internal.discovery._enrich_markets_missing_prices", side_effect=lambda x: x):
        result = fetch_markets()

    assert len(result) == 3

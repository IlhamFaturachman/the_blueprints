from unittest.mock import patch

from market_discovery import wired_run_discovery_cycle as run_discovery_cycle


def make_parsed_market(token_id, city="new york city", date="2026-04-15", yes_price=0.20):
    return {
        "city": city,
        "date": date,
        "end_date": "2026-04-15T20:00:00+00:00",
        "market_question": f"Will {city.title()} exceed 75F?",
        "threshold": 24.0,  # °C — close to forecast 25°C so sigmoid prob ≈ 0.82
        "unit": "C",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": 6.0,
        "icao_explicit": True,  # Skip AI station resolution
    }


def test_run_discovery_cycle_reuses_successful_forecast_per_city_date():
    parsed_one = make_parsed_market("0x1")
    parsed_two = make_parsed_market("0x2")
    raw_markets = [{"id": "m1"}, {"id": "m2"}]

    with patch("market_discovery.fetch_markets", return_value=raw_markets), patch(
        "market_discovery.parse_market",
        side_effect=[(parsed_one, None), (parsed_two, None)],
    ), patch("market_discovery.fetch_forecast", return_value=25.0) as mock_forecast:
        discovery = run_discovery_cycle()

    assert mock_forecast.call_count == 1
    assert len(discovery["parsed"]) == 2
    assert len(discovery["enriched"]) == 2
    assert len(discovery["opportunities"]) == 2
    assert isinstance(discovery.get("performance"), dict)
    assert discovery["performance"]["total_ms"] >= 0
    cache = discovery["performance"].get("forecast_cache", {})
    assert cache.get("size") == 1
    assert cache.get("hits") == 1
    assert cache.get("misses") == 1


def test_run_discovery_cycle_does_not_cache_failed_forecast_attempts():
    parsed_one = make_parsed_market("0x1")
    parsed_two = make_parsed_market("0x2")
    raw_markets = [{"id": "m1"}, {"id": "m2"}]

    with patch("market_discovery.fetch_markets", return_value=raw_markets), patch(
        "market_discovery.parse_market",
        side_effect=[(parsed_one, None), (parsed_two, None)],
    ), patch("market_discovery.fetch_forecast", side_effect=[None, 25.0]) as mock_forecast:
        discovery = run_discovery_cycle()

    assert mock_forecast.call_count == 2
    assert len(discovery["parsed"]) == 2
    assert len(discovery["enriched"]) == 1
    assert "new york city" in discovery["failed_cities"]
    cache = discovery["performance"].get("forecast_cache", {})
    assert cache.get("size") == 1
    assert cache.get("hits") == 0
    assert cache.get("misses") == 2


def test_run_discovery_cycle_prefetches_forecasts_when_threshold_reached():
    parsed_one = make_parsed_market("0x1", city="new york city", date="2026-04-15")
    parsed_two = make_parsed_market("0x2", city="chicago", date="2026-04-15")
    parsed_three = make_parsed_market("0x3", city="toronto", date="2026-04-15")
    raw_markets = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]

    with patch("market_discovery.DISCOVERY_FORECAST_PREFETCH_MIN_KEYS", 1), patch(
        "market_discovery.DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS", 2
    ), patch("market_discovery.fetch_markets", return_value=raw_markets), patch(
        "market_discovery.parse_market",
        side_effect=[(parsed_one, None), (parsed_two, None), (parsed_three, None)],
    ), patch("market_discovery.fetch_forecast", return_value=25.0) as mock_forecast:
        discovery = run_discovery_cycle()

    assert mock_forecast.call_count == 3
    cache = discovery["performance"].get("forecast_cache", {})
    assert cache.get("size") == 3
    assert cache.get("hits") == 3
    assert cache.get("misses") == 0

    prefetch = discovery["performance"].get("forecast_prefetch", {})
    assert prefetch.get("eligible") == 3
    assert prefetch.get("attempted") == 3
    assert prefetch.get("successful") == 3
    assert prefetch.get("failed") == 0
    assert prefetch.get("workers") == 2
    assert prefetch.get("skipped") is False

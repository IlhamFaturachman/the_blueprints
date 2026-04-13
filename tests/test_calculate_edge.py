import pytest
from market_discovery import calculate_edge


def make_market(threshold, unit, direction, yes_price):
    return {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T12:00:00+00:00",
        "market_question": "Will New York hit threshold?",
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
    }


def test_converts_celsius_forecast_to_fahrenheit():
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result["forecast_temp_converted"] == pytest.approx(77.0, abs=0.1)


def test_no_conversion_for_celsius_market():
    market = make_market(25.0, "C", "above", 0.28)
    result = calculate_edge(market, 27.0)
    assert result["forecast_temp_converted"] == pytest.approx(27.0, abs=0.1)


def test_above_direction_probability_logic():
    market = make_market(75.0, "F", "above", 0.28)
    assert calculate_edge(market, 25.0)["model_prob"] == 1.0  # 77F >= 75F
    assert calculate_edge(market, 20.0)["model_prob"] == 0.0  # 68F < 75F


def test_below_direction_is_strictly_less_than():
    market = make_market(60.0, "F", "below", 0.28)
    assert calculate_edge(market, 15.0)["model_prob"] == 1.0  # 59F < 60F
    assert calculate_edge(market, 15.5556)["model_prob"] == 0.0  # ~60F is not below


def test_edge_is_model_prob_minus_yes_price():
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result["edge"] == pytest.approx(0.72, abs=0.0001)


def test_returns_none_for_exact_direction_in_v1():
    market = make_market(75.0, "F", "exact", 0.28)
    assert calculate_edge(market, 25.0) is None


def test_returns_none_when_forecast_is_missing():
    market = make_market(75.0, "F", "above", 0.28)
    assert calculate_edge(market, None) is None

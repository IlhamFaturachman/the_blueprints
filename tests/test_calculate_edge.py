"""Tests for calculate_edge() — sigmoid model, calibrated probabilities, market_implied path.

Contract (current):
- threshold in market dict is already in °C (parsing handles F→C; calculate_edge does NOT convert)
- forecast_temp argument is in °C
- Sigmoid model for above/below; Gaussian for exact
- Returns: model_prob, raw_prob, edge, forecast, prob_source, slippage_penalty_applied,
           horizon_bin, price_bin, calibration_* fields
- No 'forecast_temp_converted' field
- slippage_penalty defaults to 0.0175 when gamma_spread absent
- edge = model_prob - (yes_price + slippage_penalty)
"""
import pytest
from market_discovery import calculate_edge


def make_market(threshold_c, direction, yes_price, **kwargs):
    """Create a market dict with threshold already in °C."""
    m = {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T12:00:00+00:00",
        "market_question": "Will New York hit threshold?",
        "threshold": threshold_c,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
    }
    m.update(kwargs)
    return m


# ---------------------------------------------------------------------------
# Sigmoid model — above direction
# ---------------------------------------------------------------------------

def test_above_direction_forecast_well_above_threshold():
    # forecast +2°C above threshold → sigmoid strong → model_prob > 0.90
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 27.0)
    assert result is not None
    assert result["model_prob"] > 0.90


def test_above_direction_forecast_well_below_threshold():
    # forecast 5°C below threshold → sigmoid weak → model_prob < 0.10
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 20.0)
    assert result is not None
    assert result["model_prob"] < 0.10


def test_above_direction_at_threshold():
    # forecast == threshold → sigmoid(0) = 0.5
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result is not None
    assert result["model_prob"] == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Sigmoid model — below direction
# ---------------------------------------------------------------------------

def test_below_direction_forecast_well_below_threshold():
    # forecast 5°C below threshold → model_prob > 0.90
    market = make_market(15.0, "below", 0.28)
    result = calculate_edge(market, 10.0)
    assert result is not None
    assert result["model_prob"] > 0.90


def test_below_direction_forecast_well_above_threshold():
    # forecast 2°C above threshold → model_prob < 0.10
    market = make_market(15.0, "below", 0.28)
    result = calculate_edge(market, 17.0)
    assert result is not None
    assert result["model_prob"] < 0.10


# ---------------------------------------------------------------------------
# Gaussian model — exact direction
# ---------------------------------------------------------------------------

def test_exact_direction_at_threshold_max_prob():
    # forecast == threshold → Gaussian peak → raw_prob = 1.0
    market = make_market(25.0, "exact", 0.05)
    result = calculate_edge(market, 25.0)
    assert result is not None
    assert result["raw_prob"] == pytest.approx(1.0, abs=0.001)
    assert result["model_prob"] > 0


def test_exact_direction_far_forecast_low_prob():
    # 5°C away from threshold → very low prob (sigma=1.5°C)
    market = make_market(25.0, "exact", 0.28)
    result = calculate_edge(market, 30.0)
    assert result is not None
    assert result["model_prob"] < 0.15


# ---------------------------------------------------------------------------
# Edge calculation — includes slippage penalty
# ---------------------------------------------------------------------------

def test_edge_is_model_prob_minus_price_minus_slippage():
    # With no gamma_spread: slippage_penalty = 0.0175
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 27.0)
    assert result is not None
    expected_edge = result["model_prob"] - (0.28 + result["slippage_penalty_applied"])
    assert result["edge"] == pytest.approx(expected_edge, abs=0.0001)
    assert result["slippage_penalty_applied"] == pytest.approx(0.0175, abs=0.0001)


def test_edge_uses_gamma_spread_when_available():
    # gamma_spread provided → slippage = max(0.0175, spread/2)
    market = make_market(25.0, "above", 0.28, gamma_spread=0.06)
    result = calculate_edge(market, 27.0)
    assert result is not None
    assert result["slippage_penalty_applied"] == pytest.approx(0.03, abs=0.0001)


# ---------------------------------------------------------------------------
# None handling
# ---------------------------------------------------------------------------

def test_returns_none_when_forecast_is_missing():
    market = make_market(25.0, "above", 0.28)
    assert calculate_edge(market, None) is None


# ---------------------------------------------------------------------------
# prob_source field
# ---------------------------------------------------------------------------

def test_gaussian_path_has_prob_source_field():
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 27.0)
    assert result is not None
    assert result["prob_source"] == "gaussian_openmeteo"


# ---------------------------------------------------------------------------
# market_implied_prob path
# ---------------------------------------------------------------------------

def test_market_implied_prob_used_when_available():
    market = make_market(28.0, "exact", 0.14)
    market["market_implied_prob"] = 0.22
    market["market_implied_expected_temp_c"] = 28.5

    result = calculate_edge(market, forecast_temp=None)

    assert result is not None
    assert result["model_prob"] == pytest.approx(0.22, abs=0.001)
    assert result["prob_source"] == "market_implied"
    # edge = 0.22 - (0.14 + 0.0175)
    assert result["edge"] == pytest.approx(0.22 - 0.14 - 0.0175, abs=0.001)


def test_market_implied_takes_priority_over_gaussian_forecast():
    market = make_market(28.0, "exact", 0.14)
    market["market_implied_prob"] = 0.20

    # forecast_temp provided but should be ignored
    result = calculate_edge(market, forecast_temp=5.0)

    assert result is not None
    assert result["prob_source"] == "market_implied"
    assert result["model_prob"] == pytest.approx(0.20, abs=0.001)


def test_gaussian_fallback_when_market_implied_not_available():
    market = make_market(25.0, "exact", 0.05)
    result = calculate_edge(market, 25.0)

    assert result is not None
    assert result["prob_source"] == "gaussian_openmeteo"
    assert result["model_prob"] > 0


def test_no_forecast_temp_converted_field():
    """calculate_edge no longer does F→C conversion — that's parsing's job."""
    market = make_market(25.0, "above", 0.28)
    result = calculate_edge(market, 27.0)
    assert result is not None
    assert "forecast_temp_converted" not in result

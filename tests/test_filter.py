from market_discovery import filter_opportunities


def make_opp(yes_price, model_prob, edge=None, city="new york"):
    return {
        "city": city,
        "date": "2026-04-15",
        "market_question": "Will test hit threshold?",
        "yes_price": yes_price,
        "model_prob": model_prob,
        "edge": edge if edge is not None else round(model_prob - yes_price, 4),
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
        "forecast_temp_c": 25.0,
        "forecast_temp_converted": 77.0,
        "unit": "F",
        "threshold": 75.0,
        "direction": "above",
    }


def test_passes_high_edge_opportunity():
    markets = [make_opp(0.28, 1.0)]
    result = filter_opportunities(markets)
    assert len(result) == 1


def test_rejects_yes_price_at_or_above_035():
    markets = [make_opp(0.35, 1.0), make_opp(0.40, 1.0)]
    result = filter_opportunities(markets, max_yes_price=0.34)
    assert len(result) == 0


def test_rejects_model_prob_below_070():
    markets = [make_opp(0.28, 0.65)]
    result = filter_opportunities(markets, min_model_prob=0.70)
    assert len(result) == 0


def test_accepts_boundary_values():
    # Pass explicit thresholds to be independent of .env overrides
    markets = [make_opp(0.34, 0.70)]
    result = filter_opportunities(markets, max_yes_price=0.34, min_model_prob=0.70, min_edge=0.20)
    assert len(result) == 1


def test_sorts_by_edge_descending():
    markets = [
        make_opp(0.28, 1.0, edge=0.72),
        make_opp(0.20, 1.0, edge=0.80),
        make_opp(0.30, 1.0, edge=0.70),
    ]
    result = filter_opportunities(markets)
    assert result[0]["edge"] == 0.80
    assert result[1]["edge"] == 0.72
    assert result[2]["edge"] == 0.70


def test_returns_empty_list_when_no_opportunities():
    markets = [make_opp(0.50, 0.50)]
    result = filter_opportunities(markets)
    assert result == []


def test_rejects_when_edge_below_min_edge():
    markets = [make_opp(0.34, 0.70, edge=0.20)]
    result = filter_opportunities(markets, min_edge=0.25)
    assert result == []


def test_allows_relaxed_threshold_override():
    markets = [make_opp(0.45, 0.60, edge=0.15)]
    result = filter_opportunities(
        markets,
        max_yes_price=0.50,
        min_model_prob=0.55,
        min_edge=0.10,
    )
    assert len(result) == 1

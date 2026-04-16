from unittest.mock import patch

from market_discovery import _build_tuned_filter_opportunities


def make_exact_market(token_id="0xexact"):
    return {
        "city": "new york city",
        "date": "2026-04-15",
        "market_question": "Will New York be exactly 70F?",
        "token_id": token_id,
        "direction": "exact",
        "threshold": 70.0,
        "unit": "F",
        "yes_price": 0.20,
        "best_ask": 0.20,
        "model_prob": 0.35,
        "edge": 0.15,
    }


def test_tuned_filter_blocks_exact_market_when_sonnet_rejects():
    market = make_exact_market()

    with patch("market_discovery.SONNET_ENTRY_ENABLED", True), patch(
        "market_discovery.ANTHROPIC_API_KEY", "test-key"
    ), patch("market_discovery.STRATEGY_EXACT_MIN_MODEL_PROB", 0.10), patch(
        "market_discovery.STRATEGY_EXACT_MIN_EDGE", 0.02
    ), patch("market_discovery._sonnet_entry_analysis", return_value={"confidence": 0.40, "recommendation": "skip"}):
        filter_fn = _build_tuned_filter_opportunities({"city_scores": {}})
        result = filter_fn([market])

    assert result == []


def test_tuned_filter_accepts_exact_market_when_sonnet_confident():
    market = make_exact_market()

    with patch("market_discovery.SONNET_ENTRY_ENABLED", True), patch(
        "market_discovery.ANTHROPIC_API_KEY", "test-key"
    ), patch("market_discovery.SONNET_ENTRY_MIN_CONFIDENCE", 0.80), patch(
        "market_discovery.STRATEGY_EXACT_MIN_MODEL_PROB", 0.10
    ), patch("market_discovery.STRATEGY_EXACT_MIN_EDGE", 0.02), patch(
        "market_discovery._sonnet_entry_analysis",
        return_value={
            "confidence": 0.91,
            "recommendation": "enter",
            "sonnet_temp_c": 21.0,
            "metar_temp_c": 20.0,
            "nws_forecast_c": 22.0,
            "reasoning": "Signal is strong.",
        },
    ):
        filter_fn = _build_tuned_filter_opportunities({"city_scores": {}})
        result = filter_fn([market])

    assert len(result) == 1
    assert result[0]["sonnet_confidence"] == 0.91
    assert result[0]["sonnet_reasoning"] == "Signal is strong."

from market_discovery import build_discovery_diagnostics


def make_opportunity(yes_price=0.25, model_prob=1.0, edge=0.75):
    return {
        "city": "seoul",
        "date": "2026-04-16",
        "market_question": "Will the lowest temperature in Seoul be 18C or higher?",
        "threshold": 18.0,
        "unit": "C",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": 16.0,
        "model_prob": model_prob,
        "edge": edge,
    }


def test_discovery_diagnostics_includes_bucket_and_evidence_counts():
    opp = make_opportunity()
    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [
            {"weather_evidence_valid": True},
            {"weather_evidence_valid": False},
        ],
        "opportunities": [opp],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
        "daily_skip_reasons": {
            "daily_date_mismatch": 2,
            "daily_min_hours_not_met": 1,
        },
        "daily_resolve_only": True,
        "daily_min_hours_to_resolve": 6,
    }

    diag = build_discovery_diagnostics(discovery)

    assert diag["evidence_valid"] == 1
    assert diag["evidence_invalid"] == 1
    assert diag["bucket_counts"]["enter_hold_candidate"] == 1
    assert "high_confidence_hold_candidate" in diag["bucket_reason_counts"]
    assert diag["ai_status_counts"]["off"] == 0
    assert diag["ai_status_counts"]["unknown"] == 2
    assert diag["daily_mode_enabled"] is True
    assert diag["daily_date_mismatch"] == 2
    assert diag["daily_min_hours_not_met"] == 1
    assert diag["daily_skipped_total"] == 3

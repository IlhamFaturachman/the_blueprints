from market_discovery import (
    build_discovery_diagnostics, decide_entry_bucket,
    collect_discovery_evidence_and_ai, collect_discovery_bucket_counts,
    classify_discovery_rejection_reason,
)
from market_discovery_internal.config import (
    PAPER_ENTRY_MIN_PRICE, PAPER_ENTRY_MAX_PRICE,
    THRESHOLD_RE, WEATHER_CONTEXT_RE, DIRECTION_CANDIDATE_RE,
)
from market_discovery_internal.parsing import _market_search_text, _has_target_city
from market_discovery_internal.diagnostics import analyze_discovery_raw_market


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

    diag = build_discovery_diagnostics(
        discovery,
        collect_discovery_evidence_and_ai_fn=collect_discovery_evidence_and_ai,
        collect_discovery_bucket_counts_fn=collect_discovery_bucket_counts,
        analyze_discovery_raw_market_fn=lambda raw: analyze_discovery_raw_market(
            raw,
            market_search_text_fn=_market_search_text,
            has_target_city_fn=_has_target_city,
            threshold_re=THRESHOLD_RE,
            weather_context_re=WEATHER_CONTEXT_RE,
            direction_candidate_re=DIRECTION_CANDIDATE_RE,
        ),
        classify_discovery_rejection_reason_fn=classify_discovery_rejection_reason,
        decide_entry_bucket_fn=decide_entry_bucket,
        paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
        paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
        daily_resolve_only=True,
        daily_min_hours_to_resolve=6,
    )

    assert diag["evidence_valid"] == 1
    assert diag["evidence_invalid"] == 1
    assert diag["bucket_counts"]["enter_hold_candidate"] == 1
    # The reason is now a descriptive string, not a short key
    assert any("HOLD" in reason for reason in diag["bucket_reason_counts"])
    assert diag["ai_status_counts"]["off"] == 0
    assert diag["ai_status_counts"]["unknown"] == 2
    assert diag["daily_mode_enabled"] is True
    assert diag["daily_date_mismatch"] == 2
    assert diag["daily_min_hours_not_met"] == 1
    assert diag["too_close_to_resolve"] == 0
    assert diag["daily_skipped_total"] == 3

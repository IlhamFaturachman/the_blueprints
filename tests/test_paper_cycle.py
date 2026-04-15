import json
from datetime import datetime, timezone
from unittest.mock import patch

from market_discovery import (
    build_paper_position,
    load_paper_state,
    print_paper_state_report,
    run_paper_trading_cycle,
    save_paper_state,
)


def make_opportunity(
    yes_price=0.25,
    token_id="0xabc",
    city="new york",
    model_prob=1.0,
    edge=0.75,
    hours_until_resolve=6.0,
):
    return {
        "city": city,
        "date": "2026-04-15",
        "end_date": "2026-04-15T20:00:00+00:00",
        "market_question": f"Will {city.title()} exceed 75F?",
        "threshold": 75.0,
        "unit": "F",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": hours_until_resolve,
        "model_prob": model_prob,
        "edge": edge,
    }


def test_load_paper_state_empty_when_file_missing(tmp_path):
    state_file = tmp_path / "paper_state.json"
    state = load_paper_state(path=str(state_file))
    assert state["positions"] == []
    assert state["history"] == []
    assert state["cycle_journal"] == []
    assert isinstance(state["meta"], dict)
    assert state["meta"]["base_wallet"] > 0
    assert "daily_session" in state["meta"]
    assert "auto_tuner" in state["meta"]


def test_save_then_load_paper_state_round_trip(tmp_path):
    state_file = tmp_path / "paper_state.json"
    initial = {
        "positions": [{"token_id": "0x1", "status": "open"}],
        "history": [{"token_id": "0x0", "status": "closed"}],
        "cycle_journal": [{"timestamp": "2026-04-13T00:00:00+00:00"}],
        "updated_at": "2026-04-13T00:00:00+00:00",
        "meta": {"empty_temperature_cycles": 0},
    }
    save_paper_state(initial, path=str(state_file))
    loaded = load_paper_state(path=str(state_file))
    assert loaded["positions"] == initial["positions"]
    assert loaded["history"] == initial["history"]
    assert loaded["cycle_journal"] == initial["cycle_journal"]
    assert loaded["updated_at"] == initial["updated_at"]
    assert loaded["meta"]["empty_temperature_cycles"] == 0
    assert loaded["meta"]["base_wallet"] > 0


def test_save_paper_state_atomic_does_not_leave_temp_files(tmp_path):
    state_file = tmp_path / "paper_state.json"
    save_paper_state({"positions": [], "history": [], "updated_at": None}, path=str(state_file))

    assert state_file.exists()
    assert list(tmp_path.glob(".paper_state_*.tmp")) == []


def test_cash_state_uses_configured_base_wallet_in_meta(tmp_path):
    state_file = tmp_path / "paper_state.json"

    with patch("market_discovery.PAPER_BASE_WALLET", 42.0), patch("market_discovery.PAPER_STAKE_USD", 2.0):
        save_paper_state({"positions": [], "history": [], "updated_at": None}, path=str(state_file))
        loaded = load_paper_state(path=str(state_file))

    assert loaded["meta"]["base_wallet"] == 42.0
    assert loaded["meta"]["current_wallet"] == 42.0
    assert loaded["meta"]["cash"] == 42.0
    assert loaded["meta"]["recommended_stake_usd"] == 8.0


def test_run_paper_cycle_opens_new_position(tmp_path):
    state_file = tmp_path / "paper_state.json"
    opp = make_opportunity(yes_price=0.25)

    discovery = {
        "markets_raw": [],
        "parsed": [opp],
        "enriched": [opp],
        "opportunities": [opp],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        return_value={"best_bid": 0.24, "best_ask": 0.25},
    ):
        cycle = run_paper_trading_cycle(
            min_price=0.20,
            max_price=0.35,
            stake_usd=100,
            state_path=str(state_file),
        )

    assert len(cycle["opened"]) == 1
    assert len(cycle["open_positions"]) == 1
    assert cycle["opened"][0]["token_id"] == "0xabc"
    assert cycle["opened"][0]["entry_price_source"] == "buy_ask"
    assert cycle["opened"][0]["entry_price"] == 0.25
    assert cycle["opened"][0]["entry_yes_reference"] == 0.25
    assert cycle["opened"][0]["entry_bucket"] in {"enter_swing", "enter_hold_candidate"}
    assert cycle["acceptance_metrics"]["opportunities_total"] == 1
    assert cycle["acceptance_metrics"]["opened_total"] == 1
    assert cycle["acceptance_metrics"]["opportunity_to_open_rate"] == 1.0
    assert isinstance(cycle.get("performance"), dict)
    assert cycle["performance"]["total_ms"] >= 0

    persisted = load_paper_state(path=str(state_file))
    assert len(persisted["cycle_journal"]) == 1
    assert persisted["meta"]["acceptance_metrics_rolling"]["cycles_total"] == 1
    assert persisted["meta"]["acceptance_metrics_rolling"]["opportunities_total"] == 1
    assert persisted["meta"]["acceptance_metrics_rolling"]["opened_total"] == 1
    assert isinstance(persisted["meta"].get("last_cycle_performance"), dict)


def test_run_paper_cycle_closes_position_on_take_profit(tmp_path):
    state_file = tmp_path / "paper_state.json"
    entry_opp = make_opportunity(yes_price=0.25, token_id="0xabc")
    open_position = build_paper_position(entry_opp, stake_usd=100)
    save_paper_state({"positions": [open_position], "history": [], "updated_at": None}, path=str(state_file))

    live_market = make_opportunity(yes_price=0.50, token_id="0xabc")
    discovery = {
        "markets_raw": [],
        "parsed": [live_market],
        "enriched": [live_market],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery._forecast_still_valid", return_value=True
    ), patch(
        "market_discovery.fetch_orderbook_quote",
        return_value={"best_bid": 0.50, "best_ask": 0.51},
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert len(cycle["closed"]) == 1
    assert cycle["closed"][0]["close_reason"] == "take_profit_100pct"


def test_run_paper_cycle_force_aggressive_scan_passes_through(tmp_path):
    state_file = tmp_path / "paper_state.json"
    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery) as mock_discovery, patch(
        "market_discovery.fetch_orderbook_quote",
        return_value=None,
    ):
        cycle = run_paper_trading_cycle(
            state_path=str(state_file),
            force_aggressive_scan=True,
        )

    assert cycle["used_aggressive_scan"] is True
    mock_discovery.assert_called_once()
    kwargs = mock_discovery.call_args.kwargs
    assert kwargs["inspect"] is False
    assert kwargs["aggressive_scan"] is True
    assert callable(kwargs.get("filter_opportunities_fn"))


def test_run_paper_cycle_auto_aggressive_scan_after_empty_cycles(tmp_path):
    state_file = tmp_path / "paper_state.json"
    save_paper_state(
        {
            "positions": [],
            "history": [],
            "updated_at": None,
            "meta": {"empty_temperature_cycles": 3},
        },
        path=str(state_file),
    )

    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        return_value=None,
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert cycle["used_aggressive_scan"] is True
    assert cycle["empty_temperature_cycles"] == 4

    persisted = load_paper_state(path=str(state_file))
    assert persisted["meta"]["empty_temperature_cycles"] == 4


def test_run_paper_cycle_rolling_metrics_accumulate_across_cycles(tmp_path):
    state_file = tmp_path / "paper_state.json"
    opp = make_opportunity(yes_price=0.25, token_id="0xroll")
    discovery = {
        "markets_raw": [],
        "parsed": [opp],
        "enriched": [opp],
        "opportunities": [opp],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", side_effect=[discovery, discovery]), patch(
        "market_discovery._forecast_still_valid", return_value=True
    ), patch(
        "market_discovery.fetch_orderbook_quote",
        side_effect=[
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
        ],
    ):
        first = run_paper_trading_cycle(state_path=str(state_file))
        second = run_paper_trading_cycle(state_path=str(state_file))

    assert first["acceptance_metrics"]["opened_total"] == 1
    assert second["acceptance_metrics"]["opened_total"] == 0

    rolling = second["rolling_acceptance_metrics"]
    assert rolling["cycles_total"] == 2
    assert rolling["opportunities_total"] == 2
    assert rolling["entry_candidates_total"] == 2
    assert rolling["opened_total"] == 1
    assert rolling["opportunity_to_open_rate"] == 0.5
    assert rolling["candidate_to_open_rate"] == 0.5

    persisted = load_paper_state(path=str(state_file))
    assert len(persisted["cycle_journal"]) == 2
    assert persisted["meta"]["acceptance_metrics_rolling"]["cycles_total"] == 2


def test_run_paper_cycle_selects_best_candidate_per_city_by_confidence(tmp_path):
    state_file = tmp_path / "paper_state.json"
    toronto_edge_heavier = make_opportunity(
        city="toronto",
        token_id="0xedge",
        yes_price=0.20,
        model_prob=0.80,
        edge=0.60,
        hours_until_resolve=12.0,
    )
    toronto_confidence_higher = make_opportunity(
        city="toronto",
        token_id="0xconf",
        yes_price=0.30,
        model_prob=0.85,
        edge=0.55,
        hours_until_resolve=12.0,
    )
    chicago = make_opportunity(city="chicago", token_id="0xchi")
    discovery = {
        "markets_raw": [],
        "parsed": [toronto_edge_heavier, toronto_confidence_higher, chicago],
        "enriched": [toronto_edge_heavier, toronto_confidence_higher, chicago],
        "opportunities": [toronto_edge_heavier, toronto_confidence_higher, chicago],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        side_effect=[
            {"best_bid": 0.19, "best_ask": 0.20},
            {"best_bid": 0.29, "best_ask": 0.30},
            {"best_bid": 0.24, "best_ask": 0.25},
        ],
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    opened_tokens = {position["token_id"] for position in cycle["opened"]}
    opened_toronto = [position for position in cycle["opened"] if position["city"] == "toronto"]

    assert "0xconf" in opened_tokens
    assert "0xedge" not in opened_tokens
    assert len(opened_toronto) == 1


def test_run_paper_cycle_skips_new_entry_for_city_with_existing_open_position(tmp_path):
    state_file = tmp_path / "paper_state.json"
    existing_new_york = build_paper_position(
        make_opportunity(city="new york", token_id="0xny_open"),
        stake_usd=100,
    )
    save_paper_state({"positions": [existing_new_york], "history": [], "updated_at": None}, path=str(state_file))

    new_york_candidate = make_opportunity(city="new york", token_id="0xny_new")
    chicago_candidate = make_opportunity(city="chicago", token_id="0xchi")
    discovery = {
        "markets_raw": [],
        "parsed": [new_york_candidate, chicago_candidate],
        "enriched": [new_york_candidate, chicago_candidate],
        "opportunities": [new_york_candidate, chicago_candidate],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        side_effect=[
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
        ],
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert len(cycle["opened"]) == 1
    assert cycle["opened"][0]["token_id"] == "0xchi"
    assert not any(position["token_id"] == "0xny_new" for position in cycle["opened"])
    assert {position["city"] for position in cycle["open_positions"]} == {"new york", "chicago"}


def test_run_paper_cycle_keeps_token_dedupe_with_city_filter(tmp_path):
    state_file = tmp_path / "paper_state.json"
    existing = build_paper_position(
        make_opportunity(city="new york", token_id="0xdup"),
        stake_usd=100,
    )
    save_paper_state({"positions": [existing], "history": [], "updated_at": None}, path=str(state_file))

    duplicate_token_other_city = make_opportunity(city="chicago", token_id="0xdup")
    unique_token = make_opportunity(city="london", token_id="0xlon")
    discovery = {
        "markets_raw": [],
        "parsed": [duplicate_token_other_city, unique_token],
        "enriched": [duplicate_token_other_city, unique_token],
        "opportunities": [duplicate_token_other_city, unique_token],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        side_effect=[
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
        ],
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert len(cycle["opened"]) == 1
    assert cycle["opened"][0]["token_id"] == "0xlon"


def test_run_paper_cycle_city_coverage_warns_below_target(tmp_path):
    state_file = tmp_path / "paper_state.json"
    opportunities = [
        make_opportunity(city="new york", token_id="0xny"),
        make_opportunity(city="chicago", token_id="0xchi"),
        make_opportunity(city="london", token_id="0xlon"),
    ]
    discovery = {
        "markets_raw": [],
        "parsed": opportunities,
        "enriched": opportunities,
        "opportunities": opportunities,
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.PAPER_MIN_CITY_DIVERSITY", 5), patch(
        "market_discovery.run_discovery_cycle", return_value=discovery
    ), patch(
        "market_discovery.fetch_orderbook_quote",
        side_effect=[
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
            {"best_bid": 0.24, "best_ask": 0.25},
        ],
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    city_metrics = cycle["city_coverage_metrics"]
    assert city_metrics["unique_opportunity_cities"] == 3
    assert city_metrics["target_met"] is False
    assert city_metrics["shortfall"] == 2
    assert city_metrics["warning"] is True
    assert cycle["rolling_city_coverage_metrics"]["cycles_below_target"] == 1


def test_run_paper_cycle_prefetches_open_position_forecasts(tmp_path):
    state_file = tmp_path / "paper_state.json"
    existing_positions = [
        build_paper_position(make_opportunity(city="new york", token_id="0xny"), stake_usd=100),
        build_paper_position(make_opportunity(city="chicago", token_id="0xchi"), stake_usd=100),
        build_paper_position(make_opportunity(city="toronto", token_id="0xto"), stake_usd=100),
    ]
    save_paper_state(
        {
            "positions": existing_positions,
            "history": [],
            "updated_at": None,
            "meta": {},
        },
        path=str(state_file),
    )

    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS", 1), patch(
        "market_discovery.PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS", 2
    ), patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_forecast", return_value=25.0
    ) as mock_forecast:
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert mock_forecast.call_count == 3
    performance = cycle.get("performance", {})
    position_cache = performance.get("position_forecast_cache", {})
    position_prefetch = performance.get("position_forecast_prefetch", {})

    assert position_cache.get("size") == 3
    assert position_cache.get("hits") == 0
    assert position_cache.get("misses") == 0
    assert position_prefetch.get("eligible") == 3
    assert position_prefetch.get("attempted") == 3
    assert position_prefetch.get("successful") == 3
    assert position_prefetch.get("failed") == 0
    assert position_prefetch.get("workers") == 2
    assert position_prefetch.get("skipped") is False


def test_run_paper_cycle_skips_entry_when_best_ask_missing(tmp_path):
    state_file = tmp_path / "paper_state.json"
    opp = make_opportunity(yes_price=0.25, token_id="0xskip")
    discovery = {
        "markets_raw": [],
        "parsed": [opp],
        "enriched": [opp],
        "opportunities": [opp],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery), patch(
        "market_discovery.fetch_orderbook_quote",
        return_value={"best_bid": 0.24, "best_ask": None},
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert cycle["opened"] == []
    assert cycle["open_positions"] == []


def test_print_paper_state_report_json_output(capsys):
    state = {
        "positions": [{"token_id": "0xopen", "status": "open"}],
        "history": [{"token_id": "0xclosed", "status": "closed"}],
        "cycle_journal": [
            {
                "timestamp": "2026-04-10T01:00:00+00:00",
                "counts": {"opportunities": 2, "entry_candidates": 1, "opened": 1, "closed": 0},
                "acceptance_rates": {"opportunity_to_open_rate": 0.5},
            },
            {
                "timestamp": "2026-04-14T01:05:00+00:00",
                "counts": {"opportunities": 3, "entry_candidates": 1, "opened": 1, "closed": 1},
                "acceptance_rates": {"opportunity_to_open_rate": 0.3333},
            },
        ],
        "updated_at": "2026-04-14T01:05:00+00:00",
        "meta": {
            "acceptance_metrics_rolling": {
                "cycles_total": 5,
                "opportunities_total": 5,
                "opened_total": 2,
                "closed_total": 1,
                "opportunity_to_open_rate": 0.4,
            },
            "city_coverage_rolling": {
                "cycles_total": 5,
                "min_city_target": 5,
                "cycles_below_target": 2,
                "avg_opportunity_cities": 3.2,
                "avg_opened_cities": 1.8,
            },
            "last_cycle_performance": {
                "total_ms": 123.456,
                "state_load_ms": 1.1,
                "discovery_ms": 90.0,
                "position_management_ms": 10.0,
                "entry_selection_ms": 5.0,
                "metrics_persist_ms": 6.0,
                "position_forecast_cache": {"size": 1, "hits": 3, "misses": 2},
            },
        },
    }

    print_paper_state_report(
        state=state,
        state_path="logs/test_paper_state.json",
        recent_entries=1,
        output_format="json",
        now_utc=datetime(2026, 4, 14, 2, 0, tzinfo=timezone.utc),
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["state_path"] == "logs/test_paper_state.json"
    assert payload["open_positions_count"] == 1
    assert payload["closed_history_count"] == 1
    assert payload["journal_entries_count"] == 2
    assert payload["anomaly_counters"]["current_zero_opportunity_streak"] == 0
    assert payload["anomaly_counters"]["current_reject_dominant_streak"] == 0
    assert payload["journal_retention"]["recent_entries_shown"] == 1
    assert payload["journal_retention"]["older_entries_not_shown"] == 1
    assert payload["journal_retention"]["journal_capacity_limit"] >= 1
    assert payload["journal_retention"]["age_buckets"]["lt_24h"] == 1
    assert payload["journal_retention"]["age_buckets"]["gt_72h"] == 1
    assert payload["journal_retention"]["rotation_summary"]["policy"] == "rolling_tail_keep_latest"
    assert payload["journal_retention"]["rotation_summary"]["estimated_entries_rotated_out"] == 3
    assert payload["rolling_acceptance_metrics"]["cycles_total"] == 5
    assert payload["rolling_city_coverage_metrics"]["cycles_total"] == 5
    assert payload["rolling_city_coverage_metrics"]["cycles_below_target"] == 2
    assert payload["last_cycle_performance"]["total_ms"] == 123.456
    assert payload["last_cycle_performance"]["position_forecast_cache"]["hits"] == 3
    assert len(payload["recent_journal"]) == 1
    assert payload["recent_journal"][0]["timestamp"] == "2026-04-14T01:05:00+00:00"


def test_print_paper_state_report_json_anomaly_alerts(capsys):
    state = {
        "positions": [],
        "history": [],
        "cycle_journal": [
            {
                "timestamp": "2026-04-14T00:00:00+00:00",
                "counts": {"opportunities": 2},
                "bucket_counts": {"reject": 2, "watchlist": 0, "enter_swing": 0, "enter_hold_candidate": 0},
            },
            {
                "timestamp": "2026-04-14T01:00:00+00:00",
                "counts": {"opportunities": 3},
                "bucket_counts": {"reject": 3, "watchlist": 0, "enter_swing": 0, "enter_hold_candidate": 0},
            },
            {
                "timestamp": "2026-04-14T02:00:00+00:00",
                "counts": {"opportunities": 4},
                "bucket_counts": {"reject": 4, "watchlist": 0, "enter_swing": 0, "enter_hold_candidate": 0},
            },
        ],
        "updated_at": "2026-04-14T02:00:00+00:00",
        "meta": {},
    }

    with patch("market_discovery.PAPER_REPORT_ANOMALY_STREAK_ALERT", 3), patch(
        "market_discovery.PAPER_REPORT_REJECT_DOMINANT_RATIO", 0.8
    ):
        print_paper_state_report(
            state=state,
            state_path="logs/test_anomaly_state.json",
            recent_entries=3,
            output_format="json",
            now_utc=datetime(2026, 4, 14, 3, 0, tzinfo=timezone.utc),
        )

    payload = json.loads(capsys.readouterr().out)
    anomaly = payload["anomaly_counters"]

    assert anomaly["current_reject_dominant_streak"] == 3
    assert anomaly["max_reject_dominant_streak"] == 3
    assert anomaly["current_zero_opportunity_streak"] == 0
    assert anomaly["streak_alert_threshold"] == 3
    assert anomaly["reject_dominant_ratio_threshold"] == 0.8
    assert anomaly["latest_reject_ratio"] == 1.0
    assert anomaly["alerts"]["reject_dominant_streak"] is True
    assert anomaly["alerts"]["zero_opportunity_streak"] is False

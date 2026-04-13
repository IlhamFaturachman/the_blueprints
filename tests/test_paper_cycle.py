from unittest.mock import patch

from market_discovery import (
    build_paper_position,
    load_paper_state,
    run_paper_trading_cycle,
    save_paper_state,
)


def make_opportunity(yes_price=0.25, token_id="0xabc"):
    return {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T20:00:00+00:00",
        "market_question": "Will New York exceed 75F?",
        "threshold": 75.0,
        "unit": "F",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": 6.0,
        "model_prob": 1.0,
        "edge": 0.75,
    }


def test_load_paper_state_empty_when_file_missing(tmp_path):
    state_file = tmp_path / "paper_state.json"
    state = load_paper_state(path=str(state_file))
    assert state["positions"] == []
    assert state["history"] == []


def test_save_then_load_paper_state_round_trip(tmp_path):
    state_file = tmp_path / "paper_state.json"
    initial = {
        "positions": [{"token_id": "0x1", "status": "open"}],
        "history": [{"token_id": "0x0", "status": "closed"}],
        "updated_at": "2026-04-13T00:00:00+00:00",
    }
    save_paper_state(initial, path=str(state_file))
    loaded = load_paper_state(path=str(state_file))
    assert loaded == initial


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

    with patch("market_discovery.run_discovery_cycle", return_value=discovery):
        cycle = run_paper_trading_cycle(
            min_price=0.20,
            max_price=0.35,
            stake_usd=100,
            state_path=str(state_file),
        )

    assert len(cycle["opened"]) == 1
    assert len(cycle["open_positions"]) == 1
    assert cycle["opened"][0]["token_id"] == "0xabc"


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
    ):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert len(cycle["closed"]) == 1
    assert cycle["closed"][0]["close_reason"] == "take_profit_band"


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

    with patch("market_discovery.run_discovery_cycle", return_value=discovery) as mock_discovery:
        cycle = run_paper_trading_cycle(
            state_path=str(state_file),
            force_aggressive_scan=True,
        )

    assert cycle["used_aggressive_scan"] is True
    mock_discovery.assert_called_once_with(inspect=False, aggressive_scan=True)


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

    with patch("market_discovery.run_discovery_cycle", return_value=discovery):
        cycle = run_paper_trading_cycle(state_path=str(state_file))

    assert cycle["used_aggressive_scan"] is True
    assert cycle["empty_temperature_cycles"] == 4

    persisted = load_paper_state(path=str(state_file))
    assert persisted["meta"]["empty_temperature_cycles"] == 4

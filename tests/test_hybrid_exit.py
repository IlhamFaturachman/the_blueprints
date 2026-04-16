from market_discovery_internal.cycles import (
    build_paper_position,
    evaluate_hybrid_exit,
    update_paper_position,
)


def make_opportunity(yes_price=0.25, hours_until_resolve=8.0):
    return {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T20:00:00+00:00",
        "market_question": "Will New York exceed 75F?",
        "threshold": 75.0,
        "unit": "F",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": hours_until_resolve,
    }


def test_build_position_sets_take_profit_to_100pct_target():
    position = build_paper_position(make_opportunity(yes_price=0.20), stake_usd=100)
    assert position["target_price"] == 0.4
    assert position["target_policy"] == "take_profit_100pct"


def test_build_position_uses_entry_price_override_as_buy_ask():
    opp = make_opportunity(yes_price=0.20)
    opp["entry_price"] = 0.24
    opp["entry_price_source"] = "buy_ask"
    opp["entry_quote_best_bid"] = 0.22
    opp["entry_quote_best_ask"] = 0.24

    position = build_paper_position(opp, stake_usd=100)

    assert position["entry_price"] == 0.24
    assert position["entry_price_source"] == "buy_ask"
    assert position["entry_yes_reference"] == 0.20
    assert position["entry_quote_best_bid"] == 0.22
    assert position["entry_quote_best_ask"] == 0.24
    assert position["last_price"] == 0.22


def test_take_profit_100pct_exit():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.50,
        forecast_still_valid=True,
        hours_until_resolve=5,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "take_profit_100pct"


def test_stop_loss_exit():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.12,
        forecast_still_valid=True,
        hours_until_resolve=5,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "stop_loss"


def test_hybrid_holds_to_resolve_when_late_and_forecast_valid():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.48,
        forecast_still_valid=True,
        hours_until_resolve=1,
        confidence_score=0.90,
    )
    assert decision["action"] == "hold_to_resolve"
    assert decision["reason"] == "late_window_confidence_pass"


def test_hybrid_exits_when_late_and_forecast_invalid():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.48,
        forecast_still_valid=False,
        hours_until_resolve=1,
        confidence_score=0.95,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "late_window_forecast_invalid"


def test_hybrid_exits_when_late_and_confidence_below_minimum():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.48,
        forecast_still_valid=True,
        hours_until_resolve=1,
        confidence_score=0.60,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "late_window_confidence_below_min"


def test_holds_while_waiting_target():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.35,
        forecast_still_valid=True,
        hours_until_resolve=6,
    )
    assert decision["action"] == "hold"
    assert decision["reason"] == "await_target"


def test_update_paper_position_closes_and_calculates_pnl():
    position = build_paper_position(make_opportunity(yes_price=0.25), stake_usd=100)
    updated, decision = update_paper_position(
        position=position,
        current_yes_price=0.50,
        forecast_still_valid=True,
        hours_until_resolve=4,
    )

    assert decision["action"] == "sell"
    assert updated["status"] == "closed"
    assert updated["close_reason"] == "take_profit_100pct"
    assert updated["realized_pnl_usd"] == 100.0
    assert updated["realized_roi_pct"] == 100.0

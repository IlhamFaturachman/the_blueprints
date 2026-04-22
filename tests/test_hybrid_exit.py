"""Tests for build_paper_position, evaluate_hybrid_exit, update_paper_position.

Key contracts:
- build_paper_position: when PREFER_MAKER_ORDERS=True, no slippage (effective_price = entry_price)
  when PREFER_MAKER_ORDERS=False, 1% slippage (effective_price = entry_price * 1.01)
- target_price = effective_price * 2.0 (100% TP)
- check_liquidity_depth is always mocked True (test isolation — no live API)
- stop_loss = effective_price * HYBRID_STOP_LOSS_MULTIPLIER (0.48 default)
"""
from unittest.mock import patch

from market_discovery_internal.cycles import (
    build_paper_position,
    evaluate_hybrid_exit,
    update_paper_position,
)

# ---- constant: PREFER_MAKER_ORDERS=True → no slippage (1.0) ----
# When PREFER_MAKER_ORDERS=False, this would be 1.01
_SLIP = 1.0


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


def _build(yes_price=0.25, stake_usd=100, **opp_kwargs):
    """Helper: build position with liquidity check mocked as True."""
    opp = make_opportunity(yes_price=yes_price, **opp_kwargs)
    with patch("market_discovery_internal.pricing.check_liquidity_depth", return_value=True):
        return build_paper_position(opp, stake_usd=stake_usd)


def test_build_position_sets_take_profit_to_100pct_target():
    position = _build(yes_price=0.20)
    ep = round(0.20 * _SLIP, 4)          # 0.202
    assert position["entry_price"] == ep
    assert position["target_price"] == round(ep * 2.0, 4)   # 0.404
    assert position["target_policy"] == "take_profit_100pct"


def test_build_position_uses_entry_price_override_as_buy_ask():
    opp = make_opportunity(yes_price=0.20)
    opp["entry_price"] = 0.24
    opp["entry_price_source"] = "buy_ask"
    opp["entry_quote_best_bid"] = 0.22
    opp["entry_quote_best_ask"] = 0.24

    with patch("market_discovery_internal.pricing.check_liquidity_depth", return_value=True):
        position = build_paper_position(opp, stake_usd=100)

    ep = round(0.24 * _SLIP, 4)   # 0.2424
    assert position["entry_price"] == ep
    assert position["entry_price_source"] == "buy_ask"
    assert position["entry_yes_reference"] == 0.20
    assert position["entry_quote_best_bid"] == 0.22
    assert position["entry_quote_best_ask"] == 0.24
    assert position["last_price"] == 0.22


def test_take_profit_100pct_exit():
    position = _build(yes_price=0.25)
    target = position["target_price"]   # 0.2525 * 2 = 0.505
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=target + 0.01,  # just above target
        forecast_still_valid=True,
        hours_until_resolve=5,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "take_profit_100pct"


def test_stop_loss_exit():
    position = _build(yes_price=0.25)
    sl = position["stop_loss_price"]  # 0.2525 * 0.48 ≈ 0.1212
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=sl - 0.001,  # just below stop_loss
        forecast_still_valid=True,
        hours_until_resolve=5,
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "stop_loss"


def test_hybrid_holds_to_resolve_when_late_and_forecast_valid():
    position = _build(yes_price=0.25)
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
    position = _build(yes_price=0.25)
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
    position = _build(yes_price=0.25)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.48,
        forecast_still_valid=True,
        hours_until_resolve=1,
        confidence_score=0.50,  # Below HYBRID_MIN_CONFIDENCE_TO_HOLD (0.55)
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "late_window_confidence_below_min"


def test_hybrid_exits_on_thesis_decay():
    """In late window with very low confidence → thesis_decay_exit."""
    position = _build(yes_price=0.25)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.30,
        forecast_still_valid=True,
        hours_until_resolve=1,
        confidence_score=0.30,  # Below THESIS_DECAY_THRESHOLD (0.35)
    )
    assert decision["action"] == "sell"
    assert decision["reason"] == "thesis_decay_exit"


def test_holds_while_waiting_target():
    position = _build(yes_price=0.25)
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=0.35,
        forecast_still_valid=True,
        hours_until_resolve=6,
    )
    assert decision["action"] == "hold"
    assert decision["reason"] == "await_target"


def test_update_paper_position_closes_and_calculates_pnl():
    position = _build(yes_price=0.25)
    target = position["target_price"]   # 0.2525 * 2 = 0.505

    with patch("market_discovery_internal.pricing.check_liquidity_depth", return_value=True), \
         patch("market_discovery_internal.cycles.db"):
        updated, decision = update_paper_position(
            position=position,
            current_yes_price=target + 0.01,
            forecast_still_valid=True,
            hours_until_resolve=4,
        )

    assert decision["action"] == "sell"
    assert updated["status"] == "closed"
    assert updated["close_reason"] == "take_profit_100pct"
    assert updated["realized_roi_pct"] > 80.0  # sold at/above 2x target (maker fee=0% for TP exit)


import pytest  # noqa: E402 (intentional late import to keep module readable)

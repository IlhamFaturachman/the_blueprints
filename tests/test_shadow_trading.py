# tests/test_shadow_trading.py
import pytest
from datetime import datetime, timezone


def test_shadow_buy_triggers_on_lock():
    from scripts.shadow_trading import evaluate_shadow_entry
    result = evaluate_shadow_entry(
        lock_probability=0.90, winning_bracket_price=0.15,
        min_lock_prob=0.85, entry_min=0.10, entry_max=0.30,
    )
    assert result["should_buy"] is True
    assert result["entry_price"] == 0.15


def test_shadow_buy_skips_low_lock():
    from scripts.shadow_trading import evaluate_shadow_entry
    result = evaluate_shadow_entry(0.70, 0.15, 0.85, 0.10, 0.30)
    assert result["should_buy"] is False


def test_shadow_buy_skips_price_out_of_range():
    from scripts.shadow_trading import evaluate_shadow_entry
    result = evaluate_shadow_entry(0.90, 0.05, 0.85, 0.10, 0.30)
    assert result["should_buy"] is False
    result2 = evaluate_shadow_entry(0.90, 0.35, 0.85, 0.10, 0.30)
    assert result2["should_buy"] is False


def test_shadow_tp_triggers():
    from scripts.shadow_trading import evaluate_shadow_exit
    result = evaluate_shadow_exit(
        current_bid=0.30, entry_price=0.15, tp_mult=2.0, sl_mult=0.25,
    )
    assert result["action"] == "tp"
    assert result["exit_price"] == 0.30


def test_shadow_sl_triggers():
    from scripts.shadow_trading import evaluate_shadow_exit
    result = evaluate_shadow_exit(
        current_bid=0.03, entry_price=0.15, tp_mult=2.0, sl_mult=0.25,
    )
    assert result["action"] == "sl"
    assert result["exit_price"] == 0.03


def test_shadow_hold():
    from scripts.shadow_trading import evaluate_shadow_exit
    result = evaluate_shadow_exit(
        current_bid=0.20, entry_price=0.15, tp_mult=2.0, sl_mult=0.25,
    )
    assert result["action"] == "hold"

"""Unit tests for ws_price_watcher — no real WebSocket connection needed."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from market_discovery_internal.ws_price_watcher import PriceWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_best_bid_ask(asset_id, best_bid, best_ask="0.99"):
    return json.dumps({
        "event_type": "best_bid_ask",
        "asset_id": asset_id,
        "best_bid": str(best_bid),
        "best_ask": str(best_ask),
    })


def make_price_change(asset_id, price, best_bid=None):
    return json.dumps({
        "event_type": "price_change",
        "price_changes": [
            {
                "asset_id": asset_id,
                "price": str(price),
                "best_bid": str(best_bid if best_bid is not None else price),
            }
        ],
    })


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------

def test_best_bid_ask_calls_callback():
    """best_bid_ask event fires callback with (token_id, float(best_bid))."""
    received = []
    watcher = PriceWatcher(
        url="wss://fake",
        on_price_update=lambda tid, price: received.append((tid, price)),
    )
    watcher._handle_message(make_best_bid_ask("tok1", "0.18"))
    assert received == [("tok1", 0.18)]


def test_price_change_calls_callback():
    """price_change event fires callback using best_bid field."""
    received = []
    watcher = PriceWatcher(
        url="wss://fake",
        on_price_update=lambda tid, price: received.append((tid, price)),
    )
    watcher._handle_message(make_price_change("tok2", "0.22", best_bid="0.21"))
    assert received == [("tok2", 0.21)]


def test_price_change_multiple_assets_fires_all():
    """price_change with multiple assets fires callback for each."""
    received = []
    msg = json.dumps({
        "event_type": "price_change",
        "price_changes": [
            {"asset_id": "tokA", "price": "0.10", "best_bid": "0.09"},
            {"asset_id": "tokB", "price": "0.50", "best_bid": "0.49"},
        ],
    })
    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: received.append((tid, price)))
    watcher._handle_message(msg)
    assert ("tokA", 0.09) in received
    assert ("tokB", 0.49) in received


def test_unknown_event_type_ignored():
    """Events with unknown event_type do not raise and do not call callback."""
    received = []
    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: received.append((tid, price)))
    watcher._handle_message(json.dumps({"event_type": "last_trade_price", "asset_id": "tok1", "price": "0.5"}))
    assert received == []


def test_malformed_json_ignored():
    """Malformed JSON does not crash the watcher."""
    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: None)
    watcher._handle_message("not json at all{{{")  # should not raise


def test_missing_best_bid_falls_back_to_price():
    """price_change without best_bid uses price field as fallback."""
    received = []
    msg = json.dumps({
        "event_type": "price_change",
        "price_changes": [{"asset_id": "tokX", "price": "0.30"}],
    })
    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: received.append((tid, price)))
    watcher._handle_message(msg)
    assert received == [("tokX", 0.30)]


# ---------------------------------------------------------------------------
# update_subscriptions
# ---------------------------------------------------------------------------

def test_update_subscriptions_tracks_current_set():
    """update_subscriptions stores the provided token_ids."""
    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: None)
    watcher.update_subscriptions({"tok1", "tok2"})
    assert watcher._desired == {"tok1", "tok2"}


def test_update_subscriptions_sends_subscribe_for_new_tokens():
    """update_subscriptions sends subscribe message for newly added tokens."""
    sent = []
    ws_mock = MagicMock()
    ws_mock.send = lambda msg: sent.append(json.loads(msg))

    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: None)
    watcher._ws = ws_mock
    watcher._subscribed = {"existing_tok"}
    watcher._desired = {"existing_tok"}

    watcher.update_subscriptions({"existing_tok", "new_tok"})

    subscribe_msgs = [m for m in sent if m.get("operation") == "subscribe"]
    assert len(subscribe_msgs) == 1
    assert "new_tok" in subscribe_msgs[0]["assets_ids"]


def test_update_subscriptions_sends_unsubscribe_for_removed_tokens():
    """update_subscriptions sends unsubscribe for tokens no longer needed."""
    sent = []
    ws_mock = MagicMock()
    ws_mock.send = lambda msg: sent.append(json.loads(msg))

    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: None)
    watcher._ws = ws_mock
    watcher._subscribed = {"old_tok", "keep_tok"}
    watcher._desired = {"old_tok", "keep_tok"}

    watcher.update_subscriptions({"keep_tok"})

    unsub_msgs = [m for m in sent if m.get("operation") == "unsubscribe"]
    assert len(unsub_msgs) == 1
    assert "old_tok" in unsub_msgs[0]["assets_ids"]


def test_update_subscriptions_no_message_when_no_change():
    """update_subscriptions does not send messages when set is unchanged."""
    sent = []
    ws_mock = MagicMock()
    ws_mock.send = lambda msg: sent.append(msg)

    watcher = PriceWatcher(url="wss://fake", on_price_update=lambda tid, price: None)
    watcher._ws = ws_mock
    watcher._subscribed = {"tok1"}
    watcher._desired = {"tok1"}

    watcher.update_subscriptions({"tok1"})
    assert sent == []


# ---------------------------------------------------------------------------
# _ws_on_price_update callback (integration with state)
# ---------------------------------------------------------------------------

import os
import tempfile
from market_discovery import build_paper_position, save_paper_state, load_paper_state
from market_discovery_internal.ws_price_watcher import make_ws_exit_callback


def _make_open_position(token_id="tok1", entry_price=0.25, city="new york"):
    opp = {
        "city": city,
        "date": "2026-04-16",
        "end_date": "2026-04-16T20:00:00+00:00",
        "market_question": f"Test {city}",
        "threshold": 75.0,
        "unit": "F",
        "direction": "above",
        "yes_price": entry_price,
        "token_id": token_id,
        "hours_until_resolve": 6.0,
        "model_prob": 1.0,
        "edge": 0.75,
    }
    return build_paper_position(opp, stake_usd=100)


def _write_state_with_position(state_path, position):
    state = {
        "positions": [position],
        "history": [],
        "cycle_journal": [],
        "updated_at": None,
        "meta": {},
    }
    save_paper_state(state, state_path)


def test_ws_callback_closes_on_stop_loss():
    """WS callback closes position when price hits stop-loss."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_sl", entry_price=0.25)
        # stop_loss_price = 0.25 * 0.48 = 0.12
        stop_loss_price = pos["stop_loss_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)
        callback("tok_sl", stop_loss_price - 0.01)  # price below stop-loss

        state = load_paper_state(state_path)
        closed = [p for p in state.get("history", []) if p.get("token_id") == "tok_sl"]
        assert len(closed) == 1
        assert closed[0]["status"] == "closed"
        assert closed[0]["close_reason"] == "stop_loss"


def test_ws_callback_closes_on_take_profit():
    """WS callback closes position when price hits take-profit target."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_tp", entry_price=0.25)
        # target_price = min(0.25 * 2.0, 1.0) = 0.50
        target_price = pos["target_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)
        callback("tok_tp", target_price + 0.01)  # price above take-profit

        state = load_paper_state(state_path)
        closed = [p for p in state.get("history", []) if p.get("token_id") == "tok_tp"]
        assert len(closed) == 1
        assert closed[0]["close_reason"] == "take_profit_100pct"


def test_ws_callback_does_nothing_between_sl_and_tp():
    """WS callback does not close position when price is between stop-loss and take-profit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_hold", entry_price=0.25)
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)
        callback("tok_hold", 0.30)  # between 0.12 (SL) and 0.50 (TP)

        state = load_paper_state(state_path)
        assert len(state.get("positions", [])) == 1
        assert state["positions"][0]["status"] == "open"


def test_ws_callback_ignores_unknown_token():
    """WS callback for a token_id not in state does nothing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_known")
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)
        callback("tok_unknown", 0.01)  # should not raise or corrupt state

        state = load_paper_state(state_path)
        assert len(state.get("positions", [])) == 1

"""Unit tests for ws_price_watcher — no real WebSocket connection needed."""

import json
import logging
import multiprocessing
import os
import queue
import tempfile
import threading
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from market_discovery_internal.ws_price_watcher import PriceWatcher, make_ws_exit_callback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_watcher():
    """Create a PriceWatcher with in-process queues and mock logger for testing.

    Uses queue.Queue (thread-safe, synchronous) instead of multiprocessing.Queue
    to avoid cross-process buffering delays in single-process test execution.
    """
    mp_q = multiprocessing.Queue()  # needed for constructor signature
    watcher = PriceWatcher(url="wss://fake", update_queue=mp_q)
    # Replace with in-process queues for immediate put/get in tests
    watcher._update_queue = queue.Queue()
    watcher._sub_update_queue = queue.Queue()
    # Set up internal logger that _on_message expects (normally set in _run_loop)
    watcher._ilogger = logging.getLogger("test_price_watcher")
    watcher._ilogger.setLevel(logging.DEBUG)
    watcher._last_msg_at = time.time()
    return watcher


def _drain_queue(q):
    """Drain all items from a queue.Queue and return as list."""
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except (queue.Empty, Exception):
            break
    return items


def make_best_bid_ask(asset_id, best_bid, best_ask="0.99"):
    return json.dumps({
        "event_type": "best_bid_ask",
        "asset_id": asset_id,
        "best_bid": str(best_bid),
        "best_ask": str(best_ask),
    })


def make_price_change(asset_id, price, best_bid=None):
    """Build a price_change JSON message.

    The current implementation requires ``asset_id`` at the top level of the
    event (used as a gate before the price_changes loop), so we include it.
    """
    pc_entry = {"asset_id": asset_id, "price": str(price)}
    if best_bid is not None:
        pc_entry["best_bid"] = str(best_bid)
    return json.dumps({
        "event_type": "price_change",
        "asset_id": asset_id,
        "price_changes": [pc_entry],
    })


# ---------------------------------------------------------------------------
# _on_message tests
# ---------------------------------------------------------------------------

def test_best_bid_ask_calls_callback():
    """best_bid_ask event puts (token_id, float(best_bid)) on the queue."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_best_bid_ask("tok1", "0.18"))
    items = _drain_queue(watcher._update_queue)
    assert ("tok1", 0.18) in items


def test_price_change_calls_callback():
    """price_change event puts (token_id, float(best_bid)) on the queue."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_price_change("tok2", "0.22", best_bid="0.21"))
    items = _drain_queue(watcher._update_queue)
    assert ("tok2", 0.21) in items


def test_price_change_multiple_assets_fires_all():
    """price_change with multiple assets puts each on the queue."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    msg = json.dumps({
        "event_type": "price_change",
        "asset_id": "tokA",  # top-level asset_id required by implementation
        "price_changes": [
            {"asset_id": "tokA", "price": "0.10", "best_bid": "0.09"},
            {"asset_id": "tokB", "price": "0.50", "best_bid": "0.49"},
        ],
    })
    watcher._on_message(mock_ws, msg)
    items = _drain_queue(watcher._update_queue)
    assert ("tokA", 0.09) in items
    assert ("tokB", 0.49) in items


def test_unknown_event_type_ignored():
    """Events with unrecognized event_type do not put anything on the queue.

    Note: The current implementation handles last_trade_price, so we use a
    truly unknown type here.
    """
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, json.dumps({"event_type": "some_unknown_type", "asset_id": "tok1", "price": "0.5"}))
    items = _drain_queue(watcher._update_queue)
    assert items == []


def test_last_trade_price_puts_on_queue():
    """last_trade_price event is now handled and puts price on queue."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, json.dumps({
        "event_type": "last_trade_price",
        "asset_id": "tok_ltp",
        "price": "0.55",
    }))
    items = _drain_queue(watcher._update_queue)
    assert ("tok_ltp", 0.55) in items


def test_list_payload_calls_callback():
    """Polymarket may send a list of events in one message."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    msg = json.dumps([
        {
            "event_type": "best_bid_ask",
            "asset_id": "tok_list",
            "best_bid": "0.42",
            "best_ask": "0.43",
        }
    ])
    watcher._on_message(mock_ws, msg)
    items = _drain_queue(watcher._update_queue)
    assert ("tok_list", 0.42) in items


def test_list_payload_ignores_non_dict_entries():
    """Mixed list payload should ignore non-dict items and process valid events."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    msg = json.dumps([
        "heartbeat",
        123,
        {
            "event_type": "price_change",
            "asset_id": "tok_list_2",
            "price_changes": [{"asset_id": "tok_list_2", "price": "0.11", "best_bid": "0.10"}],
        },
    ])
    watcher._on_message(mock_ws, msg)
    items = _drain_queue(watcher._update_queue)
    assert ("tok_list_2", 0.10) in items


def test_malformed_json_ignored():
    """Malformed JSON does not crash the watcher."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, "not json at all{{{")  # should not raise


def test_missing_best_bid_falls_back_to_price():
    """price_change without best_bid uses price field as fallback."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    msg = json.dumps({
        "event_type": "price_change",
        "asset_id": "tokX",
        "price_changes": [{"asset_id": "tokX", "price": "0.30"}],
    })
    watcher._on_message(mock_ws, msg)
    items = _drain_queue(watcher._update_queue)
    assert ("tokX", 0.30) in items


def test_price_change_uses_zero_best_bid_without_fallback():
    """best_bid='0' is a valid value (0.0) and should be preserved."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    msg = json.dumps({
        "event_type": "price_change",
        "asset_id": "tokZ",
        "price_changes": [{"asset_id": "tokZ", "price": "0.30", "best_bid": "0"}],
    })
    watcher._on_message(mock_ws, msg)
    items = _drain_queue(watcher._update_queue)
    assert ("tokZ", 0.0) in items


def test_price_filter_rejects_above_one():
    """Prices > 1.0 are filtered out for best_bid_ask events."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_best_bid_ask("tok_high", "1.5"))
    items = _drain_queue(watcher._update_queue)
    assert items == []


def test_price_filter_rejects_negative():
    """Negative prices are filtered out."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_best_bid_ask("tok_neg", "-0.1"))
    items = _drain_queue(watcher._update_queue)
    assert items == []


def test_price_filter_accepts_zero():
    """Price of exactly 0.0 is accepted."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_best_bid_ask("tok_zero", "0.0"))
    items = _drain_queue(watcher._update_queue)
    assert ("tok_zero", 0.0) in items


def test_price_filter_accepts_one():
    """Price of exactly 1.0 is accepted."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_message(mock_ws, make_best_bid_ask("tok_one", "1.0"))
    items = _drain_queue(watcher._update_queue)
    assert ("tok_one", 1.0) in items


# ---------------------------------------------------------------------------
# update_subscriptions
# ---------------------------------------------------------------------------

def test_update_subscriptions_tracks_current_set():
    """update_subscriptions puts the token list on _sub_update_queue."""
    watcher = _make_watcher()
    watcher.update_subscriptions({"tok1", "tok2"})
    items = _drain_queue(watcher._sub_update_queue)
    assert len(items) == 1
    assert set(items[0]) == {"tok1", "tok2"}


def test_update_subscriptions_sends_list_to_queue():
    """update_subscriptions converts set to list and puts on _sub_update_queue."""
    watcher = _make_watcher()
    watcher.update_subscriptions({"tokA", "tokB", "tokC"})
    items = _drain_queue(watcher._sub_update_queue)
    assert len(items) == 1
    assert isinstance(items[0], list)
    assert set(items[0]) == {"tokA", "tokB", "tokC"}


def test_update_subscriptions_multiple_calls_queue_all():
    """Multiple update_subscriptions calls each put an item on the queue."""
    watcher = _make_watcher()
    watcher.update_subscriptions({"tok1"})
    watcher.update_subscriptions({"tok2", "tok3"})
    items = _drain_queue(watcher._sub_update_queue)
    assert len(items) == 2
    assert set(items[0]) == {"tok1"}
    assert set(items[1]) == {"tok2", "tok3"}


def test_update_subscriptions_empty_set():
    """update_subscriptions with empty set puts empty list on queue."""
    watcher = _make_watcher()
    watcher.update_subscriptions(set())
    items = _drain_queue(watcher._sub_update_queue)
    assert len(items) == 1
    assert items[0] == []


# ---------------------------------------------------------------------------
# _on_open / _on_close
# ---------------------------------------------------------------------------

def test_on_open_does_not_crash():
    """_on_open should not raise when called with a mock ws."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_open(mock_ws)  # should not raise


def test_on_close_does_not_crash():
    """_on_close should not raise when called."""
    watcher = _make_watcher()
    mock_ws = MagicMock()
    watcher._on_close(mock_ws, 1000, "normal")  # should not raise


# ---------------------------------------------------------------------------
# make_ws_exit_callback (integration with state)
# ---------------------------------------------------------------------------

from market_discovery_internal.cycles import build_paper_position
from market_discovery_internal.state_persistence import load_paper_state, save_paper_state


def _make_open_position(token_id="tok1", entry_price=0.25, city="new york", opened_hours_ago=3):
    """Create a paper position that is old enough to pass the 2h SL cooldown."""
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
    pos = build_paper_position(opp, stake_usd=100)
    # Backdate opened_at to bypass the 2h cooldown for stop-loss
    opened_at = datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
    pos["opened_at"] = opened_at.isoformat()
    return pos


def _write_state_with_position(state_path, position):
    """Write state JSON directly (bypassing DB for test isolation)."""
    state = {
        "positions": [position],
        "history": [],
        "cycle_journal": [],
        "updated_at": None,
        "meta": {"cash": 1000.0},
    }
    # Write directly to JSON file for test isolation
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _load_state_from_json(state_path):
    """Load state from JSON file directly for test assertions."""
    return load_paper_state(state_path)


def test_ws_callback_closes_on_stop_loss():
    """WS callback closes position when price hits stop-loss (after 3 ticks, 2h cooldown, and 90s window)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_sl", entry_price=0.25, opened_hours_ago=3)
        stop_loss_price = pos["stop_loss_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)

        # Flash Crash Shield L2: ticks must span >= 90 seconds.
        # Mock time.time() to simulate real time passing between ticks.
        sl_price = stop_loss_price - 0.01
        base_time = 1700000000.0
        call_count = [0]
        times = [base_time, base_time + 45, base_time + 100]
        original_time = time.time
        def mock_time_fn():
            if call_count[0] < len(times):
                t = times[call_count[0]]
                call_count[0] += 1
                return t
            return original_time()

        with patch("market_discovery_internal.ws_price_watcher.time") as mock_time_mod:
            mock_time_mod.time = mock_time_fn
            # Also disable REST confirmation for unit test (no network)
            with patch("market_discovery_internal.ws_price_watcher.FLASH_CRASH_REST_CONFIRM_ENABLED", False):
                callback("tok_sl", sl_price)  # tick 1 — no exit yet
                callback("tok_sl", sl_price)  # tick 2 — no exit yet (elapsed < 90s)
                callback("tok_sl", sl_price)  # tick 3 — exit fires (elapsed 100s > 90s)

        state = _load_state_from_json(state_path)
        closed = [p for p in state.get("history", []) if p.get("token_id") == "tok_sl"]
        assert len(closed) == 1
        assert closed[0]["status"] == "closed"
        assert closed[0]["close_reason"] == "stop_loss"


def test_ws_callback_closes_on_take_profit():
    """WS callback closes position when price hits take-profit target."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_tp", entry_price=0.25)
        target_price = pos["target_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)
        callback("tok_tp", target_price + 0.01)  # price above take-profit

        state = _load_state_from_json(state_path)
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
        callback("tok_hold", 0.30)  # between SL and TP

        state = _load_state_from_json(state_path)
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

        state = _load_state_from_json(state_path)
        assert len(state.get("positions", [])) == 1


def test_ws_callback_stop_loss_blocked_by_2h_cooldown():
    """WS callback does NOT fire stop-loss if position is less than 2h old."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        # Position opened only 1 hour ago — within 2h cooldown
        pos = _make_open_position(token_id="tok_young", entry_price=0.25, opened_hours_ago=1)
        stop_loss_price = pos["stop_loss_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)

        # Even with 3+ ticks, cooldown should block
        sl_price = stop_loss_price - 0.01
        callback("tok_young", sl_price)
        callback("tok_young", sl_price)
        callback("tok_young", sl_price)
        callback("tok_young", sl_price)

        state = _load_state_from_json(state_path)
        # Position should still be open
        assert len(state.get("positions", [])) == 1
        assert state["positions"][0]["status"] == "open"


def test_ws_callback_stop_loss_resets_on_price_recovery():
    """SL tick counter resets if price recovers above stop-loss between ticks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = os.path.join(tmpdir, "paper.json")
        pos = _make_open_position(token_id="tok_reset", entry_price=0.25, opened_hours_ago=3)
        stop_loss_price = pos["stop_loss_price"]
        _write_state_with_position(state_path, pos)

        lock = threading.Lock()
        callback = make_ws_exit_callback(state_path=state_path, lock=lock)

        sl_price = stop_loss_price - 0.01
        # 2 ticks below SL
        callback("tok_reset", sl_price)
        callback("tok_reset", sl_price)
        # Price recovers — resets counter
        callback("tok_reset", 0.30)
        # 2 more ticks below SL (counter restarted, so only at 2/3)
        callback("tok_reset", sl_price)
        callback("tok_reset", sl_price)

        state = _load_state_from_json(state_path)
        # Should still be open (only 2 consecutive ticks after reset)
        assert len(state.get("positions", [])) == 1
        assert state["positions"][0]["status"] == "open"

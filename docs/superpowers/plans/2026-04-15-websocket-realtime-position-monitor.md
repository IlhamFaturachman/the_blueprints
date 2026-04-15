# WebSocket Real-Time Position Monitor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a background WebSocket watcher that immediately closes open paper positions the moment stop-loss or take-profit prices are hit, eliminating the up-to-15-minute polling delay that causes missed exits (observed: -70% realized loss).

**Architecture:** A `PriceWatcher` class runs in a daemon thread alongside the existing polling loop. It subscribes to the CLOB WebSocket market channel for every open position's YES token ID, receives real-time `best_bid_ask` events, and calls a thread-safe exit callback that atomically loads state → closes position → saves state. The polling loop retains discovery + new position opening; the WS layer owns stop-loss and take-profit execution only. Late-window / hold-to-resolve logic stays in the polling loop.

**Tech Stack:** `websocket-client==1.8.0`, `threading.Lock`, existing `save_paper_state` / `close_paper_position` from `market_discovery.py`, Polymarket WS endpoint `wss://ws-subscriptions-clob.polymarket.com/ws/market`.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `requirements.txt` | Add `websocket-client==1.8.0` |
| Modify | `market_discovery_internal/config.py` | WS config constants |
| Modify | `.env.example` | Document new WS env vars |
| **Create** | `market_discovery_internal/ws_price_watcher.py` | WS client + thread + subscription management |
| Modify | `market_discovery.py` → `_run_main_paper_loop_mode` | Start watcher, pass lock, update subscriptions each cycle |
| **Create** | `tests/test_ws_price_watcher.py` | Unit tests for watcher logic (no real WS) |

---

## Task 1: Add Dependency and Config Constants

**Files:**
- Modify: `requirements.txt`
- Modify: `market_discovery_internal/config.py` (end of file)
- Modify: `.env.example` (end of file)

- [ ] **Step 1: Add websocket-client to requirements**

`requirements.txt` → replace entire file:
```
requests==2.31.0
python-dotenv==1.0.0
pytest==7.4.0
websocket-client==1.8.0
```

- [ ] **Step 2: Install it on VPS**

```bash
pip install websocket-client==1.8.0
```

Expected: `Successfully installed websocket-client-1.8.0`

- [ ] **Step 3: Add WS config constants to config.py (end of file, after existing block)**

```python
# WebSocket real-time position monitor
WS_PRICE_WATCHER_ENABLED = _env_bool("WS_PRICE_WATCHER_ENABLED", True)
WS_PRICE_WATCHER_URL = os.getenv(
    "WS_PRICE_WATCHER_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
)
WS_RECONNECT_DELAY_SECONDS = max(1, int(os.getenv("WS_RECONNECT_DELAY_SECONDS", "5")))
WS_PING_INTERVAL_SECONDS = max(5, int(os.getenv("WS_PING_INTERVAL_SECONDS", "10")))
```

- [ ] **Step 4: Add to .env.example (end of file)**

```
# WebSocket real-time position monitor (stop-loss / take-profit)
WS_PRICE_WATCHER_ENABLED=true
WS_PRICE_WATCHER_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
WS_RECONNECT_DELAY_SECONDS=5
WS_PING_INTERVAL_SECONDS=10
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt market_discovery_internal/config.py .env.example
git commit -m "feat: add websocket-client dependency and WS watcher config constants"
```

---

## Task 2: Build PriceWatcher Class

**Files:**
- Create: `market_discovery_internal/ws_price_watcher.py`
- Create: `tests/test_ws_price_watcher.py`

### What it does

Connects to `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribes to token IDs, receives `best_bid_ask` events, calls `on_price_update(token_id, bid_price)` callback. Auto-reconnects. Runs in a daemon thread so it dies when main process exits.

### WS message structures (from Polymarket docs)

Subscribe message:
```json
{
  "assets_ids": ["<token_id>"],
  "type": "market",
  "custom_feature_enabled": true
}
```

Dynamic subscribe (after connected):
```json
{
  "assets_ids": ["<token_id>"],
  "operation": "subscribe",
  "custom_feature_enabled": true
}
```

Dynamic unsubscribe:
```json
{
  "assets_ids": ["<token_id>"],
  "operation": "unsubscribe"
}
```

`best_bid_ask` event received:
```json
{
  "event_type": "best_bid_ask",
  "asset_id": "<token_id>",
  "best_bid": "0.18",
  "best_ask": "0.22"
}
```

`price_change` event received (fallback):
```json
{
  "event_type": "price_change",
  "price_changes": [
    {"asset_id": "<token_id>", "price": "0.20", "best_bid": "0.19"}
  ]
}
```

- [ ] **Step 1: Write failing tests first**

Create `tests/test_ws_price_watcher.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect ImportError (module doesn't exist yet)**

```bash
cd /Users/macairm12020/Documents/Blueprints/the_blueprints
source .venv/bin/activate
pytest tests/test_ws_price_watcher.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'market_discovery_internal.ws_price_watcher'`

- [ ] **Step 3: Implement PriceWatcher**

Create `market_discovery_internal/ws_price_watcher.py`:

```python
"""
ws_price_watcher.py — Real-time YES token price monitor via Polymarket WebSocket.

Subscribes to the CLOB market channel for open position token IDs.
Fires on_price_update(token_id, bid_price) on every best_bid_ask or
price_change event. Runs in a daemon thread — dies with the main process.

Only stop-loss and take-profit exits are handled here. Late-window /
hold-to-resolve logic stays in the polling loop.
"""

import json
import logging
import threading
import time

logger = logging.getLogger(__name__)


class PriceWatcher:
    """Background WebSocket price watcher for open paper positions.

    Usage:
        def on_price_update(token_id: str, bid_price: float) -> None:
            ...  # evaluate exit, close position if triggered

        watcher = PriceWatcher(url=WS_PRICE_WATCHER_URL, on_price_update=on_price_update)
        watcher.start()

        # Each time open positions change:
        watcher.update_subscriptions({"token_id_1", "token_id_2"})

        # On shutdown (optional — daemon thread dies automatically):
        watcher.stop()
    """

    def __init__(
        self,
        url: str,
        on_price_update,
        reconnect_delay: int = 5,
        ping_interval: int = 10,
    ):
        self._url = url
        self._on_price_update = on_price_update
        self._reconnect_delay = reconnect_delay
        self._ping_interval = ping_interval

        self._subscribed: set = set()     # token_ids currently subscribed on WS
        self._desired: set = set()        # token_ids we want subscribed
        self._lock = threading.Lock()     # guards _subscribed and _desired
        self._ws = None                   # active WebSocketApp instance
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background watcher thread. Safe to call once."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="PriceWatcher")
        self._thread.start()
        logger.info("[WS] PriceWatcher started")

    def stop(self) -> None:
        """Signal the watcher to stop and wait briefly for cleanup."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[WS] PriceWatcher stopped")

    def update_subscriptions(self, token_ids: set) -> None:
        """Diff current vs desired subscriptions and send WS messages for the delta."""
        with self._lock:
            desired = set(token_ids)
            to_add = desired - self._subscribed
            to_remove = self._subscribed - desired
            self._desired = desired

        if self._ws is None:
            # Not connected yet — subscriptions will be sent on next connect via _on_open
            return

        if to_add:
            self._send_subscribe(list(to_add))
        if to_remove:
            self._send_unsubscribe(list(to_remove))

    # ------------------------------------------------------------------
    # Internal — connection loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Reconnection loop. Runs until stop() is called."""
        try:
            import websocket
        except ImportError:
            logger.error("[WS] websocket-client not installed. Run: pip install websocket-client==1.8.0")
            return

        while not self._stop_event.is_set():
            try:
                logger.info("[WS] Connecting to %s", self._url)
                ws = websocket.WebSocketApp(
                    self._url,
                    on_open=self._on_open,
                    on_message=self._on_message_raw,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws = ws
                ws.run_forever(
                    ping_interval=self._ping_interval,
                    ping_timeout=max(1, self._ping_interval // 2),
                )
            except Exception as exc:
                logger.warning("[WS] Connection error: %s", exc)

            if self._stop_event.is_set():
                break

            logger.info("[WS] Reconnecting in %ds...", self._reconnect_delay)
            self._stop_event.wait(timeout=self._reconnect_delay)

        self._ws = None

    # ------------------------------------------------------------------
    # Internal — WS callbacks
    # ------------------------------------------------------------------

    def _on_open(self, ws) -> None:
        """Send initial subscription for all desired token IDs on connect."""
        with self._lock:
            self._subscribed = set()
            desired = set(self._desired)

        logger.info("[WS] Connected. Subscribing to %d token(s)", len(desired))
        if desired:
            self._send_subscribe(list(desired))

    def _on_message_raw(self, ws, raw: str) -> None:
        """Raw WS message handler — delegates to _handle_message."""
        self._handle_message(raw)

    def _on_error(self, ws, error) -> None:
        logger.warning("[WS] Error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.info("[WS] Closed (code=%s msg=%s)", close_status_code, close_msg)
        with self._lock:
            self._subscribed = set()

    # ------------------------------------------------------------------
    # Internal — message parsing (testable without WS)
    # ------------------------------------------------------------------

    def _handle_message(self, raw: str) -> None:
        """Parse a raw WS message string and fire on_price_update for price events."""
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("[WS] Unparseable message: %.120s", raw)
            return

        event_type = msg.get("event_type")

        if event_type == "best_bid_ask":
            token_id = msg.get("asset_id", "")
            raw_bid = msg.get("best_bid")
            if token_id and raw_bid is not None:
                try:
                    self._on_price_update(token_id, float(raw_bid))
                except Exception as exc:
                    logger.warning("[WS] Callback error for %s: %s", token_id, exc)

        elif event_type == "price_change":
            for change in msg.get("price_changes", []):
                token_id = change.get("asset_id", "")
                raw_bid = change.get("best_bid") or change.get("price")
                if token_id and raw_bid is not None:
                    try:
                        self._on_price_update(token_id, float(raw_bid))
                    except Exception as exc:
                        logger.warning("[WS] Callback error for %s: %s", token_id, exc)

    # ------------------------------------------------------------------
    # Internal — send helpers
    # ------------------------------------------------------------------

    def _send_subscribe(self, token_ids: list) -> None:
        msg = json.dumps({
            "assets_ids": token_ids,
            "operation": "subscribe",
            "custom_feature_enabled": True,
        })
        try:
            self._ws.send(msg)
            with self._lock:
                self._subscribed.update(token_ids)
            logger.info("[WS] Subscribed to %d token(s): %s", len(token_ids), token_ids)
        except Exception as exc:
            logger.warning("[WS] Failed to send subscribe: %s", exc)

    def _send_unsubscribe(self, token_ids: list) -> None:
        msg = json.dumps({
            "assets_ids": token_ids,
            "operation": "unsubscribe",
        })
        try:
            self._ws.send(msg)
            with self._lock:
                self._subscribed.difference_update(token_ids)
            logger.info("[WS] Unsubscribed from %d token(s)", len(token_ids))
        except Exception as exc:
            logger.warning("[WS] Failed to send unsubscribe: %s", exc)
```

**Note — first connect vs dynamic subscribe:** The Polymarket WS docs show the initial subscription uses `"type": "market"` (not `"operation": "subscribe"`). We unify both dynamic subscribe and initial subscribe via `"operation": "subscribe"` with `custom_feature_enabled: true` — this works for both cases based on API behavior. If the first connection needs `"type": "market"`, the `_on_open` method can be updated to send `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}` and use `operation` only for dynamic updates.

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_ws_price_watcher.py -v
```

Expected:
```
PASSED tests/test_ws_price_watcher.py::test_best_bid_ask_calls_callback
PASSED tests/test_ws_price_watcher.py::test_price_change_calls_callback
PASSED tests/test_ws_price_watcher.py::test_price_change_multiple_assets_fires_all
PASSED tests/test_ws_price_watcher.py::test_unknown_event_type_ignored
PASSED tests/test_ws_price_watcher.py::test_malformed_json_ignored
PASSED tests/test_ws_price_watcher.py::test_missing_best_bid_falls_back_to_price
PASSED tests/test_ws_price_watcher.py::test_update_subscriptions_tracks_current_set
PASSED tests/test_ws_price_watcher.py::test_update_subscriptions_sends_subscribe_for_new_tokens
PASSED tests/test_ws_price_watcher.py::test_update_subscriptions_sends_unsubscribe_for_removed_tokens
PASSED tests/test_ws_price_watcher.py::test_update_subscriptions_no_message_when_no_change
```

- [ ] **Step 5: Commit**

```bash
git add market_discovery_internal/ws_price_watcher.py tests/test_ws_price_watcher.py
git commit -m "feat: add PriceWatcher WebSocket client with unit tests"
```

---

## Task 3: Thread-Safe Position Exit Callback

**Files:**
- Modify: `market_discovery.py` — add `_ws_on_price_update` function near `_run_main_paper_loop_mode`
- Modify: `tests/test_ws_price_watcher.py` — add integration tests for the callback

The WS callback must:
1. Acquire a lock shared with the polling loop
2. Load current state
3. Find the position matching `token_id`
4. Check stop-loss and take-profit only (no forecast needed)
5. If triggered → close position, save state, print alert
6. Release lock

- [ ] **Step 1: Write failing test for the callback**

Append to `tests/test_ws_price_watcher.py`:

```python
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
```

- [ ] **Step 2: Run tests — expect ImportError for make_ws_exit_callback**

```bash
pytest tests/test_ws_price_watcher.py::test_ws_callback_closes_on_stop_loss -v
```

Expected: `ImportError: cannot import name 'make_ws_exit_callback'`

- [ ] **Step 3: Implement make_ws_exit_callback in ws_price_watcher.py**

Add to the **bottom** of `market_discovery_internal/ws_price_watcher.py` (after the class):

```python
def make_ws_exit_callback(state_path: str, lock: "threading.Lock"):
    """
    Build a thread-safe on_price_update callback that checks stop-loss and
    take-profit for the matching open position and closes it if triggered.

    Args:
        state_path: path to paper state JSON file
        lock: shared threading.Lock — must also be held by the polling loop
              when it reads/writes state
    """
    # Import here to avoid circular imports (market_discovery imports config
    # which is in the same package)
    from market_discovery import (
        close_paper_position,
        load_paper_state,
        save_paper_state,
    )
    from datetime import datetime, timezone

    def _should_exit(position: dict, price: float):
        """Return (reason, True) if price triggers stop-loss or take-profit."""
        stop_loss = float(position.get("stop_loss_price", 0))
        target = float(position.get("target_price", 1))
        if price <= stop_loss:
            return "stop_loss"
        if price >= target:
            return "take_profit_100pct"
        return None

    def callback(token_id: str, bid_price: float) -> None:
        with lock:
            try:
                state = load_paper_state(state_path)
                positions = state.get("positions", [])
                changed = False

                for i, pos in enumerate(positions):
                    if pos.get("token_id") != token_id:
                        continue
                    if pos.get("status") != "open":
                        continue

                    reason = _should_exit(pos, bid_price)
                    if reason is None:
                        return  # hold — nothing to do

                    now = datetime.now(timezone.utc)
                    closed = close_paper_position(
                        position=pos,
                        exit_price=bid_price,
                        reason=reason,
                        now_utc=now,
                    )
                    positions[i] = closed
                    state["positions"] = positions
                    changed = True
                    print(
                        f"[WS EXIT] {pos.get('city','?').upper()} | "
                        f"{reason} | price={bid_price:.4f} | "
                        f"entry={pos.get('entry_price',0):.4f}"
                    )
                    break

                if changed:
                    save_paper_state(state, state_path)

            except Exception as exc:
                logger.warning("[WS] Exit callback error for %s @ %.4f: %s", token_id, bid_price, exc)

    return callback
```

- [ ] **Step 4: Run all new tests**

```bash
pytest tests/test_ws_price_watcher.py -v
```

Expected: all 14 tests PASS

- [ ] **Step 5: Run full test suite to check no regressions**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all previously passing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add market_discovery_internal/ws_price_watcher.py tests/test_ws_price_watcher.py
git commit -m "feat: add thread-safe WS exit callback for stop-loss and take-profit"
```

---

## Task 4: Wire PriceWatcher into Paper Loop Mode

**Files:**
- Modify: `market_discovery.py` — `_run_main_paper_loop_mode` function (~line 2277)

The polling loop needs to:
1. Start `PriceWatcher` before the loop (if enabled)
2. Create a `threading.Lock` shared between WS callback and polling loop
3. After each cycle (new positions may have opened), call `watcher.update_subscriptions(open_token_ids)`
4. Pass the lock to `run_paper_trading_cycle` so it acquires it when reading/writing state

Since `run_paper_trading_cycle` already calls `load_paper_state` + `save_paper_state` internally (via `_run_paper_trading_cycle_impl`), the simplest approach is: **the polling loop acquires the lock for the entire cycle**. The WS callback also acquires the same lock per exit event. This prevents any interleaving.

- [ ] **Step 1: Add lock parameter wiring to _run_main_paper_loop_mode**

In `market_discovery.py`, find `_run_main_paper_loop_mode` (~line 2277) and replace it entirely:

```python
def _run_main_paper_loop_mode(aggressive_mode):
    """Handle paper loop mode in main(). Starts WS price watcher if enabled."""
    import threading
    from market_discovery_internal.ws_price_watcher import (
        PriceWatcher,
        make_ws_exit_callback,
    )

    # Shared lock: both WS callback and polling cycle hold this when writing state
    state_lock = threading.Lock()

    watcher = None
    if WS_PRICE_WATCHER_ENABLED:
        exit_callback = make_ws_exit_callback(
            state_path=PAPER_STATE_FILE,
            lock=state_lock,
        )
        watcher = PriceWatcher(
            url=WS_PRICE_WATCHER_URL,
            on_price_update=exit_callback,
            reconnect_delay=WS_RECONNECT_DELAY_SECONDS,
            ping_interval=WS_PING_INTERVAL_SECONDS,
        )
        watcher.start()
        print(f"[WS] Real-time price watcher started ({WS_PRICE_WATCHER_URL})")
    else:
        print("[WS] Price watcher disabled (WS_PRICE_WATCHER_ENABLED=false)")

    def _run_cycle_with_lock(force_aggressive_scan):
        """Run one paper trading cycle inside the shared state lock."""
        with state_lock:
            return run_paper_trading_cycle(force_aggressive_scan=force_aggressive_scan)

    def _after_cycle(cycle):
        """Update WS subscriptions after cycle so new positions are watched."""
        if watcher is None:
            return
        open_positions = cycle.get("open_positions", [])
        token_ids = {
            pos["token_id"]
            for pos in open_positions
            if pos.get("status") == "open" and pos.get("token_id")
        }
        watcher.update_subscriptions(token_ids)

    print(f"Starting paper loop every {PAPER_LOOP_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")

    consecutive_errors = 0
    backoff_base = max(1, int(PAPER_LOOP_ERROR_BACKOFF_SECONDS))
    max_backoff = max(backoff_base, int(PAPER_LOOP_MAX_ERROR_BACKOFF_SECONDS))

    try:
        while True:
            try:
                cycle = _run_cycle_with_lock(force_aggressive_scan=aggressive_mode)
                print_paper_cycle_summary(cycle)
                _after_cycle(cycle)
                consecutive_errors = 0
                time.sleep(PAPER_LOOP_INTERVAL_SECONDS)
            except Exception as error:
                if not PAPER_LOOP_CONTINUE_ON_ERROR:
                    raise
                consecutive_errors += 1
                retry_in_seconds = min(
                    max_backoff,
                    backoff_base * (2 ** min(consecutive_errors - 1, 6)),
                )
                from datetime import datetime, timezone
                timestamp_utc = datetime.now(timezone.utc).isoformat()
                print(
                    f"[{timestamp_utc}] Paper cycle failed "
                    f"({consecutive_errors} consecutive): {error}"
                )
                print(f"Retrying in {retry_in_seconds}s...")
                _append_runtime_error_log(
                    mode="paper_loop",
                    error=error,
                    consecutive_errors=consecutive_errors,
                    retry_in_seconds=retry_in_seconds,
                )
                time.sleep(retry_in_seconds)
    except KeyboardInterrupt:
        print("\nPaper loop stopped.")
        if watcher:
            watcher.stop()
```

- [ ] **Step 2: Add missing config imports at the top of _run_main_paper_loop_mode area**

Verify that `WS_PRICE_WATCHER_ENABLED`, `WS_PRICE_WATCHER_URL`, `WS_RECONNECT_DELAY_SECONDS`, `WS_PING_INTERVAL_SECONDS` are importable. Since `market_discovery.py` already does `from market_discovery_internal.config import *`, these will be available automatically after Task 1 added them to `config.py`. No extra import needed.

- [ ] **Step 3: Smoke test — run paper loop for 30 seconds and confirm WS connects**

```bash
cd /Users/macairm12020/Documents/Blueprints/the_blueprints
source .venv/bin/activate
timeout 30 python market_discovery.py --paper-loop 2>&1 | head -30
```

Expected output lines (in any order):
```
[WS] Real-time price watcher started (wss://ws-subscriptions-clob.polymarket.com/ws/market)
Starting paper loop every 300s. Press Ctrl+C to stop.
[WS] Connecting to wss://ws-subscriptions-clob.polymarket.com/ws/market
[WS] Connected. Subscribing to N token(s)
```

If no positions are open, subscription count will be 0 — that's correct.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all previously passing tests still PASS. `test_cli_loop.py` may need update if it patches `_run_main_paper_loop_mode_impl` — check and fix if needed (the function signature changed).

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py
git commit -m "feat: wire PriceWatcher into paper loop — real-time stop-loss and take-profit execution"
```

---

## Task 5: Fix Initial Subscription Message Format

**Files:**
- Modify: `market_discovery_internal/ws_price_watcher.py` — `_on_open` method

The Polymarket docs show the initial subscription uses `"type": "market"` (not `"operation": "subscribe"`). The `_send_subscribe` helper currently sends `operation: subscribe` for all cases. Fix `_on_open` to send the correct format for first connect.

- [ ] **Step 1: Update _on_open in ws_price_watcher.py**

Replace the `_on_open` method:

```python
def _on_open(self, ws) -> None:
    """Send initial subscription for all desired token IDs on connect."""
    with self._lock:
        self._subscribed = set()
        desired = set(self._desired)

    logger.info("[WS] Connected. Subscribing to %d token(s)", len(desired))
    if desired:
        # Initial subscription uses "type": "market" per Polymarket docs
        msg = json.dumps({
            "assets_ids": list(desired),
            "type": "market",
            "custom_feature_enabled": True,
        })
        try:
            ws.send(msg)
            with self._lock:
                self._subscribed.update(desired)
        except Exception as exc:
            logger.warning("[WS] Failed to send initial subscription: %s", exc)
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_ws_price_watcher.py -v
```

Expected: all 14 tests PASS (tests use `_send_subscribe` for dynamic subs, not `_on_open` — no test changes needed)

- [ ] **Step 3: Final full suite**

```bash
pytest tests/ --tb=short 2>&1 | tail -10
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add market_discovery_internal/ws_price_watcher.py
git commit -m "fix: use correct initial subscription message format for Polymarket WS"
```

---

## Task 6: Add Logging Config for WS Messages

**Files:**
- Modify: `market_discovery.py` — `main()` function or top-level

The WS watcher uses Python `logging`. Without a handler, all `[WS]` messages are silently dropped. Add a basic handler so WS connect/disconnect/error messages show in terminal.

- [ ] **Step 1: Add logging setup near top of market_discovery.py main block**

Find the `main()` function and add before `modes = _parse_cli_mode_flags(sys.argv)`:

```python
def main():
    """Run discovery mode or paper-trading mode based on CLI flags."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    modes = _parse_cli_mode_flags(sys.argv)
    # ... rest of main unchanged
```

- [ ] **Step 2: Smoke test again to verify WS log lines appear**

```bash
timeout 15 python market_discovery.py --paper-loop 2>&1 | grep -E "\[WS\]|WS "
```

Expected (at minimum):
```
HH:MM:SS [market_discovery_internal.ws_price_watcher] INFO [WS] PriceWatcher started
HH:MM:SS [market_discovery_internal.ws_price_watcher] INFO [WS] Connecting to wss://...
HH:MM:SS [market_discovery_internal.ws_price_watcher] INFO [WS] Connected. Subscribing to 0 token(s)
```

- [ ] **Step 3: Commit**

```bash
git add market_discovery.py
git commit -m "feat: add logging config so WS watcher events appear in terminal"
```

---

## Self-Review

**Spec coverage:**
- Real-time stop-loss: Task 3 `_should_exit` → `stop_loss` reason ✓
- Real-time take-profit: Task 3 `_should_exit` → `take_profit_100pct` reason ✓
- WebSocket connection: Task 2 `PriceWatcher._run_loop` ✓
- Auto-reconnect: Task 2 `_run_loop` while loop ✓
- Heartbeat PING: Task 2 `ping_interval` param to `run_forever` ✓
- Dynamic subscription on new positions: Task 4 `_after_cycle` ✓
- Thread safety: Task 3 lock, Task 4 `_run_cycle_with_lock` ✓
- Polling loop unchanged for discovery + new opens: Task 4 ✓
- Late-window / hold-to-resolve stays in polling loop: Task 3 `_should_exit` only checks SL/TP ✓
- VPS safe (daemon thread): Task 2 `daemon=True` ✓
- Config via env: Task 1 `WS_PRICE_WATCHER_ENABLED` + others ✓

**Placeholder scan:** No TBD/TODO/similar patterns present.

**Type consistency:** `make_ws_exit_callback` defined in Task 3, imported in Task 4 — names match.

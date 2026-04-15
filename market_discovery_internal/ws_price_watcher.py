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
                raw_best_bid = change.get("best_bid")
                raw_bid = raw_best_bid if raw_best_bid is not None else change.get("price")
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


def make_ws_exit_callback(state_path: str, lock: "threading.Lock", broadcaster=None):
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
        """Return reason string if price triggers stop-loss or take-profit, else None."""
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
                    positions.pop(i)
                    state["positions"] = positions
                    history = state.get("history", [])
                    history.append(closed)
                    state["history"] = history
                    changed = True
                    print(
                        f"[WS EXIT] {pos.get('city','?').upper()} | "
                        f"{reason} | price={bid_price:.4f} | "
                        f"entry={pos.get('entry_price',0):.4f}"
                    )

                    if broadcaster is not None:
                        try:
                            broadcaster.broadcast_closed(
                                token_id=token_id,
                                city=pos.get("city", ""),
                                reason=reason,
                                exit_price=bid_price,
                            )
                        except Exception:
                            # WS push is best-effort; trading state must remain authoritative.
                            pass
                    break

                if changed:
                    save_paper_state(state, state_path)

            except Exception as exc:
                logger.warning("[WS] Exit callback error for %s @ %.4f: %s", token_id, bid_price, exc)

    return callback

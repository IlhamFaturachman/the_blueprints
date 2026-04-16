"""
ws_price_watcher.py — Real-time YES token price monitor via Polymarket WebSocket.

Subscribes to the CLOB market channel for open position token IDs.
Uses MULTIPROCESSING to ensure zero-lag price monitoring, bypassing the Python GIL.

This process is a "Source" of price events; it sends (token_id, bid_price) 
records via a multiprocessing.Queue to the main bot process.
"""

import json
import logging
import multiprocessing
import socket
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class PriceWatcher:
    """Background WebSocket price watcher (Process-isolated).

    Runs in a dedicated OS process to ensure network I/O and message 
    parsing are never blocked by the main bot's heavy computations 
    or file operations.
    """

    def __init__(
        self,
        url: str,
        update_queue: multiprocessing.Queue,
        reconnect_delay: int = 5,
        ping_interval: int = 30,
        watchdog_timeout: int = 60,
    ):
        self._url = url
        self._update_queue = update_queue
        self._reconnect_delay = reconnect_delay
        self._ping_interval = ping_interval
        self._watchdog_timeout = watchdog_timeout

        # Managed by the main process, synced to subprocess on start/update
        self._desired_ids = multiprocessing.Manager().list()
        self._lock = multiprocessing.Lock()

        self._process: multiprocessing.Process | None = None
        self._stop_event = multiprocessing.Event()

    # ------------------------------------------------------------------
    # Public API (Main Process)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background watcher process."""
        if self._process and self._process.is_alive():
            return
        self._stop_event.clear()
        self._process = multiprocessing.Process(
            target=self._run_loop,
            daemon=True,
            name="PriceWatcherProcess"
        )
        self._process.start()
        logger.info("[WS-MPC] PriceWatcher process started (PID %d)", self._process.pid)

    def stop(self) -> None:
        """Signal the watcher to stop and terminate the process."""
        self._stop_event.set()
        if self._process:
            self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()
        logger.info("[WS-MPC] PriceWatcher stopped")

    def update_subscriptions(self, token_ids: set) -> None:
        """Update the shared list of desired token IDs."""
        with self._lock:
            # Atomic update of the shared list
            while len(self._desired_ids) > 0:
                self._desired_ids.pop()
            self._desired_ids.extend(list(token_ids))
        logger.debug("[WS-MPC] Subscriptions updated: %d IDs", len(token_ids))

    # ------------------------------------------------------------------
    # Subprocess entry point
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Internal subprocess loop — handles connection and watchdog."""
        try:
            import websocket
        except ImportError:
            logger.error("[WS-MPC] websocket-client not installed.")
            return

        # Setup DNS workaround in subprocess
        parsed_host = (urlparse(self._url).hostname or "").lower()
        original_getaddrinfo = socket.getaddrinfo

        def _prefer_ipv4_for_target(host, port, family=0, type=0, proto=0, flags=0):
            host_text = str(host).lower() if host is not None else ""
            if parsed_host and host_text == parsed_host:
                try:
                    return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                except OSError:
                    pass
            return original_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = _prefer_ipv4_for_target

        last_msg_at = time.time()
        current_subscriptions = set()

        try:
            while not self._stop_event.is_set():
                try:
                    logger.info("[WS-MPC] Connecting to %s", self._url)
                    ws = websocket.WebSocketApp(
                        self._url,
                        on_open=lambda w: self._on_open(w, current_subscriptions),
                        on_message=lambda w, m: self._on_message(w, m, last_msg_at),
                        on_error=self._on_error,
                        on_close=lambda w, c, m: self._on_close(w, c, m, current_subscriptions),
                    )

                    # Monitor loop for run_forever
                    def _check_staleness():
                        while ws.sock and ws.sock.connected:
                            # Watchdog check
                            if time.time() - last_msg_at > self._watchdog_timeout:
                                logger.warning("[WS-MPC] Watchdog timeout! Closing stale connection.")
                                ws.close()
                                break
                            
                            # Dynamic subscription check
                            with self._lock:
                                desired = set(self._desired_ids)
                            
                            to_add = desired - current_subscriptions
                            to_remove = current_subscriptions - desired
                            
                            if to_add:
                                self._send_op(ws, list(to_add), "subscribe")
                                current_subscriptions.update(to_add)
                            if to_remove:
                                self._send_op(ws, list(to_remove), "unsubscribe")
                                current_subscriptions.difference_update(to_remove)

                            time.sleep(1)

                    monitor_thread = threading.Thread(target=_check_staleness, daemon=True)
                    monitor_thread.start()

                    ws.run_forever(
                        ping_interval=self._ping_interval,
                        ping_timeout=max(5, self._ping_interval // 2),
                    )
                except Exception as exc:
                    logger.warning("[WS-MPC] Loop error: %s", exc)

                if self._stop_event.is_set():
                    break
                
                logger.info("[WS-MPC] Reconnecting in %ds...", self._reconnect_delay)
                time.sleep(self._reconnect_delay)
        finally:
            socket.getaddrinfo = original_getaddrinfo

    # ------------------------------------------------------------------
    # Subprocess WS Callbacks
    # ------------------------------------------------------------------

    def _on_open(self, ws, current_subs: set) -> None:
        with self._lock:
            desired = list(self._desired_ids)
        
        logger.info("[WS-MPC] Connected. Syncing %d subscriptions", len(desired))
        if desired:
            msg = json.dumps({
                "assets_ids": desired,
                "type": "market",
                "custom_feature_enabled": True,
            })
            try:
                ws.send(msg)
                current_subs.clear()
                current_subs.update(desired)
            except Exception as exc:
                logger.warning("[WS-MPC] Initial sub failed: %s", exc)

    def _on_message(self, ws, raw: str, last_msg_ref: list) -> None:
        # Note: can't use mutable float directly effectively in closure easily
        # but we can just update a timestamp in a shared object or use nonlocal
        # Actually in this structure, we'll just parse and push to queue.
        # We'll use a local 'last_msg_at' updated by the message handler.
        
        # We need a way to pass 'last_msg_at' back up. 
        # For simplicity in this proof-of-concept, I'll use a hack or just
        # update the timestamp inside the class if needed (but class attrs in 
        # separate process are local to that process)
        pass

    # Wait, let's rewrite the callbacks to be more robust for multiprocessing
    def _on_message_hardened(self, ws, raw: str):
        self._last_msg_at = time.time() # This is the subprocess's instance attribute
        try:
            msg = json.loads(raw)
        except:
            return

        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            if not isinstance(event, dict): continue
            
            etype = event.get("event_type")
            if etype == "best_bid_ask":
                tid = event.get("asset_id")
                bid = event.get("best_bid")
                if tid and bid is not None:
                    self._update_queue.put((tid, float(bid)))
            elif etype == "price_change":
                for c in event.get("price_changes", []):
                    tid = c.get("asset_id")
                    bid = c.get("best_bid") or c.get("price")
                    if tid and bid is not None:
                        self._update_queue.put((tid, float(bid)))

    def _on_error(self, ws, error):
        logger.warning("[WS-MPC] Error: %s", error)

    def _on_close(self, ws, code, msg, current_subs: set):
        logger.info("[WS-MPC] Closed: %s", msg)
        current_subs.clear()

    def _send_op(self, ws, ids: list, op: str):
        msg = json.dumps({
            "assets_ids": ids,
            "operation": op,
            "custom_feature_enabled": True if op == "subscribe" else False
        })
        try:
            ws.send(msg)
            logger.info("[WS-MPC] SENT %s for %d assets", op.upper(), len(ids))
        except Exception as exc:
            logger.warning("[WS-MPC] Failed to send %s: %s", op, exc)

    # RE-IMPLEMENTING _run_loop to properly handle the local mutable state
    def _run_loop(self) -> None:
        try:
            import websocket
        except:
            return

        self._last_msg_at = time.time()
        current_subs = set()
        
        while not self._stop_event.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self._url,
                    on_open=lambda w: self._on_open(w, current_subs),
                    on_message=lambda w, m: self._on_message_hardened(w, m),
                    on_error=self._on_error,
                    on_close=lambda w, c, m: self._on_close(w, c, m, current_subs),
                )
                
                def watchdog_fn():
                    while ws.sock and ws.sock.connected:
                        if time.time() - self._last_msg_at > self._watchdog_timeout:
                            logger.warning("[WS-MPC] WATCHDOG! No messages for %ds", self._watchdog_timeout)
                            ws.close()
                            break
                        
                        # Sync subscriptions
                        with self._lock:
                            desired = set(self._desired_ids)
                        
                        to_add = desired - current_subs
                        to_remove = current_subs - desired
                        
                        if to_add:
                            self._send_op(ws, list(to_add), "subscribe")
                            current_subs.update(to_add)
                        if to_remove:
                            self._send_op(ws, list(to_remove), "unsubscribe")
                            current_subs.difference_update(to_remove)
                            
                        time.sleep(1)

                threading.Thread(target=watchdog_fn, daemon=True).start()
                
                ws.run_forever(
                    ping_interval=self._ping_interval,
                    ping_timeout=10
                )
            except:
                pass
            time.sleep(self._reconnect_delay)


def make_ws_exit_callback(state_path: str, lock: "threading.Lock", broadcaster=None):
    """
    (Main Process) Callback that handles price updates from the queue.
    """
    from market_discovery_internal.state_persistence import (
        load_paper_state,
        save_paper_state,
    )
    from market_discovery_internal.cycles import close_paper_position
    from datetime import datetime, timezone

    def callback(token_id: str, bid_price: float) -> None:
        with lock:
            try:
                state = load_paper_state(state_path)
                positions = state.get("positions", [])
                changed = False

                for i, pos in enumerate(positions):
                    if pos.get("token_id") != token_id: continue
                    if pos.get("status") != "open": continue

                    # Evaluate exit
                    stop = float(pos.get("stop_loss_price", 0))
                    target = float(pos.get("target_price", 1))
                    reason = None
                    if bid_price <= stop: reason = "stop_loss"
                    elif bid_price >= target: reason = "take_profit_100pct"

                    if reason:
                        closed = close_paper_position(pos, bid_price, reason, datetime.now(timezone.utc))
                        positions.pop(i)
                        state["positions"] = positions
                        state.setdefault("history", []).append(closed)
                        changed = True
                        print(f"[WS-EXIT] {pos.get('city','?').upper()} | {reason} @ {bid_price:.4f}")
                        if broadcaster:
                            broadcaster.broadcast_closed(token_id, pos.get('city',''), reason, bid_price)
                        break

                if changed:
                    save_paper_state(state, state_path)

            except Exception as exc:
                logger.warning("[WS-CALLBACK] Error: %s", exc)

    return callback

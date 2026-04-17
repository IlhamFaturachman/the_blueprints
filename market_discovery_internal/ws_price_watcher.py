"""
ws_price_watcher.py — Real-time YES token price monitor via Polymarket WebSocket.

Subscribes to the CLOB market channel for open position token IDs.
Uses MULTIPROCESSING to ensure zero-lag price monitoring, bypassing the Python GIL.
"""

import json
import logging
import logging.handlers
import multiprocessing
import random
import socket
import threading
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class PriceWatcher:
    """Background WebSocket price watcher (Process-isolated)."""

    def __init__(
        self,
        url: str,
        update_queue: multiprocessing.Queue,
        reconnect_delay: int = 10,
        ping_interval: int = 20,
        watchdog_timeout: int = 120,
    ):
        self._url = url
        self._update_queue = update_queue
        self._reconnect_delay = reconnect_delay
        self._ping_interval = ping_interval
        self._watchdog_timeout = watchdog_timeout

        # Shared state between main process and subprocess.
        # Use a Queue for subscription updates to avoid multiprocessing.Manager()
        # which spawns a non-daemon SyncManager process that becomes an orphan
        # when the service is restarted by systemd.
        self._sub_update_queue: multiprocessing.Queue = multiprocessing.Queue()
        self._stop_event = multiprocessing.Event()
        self._process: multiprocessing.Process | None = None

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
        """Send a subscription update to the watcher subprocess via queue."""
        try:
            self._sub_update_queue.put_nowait(list(token_ids))
        except Exception as exc:
            logger.warning("[WS-MPC] Could not queue subscription update: %s", exc)
        logger.debug("[WS-MPC] Subscriptions update queued: %d IDs", len(token_ids))

    def _run_loop(self) -> None:
        """Internal subprocess loop — handles connection and watchdog."""
        import os
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        internal_log_path = os.path.join(log_dir, "ws_internal.log")
        ilogger = logging.getLogger("PriceWatcherSub")
        ilogger.setLevel(logging.INFO)
        # Avoid duplicate handlers if loop restarts within same process (unlikely but safe)
        if not ilogger.handlers:
            fh = logging.handlers.RotatingFileHandler(internal_log_path, maxBytes=1024*1024*5, backupCount=2)
            fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            ilogger.addHandler(fh)
        
        ilogger.info("=== PriceWatcher Subprocess Starting ===")

        try:
            import websocket
        except ImportError:
            ilogger.error("[WS-MPC] websocket-client NOT INSTALLED.")
            return

        # DNS/IPv4 Workaround
        parsed_host = (urlparse(self._url).hostname or "").lower()
        original_getaddrinfo = socket.getaddrinfo
        def _prefer_ipv4(host, port, family=0, type=0, proto=0, flags=0):
            if parsed_host and str(host).lower() == parsed_host:
                try: return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
                except: pass
            return original_getaddrinfo(host, port, family, type, proto, flags)
        socket.getaddrinfo = _prefer_ipv4

        self._last_msg_at = time.time()
        current_subs = set()
        desired_subs = set()  # Tracks the latest desired subscription set

        while not self._stop_event.is_set():
            try:
                ilogger.info("[WS-MPC] Connecting to %s (with SSL Tweak)", self._url)
                import ssl
                ws = websocket.WebSocketApp(
                    self._url,
                    on_open=lambda w: self._on_open(w, current_subs, desired_subs, ilogger),
                    on_message=lambda w, m: self._on_message(w, m, ilogger),
                    on_error=lambda w, e: ilogger.warning("[WS-MPC] Error: %s", e),
                    on_close=lambda w, c, m: self._on_close(w, c, m, current_subs, ilogger),
                )

                # Enterprise SSL Tweak (Handle Cloudflare/Handshake issues)
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE  # Last resort for handshake hang

                def watchdog_fn():
                    while ws.sock and ws.sock.connected:
                        if time.time() - self._last_msg_at > self._watchdog_timeout:
                            ilogger.warning("[WS-MPC] WATCHDOG TIMEOUT (%ds). Closing.", self._watchdog_timeout)
                            ws.close()
                            break

                        # Drain subscription updates from queue (last write wins)
                        latest = None
                        while not self._sub_update_queue.empty():
                            try:
                                latest = self._sub_update_queue.get_nowait()
                            except Exception:
                                pass
                        if latest is not None:
                            desired_subs.clear()
                            desired_subs.update(latest)

                        to_add = desired_subs - current_subs
                        to_remove = current_subs - desired_subs

                        if to_add:
                            self._send_op(ws, list(to_add), "subscribe", ilogger)
                            current_subs.update(to_add)
                        if to_remove:
                            self._send_op(ws, list(to_remove), "unsubscribe", ilogger)
                            current_subs.difference_update(to_remove)

                        time.sleep(1)

                threading.Thread(target=watchdog_fn, daemon=True).start()
                
                ws.run_forever(
                    ping_interval=self._ping_interval,
                    ping_timeout=max(10, self._ping_interval // 2),
                    sslopt={"context": ssl_context}
                )
            except Exception as e:
                ilogger.error("[WS-MPC] Connection loop error: %s", e)
            
            if self._stop_event.is_set(): break
            backoff = self._reconnect_delay + random.uniform(0, 5)
            ilogger.info("[WS-MPC] Retrying in %.1fs...", backoff)
            time.sleep(backoff)

    def _on_message(self, ws, raw: str, ilogger: logging.Logger):
        self._last_msg_at = time.time()
        try:
            msg = json.loads(raw)
        except: return

        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            if not isinstance(event, dict): continue
            etype = event.get("event_type")
            
            # Polymarket use best_bid_ask or price_change
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

    def _on_open(self, ws, current_subs: set, desired_subs: set, ilogger: logging.Logger):
        # Drain any queued updates before subscribing on (re)connect
        latest = None
        while not self._sub_update_queue.empty():
            try:
                latest = self._sub_update_queue.get_nowait()
            except Exception:
                pass
        if latest is not None:
            desired_subs.clear()
            desired_subs.update(latest)

        ilogger.info("[WS-MPC] Connected. Syncing %d subscriptions", len(desired_subs))
        if desired_subs:
            self._send_op(ws, list(desired_subs), "subscribe", ilogger)
            current_subs.clear()
            current_subs.update(desired_subs)

    def _on_close(self, ws, code, msg, current_subs: set, ilogger: logging.Logger):
        ilogger.info("[WS-MPC] Closed: %s (code: %s)", msg, code)
        current_subs.clear()

    def _send_op(self, ws, ids: list, op: str, ilogger: logging.Logger):
        msg = json.dumps({
            "assets_ids": ids,
            "type": "market",
            "operation": op,
            "custom_feature_enabled": True if op == "subscribe" else False
        })
        try:
            ws.send(msg)
            ilogger.info("[WS-MPC] SENT %s for %d assets", op.upper(), len(ids))
        except Exception as exc:
            ilogger.warning("[WS-MPC] Send %s failed: %s", op, exc)


def make_ws_exit_callback(state_path: str, lock, broadcaster=None):
    """(Main Process) Handles price updates and triggers exits (TP/SL)."""
    from market_discovery_internal.state_persistence import load_paper_state, save_paper_state
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

                    # Evaluate Exit (Strategy-Aware)
                    stop = float(pos.get("stop_loss_price", 0))
                    target = float(pos.get("target_price", 1))
                    strategy = pos.get("target_strategy", "swing")
                    
                    reason = None
                    if bid_price <= stop: 
                        reason = "stop_loss"
                    elif bid_price >= target and strategy == "swing":
                        reason = "take_profit_100pct"

                    if reason:
                        closed = close_paper_position(pos, bid_price, reason, datetime.now(timezone.utc))
                        positions.pop(i)
                        state["positions"] = positions
                        state.setdefault("history", []).append(closed)
                        changed = True
                        print(f"[WS-EXIT] {pos.get('city','?').upper()} | {reason} @ {bid_price:.4f} | Strategy: {strategy}")
                        if broadcaster:
                            broadcaster.broadcast_closed(token_id, pos.get('city',''), reason, bid_price)
                        break

                if changed:
                    save_paper_state(state, state_path)
            except Exception as exc:
                logger.warning("[WS-CALLBACK] Error: %s", exc)
    return callback

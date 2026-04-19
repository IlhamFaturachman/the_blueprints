"""
ws_price_watcher.py — Real-time YES token price monitor via Polymarket WebSocket.
Hardened for absolute stability and safe process termination.
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
        """Signal the watcher to stop and terminate the process with hardened cleanup."""
        self._stop_event.set()
        
        # Local capture of the process handle to avoid race conditions during shutdown.
        p = self._process
        if p is not None:
            try:
                if p.is_alive():
                    pid = getattr(p, 'pid', 'unknown')
                    logger.info("[WS-MPC] Terminating watcher process %s...", pid)
                    p.terminate()
                    p.join(timeout=5)
                    if p.is_alive():
                        p.kill()
            except (AttributeError, Exception) as e:
                logger.warning("[WS-MPC] Error during process termination: %s", e)
            finally:
                # Always clear the handle regardless of success to allow restart.
                self._process = None
        
        logger.info("[WS-MPC] PriceWatcher cleanup complete.")

    def update_subscriptions(self, token_ids: set) -> None:
        """Send a subscription update to the watcher subprocess via queue."""
        try:
            self._sub_update_queue.put_nowait(list(token_ids))
        except Exception as exc:
            logger.warning("[WS-MPC] Could not queue subscription update: %s", exc)

    def _run_loop(self) -> None:
        """Internal subprocess loop — handles connection and watchdog."""
        import os
        log_dir = "logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        internal_log_path = os.path.join(log_dir, "ws_internal.log")
        ilogger = logging.getLogger("PriceWatcherSub")
        ilogger.setLevel(logging.INFO)
        if not ilogger.handlers:
            fh = logging.handlers.RotatingFileHandler(internal_log_path, maxBytes=1024*1024*5, backupCount=2)
            fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            ilogger.addHandler(fh)
        
        ilogger.info("=== PriceWatcher Subprocess Starting ===")
        self._ilogger = ilogger

        try:
            import websocket
        except ImportError:
            ilogger.error("[WS-MPC] websocket-client NOT INSTALLED.")
            return

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
        desired_subs = set()

        while not self._stop_event.is_set():
            try:
                ilogger.info("[WS-MPC] Connecting to %s", self._url)
                import ssl
                ilogger.info("[WS-MPC] Handshake started for %s (Origin: https://polymarket.com)", self._url)
                ws = websocket.WebSocketApp(
                    self._url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_pong=self._on_pong,
                    header={"Origin": "https://polymarket.com"}
                )

                def watchdog_fn():
                    ilogger.info("[WS-MPC] Watchdog thread started.")
                    while not self._stop_event.is_set():
                        if ws.sock and ws.sock.connected:
                            # 1. Heartbeat timeout check
                            if time.time() - self._last_msg_at > self._watchdog_timeout:
                                ilogger.warning("[WS-MPC] Watchdog timeout: closing stale connection")
                                ws.close()
                                break
                                
                            # 2. Process subscription updates
                            latest = None
                            while not self._sub_update_queue.empty():
                                try: latest = self._sub_update_queue.get_nowait()
                                except: pass
                            
                            if latest is not None:
                                ilogger.info("[WS-MPC] Received subscription update: %d tokens", len(latest))
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
                    ilogger.info("[WS-MPC] Watchdog thread exiting.")

                threading.Thread(target=watchdog_fn, daemon=True).start()
                
                ilogger.info("[WS-MPC] Starting run_forever loop (IPv4 Force)...")
                ws.run_forever(
                    ping_interval=self._ping_interval, 
                    sslopt={"cert_reqs": ssl.CERT_NONE}, # Simpler SSL bypass
                    # Force AF_INET (IPv4) to avoid IPv6 deadlock on VPS
                    sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),)
                )
            except Exception as e:
                ilogger.error("[WS-MPC] Connection loop error: %s", e)
            
            if self._stop_event.is_set(): break
            time.sleep(self._reconnect_delay + random.uniform(0, 5))

    def _on_message(self, ws, raw: str):
        self._last_msg_at = time.time()
        ilogger = self._ilogger
        try:
            msg = json.loads(raw)
        except: return
        
        # Polymarket often sends single objects or lists
        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            if not isinstance(event, dict): continue
            etype = event.get("event_type")
            
            # [HEARTBEAT] Log raw event arrival occasionally to confirm connection activity
            if random.random() < 0.05: # 5% sample for internal logs
                ilogger.info("[WS-MPC] Heartbeat: received %s for asset/market %s", etype, event.get("asset_id") or event.get("market"))

            # Match Polymarket CLI/WS event types: book, best_bid_ask, price_change, last_trade_price
            if etype in ["best_bid_ask", "price_change", "book", "last_trade_price"]:
                tid = event.get("asset_id") or event.get("market") 
                if not tid: continue
                
                # Universal Price Extraction Logic
                price_val = None
                
                if etype == "price_change":
                    for c in event.get("price_changes", []):
                        cid = c.get("asset_id") or tid
                        # Extract price from various possible fields
                        cp = c.get("best_bid") or c.get("price") or c.get("best_ask")
                        if cid and cp is not None:
                            try: self._update_queue.put((str(cid), float(cp)))
                            except: pass
                    continue
                    
                elif etype == "book":
                    # Orderbook format varies: [[price, size], ...] or [{"price":"0.5", ...}, ...]
                    bids = event.get("bids", [])
                    if bids:
                        try:
                            # Safely extract from first element (highest bid)
                            first = bids[0]
                            if isinstance(first, list) and len(first) > 0:
                                price_val = first[0]
                            elif isinstance(first, dict):
                                price_val = first.get("price") or first.get("best_bid")
                        except: pass
                
                elif etype == "last_trade_price":
                    price_val = event.get("price")
                
                else: # best_bid_ask
                    price_val = event.get("best_bid")

                if tid and price_val is not None:
                    try:
                        f_price = float(price_val)
                        # Filter out obviously rogue prices like $0 or extreme spikes
                        if 0.001 <= f_price <= 0.999:
                            self._update_queue.put((str(tid), f_price))
                            if random.random() < 0.1: # 10% sample for successful parse logs
                                ilogger.info("[WS-MPC] Parsed %s: %s -> %s", etype, tid, f_price)
                    except (ValueError, TypeError):
                        pass

    def _on_error(self, ws, error):
        self._ilogger.warning("[WS-MPC] Socket Error: %s", error)

    def _on_pong(self, ws, message):
        self._last_msg_at = time.time()
        if random.random() < 0.1:
            self._ilogger.info("[WS-MPC] Pong received (link healthy)")

    def _on_open(self, ws):
        self._ilogger.info("[WS-MPC] Connection established. Initializing subscriptions...")
        # Note: Do not clear sub_update_queue here, let the watchdog process the backlog.
        
        # We will let the watchdog thread handle the initial 'desired_subs' sync 
        # on the next 1s tick to avoid race conditions.

    def _on_close(self, ws, code, msg):
        self._ilogger.info("[WS-MPC] Connection closed: Code=%s, Msg=%s", code, msg)

    def _send_op(self, ws, ids, op, ilogger):
        """Send subscription/unsubscription operation to Polymarket CLOB."""
        # Standard Polymarket CLOB subscription follows this pattern:
        payload = {
            "type": "subscribe", # or 'unsubscribe' logic
            "topic": "price_change", # Monitor price changes for these specific assets
            "assets_ids": ids
        }
        
        if op == "unsubscribe":
             payload["type"] = "unsubscribe"
        
        msg = json.dumps(payload)
        try:
            ws.send(msg)
            ilogger.info("[WS-MPC] Sent %s for %d assets (%s...)", op, len(ids), str(ids[0])[:12] if ids else "")
        except Exception as e:
            ilogger.warning("[WS-MPC] Send error: %s", e)


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
                        # [AUDIT] Fix B: 2h cooldown for price-based Stop Loss
                        # This prevents "noise" exits during the initial spread friction.
                        opened_at_str = pos.get("opened_at")
                        can_sl_fire = True
                        if opened_at_str:
                            try:
                                opened_at = datetime.fromisoformat(opened_at_str)
                                age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                                if age_hours < 2:
                                    can_sl_fire = False
                            except (ValueError, TypeError):
                                pass
                        
                        if can_sl_fire:
                            reason = "stop_loss"
                    elif bid_price >= target and strategy == "swing":
                        reason = "take_profit_100pct"

                    if reason:
                        closed = close_paper_position(pos, bid_price, reason, datetime.now(timezone.utc))
                        positions.pop(i)
                        state["positions"] = positions
                        state.setdefault("history", []).append(closed)
                        # [ACCOUNTING] Credit cash with exit proceeds
                        exit_value = float(closed.get("exit_value", 0.0))
                        state.setdefault("meta", {})["cash"] = round(
                            float(state["meta"].get("cash", 0.0)) + exit_value, 4
                        )
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

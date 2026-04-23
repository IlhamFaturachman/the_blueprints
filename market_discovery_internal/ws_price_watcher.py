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

from market_discovery_internal.config import (
    SL_COOLDOWN_SECONDS, SL_TICK_COUNT, WS_SL_CLEANUP_INTERVAL,
    PENNY_BID_THRESHOLD,
    FLASH_CRASH_MAX_DROP_PCT,
    FLASH_CRASH_MIN_TICK_WINDOW_SECONDS,
    FLASH_CRASH_REST_CONFIRM_ENABLED,
    FLASH_CRASH_MIN_DEPTH_USD,
    FLASH_CRASH_L1_ESCAPE_SECONDS,
    SL_MAX_DELAY_SECONDS,
    EXACT_BRACKET_SL_COOLDOWN_HOURS,
)

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
        os.makedirs(log_dir, exist_ok=True)
        
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
                except Exception: pass
            return original_getaddrinfo(host, port, family, type, proto, flags)
        socket.getaddrinfo = _prefer_ipv4

        self._last_msg_at = time.time()
        current_subs = set()
        desired_subs = set()
        _subs_lock = threading.Lock()  # HIGH-1: Protect current_subs and desired_subs
        # [FIX] Flag to signal watchdog that a fresh connection was established
        # and current_subs must be cleared so all desired_subs get re-subscribed.
        _needs_resub = threading.Event()
        # CRITICAL-1: Event to signal old watchdog thread to exit before spawning new one
        _watchdog_stop = threading.Event()

        import ssl
        import certifi

        while not self._stop_event.is_set():
            try:
                ilogger.info("[WS-MPC] Connecting to %s", self._url)
                ilogger.info("[WS-MPC] Handshake started for %s (Origin: https://polymarket.com)", self._url)

                # CRITICAL-1: Signal old watchdog to stop, wait for it to exit
                _watchdog_stop.set()
                time.sleep(0.5)
                _watchdog_stop.clear()

                # [FIX] Signal that the next connection needs full re-subscribe
                _needs_resub.set()

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
                    from queue import Empty
                    ilogger.info("[WS-MPC] Watchdog thread started.")
                    while not self._stop_event.is_set() and not _watchdog_stop.is_set():
                        if ws.sock and ws.sock.connected:
                            # [FIX] On fresh connection, clear current_subs so all
                            # desired tokens get re-subscribed on the new socket.
                            if _needs_resub.is_set():
                                _needs_resub.clear()
                                with _subs_lock:
                                    if current_subs:
                                        ilogger.info("[WS-MPC] Reconnect detected — clearing %d stale subs for full re-subscribe", len(current_subs))
                                    current_subs.clear()

                            # 1. Heartbeat timeout check
                            if time.time() - self._last_msg_at > self._watchdog_timeout:
                                ilogger.warning("[WS-MPC] Watchdog timeout: closing stale connection")
                                ws.close()
                                break

                            # 2. Process subscription updates (HIGH-3: drain with try/except Empty)
                            latest = None
                            while True:
                                try:
                                    latest = self._sub_update_queue.get_nowait()
                                except Exception:
                                    break

                            if latest is not None:
                                ilogger.info("[WS-MPC] Received subscription update: %d tokens", len(latest))
                                with _subs_lock:
                                    desired_subs.clear()
                                    desired_subs.update(latest)

                            with _subs_lock:
                                to_add = desired_subs - current_subs
                                to_remove = current_subs - desired_subs
                            if to_add:
                                self._send_op(ws, list(to_add), "subscribe", ilogger)
                                with _subs_lock:
                                    current_subs.update(to_add)
                            if to_remove:
                                self._send_op(ws, list(to_remove), "unsubscribe", ilogger)
                                with _subs_lock:
                                    current_subs.difference_update(to_remove)

                        time.sleep(1)
                    ilogger.info("[WS-MPC] Watchdog thread exiting.")

                threading.Thread(target=watchdog_fn, daemon=True).start()

                # CRITICAL-2: Use proper SSL verification with certifi CA bundle
                ssl_context = ssl.create_default_context(cafile=certifi.where())
                ilogger.info("[WS-MPC] Starting run_forever loop (IPv4 Force)...")
                ws.run_forever(
                    ping_interval=self._ping_interval,
                    sslopt={"ssl_context": ssl_context},
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
        except Exception:
            ilogger.debug("[WS-MPC] Failed to parse JSON message")
            return
        
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
                            try: self._update_queue.put_nowait((str(cid), float(cp)))
                            except Exception: pass
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
                        except Exception:
                            pass  # Malformed orderbook level — skip silently
                
                elif etype == "last_trade_price":
                    price_val = event.get("price")
                
                else: # best_bid_ask
                    price_val = event.get("best_bid")

                if tid and price_val is not None:
                    try:
                        f_price = float(price_val)
                        # Filter out rogue prices outside valid 0-1 range
                        if 0.0 <= f_price <= 1.0:
                            try:
                                self._update_queue.put_nowait((str(tid), f_price))
                            except Exception:
                                pass  # Queue full — drop stale data
                            if random.random() < 0.1: # 10% sample for successful parse logs
                                ilogger.info("[WS-MPC] Parsed %s: %s -> %s", etype, tid, f_price)
                    except (ValueError, TypeError):
                        pass

    def _on_error(self, ws, error):
        self._ilogger.warning("[WS-MPC] Socket Error: %s", error)

    def _on_pong(self, ws, message):
        self._last_msg_at = time.time()
        # [FIX] Send liveness heartbeat to main process queue so
        # _last_ws_update_at stays fresh even when no price data flows.
        # Uses special sentinel token "__ws_heartbeat__" that the consumer
        # recognizes and uses only to update the timestamp.
        try:
            self._update_queue.put_nowait(("__ws_heartbeat__", 0.0))
        except Exception:
            pass  # Queue full — heartbeat drop is non-critical
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
        """Send subscription/unsubscription operation to Polymarket CLOB.
        
        Polymarket Market Channel subscription format (from official docs):
        {
            "assets_ids": ["<token_id_1>", ...],
            "type": "market",
            "custom_feature_enabled": true
        }
        Unsubscribe uses the same format but no "type" field — just send
        an updated subscription with only the assets you want.
        
        Note: We subscribe per batch. For unsubscribe, Polymarket doesn't
        have a dedicated unsubscribe message — the server tracks what you
        subscribed to and you simply stop receiving updates for assets
        not in your latest subscription.
        """
        # Ensure all IDs are strings (Polymarket expects string token IDs)
        str_ids = [str(tid) for tid in ids]
        
        if op == "subscribe":
            payload = {
                "assets_ids": str_ids,
                "type": "market",
                "custom_feature_enabled": True,
            }
        else:
            # Unsubscribe: re-subscribe with empty list or just log
            # Polymarket WS doesn't have explicit unsubscribe — we just
            # don't include these assets in the next subscription.
            ilogger.info("[WS-MPC] Unsubscribe requested for %d assets (will exclude from next sub)", len(str_ids))
            return
        
        msg = json.dumps(payload)
        try:
            ws.send(msg)
            ilogger.info("[WS-MPC] Sent %s for %d assets (%s...)", op, len(str_ids), str(str_ids[0])[:20] if str_ids else "")
        except Exception as e:
            ilogger.warning("[WS-MPC] Send error: %s", e)


def make_ws_exit_callback(state_path: str, lock, broadcaster=None, exchange=None):
    """(Main Process) Handles price updates and triggers exits (TP/SL).

    Args:
        exchange: BlueprintsExchange instance for live trading (None for paper mode).

    Flash Crash Shield (4 layers):
      L1 — Spike Detector: ignore single-tick drops > FLASH_CRASH_MAX_DROP_PCT
      L2 — Time-Windowed Ticks: SL ticks must span >= FLASH_CRASH_MIN_TICK_WINDOW_SECONDS
      L3 — REST Confirmation: verify bid via REST orderbook before executing SL
      L4 — Depth Validation: require minimum bid-side depth (USD) to confirm real selling
    """
    from market_discovery_internal.state_persistence import load_paper_state, save_paper_state
    from market_discovery_internal.cycles import close_paper_position
    from datetime import datetime, timezone

    # [Hardening] Track SL ticks as (count, first_tick_timestamp) per token
    # to enforce both tick-count AND time-window requirements.
    _sl_tick_counters = {}   # {token_id: (count, first_tick_timestamp)}
    _sl_cleanup_counter = 0  # HIGH-5: Track calls for periodic cleanup

    # [Flash-Shield L1] Track last known good (non-spike) price per token
    _last_good_prices = {}

    # [P5-FIX] Track when position first went below SL — force-close after SL_MAX_DELAY_SECONDS
    _sl_first_below_at = {}  # {token_id: timestamp}

    def callback(token_id: str, bid_price: float) -> None:
        nonlocal _sl_cleanup_counter
        with lock:
            try:
                state = load_paper_state(state_path)
                positions = state.get("positions", [])

                # HIGH-5: Periodically clean up _sl_tick_counters and
                # _last_good_prices for tokens no longer in open positions
                _sl_cleanup_counter += 1
                if _sl_cleanup_counter >= WS_SL_CLEANUP_INTERVAL:
                    _sl_cleanup_counter = 0
                    open_token_ids = {
                        p.get("token_id") for p in positions
                        if p.get("status") == "open"
                    }
                    stale_keys = [k for k in _sl_tick_counters if k not in open_token_ids]
                    for k in stale_keys:
                        del _sl_tick_counters[k]
                    stale_good = [k for k in _last_good_prices if k not in open_token_ids]
                    for k in stale_good:
                        del _last_good_prices[k]
                    stale_below = [k for k in _sl_first_below_at if k not in open_token_ids]
                    for k in stale_below:
                        del _sl_first_below_at[k]
                changed = False

                for i, pos in enumerate(positions):
                    if pos.get("token_id") != token_id: continue
                    if pos.get("status") != "open": continue

                    # Evaluate Exit (Strategy-Aware)
                    stop = float(pos.get("stop_loss_price", 0))
                    target = float(pos.get("target_price", 1))
                    strategy = pos.get("target_strategy", "swing")

                    # ---------------------------------------------------
                    # L1: Spike Detector — ignore single-tick flash crashes
                    # Escape hatch: if L2 tick counter already has 10+ ticks
                    # spanning 5+ minutes, this is NOT a spike — it's a real
                    # price movement. Allow it through.
                    # ---------------------------------------------------
                    last_good = _last_good_prices.get(token_id)
                    if last_good is not None and last_good > 0:
                        drop_pct = (last_good - bid_price) / last_good
                        if drop_pct > FLASH_CRASH_MAX_DROP_PCT:
                            # Check escape hatch: has L2 accumulated enough evidence?
                            _existing_l2 = _sl_tick_counters.get(token_id)
                            _l1_override = False
                            if _existing_l2:
                                _l2_count, _l2_first = _existing_l2
                                _l2_elapsed = time.time() - _l2_first
                                if _l2_count >= 10 and _l2_elapsed >= FLASH_CRASH_L1_ESCAPE_SECONDS:
                                    # 10+ ticks over escape threshold — this is real, not a spike
                                    _l1_override = True
                                    logger.warning(
                                        "[FLASH-SHIELD] L1: Override — %d ticks over %.0fs for %s. Allowing through.",
                                        _l2_count, _l2_elapsed, pos.get('city', '?'),
                                    )
                            if not _l1_override:
                                logger.warning(
                                    "[FLASH-SHIELD] L1: Spike detected for %s: %.4f -> %.4f (%.0f%% drop). IGNORING.",
                                    pos.get('city', '?'), last_good, bid_price, drop_pct * 100,
                                )
                                continue  # Skip this position entirely for this tick

                    # Update last known good price (only for non-penny bids)
                    if bid_price > PENNY_BID_THRESHOLD:
                        _last_good_prices[token_id] = bid_price

                    reason = None
                    if bid_price <= stop:
                        # [P5-FIX] Track first time below SL for max delay timer
                        if token_id not in _sl_first_below_at:
                            _sl_first_below_at[token_id] = time.time()

                        # [AUDIT] Fix B: cooldown for price-based Stop Loss
                        # This prevents "noise" exits during the initial spread friction.
                        # [P5-FIX] Exact brackets use shorter cooldown (more volatile)
                        opened_at_str = pos.get("opened_at")
                        can_sl_fire = True
                        _direction = pos.get("direction", "")
                        _sl_cooldown = EXACT_BRACKET_SL_COOLDOWN_HOURS * 3600 if _direction == "exact" else SL_COOLDOWN_SECONDS
                        if opened_at_str:
                            try:
                                opened_at = datetime.fromisoformat(opened_at_str)
                                age_seconds = (datetime.now(timezone.utc) - opened_at).total_seconds()
                                if age_seconds < _sl_cooldown:
                                    can_sl_fire = False
                            except (ValueError, TypeError):
                                pass

                        # [P5-FIX] Max SL delay: force-close if below SL for too long
                        # Safety net for thin markets where flash crash shield blocks legitimate SL
                        _below_since = _sl_first_below_at.get(token_id)
                        if _below_since and (time.time() - _below_since) >= SL_MAX_DELAY_SECONDS:
                            reason = "stop_loss"
                            _sl_tick_counters.pop(token_id, None)
                            _sl_first_below_at.pop(token_id, None)
                            logger.warning(
                                "[FLASH-SHIELD] MAX-DELAY: %s below SL for %.0fs — force-closing.",
                                pos.get('city', '?'), time.time() - _below_since,
                            )

                        if can_sl_fire and reason is None:
                            # ---------------------------------------------------
                            # L2: Time-Windowed Ticks — SL ticks must span a
                            #     minimum wall-clock window before triggering exit.
                            #     first_tick_time is set when price FIRST drops
                            #     below SL and is NOT reset while price stays
                            #     below SL. This ensures that sustained drops
                            #     (not spikes) eventually trigger exit.
                            # ---------------------------------------------------
                            current_time = time.time()
                            existing = _sl_tick_counters.get(token_id)
                            if existing:
                                count, first_tick_time = existing
                                count += 1
                                _sl_tick_counters[token_id] = (count, first_tick_time)
                                elapsed = current_time - first_tick_time
                                if count >= SL_TICK_COUNT and elapsed >= FLASH_CRASH_MIN_TICK_WINDOW_SECONDS:
                                    reason = "stop_loss"  # Confirmed real SL
                                else:
                                    # Only log every 10th tick to avoid log spam
                                    if count <= 3 or count % 10 == 0:
                                        logger.warning(
                                            "[FLASH-SHIELD] L2: SL tick %d/%d for %s, elapsed %.1fs/%.1fs",
                                            count, SL_TICK_COUNT, pos.get('city', '?'),
                                            elapsed, FLASH_CRASH_MIN_TICK_WINDOW_SECONDS,
                                        )
                                    continue
                            else:
                                _sl_tick_counters[token_id] = (1, current_time)
                                logger.warning(
                                    "[FLASH-SHIELD] L2: SL tick 1/%d for %s @ %.4f",
                                    SL_TICK_COUNT, pos.get('city', '?'), bid_price,
                                )
                                continue

                            # ---------------------------------------------------
                            # L3: REST Confirmation — verify price via REST API
                            # L4: Depth Validation — require minimum bid depth
                            # ---------------------------------------------------
                            if reason == "stop_loss" and FLASH_CRASH_REST_CONFIRM_ENABLED:
                                try:
                                    import requests as _req
                                    rest_url = f"https://clob.polymarket.com/book?token_id={token_id}"
                                    resp = _req.get(rest_url, timeout=5)
                                    if resp.status_code == 200:
                                        book = resp.json()
                                        # Extract best bid — handle both list [[price,size],...] and dict [{"price":..,"size":..},...] formats
                                        rest_bid = None
                                        total_bid_depth = 0.0
                                        for b in book.get("bids", []):
                                            if isinstance(b, dict):
                                                bp = float(b.get("price", 0))
                                                bs = float(b.get("size", 0))
                                            elif isinstance(b, (list, tuple)) and len(b) >= 2:
                                                bp = float(b[0])
                                                bs = float(b[1])
                                            else:
                                                continue
                                            if rest_bid is None:
                                                rest_bid = bp  # First = best bid
                                            total_bid_depth += bp * bs

                                        # L3: REST bid above SL → WS price is stale/spike
                                        if rest_bid is not None and rest_bid > stop:
                                            logger.warning(
                                                "[FLASH-SHIELD] L3: REST bid %.4f > SL %.4f for %s. "
                                                "WS bid %.4f is stale/spike. BLOCKING exit.",
                                                rest_bid, stop, pos.get('city', '?'), bid_price,
                                            )
                                            _sl_tick_counters.pop(token_id, None)
                                            reason = None

                                        # L4: Depth check — thin book means rogue order
                                        if reason == "stop_loss" and total_bid_depth < FLASH_CRASH_MIN_DEPTH_USD:
                                            logger.warning(
                                                "[FLASH-SHIELD] L4: Bid depth $%.2f < $%.2f minimum for %s. BLOCKING exit.",
                                                total_bid_depth, FLASH_CRASH_MIN_DEPTH_USD,
                                                pos.get('city', '?'),
                                            )
                                            _sl_tick_counters.pop(token_id, None)
                                            reason = None
                                except Exception as e:
                                    logger.warning(
                                        "[FLASH-SHIELD] L3: REST check failed: %s. Proceeding with WS price.", e,
                                    )

                            # Clear SL tick counter on confirmed exit
                            if reason == "stop_loss":
                                _sl_tick_counters.pop(token_id, None)
                    else:
                        # Reset counter if price is back in safety zone
                        _sl_tick_counters.pop(token_id, None)
                        _sl_first_below_at.pop(token_id, None)  # [P5-FIX] Clear max delay timer

                    if bid_price >= target and strategy == "swing":
                        reason = "take_profit_100pct"

                    if reason:
                        # [EXECUTION BRIDGE] Check _exit_in_progress to prevent double-sell
                        if pos.get("_exit_in_progress"):
                            continue

                        # [EXECUTION BRIDGE] Live trading exit
                        from market_discovery_internal.config import LIVE_TRADING_ENABLED as _WS_LIVE
                        if _WS_LIVE and exchange is not None and getattr(exchange, 'available', False):
                            pos["_exit_in_progress"] = True
                            from market_discovery_internal.config import URGENT_EXIT_REASONS
                            _ws_token = pos.get("token_id")
                            _ws_qty = float(pos.get("quantity", 0))

                            if reason in URGENT_EXIT_REASONS:
                                # Taker sell (urgent — stop loss, flash crash)
                                book = exchange.get_orderbook_info(_ws_token) if _ws_token else None
                                if book and book.get("best_bid") is not None:
                                    worst_price = round(book["best_bid"] * 0.95, 4)
                                    sell_result = exchange.place_taker_sell(
                                        _ws_token, _ws_qty, worst_price,
                                        book["tick_size"], book["neg_risk"],
                                    )
                                    if sell_result.get("success"):
                                        fill_price = book["best_bid"]
                                        closed = close_paper_position(pos, fill_price, reason, datetime.now(timezone.utc))
                                    else:
                                        logger.error("[WS-LIVE-EXIT] Taker sell FAILED for %s: %s",
                                                     pos.get("city", "?"), sell_result.get("reason"))
                                        pos.pop("_exit_in_progress", None)
                                        continue
                                else:
                                    pos.pop("_exit_in_progress", None)
                                    continue
                            else:
                                # Maker sell (normal — take profit)
                                book = exchange.get_orderbook_info(_ws_token) if _ws_token else None
                                if book and book.get("best_ask") is not None:
                                    maker_price = exchange.compute_maker_sell_price(
                                        book["best_ask"], book["tick_size_float"],
                                    )
                                    if maker_price and maker_price > 0:
                                        sell_result = exchange.place_maker_sell(
                                            _ws_token, _ws_qty, maker_price,
                                            book["tick_size"], book["neg_risk"],
                                        )
                                        if sell_result.get("success"):
                                            # Set pending_exit — main cycle monitors fill
                                            pos["status"] = "pending_exit"
                                            pos["pending_exit_order_id"] = sell_result["order_id"]
                                            pos["pending_exit_reason"] = reason
                                            pos["pending_exit_retry_count"] = 0
                                            pos["pending_exit_placed_at"] = datetime.now(timezone.utc).isoformat()
                                            pos.pop("_exit_in_progress", None)
                                            changed = True
                                            logger.info("[WS-LIVE-EXIT] Maker sell placed for %s @ $%.4f",
                                                        pos.get("city", "?"), maker_price)
                                            break
                                # Fallback: paper close if maker sell fails
                                closed = close_paper_position(pos, bid_price, reason, datetime.now(timezone.utc))
                        else:
                            # [PAPER MODE] Unchanged paper trading exit
                            closed = close_paper_position(pos, bid_price, reason, datetime.now(timezone.utc))

                        positions.pop(i)
                        state["positions"] = positions
                        state.setdefault("history", []).append(closed)
                        # [ACCOUNTING] Credit cash with NET exit proceeds (fees deducted)
                        net_exit = float(closed.get("net_exit_value", closed.get("exit_value", 0.0)))
                        state.setdefault("meta", {})["cash"] = round(
                            float(state["meta"].get("cash", 0.0)) + net_exit, 4
                        )
                        changed = True
                        logger.info("[WS-EXIT] %s | %s @ %.4f | Strategy: %s", pos.get('city','?').upper(), reason, bid_price, strategy)
                        if broadcaster:
                            broadcaster.broadcast_closed(token_id, pos.get('city',''), reason, bid_price)
                        break

                if changed:
                    save_paper_state(state, state_path)
            except Exception as exc:
                logger.warning("[WS-CALLBACK] Error: %s", exc)
    return callback

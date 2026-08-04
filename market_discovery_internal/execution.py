"""
Execution Bridge - Live Trading via Polymarket CLOB API.

Provides BlueprintsExchange class for placing maker (post-only) and taker (FOK)
orders on Polymarket. Thread-safe, graceful error handling, heartbeat management.

CRITICAL IMPLEMENTATION NOTES (verified against py_clob_client v0.34.6 via
inspect.signature on 22 April 2026):

1. MUST use two-step order flow for post-only (maker) orders:
   signed = client.create_order(OrderArgs, options)
   result = client.post_order(signed, OrderType.GTC, post_only=True)

   create_and_post_order() does NOT support post_only - it hardcodes
   post_order(order) with defaults (post_only=False). Using it would
   make us TAKER and pay 5% fee.

2. MUST use two-step for market (taker) orders too:
   signed = client.create_market_order(MarketOrderArgs, options)
   result = client.post_order(signed, OrderType.FOK)

   create_market_order() only signs - does NOT post.

3. MarketOrderArgs uses 'amount' NOT 'size':
   - BUY: amount = dollar amount to spend
   - SELL: amount = number of shares to sell

4. tick_size in PartialCreateOrderOptions MUST be a string ("0.01"),
   not a float (0.01). TickSize is Literal['0.1','0.01','0.001','0.0001'].

5. get_order_book returns fields as STRINGS (min_order_size="5",
   tick_size="0.01"). Must cast to int/float for arithmetic.

6. ALL trading operations require Level 2 auth (API creds).
   Must call create_or_derive_api_creds() and pass creds= to constructor.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import (
        OrderArgsV2 as OrderArgs,
        MarketOrderArgsV2 as MarketOrderArgs,
        OrderType,
        PartialCreateOrderOptions,
    )
    from py_clob_client_v2.order_builder.constants import BUY, SELL

    PY_CLOB_AVAILABLE = True
except ImportError:
    # Fallback to v1 if v2 not installed (paper mode)
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import (
            OrderArgs,
            MarketOrderArgs,
            OrderType,
            PartialCreateOrderOptions,
        )
        from py_clob_client.order_builder.constants import BUY, SELL
        PY_CLOB_AVAILABLE = True
        PY_CLOB_V2 = False
    except ImportError:
        PY_CLOB_AVAILABLE = False
        PY_CLOB_V2 = False
else:
    PY_CLOB_V2 = True


class BlueprintsExchange:
    """Live trading bridge to Polymarket CLOB API.

    Thread-safe: all CLOB operations protected by self._lock.
    Heartbeat: background daemon thread sends heartbeat every 5s.
    Graceful: never crashes - all errors caught, logged, and returned as status dicts.
    """

    # ------------------------------------------------------------------ init
    def __init__(self):
        """Initialize ClobClient with L1 + L2 credentials from env.

        Steps:
        1. Read PRIVATE_KEY, FUNDER_ADDRESS, SIGNATURE_TYPE from config
        2. Create temp ClobClient (L1 auth only - for deriving creds)
        3. Derive API credentials via create_or_derive_api_creds() (L2 auth)
        4. Create FULL ClobClient with L1 + L2 auth (creds= parameter)
        5. Verify connection with get_ok()

        If ANY step fails: set self.available = False, log CRITICAL.
        Bot falls back to paper mode.
        """
        from market_discovery_internal.config import (
            POLYMARKET_CLOB_API_URL,
            POLYMARKET_PRIVATE_KEY,
            POLYMARKET_SIGNATURE_TYPE,
            POLYMARKET_FUNDER_ADDRESS,
        )

        self.available = False
        self.client = None
        self._lock = threading.Lock()
        self._running = False
        self._heartbeat_thread = None
        self.heartbeat_healthy = False
        self._heartbeat_id = ""

        if not PY_CLOB_AVAILABLE:
            logger.critical("[EXCHANGE] py_clob_client not installed - live trading unavailable")
            return

        pk = POLYMARKET_PRIVATE_KEY
        if not pk:
            logger.critical("[EXCHANGE] PRIVATE_KEY not set - live trading unavailable")
            return

        host = POLYMARKET_CLOB_API_URL
        chain_id = 137  # Polygon
        sig_type = POLYMARKET_SIGNATURE_TYPE
        funder = POLYMARKET_FUNDER_ADDRESS or None

        try:
            # Step 1-2: Temp L1 client to derive API creds
            temp_client = ClobClient(
                host=host,
                chain_id=chain_id,
                key=pk,
                signature_type=sig_type,
                funder=funder,
            )

            # Step 3: Derive L2 API credentials
            api_creds = temp_client.create_or_derive_api_key()
            logger.info("[EXCHANGE] L2 API credentials derived successfully")

            # Step 4: Create FULL client with L2 auth (creds= CRITICAL)
            self.client = ClobClient(
                host=host,
                chain_id=chain_id,
                key=pk,
                creds=api_creds,  # CRITICAL: without this, all trading ops throw AssertionError
                signature_type=sig_type,
                funder=funder,
            )

            # Step 5: Verify connection
            ok = self.client.get_ok()
            if ok == "OK":
                self.available = True
                logger.info("[EXCHANGE] ClobClient initialized with L2 auth - live trading ready")
            else:
                logger.critical("[EXCHANGE] ClobClient health check failed: %s", ok)

        except Exception as exc:
            logger.critical("[EXCHANGE] Initialization failed: %s", exc, exc_info=True)

    # -------------------------------------------------------------- heartbeat
    def start_heartbeat(self):
        """Start background heartbeat thread (daemon).

        Thread sends POST /heartbeat every 5 seconds.
        If 3 consecutive failures: set self.heartbeat_healthy = False.
        """
        if not self.available:
            logger.warning("[HEARTBEAT] Exchange not available - skipping heartbeat start")
            return

        self._running = True
        self.heartbeat_healthy = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="exchange-heartbeat"
        )
        self._heartbeat_thread.start()
        logger.info("[HEARTBEAT] Background heartbeat thread started (5s interval)")

    def _heartbeat_loop(self):
        """Internal heartbeat loop - runs in daemon thread."""
        consecutive_failures = 0

        while self._running:
            try:
                with self._lock:
                    resp = self.client.post_heartbeat(self._heartbeat_id)
                # post_heartbeat returns a dict; extract heartbeat_id for next call
                if isinstance(resp, dict):
                    self._heartbeat_id = resp.get("heartbeat_id", self._heartbeat_id)
                elif isinstance(resp, str):
                    self._heartbeat_id = resp
                consecutive_failures = 0
                self.heartbeat_healthy = True
            except Exception as exc:
                consecutive_failures += 1
                logger.error("[HEARTBEAT] Failure #%d: %s", consecutive_failures, exc)
                if consecutive_failures >= 3:
                    self.heartbeat_healthy = False
                    logger.critical(
                        "[HEARTBEAT] 3 consecutive failures - orders may be cancelled!"
                    )

            time.sleep(5)

    def stop(self):
        """Stop heartbeat thread, cancel all open orders."""
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=10)
        if self.available:
            try:
                self.cancel_all_orders()
            except Exception as exc:
                logger.error("[EXCHANGE] Error during shutdown cancel_all: %s", exc)
        logger.info("[EXCHANGE] Stopped")

    # --------------------------------------------------------- order placement
    def place_maker_buy(self, token_id, price, size, tick_size, neg_risk):
        """Place GTC post-only limit BUY order (two-step: create_order -> post_order).

        Args:
            token_id: Polymarket token ID
            price: Limit price (float)
            size: Number of shares (float/int)
            tick_size: Market tick size as STRING ("0.01")
            neg_risk: Market neg_risk flag (bool)

        Returns:
            {"success": True, "order_id": "...", "status": "..."}
            {"success": False, "reason": "..."}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        # [FIX-H1] Block orders when heartbeat is dead — exchange may auto-cancel
        if not self.heartbeat_healthy:
            logger.warning("[EXCHANGE] Heartbeat unhealthy — blocking maker buy for %s", token_id)
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            order_args = OrderArgs(
                token_id=str(token_id),
                price=float(price),
                size=float(size),
                side=BUY,
            )
            options = PartialCreateOrderOptions(
                tick_size=str(tick_size),
                neg_risk=bool(neg_risk),
            )
            with self._lock:
                signed = self.client.create_order(order_args, options)
                result = self.client.post_order(
                    signed, order_type=OrderType.GTC, post_only=True
                )
            # result is a dict with order details
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id") or ""
                status = result.get("status", "unknown")
                if result.get("success") is False or result.get("errorMsg"):
                    return {
                        "success": False,
                        "reason": result.get("errorMsg", "post_only_rejected"),
                    }
                return {"success": True, "order_id": order_id, "status": status}
            return {"success": True, "order_id": str(result), "status": "submitted"}
        except Exception as exc:
            error_str = str(exc)
            logger.error("[EXCHANGE] place_maker_buy failed: %s", error_str)
            return {"success": False, "reason": error_str}

    def place_taker_buy(self, token_id, amount_usd, worst_price, tick_size, neg_risk):
        """Place FOK market BUY order (two-step: create_market_order -> post_order).

        Args:
            token_id: Polymarket token ID
            amount_usd: Dollar amount to spend (float)
            worst_price: Maximum price willing to pay (float)
            tick_size: Market tick size as STRING ("0.01")
            neg_risk: Market neg_risk flag (bool)

        Returns:
            {"success": True, "order_id": "...", "status": "..."}
            {"success": False, "reason": "..."}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            logger.warning("[EXCHANGE] Heartbeat unhealthy — blocking taker buy for %s", token_id)
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            order_args = MarketOrderArgs(
                token_id=str(token_id),
                amount=float(amount_usd),  # BUY: amount = dollars
                side=BUY,
                price=float(worst_price),
            )
            options = PartialCreateOrderOptions(
                tick_size=str(tick_size),
                neg_risk=bool(neg_risk),
            )
            with self._lock:
                signed = self.client.create_market_order(order_args, options)
                result = self.client.post_order(signed, order_type=OrderType.FOK)
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id") or ""
                return {"success": True, "order_id": order_id, "status": result.get("status", "submitted")}
            return {"success": True, "order_id": str(result), "status": "submitted"}
        except Exception as exc:
            logger.error("[EXCHANGE] place_taker_buy failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def place_maker_sell(self, token_id, size, price, tick_size, neg_risk):
        """Place GTC post-only limit SELL order (two-step: create_order -> post_order).

        Args:
            token_id: Polymarket token ID
            size: Number of shares to sell (float/int)
            price: Limit price (float)
            tick_size: Market tick size as STRING ("0.01")
            neg_risk: Market neg_risk flag (bool)

        Returns:
            {"success": True, "order_id": "...", "status": "..."}
            {"success": False, "reason": "..."}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            logger.warning("[EXCHANGE] Heartbeat unhealthy — blocking maker sell for %s", token_id)
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            order_args = OrderArgs(
                token_id=str(token_id),
                price=float(price),
                size=float(size),
                side=SELL,
            )
            options = PartialCreateOrderOptions(
                tick_size=str(tick_size),
                neg_risk=bool(neg_risk),
            )
            with self._lock:
                signed = self.client.create_order(order_args, options)
                result = self.client.post_order(
                    signed, order_type=OrderType.GTC, post_only=True
                )
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id") or ""
                if result.get("success") is False or result.get("errorMsg"):
                    return {
                        "success": False,
                        "reason": result.get("errorMsg", "post_only_rejected"),
                    }
                return {"success": True, "order_id": order_id, "status": result.get("status", "submitted")}
            return {"success": True, "order_id": str(result), "status": "submitted"}
        except Exception as exc:
            logger.error("[EXCHANGE] place_maker_sell failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def place_taker_sell(self, token_id, size, worst_price, tick_size, neg_risk):
        """Place FOK market SELL order (two-step: create_market_order -> post_order).

        Args:
            token_id: Polymarket token ID
            size: Number of shares to sell (float/int) - MarketOrderArgs.amount for SELL
            worst_price: Minimum price willing to accept (float)
            tick_size: Market tick size as STRING ("0.01")
            neg_risk: Market neg_risk flag (bool)

        Returns:
            {"success": True, "order_id": "...", "status": "..."}
            {"success": False, "reason": "..."}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            logger.warning("[EXCHANGE] Heartbeat unhealthy — blocking taker sell for %s", token_id)
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            order_args = MarketOrderArgs(
                token_id=str(token_id),
                amount=float(size),  # SELL: amount = number of shares
                side=SELL,
                price=float(worst_price),
            )
            options = PartialCreateOrderOptions(
                tick_size=str(tick_size),
                neg_risk=bool(neg_risk),
            )
            with self._lock:
                signed = self.client.create_market_order(order_args, options)
                result = self.client.post_order(signed, order_type=OrderType.FOK)
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id") or ""
                return {"success": True, "order_id": order_id, "status": result.get("status", "submitted")}
            return {"success": True, "order_id": str(result), "status": "submitted"}
        except Exception as exc:
            logger.error("[EXCHANGE] place_taker_sell failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    # -------------------------------------------------------- order management
    def cancel_order(self, order_id):
        """Cancel single order. Returns True if cancelled."""
        if not self.available:
            return False
        try:
            with self._lock:
                if PY_CLOB_V2:
                    from py_clob_client_v2.clob_types import OrderPayload
                    self.client.cancel_order(OrderPayload(orderID=str(order_id)))
                else:
                    self.client.cancel_order(order_id)
            logger.info("[EXCHANGE] Cancelled order %s", order_id)
            return True
        except Exception as exc:
            logger.error("[EXCHANGE] cancel_order failed for %s: %s", order_id, exc)
            return False

    def cancel_all_orders(self):
        """Cancel all open orders. Returns count of cancelled orders."""
        if not self.available:
            return 0
        try:
            with self._lock:
                result = self.client.cancel_all()
            # cancel_all returns a list or dict of cancelled orders
            if isinstance(result, list):
                count = len(result)
            elif isinstance(result, dict):
                count = len(result.get("canceled", result.get("cancelled", [])))
            else:
                count = 0
            logger.info("[EXCHANGE] Cancelled %d orders", count)
            return count
        except Exception as exc:
            logger.error("[EXCHANGE] cancel_all_orders failed: %s", exc)
            return 0

    def get_order_status(self, order_id):
        """Get order fill status.

        Returns RAW dict from API. Use .get() defensively:
            resp.get("status")        # "live", "matched", "cancelled"
            resp.get("size_matched")  # string, must cast to float
            resp.get("price")         # string, must cast to float
        """
        if not self.available:
            return None
        try:
            with self._lock:
                result = self.client.get_order(order_id)
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as exc:
            logger.error("[EXCHANGE] get_order_status failed for %s: %s", order_id, exc)
            return None

    def get_open_orders(self):
        """Get all open orders. Returns list of order dicts."""
        if not self.available:
            return []
        try:
            with self._lock:
                result = self.client.get_open_orders()
            if isinstance(result, list):
                return result
            return []
        except Exception as exc:
            logger.error("[EXCHANGE] get_open_orders failed: %s", exc)
            return []

    # ----------------------------------------------------------- market data
    def get_orderbook_info(self, token_id):
        """Fetch orderbook and extract key info.
        V2: get_order_book returns raw dict, not typed object.
        Bids/asks are list of dicts with string 'price' and 'size' fields.
        """
        if not self.available:
            return None
        try:
            with self._lock:
                book = self.client.get_order_book(str(token_id))

            # V2: book is a dict, not typed object
            if not isinstance(book, dict):
                logger.error("[EXCHANGE] Unexpected orderbook type: %s", type(book).__name__)
                return None

            bids = book.get("bids") or []
            asks = book.get("asks") or []

            best_bid = None
            best_ask = None
            if bids:
                best_bid = float(bids[0].get("price", 0))
            if asks:
                best_ask = float(asks[0].get("price", 0))

            if best_bid is None and best_ask is None:
                return None

            # V2: tick_size can be float or string — normalize to string for options
            _tick = book.get("tick_size")
            tick_size_str = str(_tick) if _tick is not None else "0.01"
            tick_size_float = float(_tick) if _tick is not None else 0.01

            _min_size = book.get("min_order_size")
            min_order_size = int(float(_min_size)) if _min_size is not None else 5

            _neg_risk = book.get("neg_risk")
            neg_risk = bool(_neg_risk) if _neg_risk is not None else False

            spread = round(best_ask - best_bid, 6) if (best_bid is not None and best_ask is not None) else None

            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "tick_size": tick_size_str,
                "tick_size_float": tick_size_float,
                "min_order_size": min_order_size,
                "neg_risk": neg_risk,
            }
        except Exception as exc:
            logger.error("[EXCHANGE] get_orderbook_info failed for %s: %s", token_id, exc)
            return None

    # -------------------------------------------------------- price computation
    @staticmethod
    def compute_maker_buy_price(best_bid, tick_size_float):
        """Compute maker buy price: best_bid + tick_size.

        Example: best_bid=0.04, tick_size_float=0.01 -> price=0.05
        """
        if best_bid is None:
            return None
        return round(float(best_bid) + float(tick_size_float), 4)

    @staticmethod
    def compute_maker_sell_price(best_ask, tick_size_float):
        """Compute maker sell price: best_ask - tick_size.

        Example: best_ask=0.07, tick_size_float=0.01 -> price=0.06
        """
        if best_ask is None:
            return None
        return round(float(best_ask) - float(tick_size_float), 4)

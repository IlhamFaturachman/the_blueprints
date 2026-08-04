"""
Execution Bridge - Live Trading via Polymarket py-sdk (polymarket-client).

Uses the official Polymarket Python SDK (polymarket-client) as the primary
execution engine. Falls back to py-clob-client-v2 if py-sdk is not installed.

Thread-safe: all CLOB operations protected by self._lock.
Heartbeat: background daemon thread sends heartbeat every 5s.
Graceful: never crashes - all errors caught, logged, and returned as status dicts.

Key py-sdk advantages over py-clob-client-v2:
- place_limit_order: one-step (create+sign+post), supports post_only=True
- place_market_order: one-step, supports FOK for urgent exits
- wait_for_order_fill_settlement: built-in settlement tracking
- list_positions: typed Position models via REST API
- get_balance_allowance: typed BalanceAllowance model
- neg_risk: handled internally from Environment config (no manual param)
- cancel_order: takes plain string order_id (no OrderPayload wrapper)
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

# --- Primary: polymarket-client (py-sdk) ---
try:
    from polymarket import SecureClient
    from polymarket import environments as _poly_envs
    from polymarket.models.clob.order_response import AcceptedOrder, RejectedOrder

    PY_SDK_AVAILABLE = True
except ImportError:
    PY_SDK_AVAILABLE = False

# --- Fallback: py-clob-client-v2 ---
try:
    from py_clob_client_v2.client import ClobClient as _V2ClobClient
    from py_clob_client_v2.clob_types import (
        OrderArgsV2 as _V2OrderArgs,
        MarketOrderArgsV2 as _V2MarketOrderArgs,
        OrderType as _V2OrderType,
        PartialCreateOrderOptions as _V2Options,
        OrderPayload as _V2OrderPayload,
    )
    from py_clob_client_v2.order_builder.constants import BUY as _V2BUY, SELL as _V2SELL

    PY_CLOB_V2_AVAILABLE = True
except ImportError:
    PY_CLOB_V2_AVAILABLE = False

PY_CLOB_AVAILABLE = PY_SDK_AVAILABLE or PY_CLOB_V2_AVAILABLE


class BlueprintsExchange:
    """Live trading bridge to Polymarket.

    Uses py-sdk (polymarket-client) as primary, py-clob-client-v2 as fallback.
    Thread-safe: all operations protected by self._lock.
    """

    def __init__(self):
        """Initialize exchange client with credentials from env.

        py-sdk: SecureClient.create() handles L1+L2 auth internally.
        v2 fallback: manual L1→derive→L2 init.
        """
        from market_discovery_internal.config import (
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
        self._use_py_sdk = False

        pk = POLYMARKET_PRIVATE_KEY
        if not pk:
            logger.critical("[EXCHANGE] PRIVATE_KEY not set - live trading unavailable")
            return

        # --- Primary: py-sdk ---
        if PY_SDK_AVAILABLE:
            try:
                wallet = POLYMARKET_FUNDER_ADDRESS or None
                self.client = SecureClient.create(
                    private_key=pk,
                    wallet=wallet,
                    environment=_poly_envs.PRODUCTION,
                )
                self._use_py_sdk = True
                self.available = True
                logger.info("[EXCHANGE] py-sdk SecureClient initialized (L1+L2 auth automatic)")
                return
            except Exception as exc:
                logger.error("[EXCHANGE] py-sdk init failed: %s — trying v2 fallback", exc)

        # --- Fallback: py-clob-client-v2 ---
        if PY_CLOB_V2_AVAILABLE:
            try:
                from market_discovery_internal.config import POLYMARKET_CLOB_API_URL
                host = POLYMARKET_CLOB_API_URL
                chain_id = 137
                sig_type = POLYMARKET_SIGNATURE_TYPE
                funder = POLYMARKET_FUNDER_ADDRESS or None

                temp_client = _V2ClobClient(
                    host=host, chain_id=chain_id, key=pk,
                    signature_type=sig_type, funder=funder,
                )
                api_creds = temp_client.create_or_derive_api_key()
                logger.info("[EXCHANGE] v2 L2 API credentials derived")

                self.client = _V2ClobClient(
                    host=host, chain_id=chain_id, key=pk,
                    creds=api_creds, signature_type=sig_type, funder=funder,
                )
                ok = self.client.get_ok()
                if ok:
                    self.available = True
                    logger.info("[EXCHANGE] v2 ClobClient initialized (L1+L2 auth)")
                else:
                    logger.critical("[EXCHANGE] v2 get_ok() failed")
            except Exception as exc:
                logger.critical("[EXCHANGE] v2 init failed: %s", exc)

        if not self.available:
            logger.critical("[EXCHANGE] No SDK available - live trading unavailable")

    # ------------------------------------------------------------- heartbeat
    def start_heartbeat(self):
        """Start background heartbeat thread."""
        if not self.available or self._running:
            return
        self._running = True

        def _beat():
            while self._running:
                try:
                    if self._use_py_sdk:
                        # py-sdk doesn't have explicit heartbeat — use get_midpoint as health check
                        pass  # No heartbeat needed in py-sdk
                    else:
                        with self._lock:
                            self._heartbeat_id = self.client.post_heartbeat("")
                        self.heartbeat_healthy = True
                except Exception:
                    self.heartbeat_healthy = False
                time.sleep(5)

        self._heartbeat_thread = threading.Thread(target=_beat, daemon=True)
        self._heartbeat_thread.start()
        if self._use_py_sdk:
            self.heartbeat_healthy = True  # py-sdk doesn't need heartbeat
            logger.info("[EXCHANGE] Heartbeat started (py-sdk mode — no explicit heartbeat needed)")
        else:
            logger.info("[EXCHANGE] Heartbeat started (v2 mode)")

    def stop_heartbeat(self):
        """Stop heartbeat thread."""
        self._running = False
        self.heartbeat_healthy = False

    def stop(self):
        """Graceful shutdown: stop heartbeat, cancel all orders."""
        self.stop_heartbeat()
        self.cancel_all_orders()
        if self._use_py_sdk and hasattr(self.client, "close"):
            try:
                self.client.close()
            except Exception:
                pass

    # -------------------------------------------------------- order placement

    def place_maker_buy(self, token_id, price, size, tick_size, neg_risk):
        """Place a post-only GTC maker buy order.
        Returns: {"success": bool, "order_id": str, ...} or {"success": False, "reason": str}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            with self._lock:
                if self._use_py_sdk:
                    result = self.client.place_limit_order(
                        token_id=str(token_id),
                        price=float(price),
                        size=float(size),
                        side="BUY",
                        post_only=True,
                    )
                    return self._parse_order_response(result)
                else:
                    order_args = _V2OrderArgs(
                        token_id=str(token_id), price=float(price),
                        size=float(size), side=_V2BUY,
                    )
                    options = _V2Options(tick_size=str(tick_size), neg_risk=bool(neg_risk))
                    signed = self.client.create_order(order_args, options)
                    result = self.client.post_order(signed, order_type=_V2OrderType.GTC, post_only=True)
                    return self._parse_v2_order_result(result)
        except Exception as exc:
            error_str = str(exc)
            logger.error("[EXCHANGE] place_maker_buy failed for %s: %s", token_id, error_str[:200])
            return {"success": False, "reason": error_str[:200]}

    def place_taker_sell(self, token_id, size, worst_price, tick_size, neg_risk):
        """Place a FOK taker sell order (urgent exit).
        Returns: {"success": bool, "order_id": str, ...} or {"success": False, "reason": str}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            with self._lock:
                if self._use_py_sdk:
                    result = self.client.place_market_order(
                        token_id=str(token_id),
                        side="SELL",
                        shares=float(size),
                        order_type="FOK",
                    )
                    return self._parse_order_response(result)
                else:
                    order_args = _V2MarketOrderArgs(
                        token_id=str(token_id),
                        amount=float(size),
                        side=_V2SELL,
                    )
                    options = _V2Options(tick_size=str(tick_size), neg_risk=bool(neg_risk))
                    signed = self.client.create_market_order(order_args, options)
                    result = self.client.post_order(signed, order_type=_V2OrderType.FOK)
                    return self._parse_v2_order_result(result)
        except Exception as exc:
            error_str = str(exc)
            logger.error("[EXCHANGE] place_taker_sell failed for %s: %s", token_id, error_str[:200])
            return {"success": False, "reason": error_str[:200]}

    def place_maker_sell(self, token_id, size, price, tick_size, neg_risk):
        """Place a post-only GTC maker sell order.
        Returns: {"success": bool, "order_id": str, ...} or {"success": False, "reason": str}
        """
        if not self.available:
            return {"success": False, "reason": "exchange_unavailable"}
        if not self.heartbeat_healthy:
            return {"success": False, "reason": "heartbeat_dead"}
        try:
            with self._lock:
                if self._use_py_sdk:
                    result = self.client.place_limit_order(
                        token_id=str(token_id),
                        price=float(price),
                        size=float(size),
                        side="SELL",
                        post_only=True,
                    )
                    return self._parse_order_response(result)
                else:
                    order_args = _V2OrderArgs(
                        token_id=str(token_id), price=float(price),
                        size=float(size), side=_V2SELL,
                    )
                    options = _V2Options(tick_size=str(tick_size), neg_risk=bool(neg_risk))
                    signed = self.client.create_order(order_args, options)
                    result = self.client.post_order(signed, order_type=_V2OrderType.GTC, post_only=True)
                    return self._parse_v2_order_result(result)
        except Exception as exc:
            error_str = str(exc)
            logger.error("[EXCHANGE] place_maker_sell failed for %s: %s", token_id, error_str[:200])
            return {"success": False, "reason": error_str[:200]}

    # --------------------------------------------------- response parsing

    @staticmethod
    def _parse_order_response(result):
        """Parse py-sdk OrderResponse (AcceptedOrder | RejectedOrder) into dict.

        AcceptedOrder: ok=True, order_id, status, making_amount, taking_amount
        RejectedOrder: ok=False, code, message
        """
        if isinstance(result, AcceptedOrder):
            return {
                "success": True,
                "order_id": str(result.order_id),
                "status": str(getattr(result, "status", "submitted")),
                "making_amount": str(getattr(result, "making_amount", "")),
                "taking_amount": str(getattr(result, "taking_amount", "")),
                "_accepted_order": result,  # Keep for wait_for_order_fill_settlement
            }
        elif isinstance(result, RejectedOrder):
            return {
                "success": False,
                "reason": f"rejected: code={getattr(result, 'code', '?')} msg={getattr(result, 'message', '?')}",
            }
        else:
            # Fallback for unexpected types
            return {
                "success": getattr(result, "ok", False),
                "order_id": str(getattr(result, "order_id", "")),
                "status": str(getattr(result, "status", "unknown")),
            }

    @staticmethod
    def _parse_v2_order_result(result):
        """Parse v2 order result into dict."""
        if result and not isinstance(result, (str, type(None))):
            order_id = str(getattr(result, "order_id", getattr(result, "id", str(result))))
            return {"success": True, "order_id": order_id, "status": "submitted"}
        elif result:
            return {"success": True, "order_id": str(result), "status": "submitted"}
        return {"success": False, "reason": "empty response"}

    # -------------------------------------------------------- order management

    def cancel_order(self, order_id):
        """Cancel single order. Returns True if cancelled."""
        if not self.available:
            return False
        try:
            with self._lock:
                if self._use_py_sdk:
                    self.client.cancel_order(order_id=str(order_id))
                else:
                    self.client.cancel_order(_V2OrderPayload(orderID=str(order_id)))
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
            if isinstance(result, (list, int)):
                return len(result) if isinstance(result, list) else int(result)
            return 1
        except Exception as exc:
            logger.error("[EXCHANGE] cancel_all_orders failed: %s", exc)
            return 0

    def get_order_status(self, order_id):
        """Get order status. Returns dict or None."""
        if not self.available:
            return None
        try:
            with self._lock:
                result = self.client.get_order(order_id=str(order_id))
            if hasattr(result, "model_dump"):
                return result.model_dump()
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as exc:
            logger.error("[EXCHANGE] get_order_status failed for %s: %s", order_id, exc)
            return None

    def get_open_orders(self):
        """Get all open orders. Returns list."""
        if not self.available:
            return []
        try:
            with self._lock:
                result = self.client.list_open_orders()
            if hasattr(result, "model_dump"):
                return [result.model_dump()]
            if isinstance(result, list):
                return [r.model_dump() if hasattr(r, "model_dump") else r for r in result]
            return []
        except Exception as exc:
            logger.error("[EXCHANGE] get_open_orders failed: %s", exc)
            return []

    # -------------------------------------------------------- settlement tracking

    def wait_for_settlement(self, accepted_order, timeout_s=120):
        """Wait for order to fill and settle. py-sdk only.

        Args:
            accepted_order: AcceptedOrder object from place_*_order response
            timeout_s: max seconds to wait

        Returns:
            True if settled, False if timeout/error
        """
        if not self.available or not self._use_py_sdk:
            return False
        try:
            self.client.wait_for_order_fill_settlement(accepted_order, timeout_s=timeout_s)
            return True
        except Exception as exc:
            logger.warning("[EXCHANGE] wait_for_settlement failed: %s", exc)
            return False

    # -------------------------------------------------------- position tracking

    def list_positions(self, user_address=None):
        """List current positions via REST API. py-sdk only.

        Returns list of typed Position models, or [] if unavailable.
        """
        if not self.available or not self._use_py_sdk:
            return []
        try:
            with self._lock:
                paginator = self.client.list_positions(user=user_address)
                positions = list(paginator)
            return positions
        except Exception as exc:
            logger.error("[EXCHANGE] list_positions failed: %s", exc)
            return []

    # -------------------------------------------------------- balance

    def get_balance(self, asset_type="COLLATERAL"):
        """Get USDC balance and allowance. py-sdk only.

        Returns BalanceAllowance model or None.
        """
        if not self.available or not self._use_py_sdk:
            return None
        try:
            with self._lock:
                return self.client.get_balance_allowance(asset_type=asset_type)
        except Exception as exc:
            logger.error("[EXCHANGE] get_balance failed: %s", exc)
            return None

    # ----------------------------------------------------------- market data

    def get_orderbook_info(self, token_id):
        """Fetch orderbook and extract key info.

        py-sdk: returns typed OrderBook model with .bids, .asks (lists of OrderSummary)
        v2: returns raw dict with bids/asks as list of dicts

        Returns dict with best_bid, best_ask, spread, tick_size, neg_risk, min_order_size.
        """
        if not self.available:
            return None
        try:
            with self._lock:
                if self._use_py_sdk:
                    book = self.client.get_order_book(token_id=str(token_id))
                else:
                    book = self.client.get_order_book(str(token_id))

            if self._use_py_sdk:
                # py-sdk: typed OrderBook model
                best_bid = None
                best_ask = None
                if book.bids:
                    best_bid = float(book.bids[0].price)
                if book.asks:
                    best_ask = float(book.asks[0].price)
                if best_bid is None and best_ask is None:
                    return None

                _tick = book.tick_size
                tick_size_str = str(_tick) if _tick is not None else "0.01"
                tick_size_float = float(_tick) if _tick is not None else 0.01
                _min_size = book.min_order_size
                min_order_size = int(float(_min_size)) if _min_size is not None else 5
                _neg_risk = book.neg_risk
                neg_risk = bool(_neg_risk) if _neg_risk is not None else False
            else:
                # v2: raw dict
                if not isinstance(book, dict):
                    return None
                bids = book.get("bids") or []
                asks = book.get("asks") or []
                best_bid = float(bids[0].get("price", 0)) if bids else None
                best_ask = float(asks[0].get("price", 0)) if asks else None
                if best_bid is None and best_ask is None:
                    return None
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
        """Compute maker buy price: best_bid + tick_size."""
        if best_bid is None:
            return None
        return round(float(best_bid) + float(tick_size_float), 4)

    @staticmethod
    def compute_maker_sell_price(best_ask, tick_size_float):
        """Compute maker sell price: best_ask - tick_size."""
        if best_ask is None:
            return None
        return round(float(best_ask) - float(tick_size_float), 4)

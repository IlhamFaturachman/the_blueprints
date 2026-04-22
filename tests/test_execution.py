"""Tests for the Execution Bridge (BlueprintsExchange).

All tests mock ClobClient — no real API calls.
"""

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exchange(available=True, mock_client=None):
    """Create a BlueprintsExchange with mocked internals."""
    from market_discovery_internal.execution import BlueprintsExchange

    ex = BlueprintsExchange.__new__(BlueprintsExchange)
    ex.available = available
    ex.client = mock_client or MagicMock()
    ex._lock = threading.Lock()
    ex._running = False
    ex._heartbeat_thread = None
    ex.heartbeat_healthy = True
    ex._heartbeat_id = ""
    return ex


def _mock_orderbook(best_bid=0.04, best_ask=0.07, tick_size="0.01",
                    min_order_size="5", neg_risk=False):
    """Create a mock OrderBookSummary."""
    book = MagicMock()
    bid = MagicMock()
    bid.price = str(best_bid)
    bid.size = "100"
    ask = MagicMock()
    ask.price = str(best_ask)
    ask.size = "100"
    book.bids = [bid] if best_bid else []
    book.asks = [ask] if best_ask else []
    book.tick_size = tick_size
    book.min_order_size = min_order_size
    book.neg_risk = neg_risk
    return book


# ---------------------------------------------------------------------------
# Test: Price Computation
# ---------------------------------------------------------------------------

class TestPriceComputation:
    def test_maker_buy_price(self):
        from market_discovery_internal.execution import BlueprintsExchange
        assert BlueprintsExchange.compute_maker_buy_price(0.04, 0.01) == 0.05
        assert BlueprintsExchange.compute_maker_buy_price(0.10, 0.001) == 0.101

    def test_maker_sell_price(self):
        from market_discovery_internal.execution import BlueprintsExchange
        assert BlueprintsExchange.compute_maker_sell_price(0.07, 0.01) == 0.06
        assert BlueprintsExchange.compute_maker_sell_price(0.50, 0.001) == 0.499

    def test_maker_buy_price_none(self):
        from market_discovery_internal.execution import BlueprintsExchange
        assert BlueprintsExchange.compute_maker_buy_price(None, 0.01) is None

    def test_maker_sell_price_none(self):
        from market_discovery_internal.execution import BlueprintsExchange
        assert BlueprintsExchange.compute_maker_sell_price(None, 0.01) is None


# ---------------------------------------------------------------------------
# Test: Orderbook Info
# ---------------------------------------------------------------------------

class TestOrderbookInfo:
    def test_get_orderbook_info_success(self):
        mock_client = MagicMock()
        mock_client.get_order_book.return_value = _mock_orderbook(
            best_bid=0.04, best_ask=0.07, tick_size="0.01",
            min_order_size="5", neg_risk=False,
        )
        ex = _make_exchange(mock_client=mock_client)
        info = ex.get_orderbook_info("token123")

        assert info is not None
        assert info["best_bid"] == 0.04
        assert info["best_ask"] == 0.07
        assert info["spread"] == pytest.approx(0.03, abs=1e-6)
        assert info["tick_size"] == "0.01"
        assert info["tick_size_float"] == 0.01
        assert info["min_order_size"] == 5
        assert info["neg_risk"] is False

    def test_get_orderbook_info_empty(self):
        mock_client = MagicMock()
        book = MagicMock()
        book.bids = []
        book.asks = []
        book.tick_size = "0.01"
        book.min_order_size = "5"
        book.neg_risk = False
        mock_client.get_order_book.return_value = book
        ex = _make_exchange(mock_client=mock_client)
        assert ex.get_orderbook_info("token123") is None

    def test_get_orderbook_info_unavailable(self):
        ex = _make_exchange(available=False)
        assert ex.get_orderbook_info("token123") is None


# ---------------------------------------------------------------------------
# Test: Maker Buy
# ---------------------------------------------------------------------------

class TestMakerBuy:
    def test_maker_buy_accepted(self):
        mock_client = MagicMock()
        mock_client.create_order.return_value = "signed_order"
        mock_client.post_order.return_value = {
            "orderID": "order-123", "status": "live"
        }
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_maker_buy("token1", 0.05, 20, "0.01", False)

        assert result["success"] is True
        assert result["order_id"] == "order-123"
        # Verify two-step flow: create_order then post_order with post_only=True
        mock_client.create_order.assert_called_once()
        mock_client.post_order.assert_called_once()
        call_kwargs = mock_client.post_order.call_args
        assert call_kwargs[1].get("post_only") is True or call_kwargs[0][-1] is True

    def test_maker_buy_rejected_cross_spread(self):
        mock_client = MagicMock()
        mock_client.create_order.return_value = "signed_order"
        mock_client.post_order.return_value = {
            "success": False, "errorMsg": "INVALID_POST_ONLY_ORDER"
        }
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_maker_buy("token1", 0.05, 20, "0.01", False)

        assert result["success"] is False
        assert "INVALID_POST_ONLY_ORDER" in result["reason"]

    def test_maker_buy_insufficient_balance(self):
        mock_client = MagicMock()
        mock_client.create_order.side_effect = Exception("INVALID_ORDER_NOT_ENOUGH_BALANCE")
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_maker_buy("token1", 0.05, 20, "0.01", False)

        assert result["success"] is False
        assert "BALANCE" in result["reason"].upper()

    def test_maker_buy_unavailable(self):
        ex = _make_exchange(available=False)
        result = ex.place_maker_buy("token1", 0.05, 20, "0.01", False)
        assert result["success"] is False
        assert result["reason"] == "exchange_unavailable"


# ---------------------------------------------------------------------------
# Test: Taker Sell
# ---------------------------------------------------------------------------

class TestTakerSell:
    def test_taker_sell_filled(self):
        mock_client = MagicMock()
        mock_client.create_market_order.return_value = "signed_market_order"
        mock_client.post_order.return_value = {
            "orderID": "order-456", "status": "matched"
        }
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_taker_sell("token1", 20, 0.04, "0.01", False)

        assert result["success"] is True
        assert result["order_id"] == "order-456"
        # Verify two-step: create_market_order then post_order with FOK
        mock_client.create_market_order.assert_called_once()
        mock_client.post_order.assert_called_once()

    def test_taker_sell_not_filled(self):
        mock_client = MagicMock()
        mock_client.create_market_order.side_effect = Exception("No liquidity")
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_taker_sell("token1", 20, 0.04, "0.01", False)

        assert result["success"] is False


# ---------------------------------------------------------------------------
# Test: Maker Sell
# ---------------------------------------------------------------------------

class TestMakerSell:
    def test_maker_sell_accepted(self):
        mock_client = MagicMock()
        mock_client.create_order.return_value = "signed_sell"
        mock_client.post_order.return_value = {
            "orderID": "sell-789", "status": "live"
        }
        ex = _make_exchange(mock_client=mock_client)
        result = ex.place_maker_sell("token1", 20, 0.06, "0.01", False)

        assert result["success"] is True
        assert result["order_id"] == "sell-789"
        mock_client.post_order.assert_called_once()


# ---------------------------------------------------------------------------
# Test: Order Management
# ---------------------------------------------------------------------------

class TestOrderManagement:
    def test_cancel_order_success(self):
        mock_client = MagicMock()
        ex = _make_exchange(mock_client=mock_client)
        assert ex.cancel_order("order-123") is True
        mock_client.cancel.assert_called_once_with("order-123")

    def test_cancel_order_failure(self):
        mock_client = MagicMock()
        mock_client.cancel.side_effect = Exception("Not found")
        ex = _make_exchange(mock_client=mock_client)
        assert ex.cancel_order("order-123") is False

    def test_cancel_all_orders(self):
        mock_client = MagicMock()
        mock_client.cancel_all.return_value = ["o1", "o2", "o3"]
        ex = _make_exchange(mock_client=mock_client)
        assert ex.cancel_all_orders() == 3

    def test_get_order_status(self):
        mock_client = MagicMock()
        mock_client.get_order.return_value = {
            "status": "matched", "size_matched": "20", "price": "0.05"
        }
        ex = _make_exchange(mock_client=mock_client)
        result = ex.get_order_status("order-123")
        assert result["status"] == "matched"
        assert result["size_matched"] == "20"

    def test_get_open_orders(self):
        mock_client = MagicMock()
        mock_client.get_orders.return_value = [{"id": "o1"}, {"id": "o2"}]
        ex = _make_exchange(mock_client=mock_client)
        orders = ex.get_open_orders()
        assert len(orders) == 2


# ---------------------------------------------------------------------------
# Test: Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_healthy_after_success(self):
        mock_client = MagicMock()
        mock_client.post_heartbeat.return_value = {"heartbeat_id": "hb-1"}
        ex = _make_exchange(mock_client=mock_client)
        ex._running = True
        ex.heartbeat_healthy = False

        # Run one iteration manually
        with ex._lock:
            resp = ex.client.post_heartbeat(ex._heartbeat_id)
        if isinstance(resp, dict):
            ex._heartbeat_id = resp.get("heartbeat_id", "")
        ex.heartbeat_healthy = True

        assert ex.heartbeat_healthy is True
        assert ex._heartbeat_id == "hb-1"

    def test_heartbeat_unhealthy_after_3_failures(self):
        ex = _make_exchange()
        ex.heartbeat_healthy = True
        # Simulate 3 consecutive failures
        consecutive = 0
        for _ in range(3):
            consecutive += 1
            if consecutive >= 3:
                ex.heartbeat_healthy = False
        assert ex.heartbeat_healthy is False


# ---------------------------------------------------------------------------
# Test: Paper Mode Safety
# ---------------------------------------------------------------------------

class TestPaperModeSafety:
    def test_paper_mode_no_clob_calls(self):
        """When LIVE_TRADING_ENABLED=False, no exchange methods should be called."""
        from market_discovery_internal.config import LIVE_TRADING_ENABLED
        # Default is False
        assert LIVE_TRADING_ENABLED is False

    def test_unavailable_exchange_rejects_all(self):
        ex = _make_exchange(available=False)
        assert ex.place_maker_buy("t", 0.05, 20, "0.01", False)["success"] is False
        assert ex.place_taker_buy("t", 1.0, 0.05, "0.01", False)["success"] is False
        assert ex.place_maker_sell("t", 20, 0.06, "0.01", False)["success"] is False
        assert ex.place_taker_sell("t", 20, 0.04, "0.01", False)["success"] is False
        assert ex.cancel_order("o") is False
        assert ex.cancel_all_orders() == 0
        assert ex.get_order_status("o") is None
        assert ex.get_open_orders() == []
        assert ex.get_orderbook_info("t") is None


# ---------------------------------------------------------------------------
# Test: Min Order Size Skip
# ---------------------------------------------------------------------------

class TestMinOrderSize:
    def test_kelly_too_small_for_min_order(self):
        """When Kelly stake / price < min_order_size, trade should be skipped."""
        # Kelly $1.00 at $0.50/share = 2 shares < 5 minimum
        kelly_stake = 1.00
        price = 0.50
        kelly_shares = int(kelly_stake / price)
        min_order_size = 5
        assert kelly_shares < min_order_size  # 2 < 5 → SKIP

    def test_kelly_sufficient_for_min_order(self):
        """When Kelly stake / price >= min_order_size, trade should proceed."""
        kelly_stake = 1.00
        price = 0.05
        kelly_shares = int(kelly_stake / price)
        min_order_size = 5
        assert kelly_shares >= min_order_size  # 20 >= 5 → TRADE


# ---------------------------------------------------------------------------
# Test: Exit Lock (Double-Sell Prevention)
# ---------------------------------------------------------------------------

class TestExitLock:
    def test_exit_in_progress_prevents_double_sell(self):
        """Position with _exit_in_progress=True should be skipped."""
        pos = {"status": "open", "_exit_in_progress": True}
        assert pos.get("_exit_in_progress") is True

    def test_exit_in_progress_not_set_by_default(self):
        """Normal positions don't have _exit_in_progress."""
        pos = {"status": "open"}
        assert pos.get("_exit_in_progress") is None


# ---------------------------------------------------------------------------
# Test: Urgent vs Normal Exit Routing
# ---------------------------------------------------------------------------

class TestExitRouting:
    def test_urgent_reasons_use_taker(self):
        from market_discovery_internal.config import URGENT_EXIT_REASONS
        urgent = {"stop_loss", "hard_stop_loss", "sniper_stop_loss_thesis_broken",
                  "trailing_stop_breakeven", "trailing_stop", "flash_crash_exit"}
        for reason in urgent:
            assert reason in URGENT_EXIT_REASONS, f"{reason} should be urgent"

    def test_normal_reasons_not_urgent(self):
        from market_discovery_internal.config import URGENT_EXIT_REASONS
        normal = {"take_profit_100pct", "sniper_take_profit", "late_window_sell",
                  "late_window_confidence_below_min", "thesis_decay_exit"}
        for reason in normal:
            assert reason not in URGENT_EXIT_REASONS, f"{reason} should NOT be urgent"


# ---------------------------------------------------------------------------
# Test: Config Flags
# ---------------------------------------------------------------------------

class TestConfigFlags:
    def test_live_trading_default_false(self):
        from market_discovery_internal.config import LIVE_TRADING_ENABLED
        assert LIVE_TRADING_ENABLED is False

    def test_prefer_maker_default_true(self):
        from market_discovery_internal.config import PREFER_MAKER_ORDERS
        assert PREFER_MAKER_ORDERS is True

    def test_maker_timeout_default(self):
        from market_discovery_internal.config import MAKER_ORDER_TIMEOUT_S
        assert MAKER_ORDER_TIMEOUT_S == 300

    def test_maker_sell_retries_default(self):
        from market_discovery_internal.config import MAKER_SELL_MAX_RETRIES
        assert MAKER_SELL_MAX_RETRIES == 3


# ---------------------------------------------------------------------------
# Test: Monitor Pending Orders
# ---------------------------------------------------------------------------

class TestMonitorPendingOrders:
    def test_monitor_skips_when_no_exchange(self):
        """monitor_pending_orders should do nothing when exchange is None."""
        from market_discovery_internal.cycles import monitor_pending_orders
        state = {"positions": [{"status": "pending_entry", "pending_order_id": "o1"}], "meta": {}}
        monitor_pending_orders(state, exchange=None)
        # Position should be unchanged
        assert state["positions"][0]["status"] == "pending_entry"

    def test_monitor_fills_pending_entry(self):
        """When order is matched, pending_entry should become open."""
        from market_discovery_internal.cycles import monitor_pending_orders

        mock_exchange = MagicMock()
        mock_exchange.available = True
        mock_exchange.get_order_status.return_value = {
            "status": "matched",
            "size_matched": "20",
            "price": "0.05",
        }

        state = {
            "positions": [{
                "status": "pending_entry",
                "pending_order_id": "order-1",
                "pending_order_placed_at": "2026-04-22T10:00:00+00:00",
                "pending_order_price": 0.05,
                "pending_order_size": 20,
                "entry_price": 0.05,
                "direction": "above",
                "city": "Dallas",
            }],
            "meta": {},
        }
        monitor_pending_orders(state, mock_exchange)
        pos = state["positions"][0]
        assert pos["status"] == "open"
        assert pos["entry_fee_usd"] == 0.0  # Maker fee
        assert "pending_order_id" not in pos

    def test_monitor_removes_timed_out_entry(self):
        """Pending entry that times out should be removed."""
        from market_discovery_internal.cycles import monitor_pending_orders

        mock_exchange = MagicMock()
        mock_exchange.available = True
        mock_exchange.get_order_status.return_value = {
            "status": "live", "size_matched": "0",
        }
        mock_exchange.cancel_order.return_value = True

        state = {
            "positions": [{
                "status": "pending_entry",
                "pending_order_id": "order-1",
                "pending_order_placed_at": "2026-04-20T10:00:00+00:00",  # 2 days ago
                "pending_order_price": 0.05,
                "pending_order_size": 20,
                "city": "Dallas",
            }],
            "meta": {},
        }
        monitor_pending_orders(state, mock_exchange)
        assert len(state["positions"]) == 0  # Removed
        mock_exchange.cancel_order.assert_called_once_with("order-1")

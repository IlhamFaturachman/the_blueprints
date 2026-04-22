# Execution Bridge + Maker Orders — Full Implementation Plan

**Version:** 1.0 | **Date:** 22 April 2026 | **Status:** PLAN (Not Yet Implemented)

---

## Table of Contents

1. Overview & Goals
2. Prerequisites & Current State
3. Architecture Design
4. Detailed Implementation (8 Phases)
5. Critical Self-Questioning (Step 3b)
6. Side Effect Analysis (Step 3)
7. Live-Trading Compatibility (Step 4)
8. Risk Matrix & Mitigations
9. Testing Strategy
10. Deployment Plan
11. Rollback Plan
12. Appendices

---

## 1. Overview & Goals

### What We're Building

A live execution bridge that connects The Blueprints bot to Polymarket's CLOB API,
enabling real order placement with a **maker-first strategy** (0% fee + 25% rebate).

### Goals

1. **Maker entry orders** — GTC post-only limit buy at best_bid + 1 tick (0% fee)
2. **Maker normal exit orders** — GTC post-only limit sell for TP and late-window (0% fee)
3. **Taker urgent exit orders** — FOK market sell for SL, thesis-broken, flash-crash (5% fee)
4. **Paper/Live toggle** — `LIVE_TRADING_ENABLED` config flag, default False
5. **Heartbeat management** — background thread every 5 seconds
6. **Order monitoring** — track pending orders, handle fills/cancels/timeouts
7. **Orphan cleanup** — cancel stale orders on bot restart

### What We're NOT Building (Out of Scope)

- NO-side trading (separate feature, separate plan)
- Relayer integration for token approvals (manual one-time setup via polymarket.com)
- Multi-wallet support
- Batch order optimization

---

## 2. Prerequisites & Current State

### 2.1 py-clob-client: NO UPGRADE NEEDED

**Verified:** The installed `py_clob_client` v0.34.6 already has every method we need:

| Method | Signature | Purpose |
|---|---|---|
| `post_order(order, orderType, post_only)` | `(self, order, orderType='GTC', post_only=False)` | Place order with post-only guarantee |
| `create_and_post_order(order_args, options)` | `(self, OrderArgs, PartialCreateOrderOptions)` | Create + sign + post limit order |
| `create_market_order(order_args, options)` | `(self, MarketOrderArgs, PartialCreateOrderOptions)` | Create FOK/FAK market order |
| `post_heartbeat(heartbeat_id)` | `(self, str or None)` | Keep orders alive |
| `cancel(order_id)` | `(self, str)` | Cancel single order |
| `cancel_all()` | `(self)` | Cancel all open orders |
| `get_order(order_id)` | `(self, str)` | Get order status |
| `get_orders()` | `(self, OpenOrderParams)` | Get all open orders |
| `get_order_book(token_id)` | `(self, str)` | Get orderbook with tick_size, min_order_size |
| `get_tick_size(token_id)` | `(self, str)` | Get market tick size |
| `get_neg_risk(token_id)` | `(self, str)` | Get neg_risk flag |
| `create_or_derive_api_creds()` | `(self)` | Derive L2 API credentials from private key |

**Constructor:** `ClobClient(host, chain_id, key, creds, signature_type, funder)`

**Import path:** `from py_clob_client.client import ClobClient` (already in execution.py)

**No package changes needed. Zero dependency risk.**

### 2.2 Current execution.py State

- 58-line dormant stub
- `BlueprintsClobClient` class with `is_dormant = True`
- Uses `signature_type=0` (EOA) — needs to change to `1` (POLY_PROXY)
- Missing: `funder`, `creds`, all real order methods
- `dormant_exchange` global instance — never imported by any module
- **Will be FULLY REWRITTEN** — no backward compatibility concerns

### 2.3 Current Order Flow (Paper Mode)

```
ENTRY:
  cycles.py:2101 → build_paper_position(opportunity, stake_usd)
    → effective_price = best_ask * 1.01 (1% simulated slippage)
    → quantity = stake_usd / effective_price
    → fee = calculate_taker_fee(quantity, effective_price)
    → Returns position dict with status="open"

EXIT:
  cycles.py:1849 → close_paper_position(position, exit_price, reason)
    → exit_value = exit_price * quantity
    → exit_fee = calculate_taker_fee(quantity, exit_price)
    → pnl = exit_value - exit_fee - cost_basis
    → Returns position dict with status="closed"

WS EXIT:
  ws_price_watcher.py:563 → close_paper_position(pos, bid_price, reason)
    → Same as above
```

### 2.4 Environment Variables

**Already in .env (user confirmed):**
```bash
PRIVATE_KEY=0x...           # Exported from Polymarket Magic Link
FUNDER_ADDRESS=0x...        # From polymarket.com/settings
SIGNATURE_TYPE=1            # POLY_PROXY (Magic Link login)
RELAYER_API_KEY=...         # From polymarket.com/settings?tab=api-keys
RELAYER_API_KEY_ADDRESS=0x...
```

**New (to be added):**
```bash
LIVE_TRADING_ENABLED=false          # Toggle paper/live (default: paper)
PREFER_MAKER_ORDERS=true            # Use maker (limit) when possible
MAKER_ORDER_TIMEOUT_S=300           # Cancel unfilled maker orders after 5 min
MAKER_SELL_MAX_RETRIES=3            # Retry maker sell N times before taker
MAKER_SELL_RETRY_INTERVAL_S=60     # Wait between sell retries
```

### 2.5 One-Time Manual Setup (Before Live Trading)

1. **Deposit pUSD** to Polymarket account (via polymarket.com deposit page)
2. **Approve Exchange contract** to spend pUSD (via polymarket.com UI — happens automatically on first trade via UI, or via Relayer API)
3. **Verify geoblock:** `curl https://polymarket.com/api/geoblock` from VPS — Indonesia must NOT be blocked

### 2.6 Polymarket API Constraints (Verified from Official Docs)

| Constraint | Value | Source |
|---|---|---|
| Min order size | **5 shares** (per market, from orderbook `min_order_size`) | Orderbook API response |
| Tick size | **Per market** (0.01, 0.001, etc.) | Orderbook API response |
| Heartbeat timeout | **10 seconds** (5s recommended interval) | Create Order docs |
| Post-only | Rejects order if it would cross spread (guarantees maker) | Create Order docs |
| GTD orders | Auto-expire at specified time | Create Order docs |
| Batch orders | Up to 15 per request | Create Order docs |
| Weather taker fee | `5% × price × (1-price)` per share | Fee docs |
| Weather maker fee | **0%** + 25% rebate from taker fees | Fee docs |
| Geoblock Indonesia | **NOT blocked** (verified 22 April 2026) | Geoblock docs |
| FOK (Fill-Or-Kill) | BUY: dollar amount, SELL: share count | Create Order docs |
| FAK (Fill-And-Kill) | Partial fills allowed | Create Order docs |
| Order statuses | live, matched, delayed, unmatched | Create Order docs |
| Trade statuses | MATCHED → MINED → CONFIRMED / FAILED | Cancel Order docs |

---

## 3. Architecture Design

### 3.1 Mode Toggle

```
LIVE_TRADING_ENABLED=false (default):
  ┌─────────────────────────────────────────────────┐
  │ Entry: build_paper_position() → dict            │
  │ Exit: close_paper_position() → dict             │
  │ No CLOB calls, no heartbeat, no order tracking  │
  │ IDENTICAL to current behavior                   │
  └─────────────────────────────────────────────────┘

LIVE_TRADING_ENABLED=true:
  ┌─────────────────────────────────────────────────┐
  │ Entry: place_maker_buy() → monitor → position   │
  │ Normal exit: place_maker_sell() → monitor → PnL │
  │ Urgent exit: place_taker_sell() → PnL           │
  │ Heartbeat thread running every 5s               │
  │ Order tracking in position dict                 │
  └─────────────────────────────────────────────────┘
```

### 3.2 File Changes Overview

| File | Change Type | Est. Lines | Description |
|---|---|---|---|
| `execution.py` | FULL REWRITE | ~280 | BlueprintsExchange class |
| `config.py` | ADD | ~15 | Execution config flags |
| `cycles.py` | MODIFY | ~100 | Entry/exit branching + order monitoring |
| `ws_price_watcher.py` | MODIFY | ~35 | Urgent exit via taker sell |
| `market_discovery.py` | MODIFY | ~25 | Initialize exchange, heartbeat, orphan cleanup |
| `state_persistence.py` | MODIFY | ~5 | Handle new position fields |
| `tests/test_execution.py` | NEW | ~120 | Execution bridge tests |

**Total: ~580 lines across 7 files.**

### 3.3 BlueprintsExchange Class Design

```python
class BlueprintsExchange:
    """Live trading bridge to Polymarket CLOB API.
    
    Thread-safe: all CLOB operations protected by self._lock.
    Heartbeat: background daemon thread sends heartbeat every 5s.
    Graceful: never crashes — all errors caught, logged, and returned as status dicts.
    """
    
    def __init__(self):
        """Initialize ClobClient with credentials from .env.
        
        Steps:
        1. Read PRIVATE_KEY, FUNDER_ADDRESS, SIGNATURE_TYPE from env
        2. Create temp ClobClient (L1 auth only)
        3. Derive API credentials (L2 auth)
        4. Create full ClobClient with L1 + L2 auth
        5. Verify connection with get_ok()
        
        If ANY step fails: set self.available = False, log CRITICAL.
        Bot falls back to paper mode.
        """
    
    def start_heartbeat(self):
        """Start background heartbeat thread (daemon).
        
        Thread sends POST /heartbeat every 5 seconds.
        If 3 consecutive failures: set self.heartbeat_healthy = False.
        Main cycle checks this flag before placing new orders.
        """
    
    def stop(self):
        """Stop heartbeat thread, cancel all open orders."""
    
    # === ORDER PLACEMENT ===
    
    def place_maker_buy(self, token_id, price, size, tick_size, neg_risk):
        """Place GTC post-only limit BUY order.
        
        Args:
            token_id: YES token ID from market
            price: limit price (best_bid + tick_size)
            size: number of shares (must be >= min_order_size)
            tick_size: market tick size (from orderbook)
            neg_risk: market neg_risk flag (from orderbook)
        
        Returns:
            {"success": True, "order_id": "0x...", "status": "live"}
            {"success": False, "reason": "post_only_rejected"} — would cross spread
            {"success": False, "reason": "insufficient_balance"}
            {"success": False, "reason": "error", "detail": "..."}
        
        Post-only guarantee: if order would match immediately (become taker),
        Polymarket REJECTS it. We NEVER accidentally pay taker fee.
        """
    
    def place_taker_buy(self, token_id, amount_usd, worst_price, tick_size, neg_risk):
        """Place FOK market BUY order.
        
        Args:
            amount_usd: dollar amount to spend (NOT shares)
            worst_price: maximum price willing to pay (slippage protection)
        
        Returns:
            {"success": True, "order_id": "0x...", "status": "matched", "fill_price": ...}
            {"success": False, "reason": "fok_not_filled"}
        
        Used as fallback when maker buy is repeatedly rejected.
        """
    
    def place_maker_sell(self, token_id, size, price, tick_size, neg_risk):
        """Place GTC post-only limit SELL order.
        
        Same as place_maker_buy but for selling.
        Used for: take_profit, sniper, late_window_sell.
        """
    
    def place_taker_sell(self, token_id, size, worst_price, tick_size, neg_risk):
        """Place FOK market SELL order.
        
        Args:
            size: number of shares to sell
            worst_price: minimum price willing to accept (slippage protection)
        
        Used for: stop_loss, thesis_broken, trailing_stop, flash_crash.
        Speed > fee savings for urgent exits.
        """
    
    # === ORDER MANAGEMENT ===
    
    def cancel_order(self, order_id):
        """Cancel single order. Returns True if cancelled."""
    
    def cancel_all_orders(self):
        """Cancel all open orders. Returns count of cancelled orders."""
    
    def get_order_status(self, order_id):
        """Get order fill status.
        
        Returns:
            {"status": "live", "size_matched": "0", "price": "0.05"}
            {"status": "matched", "size_matched": "20", "price": "0.05"}
            {"status": "cancelled", ...}
        """
    
    def get_open_orders(self):
        """Get all open orders. Returns list of order dicts."""
    
    # === MARKET DATA ===
    
    def get_orderbook_info(self, token_id):
        """Fetch orderbook and extract key info.
        
        Returns:
            {
                "best_bid": 0.04,
                "best_ask": 0.07,
                "spread": 0.03,
                "tick_size": "0.01",
                "min_order_size": 5,
                "neg_risk": False,
            }
        
        Returns None if orderbook is empty or API fails.
        """
    
    # === PRICE COMPUTATION ===
    
    def compute_maker_buy_price(self, best_bid, tick_size):
        """Compute maker buy price: best_bid + tick_size.
        
        Example: best_bid=0.04, tick_size=0.01 → price=0.05
        This places our order 1 tick above the best bid,
        making us the new best bid (highest priority to fill).
        """
        return round(best_bid + float(tick_size), 4)
    
    def compute_maker_sell_price(self, best_ask, tick_size):
        """Compute maker sell price: best_ask - tick_size.
        
        Example: best_ask=0.07, tick_size=0.01 → price=0.06
        This places our order 1 tick below the best ask,
        making us the new best ask (highest priority to fill).
        """
        return round(best_ask - float(tick_size), 4)
```

### 3.4 Entry Flow (Detailed)

```
CURRENT (paper):
  build_paper_position(opportunity, stake_usd)
    → effective_price = best_ask * 1.01
    → position dict with status="open"

NEW (live):
  1. Bot selects entry candidate (SAME logic as now — Kelly, regime gates, etc.)
  
  2. Fetch orderbook info:
     book = exchange.get_orderbook_info(token_id)
     → best_bid, best_ask, tick_size, min_order_size, neg_risk
  
  3. Compute shares and check minimum:
     kelly_shares = int(stake_usd / best_ask)
     if kelly_shares < min_order_size:
         → SKIP trade (Kelly can't afford minimum)
         → Log: "Skipped {city}: kelly_shares={kelly_shares} < min={min_order_size}"
         → continue to next candidate
  
  4. Compute maker price:
     maker_price = exchange.compute_maker_buy_price(best_bid, tick_size)
     
     CRITICAL CHECK: maker_price must be < best_ask
     If maker_price >= best_ask:
         → Post-only would be rejected anyway
         → SKIP (spread is 0 or negative — market is too tight)
  
  5. Place order:
     result = exchange.place_maker_buy(token_id, maker_price, kelly_shares, tick_size, neg_risk)
     
     If result["success"]:
         → Create position dict with:
           status = "pending_entry"
           pending_order_id = result["order_id"]
           pending_order_placed_at = now_utc
           pending_order_price = maker_price
           pending_order_size = kelly_shares
         → Append to state["positions"]
     
     If NOT result["success"]:
         → Log reason
         → If reason == "post_only_rejected": spread too tight, skip
         → If reason == "insufficient_balance": alert via Telegram, stop entries
         → continue to next candidate
  
  6. IMPORTANT: Do NOT call build_paper_position() yet.
     The position is "pending" until the order fills.
     build_paper_position() is called AFTER fill confirmation
     with the ACTUAL fill price (not simulated).
```

### 3.5 Order Monitoring (Every Cycle)

```
For each position with status="pending_entry":
  
  1. elapsed = now - pending_order_placed_at
  
  2. result = exchange.get_order_status(pending_order_id)
  
  3. CASE: FILLED (result["status"] == "matched" or size_matched >= pending_order_size)
     → fill_price = float(result["price"])
     → fill_size = float(result["size_matched"])
     → Update position:
       status = "open"
       entry_price = fill_price (ACTUAL, not simulated)
       quantity = fill_size
       cost_basis = fill_size * fill_price (NO taker fee — we're maker)
       entry_fee_usd = 0.0 (maker fee = 0%)
       target_price = _compute_take_profit_price(fill_price, direction)
       stop_loss_price = fill_price * HYBRID_STOP_LOSS_MULTIPLIER
     → Remove pending_order_id
     → Log: "FILLED: {city} {direction} {fill_size} shares @ ${fill_price}"
     → Telegram alert: entry filled
  
  4. CASE: PARTIALLY FILLED (0 < size_matched < pending_order_size)
     → partial_size = float(result["size_matched"])
     → If partial_size >= min_order_size:
       → Cancel remainder: exchange.cancel_order(pending_order_id)
       → Accept partial fill (same as FILLED but with partial_size)
     → If partial_size < min_order_size:
       → Wait (partial fill too small to manage)
       → If elapsed > MAKER_ORDER_TIMEOUT_S: cancel all, remove position
  
  5. CASE: NOT FILLED (size_matched == 0)
     → If elapsed > MAKER_ORDER_TIMEOUT_S:
       → exchange.cancel_order(pending_order_id)
       → Remove position from state
       → Log: "TIMEOUT: {city} order not filled after {elapsed}s"
     → If edge has changed (re-check forecast):
       → If edge <= 0: cancel order, remove position
       → If edge still positive: wait (order is still valid)
     → Otherwise: wait
  
  6. CASE: CANCELLED/REJECTED (order no longer exists)
     → Remove position from state
     → Log: "CANCELLED: {city} order was cancelled externally"

For each position with status="pending_exit":
  
  1. result = exchange.get_order_status(pending_exit_order_id)
  
  2. CASE: FILLED
     → fill_price = float(result["price"])
     → close_paper_position(position, fill_price, pending_exit_reason)
     → exit_fee_usd = 0.0 (maker fee = 0%)
  
  3. CASE: NOT FILLED
     → pending_exit_retry_count += 1
     → If retry_count <= MAKER_SELL_MAX_RETRIES:
       → Cancel current order
       → Lower price by 1 tick
       → Re-place maker sell at new price
     → If retry_count > MAKER_SELL_MAX_RETRIES:
       → Cancel current order
       → Switch to TAKER sell (urgent):
         exchange.place_taker_sell(token_id, shares, worst_price=best_bid*0.95)
       → close_paper_position(position, fill_price, reason)
       → Log: "MAKER SELL FAILED after {retry_count} retries, switched to taker"
```

### 3.6 Exit Flow (Detailed)

```
When evaluate_hybrid_exit() returns action="sell":

  URGENT EXITS (speed > fee savings):
    Reasons: stop_loss, thesis_broken, trailing_stop, flash_crash
    
    If LIVE_TRADING_ENABLED:
      1. book = exchange.get_orderbook_info(token_id)
      2. worst_price = book["best_bid"] * 0.95 (5% slippage tolerance)
      3. result = exchange.place_taker_sell(token_id, shares, worst_price, tick_size, neg_risk)
      4. If result["success"]:
         → fill_price = result.get("fill_price", book["best_bid"])
         → close_paper_position(position, fill_price, reason)
         → exit_fee = calculate_taker_fee(shares, fill_price) (5% taker fee)
      5. If NOT result["success"]:
         → Log CRITICAL: "URGENT SELL FAILED for {city}: {result['reason']}"
         → Retry with worse price (best_bid * 0.90)
         → If still fails: alert via Telegram, position stuck
    
    If NOT LIVE_TRADING_ENABLED:
      → close_paper_position(position, current_yes_price, reason) (same as now)

  NORMAL EXITS (fee savings > speed):
    Reasons: take_profit, sniper_take_profit, late_window_sell
    
    If LIVE_TRADING_ENABLED:
      1. book = exchange.get_orderbook_info(token_id)
      2. maker_price = exchange.compute_maker_sell_price(book["best_ask"], tick_size)
      3. result = exchange.place_maker_sell(token_id, shares, maker_price, tick_size, neg_risk)
      4. If result["success"]:
         → position["status"] = "pending_exit"
         → position["pending_exit_order_id"] = result["order_id"]
         → position["pending_exit_reason"] = reason
         → position["pending_exit_retry_count"] = 0
         → position["pending_exit_placed_at"] = now_utc
      5. If NOT result["success"] (post_only rejected):
         → Spread too tight — switch to taker sell
         → Same as urgent exit flow
    
    If NOT LIVE_TRADING_ENABLED:
      → close_paper_position(position, current_yes_price, reason) (same as now)

  HOLD TO RESOLVE:
    Reason: hold_to_resolve (confidence >= 0.55 in late window)
    
    → No order needed — position resolves automatically
    → Polymarket settles at $1.00 or $0.00
    → Winning tokens can be redeemed via Relayer (manual or automated later)
```

### 3.7 WebSocket Urgent Exit (Live Mode)

```
ws_price_watcher.py callback detects stop_loss or take_profit:

  If LIVE_TRADING_ENABLED:
    1. Acquire position exit lock (prevent double-sell with main cycle)
    2. For STOP LOSS:
       → exchange.place_taker_sell(token_id, shares, worst_price=bid*0.95)
       → close_paper_position(position, fill_price, "stop_loss")
    3. For TAKE PROFIT:
       → exchange.place_maker_sell(token_id, shares, price=bid, tick_size, neg_risk)
       → position["status"] = "pending_exit"
       → (Main cycle monitors fill)
    4. Release exit lock
  
  If NOT LIVE_TRADING_ENABLED:
    → Same as now: close_paper_position(pos, bid_price, reason)
```

### 3.8 Heartbeat Management

```
Background daemon thread:

  heartbeat_id = ""
  consecutive_failures = 0
  
  while self._running:
      try:
          resp = self.client.post_heartbeat(heartbeat_id)
          heartbeat_id = resp.get("heartbeat_id", "")
          consecutive_failures = 0
          self.heartbeat_healthy = True
      except Exception as e:
          consecutive_failures += 1
          logger.error("[HEARTBEAT] Failure #%d: %s", consecutive_failures, e)
          if consecutive_failures >= 3:
              self.heartbeat_healthy = False
              logger.critical("[HEARTBEAT] 3 consecutive failures — orders may be cancelled!")
      
      time.sleep(5)

Main cycle checks:
  if LIVE_TRADING_ENABLED and not exchange.heartbeat_healthy:
      → Skip new entries (existing positions still monitored)
      → Log: "Heartbeat unhealthy — skipping new entries"
      → Telegram alert: "Heartbeat issue — check bot"
```

### 3.9 Orphan Order Cleanup (On Startup)

```
On bot startup (market_discovery.py main()):

  if LIVE_TRADING_ENABLED:
      exchange = BlueprintsExchange()
      
      if not exchange.available:
          → Log CRITICAL: "Exchange initialization failed — running in paper mode"
          → Set LIVE_TRADING_ENABLED = False for this session
          → Continue with paper mode
      
      # Cancel ALL open orders (clean slate after restart)
      cancelled = exchange.cancel_all_orders()
      logger.info("[STARTUP] Cancelled %d orphan orders", cancelled)
      
      # Check for positions with status="pending_entry" or "pending_exit"
      # These were placed before the crash — orders are now cancelled
      for pos in state["positions"]:
          if pos["status"] == "pending_entry":
              → Remove from positions (order was cancelled, never filled)
              → Log: "Removed pending entry for {city} (orphan after restart)"
          if pos["status"] == "pending_exit":
              → Reset to status="open" (exit order was cancelled, position still held)
              → Remove pending_exit_* fields
              → Log: "Reset pending exit for {city} to open (will re-evaluate)"
      
      # Start heartbeat
      exchange.start_heartbeat()
```

### 3.10 5-Share Minimum Handling

```
In entry selection (cycles.py):

  kelly_stake = compute_kelly_stake(...)  # e.g., $1.00
  entry_price = best_ask                  # e.g., $0.50
  kelly_shares = int(kelly_stake / entry_price)  # e.g., 2 shares
  
  book = exchange.get_orderbook_info(token_id)
  min_order_size = book["min_order_size"]  # e.g., 5 shares
  
  if kelly_shares < min_order_size:
      # Kelly can't afford the minimum order size
      # SKIP — don't override Kelly (risk management)
      logger.info("[ENTRY] Skipped %s: kelly=%d shares < min=%d (price=$%.2f)",
                  city, kelly_shares, min_order_size, entry_price)
      continue
  
  # Kelly can afford minimum — proceed with kelly_shares
  order_size = kelly_shares  # Always use Kelly's recommendation

Impact table:
  $0.03/share → 5 shares = $0.15 → Kelly $1.00 affords 33 shares → TRADE
  $0.05/share → 5 shares = $0.25 → Kelly $1.00 affords 20 shares → TRADE
  $0.10/share → 5 shares = $0.50 → Kelly $1.00 affords 10 shares → TRADE
  $0.20/share → 5 shares = $1.00 → Kelly $1.00 affords 5 shares  → TRADE (exactly)
  $0.25/share → 5 shares = $1.25 → Kelly $1.00 affords 4 shares  → SKIP
  $0.50/share → 5 shares = $2.50 → Kelly $1.00 affords 2 shares  → SKIP

This naturally favors exact-bracket markets ($0.03-$0.10) which have the highest edges.
Above/below markets ($0.30-$0.65) are skipped until wallet grows to $20+ (Kelly > $2.50).
```

### 3.11 Thread Safety Design

```
Shared resources:
  1. exchange.client (ClobClient) — used by main cycle, WS callback, heartbeat
  2. state["positions"] — used by main cycle, WS callback
  3. exchange.heartbeat_healthy — written by heartbeat thread, read by main cycle

Protection:
  1. exchange._lock = threading.Lock()
     → ALL ClobClient method calls wrapped in: with self._lock: ...
     → Prevents concurrent API calls (ClobClient may not be thread-safe)
  
  2. position._exit_lock (per-position)
     → Prevents WS callback and main cycle from selling same position simultaneously
     → Implemented as: position["_exit_in_progress"] = True/False
     → Check before any exit: if position.get("_exit_in_progress"): skip
  
  3. exchange.heartbeat_healthy
     → Written by heartbeat thread (atomic bool assignment — thread-safe in Python)
     → Read by main cycle (no lock needed for single bool read)
```

### 3.12 Fee Calculation Changes

```
PAPER MODE (unchanged):
  Entry fee: calculate_taker_fee(shares, price) → 5% × price × (1-price) × shares
  Exit fee: calculate_taker_fee(shares, price) → same

LIVE MODE (maker entry + maker/taker exit):
  Entry fee (maker): $0.00 (always maker via post-only)
  Normal exit fee (maker): $0.00
  Urgent exit fee (taker): calculate_taker_fee(shares, price) → 5% × price × (1-price) × shares
  
  In close_paper_position():
    if exit_reason in URGENT_REASONS:
        exit_fee = calculate_taker_fee(shares, fill_price)
    else:
        exit_fee = 0.0  # Maker exit
  
  URGENT_REASONS = {"stop_loss", "hard_stop_loss", "sniper_stop_loss_thesis_broken",
                     "trailing_stop_breakeven", "flash_crash_exit"}
```

---

## 4. Detailed Implementation (8 Phases)

### Phase 1: Config flags (config.py, ~15 lines)

```python
# === Execution Bridge ===
LIVE_TRADING_ENABLED = _env_bool("LIVE_TRADING_ENABLED", False)
PREFER_MAKER_ORDERS = _env_bool("PREFER_MAKER_ORDERS", True)
MAKER_ORDER_TIMEOUT_S = int(os.getenv("MAKER_ORDER_TIMEOUT_S", "300"))
MAKER_SELL_MAX_RETRIES = int(os.getenv("MAKER_SELL_MAX_RETRIES", "3"))
MAKER_SELL_RETRY_INTERVAL_S = int(os.getenv("MAKER_SELL_RETRY_INTERVAL_S", "60"))
POLYMARKET_SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "1"))
POLYMARKET_FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS", "")
POLYMARKET_PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# Urgent exit reasons (always use taker for speed)
URGENT_EXIT_REASONS = frozenset({
    "stop_loss", "hard_stop_loss", "sniper_stop_loss_thesis_broken",
    "trailing_stop_breakeven", "flash_crash_exit",
})
```

### Phase 2: Rewrite execution.py (~280 lines)

Full `BlueprintsExchange` class as designed in Section 3.3.

Key implementation details:
- `__init__`: try/except around every step, set `self.available = False` on any failure
- All order methods: acquire `self._lock`, try/except, return status dict (never raise)
- `_heartbeat_loop`: daemon thread, 5s interval, 3-failure tolerance
- `get_orderbook_info`: parse bids/asks for best_bid/best_ask, extract tick_size/min_order_size/neg_risk
- All methods log at INFO level for successful operations, WARNING for failures, CRITICAL for unrecoverable errors

### Phase 3: Modify entry flow (cycles.py, ~50 lines)

In `append_opened_positions_from_candidates()`:
- After Kelly sizing, before `build_paper_position()`:
- Add `if LIVE_TRADING_ENABLED:` branch
- Fetch orderbook, check min_order_size, compute maker price, place order
- Create position with `status="pending_entry"`
- `else:` branch keeps current paper logic unchanged

### Phase 4: Add order monitoring (cycles.py, ~50 lines)

New function `_monitor_pending_orders(state, exchange)`:
- Called at the start of each cycle (before position management)
- Iterates positions with `status="pending_entry"` or `status="pending_exit"`
- Handles: filled, partially filled, not filled + timeout, cancelled
- Updates position status and fields accordingly

### Phase 5: Modify exit flow (cycles.py, ~30 lines)

In the exit decision logic (after `evaluate_hybrid_exit` returns "sell"):
- Add `if LIVE_TRADING_ENABLED:` branch
- Check if exit reason is urgent → taker sell
- Check if exit reason is normal → maker sell
- `else:` branch keeps current paper logic unchanged

### Phase 6: Modify WS exit callback (ws_price_watcher.py, ~35 lines)

In `make_ws_exit_callback()`:
- Add `exchange` parameter
- Add `if LIVE_TRADING_ENABLED:` branch for stop_loss and take_profit
- Add `_exit_in_progress` flag check to prevent double-sell
- `else:` branch keeps current paper logic unchanged

### Phase 7: Startup initialization (market_discovery.py, ~25 lines)

In `main()`:
- If `LIVE_TRADING_ENABLED`: initialize `BlueprintsExchange`, orphan cleanup, start heartbeat
- Pass `exchange` instance to cycle functions and WS callback
- On shutdown (SIGTERM): call `exchange.stop()` to cancel orders and stop heartbeat

### Phase 8: Tests (tests/test_execution.py, ~120 lines)

Mock `ClobClient` for all tests. Test:
- Maker buy accepted/rejected
- Taker sell filled/rejected
- Order timeout and cancel
- Heartbeat failure handling
- Orphan cleanup
- Min order size skip
- Maker price computation
- Paper mode: no CLOB calls
- Exit lock prevents double-sell

---

## 5. Critical Self-Questioning (Step 3b)

| # | Question | Answer | Action |
|---|---|---|---|
| 1 | "What if ClobClient fails to initialize (wrong credentials)?" | Set `self.available = False`. All order methods return rejection. Bot falls back to paper mode with CRITICAL log. | Handled in `__init__` |
| 2 | "What if heartbeat thread dies silently?" | All open orders cancelled by Polymarket. Bot detects via `heartbeat_healthy = False`. Skips new entries. Existing positions still monitored via main cycle. | Handled in `_heartbeat_loop` |
| 3 | "What if bot crashes after placing order but before recording order_id?" | On restart, `cancel_all_orders()` cancels orphans. Order might fill before cancel — we'd have shares we don't know about. | Handled in orphan cleanup. Also: check balance on startup to detect untracked shares. |
| 4 | "What if maker buy fills at a price where edge is gone?" | Post-only guarantees our price (best_bid + tick). This is BETTER than taker (best_ask). Edge is preserved or improved. | Not a problem — maker price is always better than taker. |
| 5 | "What if maker sell for TP never fills?" | After 3 retries (lowering price each time), switch to taker. Worst case: 5% taker fee on exit. | Handled in order monitoring. |
| 6 | "What if WS exit and cycle exit try to sell simultaneously?" | `_exit_in_progress` flag prevents double-sell. First one to set flag wins. | Handled in exit flow. |
| 7 | "What about 5-share minimum at $0.50?" | Kelly $1.00 = 2 shares < 5 minimum. Bot SKIPS. Only trades where Kelly can afford min_order_size. | Handled in entry flow. |
| 8 | "What if tick_size changes mid-trade?" | Fetch tick_size fresh from orderbook before each order. Never cache tick_size. | Handled in every order method. |
| 9 | "What if neg_risk is True for a weather market?" | Fetch per market, never assume. Pass to every order call. | Handled in get_orderbook_info. |
| 10 | "Does paper P&L change when switching to live?" | Yes — paper simulates 1% slippage + 5% taker fee. Live uses actual fill price + 0% maker fee. Live P&L should be BETTER. | Expected and correct. |
| 11 | "What if pUSD balance runs out mid-cycle?" | `place_maker_buy` returns `insufficient_balance`. Bot skips entry, alerts via Telegram. | Handled in entry flow. |
| 12 | "What if Polymarket API is down?" | All order methods catch exceptions and return failure dicts. Bot continues in paper-like mode (monitoring positions but not placing new orders). | Handled in every method. |
| 13 | "What if position has pending_entry and bot tries to exit it?" | Position with status="pending_entry" is NOT "open" — exit logic only processes "open" positions. No conflict. | Safe by design. |
| 14 | "What if position has pending_exit and WS tries to exit it again?" | `_exit_in_progress` flag prevents double-sell. WS callback checks flag before placing order. | Handled in WS callback. |
| 15 | "What about the `close_paper_position` → `db.record_trade_history` path?" | Same as now — `close_paper_position` is called AFTER fill confirmation with actual fill price. DB records actual data. | No change needed. |
| 16 | "What if we place a maker sell but the position gets resolved before fill?" | Polymarket auto-cancels orders for resolved markets. Our order monitoring detects "cancelled" status and handles it. | Handled in order monitoring. |
| 17 | "What about the state persistence? New fields like pending_order_id?" | JSON serialization handles new fields automatically. `load_paper_state` uses `.get()` with defaults for all fields. Old positions without new fields work fine. | Safe — backward compatible. |
| 18 | "What if exchange.stop() is called but heartbeat thread is stuck?" | Thread is daemon — dies when main process exits. `stop()` sets `_running = False` and joins with timeout. | Handled in stop(). |

---

## 6. Side Effect Analysis (Step 3)

| Component | Impact | Risk Level |
|---|---|---|
| **Paper trading mode** | ZERO change when `LIVE_TRADING_ENABLED=False` (default) | SAFE |
| **State persistence** | New fields: `pending_order_id`, `pending_exit_order_id`, `pending_exit_reason`, `pending_exit_retry_count`, `_exit_in_progress`. All optional with `.get()` defaults. | SAFE |
| **WebSocket handler** | Needs `exchange` parameter for live exits. Paper mode unchanged. | LOW — additive parameter |
| **Risk engine (Kelly)** | Unchanged — Kelly computes stake, execution handles order | SAFE |
| **Circuit breaker** | Unchanged — still checks daily PnL from closed positions | SAFE |
| **Database** | Trade history stores actual fill prices instead of simulated. Same schema. | SAFE |
| **Dashboard** | New statuses ("pending_entry", "pending_exit") to display. Additive. | LOW |
| **Telegram** | Alerts include order_id and fill status. Template additions. | LOW |
| **Existing 162 tests** | Must not break. Execution is opt-in (default paper). All new code gated by `LIVE_TRADING_ENABLED`. | SAFE |
| **Thread safety** | Heartbeat + WS + main cycle all access exchange. Protected by `_lock`. | MEDIUM — must verify lock coverage |
| **Forecast pipeline** | Unchanged — forecasting is independent of execution | SAFE |
| **Pricing/edge calculation** | Unchanged — edge calculation is independent of execution | SAFE |
| **Auto-tuner/calibration** | Unchanged — uses closed trade data regardless of execution method | SAFE |

---

## 7. Live-Trading Compatibility (Step 4)

| Aspect | Assessment |
|---|---|
| Preserves paper simulation? | YES — default is paper, live is opt-in via .env |
| Needs changes before going live? | YES — set `LIVE_TRADING_ENABLED=true`, deposit pUSD, approve Exchange |
| Affects order execution? | YES — real orders placed on Polymarket CLOB |
| Affects risk management? | NO — Kelly, circuit breaker, whiplash all unchanged |
| Config-gated? | YES — `LIVE_TRADING_ENABLED=false` by default |
| Gasless? | YES — signature_type=1 (POLY_PROXY) for trading, Relayer for onchain ops |
| Fee impact? | POSITIVE — maker 0% + 25% rebate vs taker 5% |
| Geoblock safe? | YES — Indonesia not blocked (verified) |
| Rollback possible? | YES — set `LIVE_TRADING_ENABLED=false` in .env, restart. Instant. |

---

## 8. Risk Matrix & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ClobClient init failure | LOW | HIGH | Fallback to paper, CRITICAL log, Telegram alert |
| Heartbeat failure | LOW | HIGH | 3-retry tolerance, health flag, skip new entries |
| Post-only rejection | MEDIUM | LOW | Log, skip trade, try next cycle |
| Partial fill | MEDIUM | MEDIUM | Accept if >= min_order_size, cancel remainder |
| Double-sell (WS + cycle) | LOW | HIGH | Per-position `_exit_in_progress` flag |
| Insufficient pUSD | MEDIUM | MEDIUM | Check before order, Telegram alert |
| Network timeout | MEDIUM | LOW | 4s timeout, 1 retry, graceful failure |
| Geoblock added for Indonesia | VERY LOW | HIGH | Check on startup, Telegram alert |
| py-clob-client breaking change | LOW | HIGH | Pin version, test before upgrade |
| Orphan orders after crash | MEDIUM | MEDIUM | `cancel_all_orders()` on startup |
| Maker sell never fills | LOW | MEDIUM | 3 retries then taker fallback |
| Market resolved with pending order | LOW | LOW | Polymarket auto-cancels, we detect |

---

## 9. Testing Strategy

### Unit Tests (tests/test_execution.py)

```
test_exchange_init_success
test_exchange_init_failure_falls_back_to_unavailable
test_maker_buy_post_only_accepted
test_maker_buy_post_only_rejected_cross_spread
test_maker_buy_insufficient_balance
test_taker_sell_fok_filled
test_taker_sell_fok_not_filled
test_maker_sell_accepted
test_order_timeout_cancels_after_300s
test_partial_fill_accepted_above_min
test_partial_fill_rejected_below_min
test_heartbeat_healthy_after_success
test_heartbeat_unhealthy_after_3_failures
test_orphan_cleanup_cancels_unknown_orders
test_min_order_size_skip_when_kelly_too_small
test_maker_buy_price_computation
test_maker_sell_price_computation
test_paper_mode_no_clob_calls
test_exit_lock_prevents_double_sell
test_urgent_exit_uses_taker
test_normal_exit_uses_maker
```

All tests mock `ClobClient` — no real API calls.

### Integration Verification

1. Deploy with `LIVE_TRADING_ENABLED=false` → run 2-3 cycles → verify identical to current behavior
2. Deploy with `LIVE_TRADING_ENABLED=true` + $0.10 pUSD → verify small order placement
3. Verify heartbeat running (check logs)
4. Verify maker buy fills (check Polymarket portfolio)
5. Trigger stop loss manually → verify taker sell works
6. Verify orphan cleanup on restart

---

## 10. Deployment Plan

### Step 1: Local Development & Testing
1. Implement all 8 phases locally
2. Run all tests (162 existing + ~20 new)
3. Verify paper mode unchanged (run full cycle)

### Step 2: VPS Deployment (Paper Mode First)
1. Push to GitHub
2. Pull on VPS, run tests
3. Restart bot with `LIVE_TRADING_ENABLED=false`
4. Verify 2-3 cycles — identical to current behavior
5. Monitor for 1 day

### Step 3: VPS Deployment (Live Mode)
1. Deposit pUSD to Polymarket account
2. Approve Exchange contract (via polymarket.com UI)
3. Verify geoblock: `curl https://polymarket.com/api/geoblock` from VPS
4. Set `LIVE_TRADING_ENABLED=true` in .env
5. Restart bot
6. Monitor first cycle — verify maker buy order placed
7. Monitor order fill — verify position opened with fill price
8. Monitor exit — verify maker/taker sell works
9. Check Polymarket portfolio — verify balances match

---

## 11. Rollback Plan

### Immediate (30 seconds)
```bash
# In .env:
LIVE_TRADING_ENABLED=false
# Restart:
systemctl restart blueprints.service
```
Bot immediately reverts to paper mode. No more orders placed.

### Cancel All Orders (1 minute)
```bash
ssh root@103.253.244.158
cd /opt/the_blueprints
./venv/bin/python -c "
from market_discovery_internal.execution import BlueprintsExchange
ex = BlueprintsExchange()
print('Cancelled:', ex.cancel_all_orders())
"
```

### Full Code Rollback (5 minutes)
```bash
git revert HEAD
systemctl restart blueprints.service
```

---

## 12. Appendices

### Appendix A: Polymarket API Quick Reference

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `GET /book?token_id=X` | Public | None | Orderbook |
| `GET /price?token_id=X&side=BUY` | Public | None | Best price |
| `POST /order` | L2 | HMAC | Place order |
| `DELETE /order` | L2 | HMAC | Cancel order |
| `DELETE /cancel-all` | L2 | HMAC | Cancel all |
| `GET /orders` | L2 | HMAC | Open orders |
| `GET /order/{id}` | L2 | HMAC | Order status |
| `POST /heartbeat` | L2 | HMAC | Keep alive |
| `GET /trades` | L2 | HMAC | Trade history |

### Appendix B: Fee Savings (Weather, 20 shares)

| Price | Taker Fee | Maker Fee | Rebate | Net Savings/Trade |
|---|---|---|---|---|
| $0.05 | $0.048 | $0.00 | +$0.012 | **$0.060** |
| $0.10 | $0.090 | $0.00 | +$0.023 | **$0.113** |
| $0.30 | $0.210 | $0.00 | +$0.053 | **$0.263** |
| $0.50 | $0.250 | $0.00 | +$0.063 | **$0.313** |

At 30 trades/week: **$1.80-$9.39 saved.**

### Appendix C: Min Order Size Impact

| Price | 5 Shares Cost | Kelly $1.00 Affords | Trade? |
|---|---|---|---|
| $0.03 | $0.15 | 33 shares | YES |
| $0.05 | $0.25 | 20 shares | YES |
| $0.10 | $0.50 | 10 shares | YES |
| $0.20 | $1.00 | 5 shares | YES |
| $0.25 | $1.25 | 4 shares | NO |
| $0.50 | $2.50 | 2 shares | NO |

### Appendix D: Position Status Lifecycle

```
Paper mode:
  → "open" → "closed"

Live mode:
  → "pending_entry" → "open" → "closed"
                    → "open" → "pending_exit" → "closed"
                    → "open" → "closed" (urgent taker exit, no pending)
  
  Edge cases:
  → "pending_entry" → removed (timeout, edge gone, cancelled)
  → "pending_exit" → "open" (maker sell failed, reset for re-evaluation)
  → "pending_exit" → "closed" (taker fallback after max retries)
```

### Appendix E: Implementation Checklist

```
[ ] Phase 1: Config flags in config.py
[ ] Phase 2: BlueprintsExchange class in execution.py
[ ] Phase 3: Entry flow branching in cycles.py
[ ] Phase 4: Order monitoring loop in cycles.py
[ ] Phase 5: Exit flow branching in cycles.py
[ ] Phase 6: WS exit callback modification in ws_price_watcher.py
[ ] Phase 7: Startup initialization in market_discovery.py
[ ] Phase 8: Tests in tests/test_execution.py
[ ] All 162 existing tests pass
[ ] New execution tests pass
[ ] Paper mode verified unchanged
[ ] Live mode tested with small amount
[ ] Heartbeat verified running
[ ] Orphan cleanup verified
[ ] Rollback tested
```

---

*This plan follows the Analysis Workflow (ANALYSIS_WORKFLOW.md): Step 1 (deep analysis), Step 2 (proposed changes), Step 3 (side effects), Step 3b (critical self-questioning), Step 4 (live-trading compatibility). Implementation (Step 5) begins only after user approval.*

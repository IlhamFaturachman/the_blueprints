# scripts/shadow_trading.py
"""Phase 3: Shadow Trading Engine.

Simulates buy 0.10-0.30, TP 2x entry, SL 0.25x entry. Zero capital.
Mode A: US cities observation edge (lock prob + running max).
Mode B: Asian/European forecast edge (ECMWF + EMOS).

Usage: python scripts/shadow_trading.py [--duration-hours 336]
Output: logs/shadow_trades.jsonl
"""
import json, logging, os, sys, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import (
    SHADOW_OUTPUT_FILE, SHADOW_TP_MULTIPLIER, SHADOW_SL_MULTIPLIER,
    SHADOW_ENTRY_MIN_PRICE, SHADOW_ENTRY_MAX_PRICE, SHADOW_MIN_LOCK_PROB,
)

logger = logging.getLogger(__name__)


def evaluate_shadow_entry(lock_probability, winning_bracket_price,
                          min_lock_prob=SHADOW_MIN_LOCK_PROB,
                          entry_min=SHADOW_ENTRY_MIN_PRICE,
                          entry_max=SHADOW_ENTRY_MAX_PRICE):
    """Determine if a shadow buy should be placed."""
    should_buy = (
        lock_probability >= min_lock_prob
        and entry_min <= winning_bracket_price <= entry_max
    )
    return {"should_buy": should_buy, "entry_price": winning_bracket_price if should_buy else None}


def evaluate_shadow_exit(current_bid, entry_price,
                         tp_mult=SHADOW_TP_MULTIPLIER,
                         sl_mult=SHADOW_SL_MULTIPLIER):
    """Determine shadow exit action."""
    if entry_price <= 0:
        return {"action": "hold", "exit_price": None}
    tp_target = entry_price * tp_mult
    sl_target = entry_price * sl_mult
    if current_bid >= tp_target:
        return {"action": "tp", "exit_price": current_bid}
    if current_bid <= sl_target:
        return {"action": "sl", "exit_price": current_bid}
    return {"action": "hold", "exit_price": None}


def run_shadow_trade_cycle(entry, brackets_poll_fn, max_polls=120, poll_interval=60):
    """Track a shadow trade from entry to TP/SL/resolve."""
    entry_price = entry["entry_price"]
    token_id = entry["token_id"]
    for _ in range(max_polls):
        quote = brackets_poll_fn(token_id)
        if not quote or quote.get("bid") is None:
            time.sleep(poll_interval)
            continue
        exit_decision = evaluate_shadow_exit(quote["bid"], entry_price)
        if exit_decision["action"] != "hold":
            return {
                **entry,
                "exit_action": exit_decision["action"],
                "exit_price": exit_decision["exit_price"],
                "pnl_pct": round(((exit_decision["exit_price"] - entry_price) / entry_price) * 100, 2),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }
        time.sleep(poll_interval)
    return {**entry, "exit_action": "unresolved", "exit_price": None, "pnl_pct": None,
            "closed_at": datetime.now(timezone.utc).isoformat()}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("[SHADOW] Shadow trading engine started")
    os.makedirs(os.path.dirname(SHADOW_OUTPUT_FILE), exist_ok=True)
    logger.info("[SHADOW] Framework ready — integrate with discovery cycle for live shadow trading")


if __name__ == "__main__":
    main()

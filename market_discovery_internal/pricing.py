"""Pricing, Orderbook, and Edge calculation logic for market_discovery."""

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from market_discovery_internal.config import (
    CLOB_BOOK_API, MODEL_EXACT_SIGMA_C, MARKET_MAX_SPREAD_GATE,
    MARKET_MIN_VOLUME_24HR
)
from market_discovery_internal.utils import (
    fetch_with_retry, _safe_float
)
from market_discovery_internal.parsing import (
    _extract_yes_token_id, THRESHOLD_RE, _market_search_text
)

# Event family cache used by market-implied probability logic.
_CURRENT_EVENT_FAMILIES = {}

def _events_to_markets(events, reset_family_cache=False):
    """Flatten events into markets and keep sibling families for implied pricing."""
    global _CURRENT_EVENT_FAMILIES
    if reset_family_cache:
        _CURRENT_EVENT_FAMILIES = {}

    markets = []
    for event in events:
        event_markets = event.get("markets", [])
        if isinstance(event_markets, list) and len(event_markets) >= 2:
            for sibling in event_markets:
                token_id = _extract_yes_token_id(sibling)
                if token_id:
                    _CURRENT_EVENT_FAMILIES[token_id] = event_markets
        for market in event_markets:
            markets.append(market)
    return markets

def _compute_market_implied_prob(token_id):
    """Compute normalized bracket probability from sibling markets in the same event."""
    siblings = _CURRENT_EVENT_FAMILIES.get(str(token_id))
    if not siblings or len(siblings) < 2:
        return None

    rows = []
    for sibling in siblings:
        sibling_token = _extract_yes_token_id(sibling)
        if not sibling_token: continue

        bid = _safe_float(sibling.get("bestBid"), -1)
        ask = _safe_float(sibling.get("bestAsk"), -1)
        if bid <= 0 or ask <= 0: continue

        title = str(sibling.get("question") or sibling.get("title") or "")
        match = THRESHOLD_RE.search(title) or THRESHOLD_RE.search(_market_search_text(sibling))
        if not match: continue

        threshold = _safe_float(match.group(1))
        unit = str(match.group(2)).upper()
        threshold_c = ((threshold - 32) * 5.0 / 9.0) if unit == "F" else threshold
        
        rows.append({
            "token_id": sibling_token,
            "threshold_c": float(threshold_c),
            "prob": (bid + ask) / 2.0
        })

    if not rows: return None
    total_prob = sum(r["prob"] for r in rows)
    if total_prob == 0: return None
    
    # Normalize
    for r in rows: r["prob"] /= total_prob
    
    target = next((r for r in rows if r["token_id"] == token_id), None)
    if not target: return None

    expected_temp = sum(r["threshold_c"] * r["prob"] for r in rows)
    bracket_distribution = sorted([{"temp": r["threshold_c"], "prob": round(r["prob"], 4)} for r in rows], key=lambda x: x["temp"])

    return {
        "market_implied_prob": round(target["prob"], 4),
        "market_implied_expected_temp_c": round(expected_temp, 2),
        "family_size": len(rows),
        "bracket_distribution": bracket_distribution,
    }

def _extract_top_orderbook_price(levels, side="ask"):
    """Extract the best price from a list of orderbook levels."""
    if not levels or not isinstance(levels, list):
        return None
    try:
        # Levels usually sorted: [ [price, size], ... ]
        return float(levels[0][0])
    except (IndexError, ValueError, TypeError):
        return None

def fetch_orderbook_quote(token_id):
    """Fetch the real-time best bid/ask for a specific token from CLOB."""
    if not token_id: return None
    url = f"{CLOB_BOOK_API}?token_id={token_id}"
    try:
        data = fetch_with_retry(url)
        # Gamma CLOB book returns { "bids": [...], "asks": [...] }
        best_bid = _extract_top_orderbook_price(data.get("bids", []), "bid")
        best_ask = _extract_top_orderbook_price(data.get("asks", []), "ask")
        return {"bid": best_bid, "ask": best_ask}
    except Exception:
        return None

def check_liquidity_depth(token_id, target_stake_usd):
    """
    [MODUL E] Check if the orderbook volume at the Best Ask exceeds our stake.
    Protects the bot from entering dry, illiquid markets.
    Returns True if liquid enough, False if dry or API failure.
    """
    if not token_id: return False
    url = f"{CLOB_BOOK_API}?token_id={token_id}"
    try:
        data = fetch_with_retry(url)
        asks = data.get("asks", [])
        if not asks or not isinstance(asks, list):
            return False

        best = asks[0]
        # Handle both list format [price, size] and dict format {"price":..., "size":...}
        if isinstance(best, (list, tuple)) and len(best) >= 2:
            price = float(best[0])
            size = float(best[1])
        elif isinstance(best, dict):
            price = float(best.get("price") or best.get("p") or 0)
            size = float(best.get("size") or best.get("s") or 0)
        else:
            return False

        if price <= 0 or size <= 0:
            return False

        depth_usd = price * size
        return depth_usd > float(target_stake_usd)
    except Exception:
        return False

def calculate_edge(market, forecast_temp):
    """
    Calculate the statistical edge of a market position compared to forecast.
    
    Logic:
    - For Above/Below: Model probability based on threshold.
    - For Exact: Model probability using Gaussian distribution around forecast.
    - Edge = Model Prob - Market Price.
    """
    if forecast_temp is None: return None
    
    price = market.get("yes_price")
    threshold = market.get("threshold")
    direction = market.get("direction")
    forecast = float(forecast_temp)
    
    model_prob = 0.0
    if direction == "above":
        # Sigmoid: smooth gradient around threshold.
        # k=1.5 → at +1°C prob≈0.82, at +2°C prob≈0.95, at -1°C prob≈0.18
        k = 1.5
        diff = max(-50, min(50, (forecast - threshold)))
        model_prob = 1.0 / (1.0 + math.exp(-k * diff))
    elif direction == "below":
        k = 1.5
        diff = max(-50, min(50, (threshold - forecast)))
        model_prob = 1.0 / (1.0 + math.exp(-k * diff))
    elif direction == "exact":
        # Gaussian approximation: exp(-0.5 * ((x-mu)/sigma)^2)
        diff = abs(forecast - threshold)
        sigma = MODEL_EXACT_SIGMA_C
        model_prob = math.exp(-0.5 * (diff / sigma)**2)

    # [MODUL P] Slippage-Aware Execution: Subtract 1.75% (mid-range of 1.5% - 2.0%)
    # This reflects the cost of bridge impact and spread widening on large/fast entries.
    slippage_penalty = 0.0175
    edge = model_prob - (price + slippage_penalty)

    return {
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 4),
        "forecast": forecast,
        "slippage_penalty_applied": slippage_penalty
    }

def _enrich_markets_missing_prices(markets, max_workers=6):
    """Concurrently fetch missing prices for markets that might have stagnant Gamma data."""
    if not markets: return []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_market = {
            executor.submit(fetch_orderbook_quote, m.get("token_id")): m
            for m in markets if m.get("yes_price") is None or m.get("yes_price") == 0
        }
        for future in as_completed(future_to_market):
            m = future_to_market[future]
            quote = future.result()
            if quote and quote["ask"]:
                m["yes_price"] = quote["ask"]
    return markets

def _extract_market_list(data):
    """Helper to extract market list from Gamma array or dict."""
    if isinstance(data, list): return data
    if isinstance(data, dict):
        return data.get("markets", data.get("data", []))
    return []

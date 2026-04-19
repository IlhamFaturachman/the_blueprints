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

def calculate_depth_adjusted_stake(token_id, base_stake, max_slippage_pct=0.03):
    """
    [MODUL L] Dynamically adjust stake based on orderbook depth and spread.
    Ensures we don't cross into prices that exceed our max slippage threshold.
    Returns the adjusted stake in USD.
    """
    from market_discovery_internal.config import LIQUIDITY_DEPTH_MULTIPLIER, MARKET_MAX_SPREAD_GATE
    
    if not token_id: return base_stake
    
    url = f"{CLOB_BOOK_API}?token_id={token_id}"
    try:
        data = fetch_with_retry(url)
        # Gamma CLOB book returns { "bids": [...], "asks": [...] }
        asks = data.get("asks", [])
        bids = data.get("bids", [])
        
        if not asks or not bids:
            return 0.0

        def _parse_level(level):
            if isinstance(level, dict):
                return float(level.get("price", 0)), float(level.get("size", 0))
            return float(level[0]), float(level[1])

        # CLOB may return asks sorted ascending OR descending — find true best (min) ask.
        ask_prices = []
        for lvl in asks:
            try:
                p, _ = _parse_level(lvl)
                if p > 0:
                    ask_prices.append(p)
            except Exception:
                continue
        bid_prices = []
        for lvl in bids:
            try:
                p, _ = _parse_level(lvl)
                if p > 0:
                    bid_prices.append(p)
            except Exception:
                continue

        if not ask_prices or not bid_prices:
            return 0.0

        best_ask = min(ask_prices)
        best_bid = max(bid_prices)

        # 1. Spread Gate
        spread = best_ask - best_bid
        if spread > MARKET_MAX_SPREAD_GATE:
            return 0.0

        # 2. Depth within slippage boundary (count asks from best_ask upward)
        max_price_allowed = best_ask * (1.0 + max_slippage_pct)

        total_depth_usd = 0.0
        for level in asks:
            try:
                price, size = _parse_level(level)
            except Exception:
                continue
            if price < best_ask or price > max_price_allowed:
                continue
            total_depth_usd += price * size
            
        # 3. Apply Multiplier Buffer
        # We only take a portion of available depth to keep slippage under control
        safe_depth = total_depth_usd / max(1.0, LIQUIDITY_DEPTH_MULTIPLIER)
        
        adjusted_stake = min(float(base_stake), safe_depth)
        return round(max(0.0, adjusted_stake), 2)
        
    except Exception:
        return 0.0

def _calibrated_prob(city, direction, horizon_bin, price_bin, raw_prob, min_samples=5):
    """
    [PACK A] Apply Bayesian shrinkage to raw model probability using historical calibration.

    Shrinkage formula: calibrated = (hits + raw_prob * prior_weight) / (total + prior_weight)
    - If total < min_samples: calibration has little effect (prior dominates).
    - prior_weight = min_samples gives 50/50 blend at exactly min_samples observations.
    - Returns (calibrated_prob, calibration_meta).
    """
    try:
        from market_discovery_internal.database_manager import db
        hits, total, brier_sum = db.get_calibration(city, direction, horizon_bin, price_bin)
    except Exception:
        hits, total, brier_sum = 0, 0, 0.0

    prior_weight = float(min_samples)
    calibrated = (hits + raw_prob * prior_weight) / (total + prior_weight)
    calibrated = max(0.0, min(1.0, calibrated))

    avg_brier = round(brier_sum / total, 4) if total > 0 else None
    return round(calibrated, 4), {
        "calibration_hits": hits,
        "calibration_total": total,
        "calibration_avg_brier": avg_brier,
        "calibration_active": total >= min_samples,
    }


def compute_regime_score(market, weather_evidence=None):
    """
    [PACK B] Compute market regime class from spread, depth, and evidence quality.

    Returns: ("good"|"neutral"|"stress", score_float, gate_thresholds_dict)
    - good    (score >= 0.65): controlled relax — use base thresholds
    - neutral (score >= 0.40): base thresholds
    - stress  (score < 0.40):  tighten gates — raise min_prob, min_edge; lower max_price
    """
    # Spread component: 0→1 (tighter spread = better)
    spread = market.get("gamma_spread")
    if spread is not None:
        try:
            spread_score = max(0.0, 1.0 - float(spread) / MARKET_MAX_SPREAD_GATE)
        except (TypeError, ValueError):
            spread_score = 0.5
    else:
        spread_score = 0.5  # Unknown spread: neutral

    # Depth component: is there enough depth for 2x stake? (best effort from volume_24hr)
    vol24 = market.get("volume_24hr", 0.0)
    try:
        vol24 = float(vol24)
        depth_score = min(1.0, vol24 / max(MARKET_MIN_VOLUME_24HR * 2, 1.0))
    except (TypeError, ValueError):
        depth_score = 0.5

    # Evidence quality component (from weather evidence)
    if weather_evidence and isinstance(weather_evidence, dict):
        evidence_score = float(weather_evidence.get("quality_score", 0.5))
    else:
        evidence_score = 0.5

    regime_score = (spread_score * 0.45 + depth_score * 0.30 + evidence_score * 0.25)
    regime_score = round(max(0.0, min(1.0, regime_score)), 4)

    if regime_score >= 0.65:
        regime_class = "good"
        gates = {"min_prob": 0.68, "min_edge": 0.18, "max_price": 0.65}
    elif regime_score >= 0.40:
        regime_class = "neutral"
        gates = {"min_prob": 0.72, "min_edge": 0.22, "max_price": 0.60}
    else:
        regime_class = "stress"
        gates = {"min_prob": 0.80, "min_edge": 0.28, "max_price": 0.55}

    return regime_class, regime_score, gates


def calculate_edge(market, forecast_temp, hours_until_resolve=None, k_factor_override=None):
    """
    Calculate the statistical edge of a market position compared to forecast.

    Logic:
    - Above/Below: sigmoid model probability around threshold (threshold already in °C from parse_market).
    - Exact: Gaussian probability around forecast.
    - If market_implied_prob available: takes priority over model (prob_source="market_implied").
    - [PACK A] Calibrated probability applied via Bayesian shrinkage.
    - Edge = calibrated_prob - (price + slippage_penalty)

    Slippage penalty (spread-aware):
      slippage_penalty = max(0.0175, gamma_spread / 2.0)
    Uses real-time spread from the market dict if available (gamma_spread field).
    Floor of 1.75% ensures conservatism when spread is unknown or very tight.
    Formula: half-spread approximates one-way transaction cost.
    """
    price = market.get("yes_price")
    threshold = market.get("threshold")
    direction = market.get("direction")
    city = str(market.get("city", "")).lower()

    # Market-implied probability path: use sibling family data when available.
    # This takes priority over the model because it embeds live market consensus.
    market_implied = market.get("market_implied_prob")
    if market_implied is not None and price is not None:
        try:
            raw_prob = float(market_implied)
            prob_source = "market_implied"
            forecast = None
        except (TypeError, ValueError):
            market_implied = None

    if market_implied is None:
        if forecast_temp is None:
            return None
        forecast = float(forecast_temp)
        prob_source = "gaussian_openmeteo"

    # Compute model probability only for non-market-implied path
    if market_implied is None:
        raw_prob = 0.0
        # Sigmoid hardening based on hours remaining
        _h_val = float(hours_until_resolve) if hours_until_resolve is not None else 24.0
        if k_factor_override is not None:
            k = k_factor_override
        elif _h_val <= 6:
            k = 1.3
        elif _h_val <= 14:
            k = 1.6
        elif _h_val <= 36:
            k = 1.1
        else:
            k = 0.75

        if direction == "above":
            # Sigmoid: smooth gradient around threshold.
            diff = max(-50, min(50, (forecast - threshold)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
        elif direction == "below":
            diff = max(-50, min(50, (threshold - forecast)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))

        # [FIX] Consensus Guard: Detect "Too-Good-To-Be-True" bargains that are likely traps.
        # If we are < 12h from resolve and we are 90%+ sure, but market price is < 30c,
        # we assume the market knows something our forecast API doesn't.
        if _h_val < 12.0 and raw_prob > 0.90 and price < 0.30:
            _gap = raw_prob - price
            if _gap > 0.50:
                raw_prob = (raw_prob + price) / 2.0 # Dampen hubris towards market center
                prob_source += "+consensus_guard"
        elif direction == "exact":
            # Probability that the daily high lands in the ±0.5°C bracket around threshold.
            # Uses Gaussian CDF integral: P = Φ((threshold+0.5 - forecast)/σ) - Φ((threshold-0.5 - forecast)/σ)
            # This replaces the old PDF-height formula which incorrectly gave 100% when forecast==threshold.
            # Calibration: σ=1.5 → exact match ≈26%, 1°C off ≈18%, 2°C off ≈11% (matches Polymarket consensus).
            from math import erf as _erf, sqrt as _sqrt
            sigma = MODEL_EXACT_SIGMA_C
            _s2 = sigma * _sqrt(2)
            raw_prob = max(0.0, min(1.0, 0.5 * (
                _erf((threshold + 0.5 - forecast) / _s2) -
                _erf((threshold - 0.5 - forecast) / _s2)
            )))
    # else: raw_prob already set from market_implied above

    # [PACK A] Calibration bins
    _h = hours_until_resolve if hours_until_resolve is not None else market.get("hours_until_resolve")
    if _h is not None:
        try:
            _h = float(_h)
            horizon_bin = 1 if _h < 12 else (2 if _h < 24 else 3)
        except (TypeError, ValueError):
            horizon_bin = 2
    else:
        horizon_bin = 2

    try:
        price_bin = min(9, max(0, int(float(price) * 10)))
    except (TypeError, ValueError):
        price_bin = 5

    calibrated, calib_meta = _calibrated_prob(city, str(direction), horizon_bin, price_bin, raw_prob)
    model_prob = calibrated  # Use calibrated for edge calculation

    # [MODUL P] Spread-Aware Slippage Penalty
    # Use gamma_spread from parsed market dict if available; floor at 1.75%.
    gamma_spread = market.get("gamma_spread")
    if gamma_spread is not None:
        try:
            slippage_penalty = max(0.0175, float(gamma_spread) / 2.0)
        except (TypeError, ValueError):
            slippage_penalty = 0.0175
    else:
        slippage_penalty = 0.0175

    edge = model_prob - (price + slippage_penalty)

    return {
        "model_prob": round(model_prob, 4),
        "raw_prob": round(raw_prob, 4),
        "edge": round(edge, 4),
        "forecast": forecast,
        "prob_source": prob_source,
        "slippage_penalty_applied": round(slippage_penalty, 4),
        "horizon_bin": horizon_bin,
        "price_bin": price_bin,
        **calib_meta,
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


def calculate_taker_fee(quantity: float, price: float, fee_rate: float = 0.05) -> float:
    """
    Compute Polymarket taker fee for a trade.
    Formula from docs: fee = C × feeRate × p × (1-p)
    C = quantity (shares), p = price (0-1 range)
    """
    if quantity <= 0 or price <= 0 or price >= 1.0:
        return 0.0
    return round(quantity * fee_rate * price * (1.0 - price), 6)

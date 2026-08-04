"""Pricing, Orderbook, and Edge calculation logic for market_discovery."""
from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from market_discovery_internal.config import (
    CLOB_BOOK_API, MODEL_EXACT_SIGMA_C, MARKET_MAX_SPREAD_GATE,
    MARKET_MIN_VOLUME_24HR,
    NOAA_OVERRIDE_ENABLED, NOAA_OVERRIDE_WINDOW_HOURS,
    NOAA_OVERRIDE_CONFIRM_PROB, NOAA_OVERRIDE_CONTRADICT_PROB,
    SIGMA_TROPICAL, SIGMA_FOUR_SEASON, SIGMA_DEFAULT,
    TROPICAL_CITIES, FOUR_SEASON_CITIES, SOUTHERN_HEMISPHERE_CITIES, SEASONAL_SIGMA_MULTIPLIERS,
    ENSEMBLE_WEIGHT, POINT_FORECAST_WEIGHT, WTRIN_WEIGHT,
)
from market_discovery_internal.utils import (
    fetch_with_retry, _safe_float
)
from market_discovery_internal.parsing import (
    _extract_yes_token_id, THRESHOLD_RE, _market_search_text
)

logger = logging.getLogger(__name__)

# Event family cache used by market-implied probability logic.
_CURRENT_EVENT_FAMILIES = {}
_EVENT_FAMILIES_LOCK = threading.Lock()

def _events_to_markets(events, reset_family_cache=False):
    """Flatten events into markets and keep sibling families for implied pricing."""
    global _CURRENT_EVENT_FAMILIES
    with _EVENT_FAMILIES_LOCK:
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
    with _EVENT_FAMILIES_LOCK:
        siblings = _CURRENT_EVENT_FAMILIES.get(str(token_id))
    if not siblings or len(siblings) < 2:
        return None

    rows = []
    for sibling in siblings:
        sibling_token = _extract_yes_token_id(sibling)
        if not sibling_token: continue

        bid = _safe_float(sibling.get("bestBid"), -1)
        ask = _safe_float(sibling.get("bestAsk"), -1)
        if not (0 < bid <= 1.0) or not (0 < ask <= 1.0):
            continue

        title = str(sibling.get("question") or sibling.get("title") or "")
        match = THRESHOLD_RE.search(title) or THRESHOLD_RE.search(_market_search_text(sibling))
        if not match: continue

        threshold = _safe_float(match.group(1))
        # [FIX-T6-2-HIGH] Handle None unit — str(None).upper() produced "NONE",
        # treating unitless F values as Celsius. Now infer unit from magnitude.
        _unit_raw = match.group(2)
        if _unit_raw:
            unit = _unit_raw.upper()
        else:
            # Infer: values >= 60 are likely Fahrenheit (same heuristic as parsing.py)
            unit = "F" if threshold >= 60 else "C"
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

def fetch_orderbook_quote(token_id: str, timeout: int = 10, max_retries: int = 3) -> Optional[dict[str, Optional[float]]]:
    """Fetch the real-time best bid/ask for a specific token from CLOB."""
    if not token_id: return None
    url = f"{CLOB_BOOK_API}?token_id={token_id}"
    try:
        data = fetch_with_retry(url, timeout=timeout, max_retries=max_retries)
        # Gamma CLOB book returns { "bids": [...], "asks": [...] }
        best_bid = _extract_top_orderbook_price(data.get("bids", []), "bid")
        best_ask = _extract_top_orderbook_price(data.get("asks", []), "ask")
        return {"bid": best_bid, "ask": best_ask}
    except Exception:
        logger.debug("Orderbook quote fetch failed for token %s", token_id)
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
        logger.debug("Liquidity depth check failed for token %s", token_id)
        return False

def calculate_depth_adjusted_stake(token_id, base_stake, max_slippage_pct=0.03, timeout=10, max_retries=3):
    """
    [MODUL L] Dynamically adjust stake based on orderbook depth and spread.
    Ensures we don't cross into prices that exceed our max slippage threshold.
    Returns the adjusted stake in USD.
    """
    from market_discovery_internal.config import LIQUIDITY_DEPTH_MULTIPLIER, MARKET_MAX_SPREAD_GATE
    
    if not token_id: return base_stake
    
    url = f"{CLOB_BOOK_API}?token_id={token_id}"
    try:
        data = fetch_with_retry(url, timeout=timeout, max_retries=max_retries)
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
        logger.debug("Depth-adjusted stake calculation failed for token %s", token_id)
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
        logger.debug("Calibration lookup failed for %s/%s, using defaults", city, direction)
        hits, total, brier_sum = 0, 0, 0.0

    prior_weight = float(min_samples)
    calibrated = (hits + raw_prob * prior_weight) / (total + prior_weight)
    # [FIX-M-T2-1] Clamp max deviation to ±0.20 from raw_prob.
    # Prevents sparse/noisy historical data from catastrophically overriding
    # a correct model probability (e.g., 85% crushed to 25% by 2/20 bin).
    calibrated = max(raw_prob - 0.20, min(raw_prob + 0.20, calibrated))
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

    from market_discovery_internal.config import (
        REGIME_GOOD_MIN_PROB, REGIME_GOOD_MIN_EDGE, REGIME_GOOD_MAX_PRICE,
        REGIME_NEUTRAL_MIN_PROB, REGIME_NEUTRAL_MIN_EDGE, REGIME_NEUTRAL_MAX_PRICE,
        REGIME_STRESS_MIN_PROB, REGIME_STRESS_MIN_EDGE, REGIME_STRESS_MAX_PRICE,
    )
    if regime_score >= 0.65:
        regime_class = "good"
        gates = {"min_prob": REGIME_GOOD_MIN_PROB, "min_edge": REGIME_GOOD_MIN_EDGE, "max_price": REGIME_GOOD_MAX_PRICE}
    elif regime_score >= 0.40:
        regime_class = "neutral"
        gates = {"min_prob": REGIME_NEUTRAL_MIN_PROB, "min_edge": REGIME_NEUTRAL_MIN_EDGE, "max_price": REGIME_NEUTRAL_MAX_PRICE}
    else:
        regime_class = "stress"
        gates = {"min_prob": REGIME_STRESS_MIN_PROB, "min_edge": REGIME_STRESS_MIN_EDGE, "max_price": REGIME_STRESS_MAX_PRICE}

    return regime_class, regime_score, gates


def _get_city_sigma(city: str, date: str = None) -> float:
    """Get the appropriate Gaussian sigma for a city based on its climate region and season.

    Seasonal multiplier is applied only to FOUR_SEASON_CITIES — tropical cities
    have stable weather year-round, and unclassified cities use the default sigma.
    """
    city_lower = (city or "").lower()
    if city_lower in TROPICAL_CITIES:
        return SIGMA_TROPICAL
    elif city_lower in FOUR_SEASON_CITIES:
        base = SIGMA_FOUR_SEASON
        # Apply seasonal multiplier if date is provided (format: "YYYY-MM-DD")
        if date and len(date) >= 7:
            try:
                month = int(date[5:7])
                # [FIX-M-T6-3b] Southern Hemisphere: invert seasons (offset by 6 months)
                # July=winter in Buenos Aires/Sao Paulo/Sydney, not summer
                if city_lower in SOUTHERN_HEMISPHERE_CITIES:
                    month = ((month + 5) % 12) + 1
                multiplier = SEASONAL_SIGMA_MULTIPLIERS.get(month, 1.0)
                return round(base * multiplier, 3)
            except (ValueError, IndexError):
                pass
        return base
    return SIGMA_DEFAULT


def calculate_edge(market: dict[str, Any], forecast_temp: Optional[float], hours_until_resolve: Optional[float] = None, k_factor_override: Optional[float] = None) -> Optional[dict[str, Any]]:
    """
    Calculate the statistical edge of a market position compared to forecast.

    Logic:
    - Above/Below: sigmoid model probability around threshold (converted to °C at entry).
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
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    threshold_raw = market.get("threshold")
    direction = market.get("direction")
    city = str(market.get("city", "")).lower()
    market_date = market.get("date")  # "YYYY-MM-DD" for seasonal sigma
    unit = str(market.get("unit", "C")).upper()

    # [CRITICAL FIX] Convert threshold to Celsius for all calculations.
    # parse_market stores threshold in original units (F or C).
    # Forecast is always in Celsius (from Open-Meteo), so threshold must match.
    if threshold_raw is not None:
        threshold = float(threshold_raw)
        if unit == "F":
            threshold = (threshold - 32.0) * 5.0 / 9.0
    else:
        threshold = None

    # For range brackets (e.g., "50-51°F"), also convert the upper bound
    threshold_high_raw = market.get("threshold_high")
    threshold_high = None
    if threshold_high_raw is not None:
        threshold_high = float(threshold_high_raw)
        if unit == "F":
            threshold_high = (threshold_high - 32.0) * 5.0 / 9.0

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
        # [EMOS INTEGRATION] Apply per-station bias correction.
        # Checks if ECMWF ensemble data exists in DB for this city+date.
        # If yes, loads cached EMOS model and applies proper Gaussian correction:
        #   mu = a + b * ensemble_mean, sigma = sqrt(c + d * ensemble_var)
        # If no model cached yet (< 60 days data), passthrough — forecast unchanged.
        try:
            from market_discovery_internal.database_manager import db
            _icao = market.get("icao_code", "")
            _stats = db.get_ecmwf_ensemble_stats(city, market_date) if _icao else None
            if _stats and _stats.get("count", 0) >= 5:
                _ensemble_mean = float(_stats["mean"])
                _ensemble_std = float(_stats["std"] or 1.0)
                # Load cached EMOS model from DB (saved by train_emos.py)
                from market_discovery_internal.emos import predict_emos
                _model_row = db.get_weather(_icao.lower(), "emos_model")
                _model = None
                if _model_row and _model_row.get("max_temp") is not None:
                    _a = float(_model_row["max_temp"])
                    _b = float(_model_row["min_temp"])
                    _c = float(_model_row["precipitation"])
                    _d_row = db.get_weather(_icao.lower(), "emos_model_d")
                    _d = float(_d_row["max_temp"]) if _d_row and _d_row.get("max_temp") is not None else 1.0
                    _model = {"a": _a, "b": _b, "c": _c, "d": _d}
                if _model is not None:
                    _pred = predict_emos(_model, _ensemble_mean, _ensemble_std)
                    _original = forecast
                    forecast = _pred["mu"]
                    prob_source = "gaussian_ecmwf_emos"
                    logger.info("[EMOS] %s: bias-corrected %.1f→%.1fC (sigma=%.2f)",
                                city, _original, forecast, _pred["sigma"])
        except Exception as exc:
            logger.debug("[EMOS] correction skipped for %s: %s", city, exc)


    # Compute model probability only for non-market-implied path
    if market_implied is None:
        raw_prob = 0.0
        # Sigmoid hardening based on hours remaining.
        # Fallback to 24h when hours_until_resolve is missing — assumes mid-range horizon
        # to avoid overly aggressive or conservative k-factor selection.
        if hours_until_resolve is None:
            logger.warning("hours_until_resolve is None for %s; defaulting to 24.0h", market.get("market_question", "unknown"))
        _h_val = float(hours_until_resolve) if hours_until_resolve is not None else 24.0
        if k_factor_override is not None:
            k = k_factor_override
        elif _h_val <= 6:
            k = 0.9
        elif _h_val <= 14:
            k = 1.1
        elif _h_val <= 36:
            k = 0.75
        else:
            k = 0.50

        if direction == "above":
            # Sigmoid: smooth gradient around threshold.
            diff = max(-50, min(50, (forecast - threshold)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
        elif direction == "below":
            diff = max(-50, min(50, (threshold - forecast)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
        elif direction == "exact":
            # Probability that the daily high lands within the bracket.
            # For range brackets (e.g., "50-51°F" → threshold_high available):
            #   P = Φ((upper - forecast)/σ) - Φ((lower - forecast)/σ)
            # For point brackets (e.g., "4°C"):
            #   P = Φ((threshold+0.5 - forecast)/σ) - Φ((threshold-0.5 - forecast)/σ)
            # Per-region sigma: tropical cities get tighter sigma (less variance),
            # four-season cities get wider sigma (more variance).
            from math import erf as _erf, sqrt as _sqrt
            sigma = _get_city_sigma(city, market_date)
            _s2 = sigma * _sqrt(2)
            if threshold_high is not None:
                # Range bracket: integrate over [threshold, threshold_high] (already in °C)
                lower_bound = threshold
                upper_bound = threshold_high
            else:
                # Point bracket: ±0.5°C window around threshold
                lower_bound = threshold - 0.5
                upper_bound = threshold + 0.5
            raw_prob = max(0.0, min(1.0, 0.5 * (
                _erf((upper_bound - forecast) / _s2) -
                _erf((lower_bound - forecast) / _s2)
            )))

        # [FIX] Hard ceiling: cap confidence based on forecast margin.
        # Weather ensemble uncertainty is ±2-3°C, so even large margins
        # should not yield near-100% certainty from the sigmoid alone.
        # Per-region sigma adjusts the caps: tropical (σ=1.0) allows higher
        # confidence at smaller margins; four-season (σ=2.0) is more conservative.
        if direction in ("above", "below"):
            city_sigma = _get_city_sigma(city, market_date)
            abs_diff = abs(forecast - threshold)
            # Scale cap thresholds by sigma ratio vs default (1.5)
            _sigma_ratio = city_sigma / SIGMA_DEFAULT if SIGMA_DEFAULT > 0 else 1.0
            if abs_diff < 1.0 * _sigma_ratio:
                raw_prob = min(raw_prob, 0.75)
            elif abs_diff < 2.0 * _sigma_ratio:
                raw_prob = min(raw_prob, 0.87)
            elif abs_diff < 3.0 * _sigma_ratio:
                raw_prob = min(raw_prob, 0.94)
            else:
                # Absolute hard cap: weather is NEVER 100% certain.
                # Even with large margins, forecast error ±2-3°C means
                # there's always residual uncertainty.
                raw_prob = min(raw_prob, 0.95)

            # [FIX] Consensus Guard: Detect "Too-Good-To-Be-True" bargains that are likely traps.
            # If we are < 12h from resolve and we are 90%+ sure, but market price is < 30c,
            # we assume the market knows something our forecast API doesn't.
            if _h_val < 12.0 and raw_prob > 0.90 and price < 0.30:
                _gap = raw_prob - price
                if _gap > 0.50:
                    raw_prob = (raw_prob + price) / 2.0 # Dampen hubris towards market center
                    prob_source += "+consensus_guard"
    # else: raw_prob already set from market_implied above

    # -------------------------------------------------------------------------
    # [NOAA METAR] Real-time Override (only in last N hours before resolution)
    # -------------------------------------------------------------------------
    # Skip NOAA override for min-temp markets: METAR reports instantaneous temperature,
    # not the daily minimum. Comparing current temp vs daily-low threshold is unreliable.
    _temp_type = market.get("temp_type", "max")
    hours_left = float(hours_until_resolve) if hours_until_resolve is not None else 24.0
    _noaa_override_fired = False  # [FIX-T2-4-HIGH] Track if NOAA override fired
    if NOAA_OVERRIDE_ENABLED and hours_left <= NOAA_OVERRIDE_WINDOW_HOURS and threshold is not None and _temp_type == "max":
        icao = market.get("icao_code") or ""
        if icao:
            from market_discovery_internal.forecasting import fetch_noaa_metar
            noaa_temp = fetch_noaa_metar(icao)
            if noaa_temp is not None:
                # threshold is already in °C (converted above)
                # Price cap: don't override if market price > $0.80 (avoid overpaying)
                _price_safe = price is not None and float(price) <= 0.80
                if direction == "above" and _price_safe:
                    # [FIX-T2-3-HIGH] Add 0.5°C margin for METAR measurement accuracy
                    if noaa_temp > threshold + 0.5:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C > %.1f°C threshold. Overriding prob to %.2f",
                                    city, noaa_temp, threshold, NOAA_OVERRIDE_CONFIRM_PROB)
                        raw_prob = max(raw_prob, NOAA_OVERRIDE_CONFIRM_PROB)
                    elif noaa_temp < threshold - 5.0 and hours_left <= 2.0:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C << %.1f°C with %.1fh left. Overriding prob to %.2f",
                                    city, noaa_temp, threshold, hours_left, NOAA_OVERRIDE_CONTRADICT_PROB)
                        raw_prob = min(raw_prob, NOAA_OVERRIDE_CONTRADICT_PROB)
                elif direction == "below" and _price_safe:
                    # [FIX-T2-3-CRIT] "Below" confirm for max-temp markets: METAR reports
                    # instantaneous temp, NOT the daily high. Current temp being below threshold
                    # does NOT mean the daily high will stay below it — temp could still rise.
                    # Only confirm when late in the day (<=2h) AND temp is well below threshold.
                    if noaa_temp < threshold - 2.0 and hours_left <= 2.0:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C < %.1f°C threshold (margin 2°C, %.1fh left). Overriding prob to %.2f",
                                    city, noaa_temp, threshold, hours_left, NOAA_OVERRIDE_CONFIRM_PROB)
                        raw_prob = max(raw_prob, NOAA_OVERRIDE_CONFIRM_PROB)
                    elif noaa_temp > threshold + 5.0 and hours_left <= 2.0:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C >> %.1f°C with %.1fh left. Overriding prob to %.2f",
                                    city, noaa_temp, threshold, hours_left, NOAA_OVERRIDE_CONTRADICT_PROB)
                        raw_prob = min(raw_prob, NOAA_OVERRIDE_CONTRADICT_PROB)
                elif direction == "exact" and _price_safe:
                    # For exact: if NOAA temp is within bracket, confirm; if far away, contradict
                    _upper = threshold_high if threshold_high is not None else threshold + 0.5
                    _lower = threshold - 0.5 if threshold_high is None else threshold
                    if _lower <= noaa_temp <= _upper:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C within [%.1f, %.1f] bracket. Overriding prob to %.2f",
                                    city, noaa_temp, _lower, _upper, NOAA_OVERRIDE_CONFIRM_PROB)
                        raw_prob = max(raw_prob, NOAA_OVERRIDE_CONFIRM_PROB)
                    elif abs(noaa_temp - ((_lower + _upper) / 2)) > 5.0 and hours_left <= 2.0:
                        logger.info("[NOAA-OVERRIDE] %s: %.1f°C far from bracket [%.1f, %.1f] with %.1fh left. Overriding prob to %.2f",
                                    city, noaa_temp, _lower, _upper, hours_left, NOAA_OVERRIDE_CONTRADICT_PROB)
                        raw_prob = min(raw_prob, NOAA_OVERRIDE_CONTRADICT_PROB)

    # [FIX-T2-4-HIGH] Detect if NOAA override changed raw_prob.
    # When NOAA fires, it's based on real-time ground truth — don't let
    # calibration and ensemble dilute it back toward model averages.
    if raw_prob >= NOAA_OVERRIDE_CONFIRM_PROB or raw_prob <= NOAA_OVERRIDE_CONTRADICT_PROB:
        _noaa_override_fired = True

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

    # [FIX-T2-4-HIGH] Skip calibration + ensemble when NOAA override fired.
    # NOAA is real-time ground truth — don't dilute it with model averages.
    if _noaa_override_fired:
        model_prob = raw_prob
        calib_meta = {"skipped": "noaa_override"}
        ensemble_applied = False
    else:
        calibrated, calib_meta = _calibrated_prob(city, str(direction), horizon_bin, price_bin, raw_prob)
        model_prob = calibrated  # Use calibrated for edge calculation

        # [ENSEMBLE] Weighted merge with GFS ensemble probability if available
        ensemble_data = market.get("ensemble_data")
        ensemble_applied = False
        if ensemble_data and isinstance(ensemble_data, dict) and ensemble_data.get("ensemble_prob") is not None:
            try:
                ensemble_prob = max(0.0, min(1.0, float(ensemble_data["ensemble_prob"])))  # [FIX-L-T2-4] Clamp
                _ew = ENSEMBLE_WEIGHT  # default 0.45
                _mw = 1.0 - _ew       # default 0.55
                merged_prob = _ew * ensemble_prob + _mw * model_prob
                model_prob = max(0.0, min(1.0, merged_prob))
                ensemble_applied = True
            except (TypeError, ValueError):
                pass  # Fall back to model_prob without ensemble

    # [ABSOLUTE CAP] Weather probability must NEVER exceed 95%.
    # Forecast models have inherent ±2-3°C error, resolution source may differ
    # from forecast source, and unexpected weather events always have >0% chance.
    # This cap applies AFTER all adjustments (calibration, ensemble, NOAA).
    model_prob = min(model_prob, 0.95)

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

    result = {
        "model_prob": round(model_prob, 4),
        "raw_prob": round(raw_prob, 4),
        "edge": round(edge, 4),
        "forecast": forecast,
        "prob_source": prob_source + ("+ensemble" if ensemble_applied else ""),
        "slippage_penalty_applied": round(slippage_penalty, 4),
        "horizon_bin": horizon_bin,
        "price_bin": price_bin,
        "ensemble_applied": ensemble_applied,
        **calib_meta,
    }
    if ensemble_applied and ensemble_data:
        result["ensemble_prob"] = round(float(ensemble_data.get("ensemble_prob", 0)), 4)
        result["ensemble_mean"] = round(float(ensemble_data.get("ensemble_mean", 0)), 1)
        result["ensemble_spread"] = round(float(ensemble_data.get("ensemble_spread", 0)), 2)
        result["ensemble_member_count"] = int(ensemble_data.get("member_count", 0))
    return result

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

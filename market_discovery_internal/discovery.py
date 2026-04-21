"""High-level discovery and filtering logic for market_discovery."""

import sys
import json
import logging
import math
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

from market_discovery_internal.config import (
    GAMMA_EVENTS_API, DISCOVERY_MAX_FETCH_PAGES, DISCOVERY_AGGRESSIVE_SCAN_PAGES,
    STRATEGY_MAX_YES_PRICE, STRATEGY_MIN_MODEL_PROB, STRATEGY_MIN_EDGE,
    STRATEGY_EXACT_MIN_MODEL_PROB, STRATEGY_EXACT_MIN_EDGE,
    DAILY_TARGET_MULTIPLIER,
    TIME_DECAY_EDGE_ENABLED, TIME_DECAY_BASE_HOURS,
)
from market_discovery_internal.utils import fetch_with_retry
from market_discovery_internal.parsing import (
    _is_temperature_market_candidate, parse_market
)
from market_discovery_internal.pricing import (
    _enrich_markets_missing_prices, _compute_market_implied_prob, calculate_edge,
    _events_to_markets
)
from market_discovery_internal.forecasting import fetch_forecast

def fetch_markets(inspect=False, aggressive_scan=False):
    """Fetch active weather markets from the Polymarket Gamma Events API."""
    base_params = {
        "tag_slug": "weather",
        "active": "true",
        "archived": "false",
        "closed": "false",
        "order": "volume_24hr",
        "ascending": "false",
        "limit": 200,
    }

    try:
        data = fetch_with_retry(GAMMA_EVENTS_API, params={**base_params, "offset": 0})
    except Exception as e:
        # Raise instead of sys.exit so the loop retry in cli.py can catch and backoff.
        raise RuntimeError(f"Could not fetch markets from Gamma API: {e}") from e

    if isinstance(data, dict):
        events = data.get("data", data.get("events", []))
    else:
        events = data

    if inspect:
        logger.info("=== INSPECT MODE: First event and its first 3 markets ===")
        for i, event in enumerate(events[:2], 1):
            logger.info("--- Event %d: %s ---", i, event.get('title', ''))
            for j, market in enumerate(event.get("markets", [])[:3], 1):
                logger.info("  Market %d:\n%s", j, json.dumps(market, indent=4, default=str))
        sys.exit(0)

    markets = _events_to_markets(events, reset_family_cache=True)
    candidates = [m for m in markets if _is_temperature_market_candidate(m)]

    # Fetch additional pages
    page_limit = base_params["limit"]
    offset = page_limit
    max_pages = DISCOVERY_AGGRESSIVE_SCAN_PAGES if aggressive_scan else DISCOVERY_MAX_FETCH_PAGES

    for _ in range(max(0, max_pages)):
        try:
            page_data = fetch_with_retry(GAMMA_EVENTS_API, params={**base_params, "offset": offset})
            if isinstance(page_data, dict):
                page_events = page_data.get("data", page_data.get("events", []))
            else:
                page_events = page_data
            
            if not page_events: break
            
            page_markets = _events_to_markets(page_events)
            page_candidates = [m for m in page_markets if _is_temperature_market_candidate(m)]
            candidates.extend(page_candidates)
            offset += page_limit
        except Exception as e:
            logger.warning(f"Pagination stopped at offset {offset}: {e}")
            break

    # Deduplicate by token_id
    from market_discovery_internal.parsing import _extract_yes_token_id
    seen = set()
    deduped = []
    for m in candidates:
        tid = _extract_yes_token_id(m)
        if tid and tid not in seen:
            seen.add(tid)
            deduped.append(m)
            
    return _enrich_markets_missing_prices(deduped)

from market_discovery_internal.database_manager import db


def filter_enriched_opportunities(
    enriched,
    max_yes_price=STRATEGY_MAX_YES_PRICE,
    min_model_prob=STRATEGY_MIN_MODEL_PROB,
    min_edge=STRATEGY_MIN_EDGE,
):
    """Filter already-enriched market dicts using gates (global or per-market regime_gates).

    Returns list sorted by edge descending.
    This is the public API used by tests and the paper cycle filter lambda.
    """
    result = []
    for m in enriched:
        gates = m.get("regime_gates") or {}
        mp = float(gates.get("max_price", max_yes_price))
        prob = float(gates.get("min_prob", min_model_prob))
        edge = float(gates.get("min_edge", min_edge))
        # Time-decay: scale min_edge by sqrt(hours / base_hours)
        if TIME_DECAY_EDGE_ENABLED:
            _h = float(m.get("hours_until_resolve") or TIME_DECAY_BASE_HOURS)
            edge = edge * math.sqrt(max(_h, 0.1) / TIME_DECAY_BASE_HOURS)
        if (float(m.get("yes_price", 1.0)) <= mp
                and float(m.get("model_prob", 0.0)) >= prob
                and float(m.get("edge", -1.0)) >= edge):
            result.append(m)
    return sorted(result, key=lambda x: x.get("edge", 0), reverse=True)


def filter_opportunities(
    markets,
    now_utc=None,
    max_yes_price=STRATEGY_MAX_YES_PRICE,
    min_model_prob=STRATEGY_MIN_MODEL_PROB,
    min_edge=STRATEGY_MIN_EDGE,
    daily_target_multiplier=DAILY_TARGET_MULTIPLIER,
    fetch_forecast_fn=fetch_forecast
):
    """
    Filter raw markets down to profitable opportunities using weather forecasts.
    Now leverages the Data Warehouse for instant bulk filtering.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
        
    # 1. Parsing and Pre-Filtering (Quick pass)
    parsed_list = []
    keys_to_fetch = []
    for raw in markets:
        parsed, skip_reason = parse_market(raw, now_utc=now_utc, return_skip_reason=True)
        if not parsed: continue
        if parsed["yes_price"] > max_yes_price: continue
        
        parsed_list.append(parsed)
        keys_to_fetch.append((parsed["city"], parsed["date"]))

    # 2. Bulk Load Forecasts from Warehouse
    try:
        bulk_cache = db.get_bulk_cached_forecasts(keys_to_fetch)
    except Exception as e:
        logger.warning("[RECOVER] Bulk cache fetch failed: %s", e)
        bulk_cache = {}
    
    opportunities = []
    for parsed in parsed_list:
        city_key = (parsed["city"].lower(), parsed["date"])
        cached = bulk_cache.get(city_key)
        
        try:
            if cached:
                # Reconstruct ForecastTemp-like behavior for cached data
                from market_discovery_internal.forecasting import ForecastTemp, _fetch_historical_average
                source = (cached.get('source') or "unknown") + " (warehouse)"
                forecast_temp = ForecastTemp(cached['forecast_temp'], source)
                forecast_temp.historical_avg = _fetch_historical_average(parsed["city"], parsed["date"], allow_live=False)
                forecast_temp.noaa_current = None
            else:
                # Fallback to direct fetch if missing from warehouse
                forecast_temp = fetch_forecast_fn(parsed["city"], parsed["date"], parsed.get("icao_code"))
            
            if forecast_temp is None: continue
            
            edge_data = calculate_edge(parsed, forecast_temp)
            if not edge_data: continue
            
            parsed.update(edge_data)
            parsed["forecast_source"] = getattr(forecast_temp, "source", "unknown")
            
            # Strategy filters — use lower thresholds for exact bracket markets
            _dir = parsed.get("direction", "")
            _min_prob = STRATEGY_EXACT_MIN_MODEL_PROB if _dir == "exact" else min_model_prob
            _min_edge = STRATEGY_EXACT_MIN_EDGE if _dir == "exact" else min_edge
            # Time-decay: scale min_edge by sqrt(hours / base_hours), exempt exact brackets
            if TIME_DECAY_EDGE_ENABLED and _dir != "exact":
                _h = float(parsed.get("hours_until_resolve") or TIME_DECAY_BASE_HOURS)
                _min_edge = _min_edge * math.sqrt(max(_h, 0.1) / TIME_DECAY_BASE_HOURS)
            if parsed["model_prob"] >= _min_prob and parsed["edge"] >= _min_edge:
                opportunities.append(parsed)
        except Exception as e:
            logger.error("[ERROR] Opportunity filtering failed for %s: %s", parsed.get('city'), e, exc_info=True)
            continue
            
    # Sort by edge descending
    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    return opportunities

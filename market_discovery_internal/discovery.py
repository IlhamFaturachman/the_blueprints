"""High-level discovery and filtering logic for market_discovery."""

import sys
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from market_discovery_internal.config import (
    GAMMA_EVENTS_API, DISCOVERY_MAX_FETCH_PAGES, DISCOVERY_AGGRESSIVE_SCAN_PAGES,
    STRATEGY_MAX_YES_PRICE, STRATEGY_MIN_MODEL_PROB, STRATEGY_MIN_EDGE,
    DAILY_TARGET_MULTIPLIER
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
        print(f"\nERROR: Could not fetch markets from Gamma API: {e}")
        sys.exit(1)

    if isinstance(data, dict):
        events = data.get("data", data.get("events", []))
    else:
        events = data

    if inspect:
        print("=== INSPECT MODE: First event and its first 3 markets ===\n")
        for i, event in enumerate(events[:2], 1):
            print(f"--- Event {i}: {event.get('title', '')} ---")
            for j, market in enumerate(event.get("markets", [])[:3], 1):
                print(f"  Market {j}:")
                print(json.dumps(market, indent=4, default=str))
            print()
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
        except Exception:
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
        print(f"[RECOVER] Bulk cache fetch failed: {e}")
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
            
            # Strategy filters
            if parsed["model_prob"] >= min_model_prob and parsed["edge"] >= min_edge:
                opportunities.append(parsed)
        except Exception as e:
            import traceback
            print(f"[ERROR] Opportunity filtering failed for {parsed.get('city')}: {e}")
            traceback.print_exc()
            continue
            
    # Sort by edge descending
    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    return opportunities

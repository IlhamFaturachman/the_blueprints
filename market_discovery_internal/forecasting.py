"""Forecast cache/prefetch helpers for market_discovery."""

from concurrent.futures import ThreadPoolExecutor, as_completed


def position_to_market(position, current_yes_price, hours_until_resolve):
    """Build calculate_edge-compatible market dict from an open position."""
    return {
        "city": position["city"],
        "date": position["date"],
        "end_date": position.get("end_date"),
        "market_question": position.get("market_question", ""),
        "threshold": position["threshold"],
        "unit": position["unit"],
        "direction": position["direction"],
        "yes_price": float(current_yes_price),
        "token_id": position["token_id"],
        "hours_until_resolve": hours_until_resolve if hours_until_resolve is not None else position.get("hours_until_resolve"),
    }


def fetch_forecast_with_cache(city, date, cache, stats=None, *, fetch_forecast_fn):
    """Fetch forecast with per-cycle cache for successful city/date lookups."""
    if not isinstance(cache, dict):
        return fetch_forecast_fn(city, date)

    cache_key = (city, date)
    cached = cache.get(cache_key)
    if cached is not None:
        if isinstance(stats, dict):
            stats["hits"] = int(stats.get("hits", 0)) + 1
        return cached

    if isinstance(stats, dict):
        stats["misses"] = int(stats.get("misses", 0)) + 1

    forecast_temp = fetch_forecast_fn(city, date)
    # Keep behavior close to legacy flow: only cache successful results.
    if forecast_temp is not None:
        cache[cache_key] = forecast_temp
    return forecast_temp


def prefetch_forecasts(cache_keys, cache, min_keys=0, max_workers=1, *, fetch_forecast_fn):
    """Warm forecast cache in parallel for large unique city/date batches."""
    if not isinstance(cache, dict):
        return {
            "eligible": 0,
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "workers": 0,
            "skipped": True,
        }

    unique_keys = []
    seen = set()
    for city, date in cache_keys:
        if not city or not date:
            continue
        key = (city, date)
        if key in cache or key in seen:
            continue
        seen.add(key)
        unique_keys.append(key)

    eligible = len(unique_keys)
    min_required = max(0, int(min_keys))
    workers = min(max(1, int(max_workers)), eligible) if eligible else 0

    if eligible < min_required or workers <= 1:
        return {
            "eligible": eligible,
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "workers": workers,
            "skipped": True,
        }

    stats = {
        "eligible": eligible,
        "attempted": eligible,
        "successful": 0,
        "failed": 0,
        "workers": workers,
        "skipped": False,
    }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_key = {
            executor.submit(fetch_forecast_fn, city, date): (city, date)
            for city, date in unique_keys
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                forecast_temp = future.result()
            except Exception:
                forecast_temp = None

            if forecast_temp is None:
                stats["failed"] += 1
                continue

            cache[key] = forecast_temp
            stats["successful"] += 1

    return stats


def forecast_still_valid(
    position,
    current_yes_price,
    hours_until_resolve,
    forecast_cache=None,
    forecast_cache_stats=None,
    *,
    fetch_forecast_with_cache_fn,
    build_weather_evidence_fn,
    is_weather_evidence_valid_fn,
    position_to_market_fn,
    calculate_edge_fn,
):
    """Evaluate whether forecast still supports the open position thesis."""
    forecast_temp = fetch_forecast_with_cache_fn(
        position["city"],
        position["date"],
        forecast_cache,
        stats=forecast_cache_stats,
    )
    evidence = build_weather_evidence_fn(position["city"], position["date"], forecast_temp)

    if not is_weather_evidence_valid_fn(evidence):
        return False

    market_view = position_to_market_fn(position, current_yes_price, hours_until_resolve)
    edge_result = calculate_edge_fn(market_view, forecast_temp)
    return bool(edge_result and edge_result["model_prob"] >= 0.70)

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import urllib.parse

from market_discovery_internal.config import (
    TARGET_CITIES, OPEN_METEO_API, OPEN_METEO_HISTORICAL_API,
    CONSENSUS_MAX_ERROR_C, HISTORICAL_DEVIATION_C, ANOMALY_LOG_FILE
)
from market_discovery_internal.utils import fetch_with_retry

class ForecastTemp(float):
    """Float wrapper to store the oracle source."""
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

def _fetch_historical_average(city, date):
    """Fetch the 10-year historical average max temp for this date/city using a single range lookup."""
    coords = TARGET_CITIES.get(city)
    if not coords or not date:
        return None
    
    try:
        year = int(date.split("-")[0])
        month_day = date[5:]
        # Fetch a 10-year window ending last year
        start_date = f"{year-10}-{month_day}"
        end_date = f"{year-1}-{month_day}"
        
        params = {
            "latitude": coords["lat"], "longitude": coords["lon"],
            "start_date": start_date, "end_date": end_date,
            "daily": "temperature_2m_max", "timezone": "auto"
        }
        res = fetch_with_retry(OPEN_METEO_HISTORICAL_API, params=params)
        daily = res.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        
        # Filter for the same month and day across the decade
        matches = [t for ts, t in zip(times, temps) if ts.endswith(month_day) and t is not None]
        return round(sum(matches) / len(matches), 1) if matches else None
    except Exception as e:
        print(f"[MODUL-K] Historical fetch error: {e}")
        return None

def _log_anomaly(city, date, forecast, historical, source="anomaly"):
    """Log rejected anomalous or non-consensus forecasts for transparency."""
    os.makedirs("logs", exist_ok=True)
    with open(ANOMALY_LOG_FILE, "a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] REJECTED {city} ({date}): Forecast={forecast}C, Reference={historical}C, Type={source}\n")

def fetch_forecast(city, date, icao_override=None):
    """Fetch the daily max temperature forecast from Open-Meteo and wttr.in.

    For same-day forecasts (date == today UTC), uses hourly data to compute
    the max of remaining daytime hours (06:00–22:00 local).
    """
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None

    def _fetch_open_meteo():
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        use_hourly = (date == today_utc)

        if use_hourly:
            params = {
                "latitude": coords["lat"],
                "longitude": coords["lon"],
                "hourly": "temperature_2m",
                "timezone": "auto",
                "forecast_days": 2,
            }
            try:
                data = fetch_with_retry(OPEN_METEO_API, params=params)
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                temps = hourly.get("temperature_2m", [])
                # Take max over daytime hours (6–22) for the target date
                day_temps = [
                    t for ts, t in zip(times, temps)
                    if ts.startswith(date) and 6 <= int(ts.split("T")[1].split(":")[0]) <= 22
                ]
                return max(day_temps) if day_temps else None
            except Exception:
                pass
        else:
            params = {
                "latitude": coords["lat"], "longitude": coords["lon"],
                "daily": "temperature_2m_max", "timezone": "auto", "forecast_days": 3
            }
            try:
                data = fetch_with_retry(OPEN_METEO_API, params=params)
                daily = data.get("daily", {})
                times = daily.get("time", [])
                temps = daily.get("temperature_2m_max", [])
                if date in times:
                    return temps[times.index(date)]
            except Exception:
                pass
        return None

    def _fetch_wttr():
        try:
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
            data = fetch_with_retry(url)
            for w in data.get("weather", []):
                if w.get("date") == date:
                    return float(w.get("maxtempC", 0))
        except Exception:
            pass
        return None

    def _fetch_noaa():
        icao = icao_override or coords.get("icao")
        if not icao:
            return None
        try:
            url = f"https://api.weather.gov/stations/{icao}/observations/latest"
            headers = {"User-Agent": "TheBlueprintsBot/2.0"}
            data = fetch_with_retry(url, headers=headers)
            temp_c = data.get("properties", {}).get("temperature", {}).get("value")
            if temp_c is not None:
                return float(temp_c)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_om = executor.submit(_fetch_open_meteo)
        f_wt = executor.submit(_fetch_wttr)
        f_noaa = executor.submit(_fetch_noaa)
        
        t_om = f_om.result()
        t_wt = f_wt.result()
        t_noaa = f_noaa.result()

    base_avg = None
    source = None

    if t_om is not None and t_wt is not None:
        # [MODUL K] Anomaly Check (Max 7°C deviation between oracles)
        if abs(t_om - t_wt) > 7.0:
            return None # Major disagreement between weather sources, reject forecast to be safe.
        
        base_avg = round((t_om + t_wt) / 2.0, 1)
        source = "verified-triple-source" if t_noaa is not None else "verified-dual-source"
        if t_noaa is not None and icao_override:
            source += " (METAR)"
    elif t_om is not None:
        base_avg = t_om
        source = "open-meteo"
    elif t_wt is not None:
        base_avg = t_wt
        source = "wttr.in"
        
    if base_avg is not None:
        # [MODUL K] Historical Anomaly Check
        hist_avg = _fetch_historical_average(city, date)
        if hist_avg is not None:
            if abs(base_avg - hist_avg) > HISTORICAL_DEVIATION_C:
                _log_anomaly(city, date, base_avg, hist_avg, "historical_anomaly")
                return None
        
        # [MODUL B] Strict Consensus Check (Ground Truth METAR)
        # If we have ground truth (NOAA), it MUST be close to our forecast.
        if t_noaa is not None:
            # Note: Latest METAR is a snapshot, forecast is max temp.
            # We only enforce consensus if we're near the current time or if it's already hotter than predicted.
            if abs(base_avg - t_noaa) > CONSENSUS_MAX_ERROR_C:
                # If current temp is already WAY higher than predicted max, that's an anomaly.
                # If current temp is way lower and it's nearly resolve time, that's also an anomaly.
                _log_anomaly(city, date, base_avg, t_noaa, "consensus_mismatch")
                return None

        ft = ForecastTemp(base_avg, source)
        ft.noaa_current = t_noaa
        ft.historical_avg = hist_avg
        return ft
    return None

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


def fetch_forecast_with_cache(city, date, cache, stats=None, *, fetch_forecast_fn, icao_override=None):
    """Fetch forecast with per-cycle cache for successful city/date/icao lookups."""
    if not isinstance(cache, dict):
        return fetch_forecast_fn(city, date, icao_override=icao_override)

    cache_key = (city, date, icao_override)
    cached = cache.get(cache_key)
    if cached is not None:
        if isinstance(stats, dict):
            stats["hits"] = int(stats.get("hits", 0)) + 1
        return cached

    if isinstance(stats, dict):
        stats["misses"] = int(stats.get("misses", 0)) + 1

    forecast_temp = fetch_forecast_fn(city, date, icao_override=icao_override)
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
    for item in cache_keys:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        
        city = item[0]
        date = item[1]
        icao = item[2] if len(item) > 2 else None
        
        if not city or not date:
            continue
        key = (city, date, icao)
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
            executor.submit(fetch_forecast_fn, city, date, icao_override=icao): (city, date, icao)
            for city, date, icao in unique_keys
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
        icao_override=position.get("icao_code")
    )
    evidence = build_weather_evidence_fn(position["city"], position["date"], forecast_temp)

    if not is_weather_evidence_valid_fn(evidence):
        return False

    market_view = position_to_market_fn(position, current_yes_price, hours_until_resolve)
    edge_result = calculate_edge_fn(market_view, forecast_temp)
    return bool(edge_result and edge_result["model_prob"] >= 0.70)

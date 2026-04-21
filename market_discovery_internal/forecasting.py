from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
import logging
import urllib.parse
import os
import json
import time
import threading

from market_discovery_internal.config import (
    TARGET_CITIES, OPEN_METEO_API, OPEN_METEO_HISTORICAL_API,
    CONSENSUS_MAX_ERROR_C, HISTORICAL_DEVIATION_C, ANOMALY_LOG_FILE,
    NOAA_METAR_API,
    OPEN_METEO_ENSEMBLE_API, ENSEMBLE_ENABLED,
    POINT_FORECAST_WEIGHT, WTRIN_WEIGHT,
)
from market_discovery_internal.utils import fetch_with_retry

logger = logging.getLogger(__name__)


def fetch_noaa_metar(icao: str) -> Optional[float]:
    """
    Fetch current temperature from NOAA METAR for an airport station.
    Returns temperature in Celsius, or None on failure.
    """
    if not icao:
        return None
    try:
        params = {"ids": icao, "format": "json", "hours": 2}
        # fetch_with_retry returns parsed JSON; use requests directly for
        # response-object control and a shorter timeout.
        import requests as _requests
        resp = _requests.get(NOAA_METAR_API, params=params, timeout=8)
        if not resp or resp.status_code != 200:
            return None
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        # METAR data: temp field (already in Celsius)
        latest = data[0]
        # Staleness check: reject observations older than 1 hour
        report_time = latest.get("reportTime") or latest.get("obsTime")
        if report_time:
            try:
                if isinstance(report_time, (int, float)):
                    obs_age_s = time.time() - float(report_time)
                else:
                    from datetime import datetime as _dt
                    obs_dt = _dt.fromisoformat(str(report_time).replace("Z", "+00:00"))
                    obs_age_s = (datetime.now(timezone.utc) - obs_dt).total_seconds()
                if obs_age_s > 3600:  # > 1 hour old
                    logger.warning("[NOAA] METAR %s: observation %.0f min old — too stale, skipping", icao, obs_age_s / 60)
                    return None
            except Exception:
                pass  # Can't parse time — proceed with caution
        temp_c = latest.get("temp")
        if temp_c is None:
            # Try parsing from rawOb
            raw = latest.get("rawOb", "")
            # METAR temp format: M05/M08 (negative) or 25/18
            import re
            temp_match = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
            if temp_match:
                t = temp_match.group(1)
                temp_c = -int(t[1:]) if t.startswith('M') else int(t)
        if temp_c is not None:
            logger.info("[NOAA] METAR %s: current temp = %.1f°C", icao, float(temp_c))
            return float(temp_c)
        return None
    except Exception as e:
        logger.warning("[NOAA] METAR fetch failed for %s: %s", icao, e)
        return None


class ForecastTemp(float):
    """Float wrapper to store the oracle source."""
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

from market_discovery_internal.database_manager import db

# ---------------------------------------------------------------------------
# [ENSEMBLE] Multi-Model Ensemble Forecast (GFS 31 + ECMWF 51 = 82 members)
# ---------------------------------------------------------------------------

def _parse_ensemble_members(daily: dict) -> list[float]:
    """Extract ensemble member values from Open-Meteo daily response."""
    members = []
    for key, values in daily.items():
        if key.startswith("temperature_2m_max") and "member" in key:
            if values and values[0] is not None:
                members.append(float(values[0]))
    # Fallback: check if data comes as a list directly
    if not members:
        temp_data = daily.get("temperature_2m_max", [])
        if isinstance(temp_data, list) and len(temp_data) > 1:
            members = [float(t) for t in temp_data if t is not None]
    return members


def fetch_ensemble_forecast(city: str, date: str, lat: float, lon: float,
                            threshold_c: float, direction: str = "above") -> Optional[dict]:
    """
    Fetch multi-model ensemble forecast and compute ensemble probability.

    Fetches from all models in ENSEMBLE_MODELS (default: GFS 31 + ECMWF 51 = 82 members).
    Each model is fetched independently; if one fails, the other still contributes.

    Returns dict with:
        - ensemble_prob: float (0-1) — fraction of members predicting above/below threshold
        - ensemble_mean: float — mean of all member predictions
        - ensemble_spread: float — std dev of member predictions
        - member_count: int — number of valid members
        - source: str — which models contributed
    Or None on failure.
    """
    if not ENSEMBLE_ENABLED:
        return None

    from market_discovery_internal.config import ENSEMBLE_MODELS
    model_list = [m.strip() for m in ENSEMBLE_MODELS.split(",") if m.strip()]
    if not model_list:
        model_list = ["gfs_seamless"]

    # Fetch all models in parallel
    all_members = []
    sources = []

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_one_model(model_name):
        try:
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max",
                "start_date": date,
                "end_date": date,
                "models": model_name,
            }
            data = fetch_with_retry(OPEN_METEO_ENSEMBLE_API, params=params, max_retries=1, timeout=8)
            if not data:
                return model_name, []
            daily = data.get("daily", {})
            members = _parse_ensemble_members(daily)
            return model_name, members
        except Exception as e:
            logger.debug("[ENSEMBLE] %s fetch failed for %s/%s: %s", model_name, city, date, e)
            return model_name, []

    with ThreadPoolExecutor(max_workers=len(model_list)) as executor:
        futures = [executor.submit(_fetch_one_model, m) for m in model_list]
        for f in futures:
            model_name, members = f.result()
            if members:
                all_members.extend(members)
                sources.append(f"{model_name}({len(members)})")

    if len(all_members) < 5:
        logger.warning("[ENSEMBLE] Only %d total members for %s on %s from %s. Skipping.",
                       len(all_members), city, date, model_list)
        return None

    import statistics
    mean_temp = statistics.mean(all_members)
    spread = statistics.stdev(all_members) if len(all_members) > 1 else 0.0

    # Calculate ensemble probability based on direction
    if direction == "above":
        count = sum(1 for m in all_members if m > threshold_c)
        ensemble_prob = count / len(all_members)
    elif direction == "below":
        count = sum(1 for m in all_members if m <= threshold_c)
        ensemble_prob = count / len(all_members)
    elif direction == "exact":
        count = sum(1 for m in all_members if abs(m - threshold_c) <= 1.0)
        ensemble_prob = count / len(all_members)
    else:
        ensemble_prob = 0.5

    source_str = "+".join(sources) if sources else "ensemble"
    logger.info("[ENSEMBLE] %s %s: %d members (%s), mean=%.1f°C, spread=%.1f°C, prob=%.2f (threshold=%.1f°C %s)",
                city, date, len(all_members), source_str, mean_temp, spread, ensemble_prob, threshold_c, direction)

    result = {
        "ensemble_prob": ensemble_prob,
        "ensemble_mean": mean_temp,
        "ensemble_spread": spread,
        "member_count": len(all_members),
        "source": source_str,
    }

    # Cache ensemble result in the database
    try:
        db.save_weather(city, f"ensemble_{date}", max_temp=mean_temp, precip=spread)
    except Exception:
        logger.debug("[ENSEMBLE] Failed to cache ensemble result for %s/%s", city, date)

    return result


# [MODUL DB] LEGACY JSON CACHE REMOVED
# _HIST_CACHE_FILE = "logs/cache/historical_avg.json"
_HIST_CACHE_TTL_DAYS = 30

def _fetch_historical_average(city, date, allow_live=True):
    """Fetch the 10-year historical average max temp for this date/city using a single range lookup.
    Results are stored in the SQLite Data Warehouse to avoid Open-Meteo 429 rate limits.
    
    If allow_live=False (Default for trading), it will ONLY return cached data or None.
    """
    coords = TARGET_CITIES.get(city)
    if not coords or not date:
        return None

    month_day = date[5:]  # e.g. "04-17"
    
    # 1. Check SQLite Warehouse first
    entry = db.get_weather(city, month_day)

    if entry:
        age_days = (time.time() - entry.get("ts", 0)) / 86400
        if age_days < _HIST_CACHE_TTL_DAYS:
            return entry.get("max_temp")

    if not allow_live:
        return entry.get("max_temp") if entry else None

    # Fallback to single fetch if bulk is not used or fails
    try:
        results = _fetch_bulk_historical_weather([city], date)
        return results.get(city)
    except Exception as e:
        logger.warning("[MODUL-K] Historical fetch fallback error for %s: %s", city, e)
        if entry:
            return entry.get("max_temp")
        return None

def _fetch_bulk_historical_weather(cities, date):
    """[MODUL U] Aggregated fetch for multiple cities in one API call.
    Reduces 429 risk by collapsing 31 requests into 1.
    """
    if not cities or not date:
        return {}

    # Validate date format is YYYY-MM-DD before splitting
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return {}

    month_day = date[5:]
    year = int(date.split("-")[0])
    start_date = f"{year-10}-{month_day}"
    end_date = f"{year-1}-{month_day}"

    lats = []
    lons = []
    valid_cities = []
    for city in cities:
        coords = TARGET_CITIES.get(city)
        if coords:
            lats.append(str(coords["lat"]))
            lons.append(str(coords["lon"]))
            valid_cities.append(city)

    if not valid_cities:
        return {}

    params = {
        "latitude": ",".join(lats),
        "longitude": ",".join(lons),
        "start_date": start_date,
        "end_date": end_date,
        "daily": ["temperature_2m_max", "precipitation_sum"],
        "timezone": "auto"
    }

    try:
        # Use a more conservative retry for bulk
        res = fetch_with_retry(OPEN_METEO_HISTORICAL_API, params=params, max_retries=3)
        if not res:
            return {}

        results = {}
        # Open-Meteo returns a list of daily dicts or a dict with lists if multiple locations
        data_list = res if isinstance(res, list) else [res]
        
        for i, data in enumerate(data_list):
            city = valid_cities[i]
            daily = data.get("daily", {})
            times = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            precips = daily.get("precipitation_sum", [])

            matches_temp = [t for ts, t in zip(times, temps) if ts.endswith(month_day) and t is not None]
            matches_precip = [p for ts, p in zip(times, precips) if ts.endswith(month_day) and p is not None]
            
            avg_temp = round(sum(matches_temp) / len(matches_temp), 1) if matches_temp else None
            avg_precip = round(sum(matches_precip) / len(matches_precip), 2) if matches_precip else None

            if avg_temp is not None:
                db.save_weather(city, month_day, max_temp=avg_temp, precip=avg_precip)
                results[city] = avg_temp
        
        return results
    except Exception as e:
        logger.error("[MODUL-U] Bulk historical fetch error: %s", e)
        return {}

def _log_anomaly(city, date, forecast, historical, source="anomaly"):
    """Log rejected anomalous or non-consensus forecasts for transparency."""
    os.makedirs("logs", exist_ok=True)
    with open(ANOMALY_LOG_FILE, "a", encoding="utf-8") as f:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{ts}] REJECTED {city} ({date}): Forecast={forecast}C, Reference={historical}C, Type={source}\n")

def fetch_forecast(city: str, date: str, icao_override: Optional[str] = None) -> Optional[ForecastTemp]:
    """Fetch the daily max temperature forecast from Open-Meteo and wttr.in.
    
    Now uses the SQLite Data Warehouse 'Discovery Cache' for persistent, high-speed lookups.
    """
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None

    # [MODUL DB] Check Discovery Cache first
    cached = db.get_cached_forecast(city, date)
    if cached:
        # Re-fetch historical avg for the full ForecastTemp object
        hist_avg = _fetch_historical_average(city, date)
        _src = cached['source']
        if not _src.endswith(" (cache)"):
            _src += " (cache)"
        ft = ForecastTemp(cached['forecast_temp'], _src)
        ft.historical_avg = hist_avg
        ft.noaa_current = None # Snapshot METAR not stored in discovery cache
        return ft

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
                logger.debug("Open-Meteo hourly fetch failed for %s/%s", city, date)
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
                logger.debug("Open-Meteo daily fetch failed for %s/%s", city, date)
        return None

    def _fetch_wttr():
        # Query by ICAO code so wttr.in returns data for the exact airport station,
        # matching the source Polymarket uses for market resolution.
        icao = icao_override or coords.get("icao")
        query = icao if icao else urllib.parse.quote(city)
        try:
            url = f"https://wttr.in/{query}?format=j1"
            data = fetch_with_retry(url)
            for w in data.get("weather", []):
                if w.get("date") == date:
                    raw_val = w.get("maxtempC")
                    if raw_val is None:
                        return None
                    return float(raw_val)
        except Exception:
            logger.debug("wttr.in fetch failed for %s/%s", city, date)
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
            logger.debug("NOAA fetch failed for %s/%s", city, date)
        return None

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_om = executor.submit(_fetch_open_meteo)
        f_wt = executor.submit(_fetch_wttr)
        f_noaa = executor.submit(_fetch_noaa)
        
        try:
            t_om = f_om.result()
        except Exception as exc:
            logger.warning("Open-Meteo fetch failed for %s/%s: %s", city, date, exc)
            t_om = None
        try:
            t_wt = f_wt.result()
        except Exception as exc:
            logger.warning("wttr.in fetch failed for %s/%s: %s", city, date, exc)
            t_wt = None
        try:
            t_noaa = f_noaa.result()
        except Exception as exc:
            logger.warning("NOAA fetch failed for %s/%s: %s", city, date, exc)
            t_noaa = None

    base_avg = None
    source = None

    if t_om is not None and t_wt is not None:
        # [MODUL K] Anomaly Check (Strict Consensus Gate)
        if abs(t_om - t_wt) > CONSENSUS_MAX_ERROR_C:
            logger.warning(
                "[CONSENSUS REJECT] %s/%s: Open-Meteo=%.1f°C, wttr.in=%.1f°C, delta=%.1f°C > max %.1f°C",
                city, date, t_om, t_wt, abs(t_om - t_wt), CONSENSUS_MAX_ERROR_C
            )
            _log_anomaly(city, date, t_om, t_wt, "consensus_disagreement")
            return None # Major disagreement between weather sources, reject forecast to be safe.
        
        # Weighted blend: Open-Meteo gets more weight (more reliable API).
        # POINT_FORECAST_WEIGHT=0.35, WTRIN_WEIGHT=0.20 → normalized 64/36.
        _w_total = POINT_FORECAST_WEIGHT + WTRIN_WEIGHT
        if _w_total > 0:
            base_avg = round((POINT_FORECAST_WEIGHT * t_om + WTRIN_WEIGHT * t_wt) / _w_total, 1)
        else:
            base_avg = round((t_om + t_wt) / 2.0, 1)  # Fallback to 50/50
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
        # Resilience: Trading engine uses Cache-Only mode to avoid 429s
        hist_avg = _fetch_historical_average(city, date, allow_live=False)
        if hist_avg is not None:
            if abs(base_avg - hist_avg) > HISTORICAL_DEVIATION_C:
                _log_anomaly(city, date, base_avg, hist_avg, "historical_anomaly")
                return None

        # [MODUL B] Strict Consensus Check (Ground Truth METAR)
        # If we have ground truth (NOAA), it MUST be close to our forecast.
        # Note: Latest METAR is a snapshot, forecast is max temp.
        # We ONLY enforce consensus mismatch if it's already hotter than predicted (High Confidence Error)
        # OR if we are in the peak heat window (12-18 local) and the temp is way off.
        if t_noaa is not None:
            # [MODUL A] Precision Timezone Sync: Use IANA timezone from config
            target_tz = (coords or {}).get("tz", "UTC")
            
            try:
                import zoneinfo
            except ImportError:
                from backports import zoneinfo # Fallback for old python

            try:
                # Use zoneinfo for high-accuracy local hour calculation
                tz = zoneinfo.ZoneInfo(target_tz)
                now_local = datetime.now(timezone.utc).astimezone(tz)
                local_hour = now_local.hour
                is_peak_heat = (12 <= local_hour <= 19)
            except Exception as e:
                # Emergency Fallback: If zoneinfo fails, use the old lon/15 heuristic
                # but log it as a non-fatal warning.
                lon = (coords or {}).get("lon", 0.0)
                utc_offset_hours = round(lon / 15.0)
                utc_offset_hours = max(-12, min(14, utc_offset_hours))
                local_hour = (datetime.now(timezone.utc).hour + utc_offset_hours) % 24
                is_peak_heat = (12 <= local_hour <= 19)
                logger.warning("[WARNING] Timezone precision fallback for %s: %s", city, e)

            error_margin = abs(base_avg - t_noaa)

            # Scenario A: Current temp is already higher than our predicted MAX (Definitive error)
            if t_noaa > (base_avg + 1.0):
                _log_anomaly(city, date, base_avg, t_noaa, "prediction_exceeded_by_ground_truth")
                return None

            # Scenario B: REMOVED — comparing daytime max forecast vs evening METAR always
            # produces false positives (METAR naturally lower than day's max in evening).
            # Only Scenario A (current temp exceeds forecast max) is a reliable error signal.

        ft = ForecastTemp(base_avg, source)
        ft.noaa_current = t_noaa
        ft.historical_avg = hist_avg

        # [MODUL DB] Save verified result to Warehouse Cache
        db.save_cached_forecast(city, date, base_avg, source)
        
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


# Negative cache: stores (None, expiry_timestamp) for failed fetches to avoid repeated API calls.
_NEGATIVE_CACHE = {}
_NEGATIVE_CACHE_LOCK = threading.Lock()
_NEGATIVE_CACHE_TTL_SECONDS = 300  # 5 minutes

def fetch_forecast_with_cache(city: str, date: str, cache: dict[str, Any], stats: Optional[dict[str, int]] = None, *, fetch_forecast_fn: Any, icao_override: Optional[str] = None) -> Optional[ForecastTemp]:
    """Fetch forecast with per-cycle cache for successful city/date/icao lookups."""
    if not isinstance(cache, dict):
        return fetch_forecast_fn(city, date, icao_override=icao_override)

    cache_key = (city, date, icao_override)
    cached = cache.get(cache_key)
    if cached is not None:
        if isinstance(stats, dict):
            stats["hits"] = int(stats.get("hits", 0)) + 1
        return cached

    # Check negative cache to avoid repeated API calls for failing cities
    with _NEGATIVE_CACHE_LOCK:
        neg_entry = _NEGATIVE_CACHE.get(cache_key)
        if neg_entry is not None:
            _neg_val, _neg_expiry = neg_entry
            if time.time() < _neg_expiry:
                if isinstance(stats, dict):
                    stats["hits"] = int(stats.get("hits", 0)) + 1
                return None
            else:
                # Expired negative cache entry — remove it
                del _NEGATIVE_CACHE[cache_key]

    if isinstance(stats, dict):
        stats["misses"] = int(stats.get("misses", 0)) + 1

    forecast_temp = fetch_forecast_fn(city, date, icao_override=icao_override)
    if forecast_temp is not None:
        cache[cache_key] = forecast_temp
    else:
        # Negative caching: cache None results for a short TTL to prevent repeated API calls
        with _NEGATIVE_CACHE_LOCK:
            _NEGATIVE_CACHE[cache_key] = (None, time.time() + _NEGATIVE_CACHE_TTL_SECONDS)
    return forecast_temp


def prefetch_forecasts(cache_keys: list[tuple[str, str, Optional[str]]], cache: dict[str, Any], min_keys: int = 0, max_workers: int = 1, *, fetch_forecast_fn: Any) -> dict[str, Any]:
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
    if eligible < min_required:
        return {
            "eligible": eligible,
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "workers": 0,
            "skipped": True,
        }

    # Group keys by date to perform bulk fetches
    by_date = {}
    for city, date, icao in unique_keys:
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(city)

    stats = {
        "eligible": eligible,
        "attempted": eligible,
        "successful": 0,
        "failed": 0,
        "workers": 1,
        "skipped": False,
    }

    for date, cities in by_date.items():
        try:
            # [MODUL U] Bulk Fetch: 1 hit per date instead of N hits
            logger.info("[MODUL-K] Prefetching %d cities for %s (Bulk Mode)...", len(cities), date)
            results = _fetch_bulk_forecasts(cities, date)
            for city in cities:
                key = (city, date, None) # Bulk doesn't handle ICAO yet
                if results.get(city) is not None:
                    cache[key] = ForecastTemp(results[city], "open-meteo (bulk)")
                    stats["successful"] += 1
                else:
                    # Optional: Fallback to individual fetch if bulk failed for one?
                    # For now, we trust bulk or fail.
                    stats["failed"] += 1
        except Exception as e:
            logger.error("[MODUL-U] Prefetch bulk error for %s: %s", date, e)
            stats["failed"] += len(cities)

    return stats


def _fetch_bulk_forecasts(cities, date):
    """[MODUL U] Aggregated forecast fetch for multiple cities.
    Fetches the daily max temperature forecast for multiple coordinates in 1 request.
    """
    if not cities or not date:
        return {}

    lats = []
    lons = []
    valid_cities = []
    for city in cities:
        coords = TARGET_CITIES.get(city)
        if coords:
            lats.append(str(coords["lat"]))
            lons.append(str(coords["lon"]))
            valid_cities.append(city)

    if not valid_cities:
        return {}

    params = {
        "latitude": ",".join(lats),
        "longitude": ",".join(lons),
        "daily": "temperature_2m_max",
        "timezone": "auto",
        "forecast_days": 10  # Cover the full warming window
    }

    try:
        res = fetch_with_retry(OPEN_METEO_API, params=params, max_retries=3)
        if not res:
            return {}

        results = {}
        data_list = res if isinstance(res, list) else [res]
        
        for i, data in enumerate(data_list):
            city = valid_cities[i]
            daily = data.get("daily", {})
            times = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            
            if date in times:
                val = temps[times.index(date)]
                if val is not None:
                    # Save to Warehouse Cache
                    db.save_cached_forecast(city, date, val, "open-meteo (bulk)")
                    results[city] = val
        
        return results
    except Exception as e:
        logger.error("[MODUL-U] Bulk forecast fetch error: %s", e)
        return {}


def forecast_still_valid(
    position: dict[str, Any],
    current_yes_price: float,
    hours_until_resolve: Optional[float],
    forecast_cache: Optional[dict[str, Any]] = None,
    forecast_cache_stats: Optional[dict[str, int]] = None,
    *,
    fetch_forecast_with_cache_fn: Any,
    build_weather_evidence_fn: Any,
    is_weather_evidence_valid_fn: Any,
    position_to_market_fn: Any,
    calculate_edge_fn: Any,
) -> float:
    """Evaluate whether forecast still supports the open position thesis.

    Returns a float 0.0-1.0 representing forecast validity strength:
      - 0.0: forecast invalid or model_prob < 0.50
      - 0.0-1.0: graduated scale for model_prob 0.50-0.70
      - 1.0: model_prob >= 0.70 (fully valid)
    All existing consumers use truthy checks (if not x:), so 0.0 is falsy
    and any positive float is truthy — backward compatible.
    """
    forecast_temp = fetch_forecast_with_cache_fn(
        position["city"],
        position["date"],
        forecast_cache,
        stats=forecast_cache_stats,
        icao_override=position.get("icao_code")
    )
    evidence = build_weather_evidence_fn(position["city"], position["date"], forecast_temp)

    if not is_weather_evidence_valid_fn(evidence):
        return 0.0

    market_view = position_to_market_fn(position, current_yes_price, hours_until_resolve)
    edge_result = calculate_edge_fn(market_view, forecast_temp)
    if not edge_result:
        return 0.0
    prob = float(edge_result.get("model_prob", 0.0))
    if prob >= 0.70:
        return 1.0
    if prob < 0.50:
        return 0.0
    # Graduated: linear scale 0.50-0.70 → 0.0-1.0
    return round((prob - 0.50) / 0.20, 4)

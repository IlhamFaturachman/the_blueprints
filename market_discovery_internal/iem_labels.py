# market_discovery_internal/iem_labels.py
"""IEM (Iowa Environmental Mesonet) daily max temperature fetcher.

Provides settlement-grade labels for EMOS training and lag study verification.
IEM daily.py max_temp_f reproduces Polymarket settlement values.
"""
import logging
from datetime import datetime, timedelta, timezone

import requests

from market_discovery_internal.config import IEM_API_BASE

logger = logging.getLogger(__name__)

_IEM_CACHE = {}
_IEM_CACHE_TTL = 3600


def _f_to_c(f):
    return round((f - 32.0) * 5.0 / 9.0, 2)


def fetch_iem_daily_max(icao, date_str):
    """Fetch daily max temperature from IEM for a station and date. Returns Celsius or None."""
    if not icao or not date_str:
        return None
    cache_key = (icao, date_str)
    cached = _IEM_CACHE.get(cache_key)
    if cached is not None:
        return cached if cached != -999 else None
    try:
        url = f"{IEM_API_BASE}/daily.py"
        params = {"station": icao, "day": date_str, "network": "ASOS"}
        resp = requests.get(url, params=params, timeout=10)
        if not resp or resp.status_code != 200:
            _IEM_CACHE[cache_key] = -999
            return None
        data = resp.json()
        records = data.get("data", [])
        if not records:
            _IEM_CACHE[cache_key] = -999
            return None
        max_temp_f = records[0].get("max_temp_f")
        if max_temp_f is None:
            _IEM_CACHE[cache_key] = -999
            return None
        try:
            max_temp_f = float(max_temp_f)
        except (ValueError, TypeError):
            _IEM_CACHE[cache_key] = -999
            return None
        result = _f_to_c(max_temp_f)
        logger.info("[IEM] %s %s: %.1fF = %.1fC", icao, date_str, max_temp_f, result)
        _IEM_CACHE[cache_key] = result
        return result
    except Exception as e:
        logger.warning("[IEM] fetch failed for %s %s: %s", icao, date_str, e)
        _IEM_CACHE[cache_key] = -999
        return None


def backfill_iem_labels(icao_list, days=730):
    """Backfill IEM daily max labels. Returns dict[icao -> list of {date, max_temp_c}]."""
    from market_discovery_internal.database_manager import db
    results = {}
    today = datetime.now(timezone.utc).date()
    for icao in icao_list:
        records = []
        for i in range(days):
            d = today - timedelta(days=i)
            date_str = d.isoformat()
            month_day = date_str[5:]
            existing = db.get_weather(icao.lower(), month_day)
            if existing and existing.get("max_temp") is not None:
                records.append({"date": date_str, "max_temp_c": existing["max_temp"]})
                continue
            temp_c = fetch_iem_daily_max(icao, date_str)
            if temp_c is not None:
                db.save_weather(icao.lower(), month_day, max_temp=temp_c)
                records.append({"date": date_str, "max_temp_c": temp_c})
        results[icao] = records
        logger.info("[IEM] Backfilled %s: %d records", icao, len(records))
    return results

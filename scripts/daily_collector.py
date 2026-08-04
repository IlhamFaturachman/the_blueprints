#!/usr/bin/env python3
"""Daily ECMWF + IEM collector daemon.

Accumulates REAL forecast features (ECMWF 51-member ensemble) and matched
observation labels (IEM daily max) every day. After 60 days of accumulation,
train_emos.py can fit per-station bias correction from matched pairs.

This is the ONLY honest path to EMOS — there is no free historical forecast archive.
ECMWF opendata only serves the latest cycle; S3 archive needs MARS credentials.

Runs as systemd service, once per day at 06:00 UTC (after ECMWF 00z run completes).

Daily cycle:
1. For each of 31 target cities:
   a. Fetch ECMWF 51-member ensemble forecast for TOMORROW
   b. Save ensemble members to ecmwf_ensemble_raw (city, tomorrow_date, member_id, temp_c)
2. For each city, fetch IEM daily max for YESTERDAY (resolved observation)
   a. Save to weather_archive (icao, month_day, max_temp)

After 60 days, run: python scripts/train_emos.py
"""
import sys
import os
import logging
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import TARGET_CITIES
from market_discovery_internal.database_manager import db
from market_discovery_internal.iem_labels import fetch_iem_daily_max
from market_discovery_internal.utils import fetch_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Open-Meteo Ensemble API for GFS 31-member (free, has 3-day lookback)
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"


def collect_ecmwf_forecasts():
    """Fetch ECMWF ensemble forecasts for all 31 cities for tomorrow.

    Tries ECMWF opendata first (51 members, GRIB2). Falls back to Open-Meteo
    GFS ensemble (31 members) if ECMWF packages unavailable.
    """
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    logger.info("[COLLECT] Fetching ensemble forecasts for %s", tomorrow)

    # Try ECMWF direct first
    try:
        from market_discovery_internal.ecmwf_fetch import ECMWF_AVAILABLE, GRIB_AVAILABLE
        if ECMWF_AVAILABLE and GRIB_AVAILABLE:
            from market_discovery_internal.ecmwf_fetch import fetch_ecmwf_ensemble_forecast
            for city, info in TARGET_CITIES.items():
                if not info.get("icao"):
                    continue
                result = fetch_ecmwf_ensemble_forecast(city, tomorrow, info["lat"], info["lon"])
                if result:
                    logger.info("[ECMWF] %s: %d members, mean=%.1fC",
                                city, result["member_count"], result["mean"])
                else:
                    # Fallback to Open-Meteo GFS ensemble
                    _collect_openmeteo_gfs(city, info, tomorrow)
            return
    except Exception as e:
        logger.warning("[ECMWF] Direct fetch failed: %s — falling back to GFS", e)

    # Fallback: Open-Meteo GFS ensemble (31 members)
    for city, info in TARGET_CITIES.items():
        if not info.get("icao"):
            continue
        _collect_openmeteo_gfs(city, info, tomorrow)


def _collect_openmeteo_gfs(city, info, date_str):
    """Fetch GFS 31-member ensemble from Open-Meteo and save to DB."""
    try:
        params = {
            "latitude": info["lat"],
            "longitude": info["lon"],
            "daily": "temperature_2m_max",
            "start_date": date_str,
            "end_date": date_str,
            "models": "gfs_seamless",
        }
        data = fetch_with_retry(ENSEMBLE_API, params=params, timeout=15)
        if not data:
            logger.warning("[GFS] %s: no data", city)
            return

        daily = data.get("daily", {})
        member_keys = sorted([k for k in daily.keys() if "member" in k])

        members = []
        for i, mk in enumerate(member_keys):
            vals = daily.get(mk, [])
            if vals and vals[0] is not None:
                members.append({"member_id": i, "temp_c": float(vals[0])})

        # Also add control forecast
        control = daily.get("temperature_2m_max", [])
        if control and control[0] is not None:
            members.append({"member_id": 999, "temp_c": float(control[0])})

        if len(members) >= 5:
            db.save_ecmwf_ensemble(city, date_str, members)
            mean_t = sum(m["temp_c"] for m in members) / len(members)
            logger.info("[GFS] %s %s: %d members, mean=%.1fC", city, date_str, len(members), mean_t)
        else:
            logger.warning("[GFS] %s: only %d valid members", city, len(members))

    except Exception as e:
        logger.warning("[GFS] %s: fetch failed: %s", city, e)


def collect_iem_labels():
    """Fetch IEM daily max observation for YESTERDAY (resolved day)."""
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    logger.info("[COLLECT] Fetching IEM labels for %s", yesterday)

    for city, info in TARGET_CITIES.items():
        icao = info.get("icao")
        if not icao:
            continue

        month_day = yesterday[5:]
        # Skip if already in DB
        existing = db.get_weather(icao.lower(), month_day)
        if existing and existing.get("max_temp") is not None:
            logger.debug("[IEM] %s %s: already cached", icao, yesterday)
            continue

        obs = fetch_iem_daily_max(icao, yesterday)
        if obs is not None:
            db.save_weather(icao.lower(), month_day, max_temp=obs)
            logger.info("[IEM] %s %s: %.1fC", icao, yesterday, obs)
        else:
            logger.warning("[IEM] %s %s: no data", icao, yesterday)


def main():
    """Run one collection cycle. Called by systemd timer daily."""
    logger.info("=== Daily collection started ===")

    # Step 1: Collect tomorrow's ensemble forecast
    collect_ecmwf_forecasts()

    # Step 2: Collect yesterday's IEM observation
    collect_iem_labels()

    # Step 3: Report DB stats
    try:
        from market_discovery_internal.database_manager import db
        conn = db._get_conn()
        row = conn.execute("SELECT COUNT(DISTINCT city || date) as days FROM ecmwf_ensemble_raw").fetchone()
        logger.info("[STATS] ECMWF ensemble days collected: %d", row["days"] if row else 0)

        row2 = conn.execute("SELECT COUNT(*) as cnt FROM weather_archive WHERE max_temp IS NOT NULL").fetchone()
        logger.info("[STATS] IEM observation labels: %d", row2["cnt"] if row2 else 0)
    except Exception as e:
        logger.warning("[STATS] Failed to query stats: %s", e)

    logger.info("=== Daily collection complete ===")

    # Check if we have enough data to train
    try:
        conn = db._get_conn()
        row = conn.execute("SELECT COUNT(DISTINCT city || date) as days FROM ecmwf_ensemble_raw").fetchone()
        days_collected = row["days"] if row else 0
        if days_collected >= 60:
            logger.info("[READY] 60+ days of ensemble data collected! Run: python scripts/train_emos.py")
        else:
            logger.info("[WAIT] %d/60 days collected — need %d more days before EMOS training",
                        days_collected, 60 - days_collected)
    except Exception:
        pass


if __name__ == "__main__":
    main()

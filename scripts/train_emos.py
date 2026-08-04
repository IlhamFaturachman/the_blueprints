#!/usr/bin/env python3
"""Train EMOS per-station bias correction model from IEM labels + ECMWF ensemble data.

This script:
1. Loads IEM daily max observations (settlement labels) from weather_archive
2. Loads ECMWF ensemble stats from ecmwf_ensemble_raw
3. Fits EMOS coefficients (a, b, c, d) per station via CRPS minimization
4. Caches the model in weather_archive with month_day='emos_model' as JSON

The cached model is loaded by pricing.py at runtime to apply bias correction.

Usage: python scripts/train_emos.py
"""
import sys
import os
import json
import logging
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import TARGET_CITIES, EMOS_MIN_TRAINING_SAMPLES
from market_discovery_internal.database_manager import db
from market_discovery_internal.iem_labels import fetch_iem_daily_max
from market_discovery_internal.emos import fit_emos
from market_discovery_internal.ecmwf_fetch import fetch_ecmwf_ensemble_forecast, ECMWF_AVAILABLE, GRIB_AVAILABLE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_training_data(icao, city, days=60):
    """Load (ensemble_mean, ensemble_std, observation) triples from DB for last N days.

    For each day:
    - Get ECMWF ensemble stats (mean, std) from ecmwf_ensemble_raw
    - Get IEM daily max observation from weather_archive
    - If both available, add to training set
    """
    today = datetime.now(timezone.utc).date()
    training_means = []
    training_stds = []
    training_obs = []

    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        month_day = date_str[5:]

        # Get ECMWF ensemble stats
        stats = db.get_ecmwf_ensemble_stats(city, date_str)
        if not stats or stats.get("count", 0) < 5 or stats.get("mean") is None:
            continue

        # Get IEM observation label
        obs_row = db.get_weather(icao.lower(), month_day)
        if obs_row and obs_row.get("max_temp") is not None:
            obs = float(obs_row["max_temp"])
        else:
            # Try live IEM fetch for recent days
            if i < 7:  # Only fetch live for last 7 days
                obs = fetch_iem_daily_max(icao, date_str)
                if obs is not None:
                    db.save_weather(icao.lower(), month_day, max_temp=obs)
            else:
                continue

        if obs is not None:
            training_means.append(float(stats["mean"]))
            training_stds.append(float(stats["std"] or 1.0))
            training_obs.append(obs)

    return training_means, training_stds, training_obs


def save_emos_model(icao, model):
    """Cache EMOS model coefficients in weather_archive with month_day='emos_model'."""
    # Store as JSON in the precipitation column (REAL) — use max_temp as a flag
    # Actually, weather_archive has: city, month_day, max_temp, min_temp, precipitation, ts
    # We'll store the model JSON in the max_temp column as a hack —
    # better: store as precipitation (it's REAL, can hold a JSON hash)
    # Best: use month_day='emos_model_<icao>' and store a, b, c, d in max_temp/min_temp/precipitation

    # Store model as JSON string in precipitation column (REAL won't work for JSON)
    # Actually let's use a simpler approach: store 4 floats in max_temp(a), min_temp(b),
    # and use precipitation for c, ts for d timestamp
    # But max_temp/min_temp are REAL — we can store floats directly

    # Use a composite key: city=icao_lower, month_day='emos_model'
    # Store: max_temp = a, min_temp = b, precipitation = c
    # d is tricky — store as ts (but ts is REAL timestamp)
    # Better: store model as JSON in max_temp as a string... but max_temp is REAL

    # Simplest: create a separate emos_models table, but that needs schema migration
    # For now: store a, b as max_temp/min_temp, c, d as precipitation/ts
    db.save_weather(
        icao.lower(),
        "emos_model",
        max_temp=model["a"],  # intercept
        min_temp=model["b"],  # slope
        precip=model["c"],    # sigma intercept
    )
    # d stored separately — we'll use a second row
    db.save_weather(
        icao.lower(),
        "emos_model_d",
        max_temp=model["d"],  # sigma slope
    )
    logger.info("[EMOS-TRAIN] Cached model for %s: a=%.2f b=%.2f c=%.2f d=%.2f",
                icao, model["a"], model["b"], model["c"], model["d"])


def load_emos_model(icao):
    """Load cached EMOS model from DB. Returns dict or None."""
    row = db.get_weather(icao.lower(), "emos_model")
    if not row or row.get("max_temp") is None:
        return None
    a = float(row["max_temp"])
    b = float(row["min_temp"])
    c = float(row["precip"])
    row_d = db.get_weather(icao.lower(), "emos_model_d")
    d = float(row_d["max_temp"]) if row_d and row_d.get("max_temp") is not None else 1.0
    return {"a": a, "b": b, "c": c, "d": d}


def train_all_stations():
    """Train EMOS model for all stations that have sufficient data."""
    stations = [(v["icao"], city) for city, v in TARGET_CITIES.items() if v.get("icao")]
    trained = 0
    skipped = 0

    for icao, city in stations:
        logger.info("[EMOS-TRAIN] Training %s (%s)...", icao, city)

        means, stds, obs = load_training_data(icao, city, days=60)

        if len(obs) < EMOS_MIN_TRAINING_SAMPLES:
            logger.info("[EMOS-TRAIN] %s: skipping — only %d training samples (need %d)",
                        icao, len(obs), EMOS_MIN_TRAINING_SAMPLES)
            skipped += 1
            continue

        model = fit_emos(means, stds, obs, min_samples=EMOS_MIN_TRAINING_SAMPLES)
        if model is None:
            logger.warning("[EMOS-TRAIN] %s: fit_emos returned None", icao)
            skipped += 1
            continue

        save_emos_model(icao, model)
        trained += 1

    logger.info("[EMOS-TRAIN] Complete: %d trained, %d skipped", trained, skipped)
    return trained, skipped


def main():
    logger.info("=== EMOS Training Pipeline ===")
    logger.info("ECMWF available: %s, GRIB available: %s", ECMWF_AVAILABLE, GRIB_AVAILABLE)

    # Step 1: Backfill recent ECMWF ensemble data (last 60 days) if not in DB
    logger.info("Step 1: Backfill ECMWF ensemble for last 14 days (recent data for training)")
    if ECMWF_AVAILABLE and GRIB_AVAILABLE:
        today = datetime.now(timezone.utc).date()
        for city, info in TARGET_CITIES.items():
            icao = info.get("icao")
            if not icao:
                continue
            for i in range(14):
                d = today - timedelta(days=i)
                date_str = d.isoformat()
                stats = db.get_ecmwf_ensemble_stats(city, date_str)
                if not stats or stats.get("count", 0) < 5:
                    logger.info("[ECMWF-BACKFILL] %s %s: fetching...", city, date_str)
                    fetch_ecmwf_ensemble_forecast(city, date_str, info["lat"], info["lon"])
    else:
        logger.warning("ECMWF/GRIB not available — skipping ensemble backfill")

    # Step 2: Train models
    logger.info("Step 2: Train EMOS models from IEM + ECMWF data")
    trained, skipped = train_all_stations()

    # Step 3: Report
    logger.info("=== Done: %d models trained, %d skipped ===", trained, skipped)
    if skipped > 0:
        logger.info("Skipped stations need more data — run IEM backfill + wait for ECMWF data accumulation")


if __name__ == "__main__":
    main()

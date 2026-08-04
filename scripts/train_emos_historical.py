#!/usr/bin/env python3
"""Fetch historical GFS forecasts from Open-Meteo Historical Forecast API,
match with IEM observations already in DB, and train EMOS per-station.

This bypasses the 60-day daily collector wait by using free historical
forecast archive data (~2021 onwards) matched with IEM backfill observations.
"""
import json
import urllib.request
import sqlite3
import time
import math
import logging
from datetime import datetime, timedelta
from market_discovery_internal.config import TARGET_CITIES
from market_discovery_internal.database_manager import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

HIST_FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FETCH_DAYS = 120  # How far back to fetch historical forecasts
RATE_LIMIT_SEC = 0.5  # Delay between API calls to avoid rate limiting


def fetch_historical_forecasts(city, lat, lon, start_str, end_str):
    """Fetch GFS historical daily max temperature forecasts from Open-Meteo."""
    url = (
        f"{HIST_FORECAST_API}?latitude={lat}&longitude={lon}"
        f"&models=gfs_global&daily=temperature_2m_max"
        f"&start_date={start_str}&end_date={end_str}&timezone=UTC"
    )
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        return list(zip(dates, temps))
    except Exception as e:
        logger.warning("[HIST-FETCH] %s failed: %s", city, e)
        return []


def fetch_ecmwf_historical(city, lat, lon, start_str, end_str):
    """Fetch ECMWF historical daily max temperature forecasts from Open-Meteo."""
    url = (
        f"{HIST_FORECAST_API}?latitude={lat}&longitude={lon}"
        f"&models=ecmwf_ifs025&daily=temperature_2m_max"
        f"&start_date={start_str}&end_date={end_str}&timezone=UTC"
    )
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        return list(zip(dates, temps))
    except Exception as e:
        logger.warning("[HIST-FETCH-ECMWF] %s failed: %s", city, e)
        return []


def match_with_observations(conn, city, forecasts):
    """Match forecast dates with IEM observations in DB.
    
    IEM stores month_day as MM-DD (no year), so we match on MM-DD.
    Returns list of (date, forecast_temp, observed_temp) tuples.
    """
    pairs = []
    for date_str, fc_temp in forecasts:
        if fc_temp is None:
            continue
        # IEM stores month_day as MM-DD
        month_day = date_str[5:]  # Extract MM-DD from YYYY-MM-DD
        row = conn.execute(
            "SELECT max_temp FROM weather_archive WHERE city=? AND month_day=? AND max_temp IS NOT NULL",
            (city, month_day),
        ).fetchone()
        if row and row["max_temp"] is not None:
            pairs.append((date_str, float(fc_temp), float(row["max_temp"])))
    return pairs


def fit_emos_gaussian(pairs):
    """Fit EMOS Gaussian model: mu = a + b*ensemble_mean, sigma = sqrt(c + d*var).
    
    For small samples (n < 20), uses simple bias correction:
      a = mean_obs - mean_fc (constant offset)
      b = 1.0 (trust forecast slope, just shift)
    For larger samples (n >= 20), uses linear regression for (a, b).
    
    Sigma is always calibrated from residual variance.
    """
    if len(pairs) < 5:
        return None
    
    n = len(pairs)
    fc_temps = [p[1] for p in pairs]
    obs_temps = [p[2] for p in pairs]
    
    mean_fc = sum(fc_temps) / n
    mean_obs = sum(obs_temps) / n
    mean_bias = mean_fc - mean_obs
    
    if n >= 20:
        # Full linear regression: obs = a + b * forecast
        var_fc = sum((f - mean_fc) ** 2 for f in fc_temps) / n
        if var_fc < 0.01:
            b = 1.0
            a = mean_obs - mean_fc
        else:
            cov = sum((fc_temps[i] - mean_fc) * (obs_temps[i] - mean_obs) for i in range(n)) / n
            b = cov / var_fc
            a = mean_obs - b * mean_fc
        # Clamp to reasonable ranges
        a = max(-5.0, min(5.0, a))
        b = max(0.7, min(1.3, b))
    else:
        # Small sample: simple bias correction only
        a = mean_obs - mean_fc  # = -mean_bias
        b = 1.0
    
    # Residual variance for sigma calibration
    residuals = [obs_temps[i] - (a + b * fc_temps[i]) for i in range(n)]
    resid_var = sum(r ** 2 for r in residuals) / max(1, n - 2)
    
    c = max(0.1, resid_var)
    d = 0.0  # Historical API gives deterministic forecast, not ensemble spread
    
    model = {"a": round(a, 4), "b": round(b, 4), "c": round(c, 4), "d": round(d, 4)}
    
    # Compute MSE improvement AFTER applying model (not before clamping)
    raw_mse = sum((fc_temps[i] - obs_temps[i]) ** 2 for i in range(n)) / n
    emos_mse = sum(((a + b * fc_temps[i]) - obs_temps[i]) ** 2 for i in range(n)) / n
    improvement = (raw_mse - emos_mse) / raw_mse * 100 if raw_mse > 0 else 0
    
    return model, {
        "n": n,
        "raw_mse": round(raw_mse, 3),
        "emos_mse": round(emos_mse, 3),
        "improvement_pct": round(improvement, 1),
        "mean_bias": round(mean_bias, 2),
    }


def save_emos_model(icao, model):
    """Save EMOS model to DB (same format as train_emos.py)."""
    db.save_weather(icao.lower(), "emos_model", max_temp=model["a"], min_temp=model["b"], precip=model["c"])
    db.save_weather(icao.lower(), "emos_model_d", max_temp=model["d"])


def main():
    conn = sqlite3.connect("logs/blueprints_master.db")
    conn.row_factory = sqlite3.Row
    
    end = datetime(2026, 8, 2)
    start = end - timedelta(days=FETCH_DAYS)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")
    
    logger.info("[EMOS-TRAIN] Fetching %d days of historical forecasts (%s to %s)",
                FETCH_DAYS, start_str, end_str)
    
    total_pairs = 0
    trained = 0
    skipped = 0
    results = []
    
    for city, info in TARGET_CITIES.items():
        lat = info["lat"]
        lon = info["lon"]
        icao = info.get("icao", "")
        
        if not icao:
            logger.warning("[EMOS-TRAIN] %s: no ICAO, skipping", city)
            skipped += 1
            continue
        
        # Fetch GFS historical forecasts
        gfs_forecasts = fetch_historical_forecasts(city, lat, lon, start_str, end_str)
        time.sleep(RATE_LIMIT_SEC)
        
        # Fetch ECMWF historical forecasts
        ecmwf_forecasts = fetch_ecmwf_historical(city, lat, lon, start_str, end_str)
        time.sleep(RATE_LIMIT_SEC)
        
        # Use GFS as primary (more data), ECMWF as secondary
        # For now, use GFS only — ECMWF historical may have gaps
        forecasts = gfs_forecasts
        
        # Match with IEM observations
        pairs = match_with_observations(conn, city, forecasts)
        
        if len(pairs) < 5:
            logger.warning("[EMOS-TRAIN] %s: only %d matched pairs (need >=5), skipping",
                          city, len(pairs))
            skipped += 1
            continue
        
        # Fit EMOS model
        result = fit_emos_gaussian(pairs)
        if result is None:
            skipped += 1
            continue
        
        model, stats = result
        
        # Save to DB
        save_emos_model(icao, model)
        
        total_pairs += len(pairs)
        trained += 1
        results.append({"city": city, "icao": icao, **stats, "model": model})
        
        logger.info("[EMOS-TRAIN] %s: n=%d bias=%+.2fC raw_mse=%.2f emos_mse=%.2f improvement=%.1f%% a=%.2f b=%.2f c=%.2f",
                     city, stats["n"], stats["mean_bias"],
                     stats["raw_mse"], stats["emos_mse"], stats["improvement_pct"],
                     model["a"], model["b"], model["c"])
    
    # Summary
    logger.info("")
    logger.info("=== EMOS TRAINING SUMMARY ===")
    logger.info("Trained: %d/%d stations", trained, len(TARGET_CITIES))
    logger.info("Skipped: %d", skipped)
    logger.info("Total matched pairs: %d", total_pairs)
    
    if results:
        avg_improvement = sum(r["improvement_pct"] for r in results) / len(results)
        avg_bias = sum(abs(r["mean_bias"]) for r in results) / len(results)
        logger.info("Average MSE improvement: %.1f%%", avg_improvement)
        logger.info("Average |bias|: %.2f°C", avg_bias)
        
        # Show best and worst
        results.sort(key=lambda x: x["improvement_pct"], reverse=True)
        logger.info("")
        logger.info("Best 3:")
        for r in results[:3]:
            logger.info("  %s: %.1f%% improvement (bias=%+.2fC, n=%d)",
                        r["city"], r["improvement_pct"], r["mean_bias"], r["n"])
        logger.info("Worst 3:")
        for r in results[-3:]:
            logger.info("  %s: %.1f%% improvement (bias=%+.2fC, n=%d)",
                        r["city"], r["improvement_pct"], r["mean_bias"], r["n"])
    
    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()

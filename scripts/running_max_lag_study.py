# scripts/running_max_lag_study.py
"""Phase 1: Running-Max Lag Study.

Restructured as a long-running tracker. Two phases:
1. detect_lock_event(): checks if running max is locked, records lock event + price at lock
2. track_price_convergence(): given a lock event, polls prices at wall-clock intervals

The main() loop runs continuously, calling detect_lock_event() for each city each cycle.
Once a lock is detected, it polls prices at real wall-clock intervals (time.sleep between fetches).

Usage: python scripts/running_max_lag_study.py [--duration-hours 168]
Output: logs/running_max_lag_study.jsonl
"""
import json, logging, os, sys, threading, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import (
    TARGET_CITIES, LAG_STUDY_POLL_INTERVAL_SECONDS, LAG_STUDY_OUTPUT_FILE,
    LAG_STUDY_LOCK_THRESHOLD, LAG_STUDY_LOCK_MIN_HOUR_LOCAL,
    GAMMA_API, GAMMA_EVENTS_API,
)
from market_discovery_internal.running_max_tracker import (
    RunningMaxTracker, fetch_metar_24h, determine_winning_bracket,
)
from market_discovery_internal.lock_probability import compute_lock_probability
from market_discovery_internal.pricing import fetch_orderbook_quote
from market_discovery_internal.parsing import parse_market
from market_discovery_internal.utils import fetch_with_retry

logger = logging.getLogger(__name__)

US_CITIES = {
    "new york city": "America/New_York", "chicago": "America/Chicago",
    "miami": "America/New_York", "toronto": "America/Toronto",
    "los angeles": "America/Los_Angeles", "houston": "America/Chicago",
    "dallas": "America/Chicago", "denver": "America/Denver",
    "atlanta": "America/New_York", "seattle": "America/Los_Angeles",
    "austin": "America/Chicago",
}

PRICE_INTERVALS = [("+1m", 60), ("+5m", 300), ("+15m", 900),
                   ("+30m", 1800), ("+1h", 3600), ("+2h", 7200), ("+3h", 10800)]


def fetch_city_brackets(city, now_utc=None):
    """Fetch live Polymarket bracket markets for a city using Gamma API + parse_market."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    params = {
        "tag_slug": "weather",
        "active": "true",
        "closed": "false",
        "limit": 200,
        "order": "volume24hr",
        "ascending": "false",
    }
    data = fetch_with_retry(GAMMA_EVENTS_API, params=params, timeout=15)
    if not data or not isinstance(data, list):
        return []
    brackets = []
    for event in data:
        markets = event.get("markets", [])
        if not markets:
            continue
        for raw_market in markets:
            parsed = parse_market(raw_market, now_utc=now_utc)
            if parsed is None:
                continue
            if parsed.get("city") != city:
                continue
            if parsed.get("direction") != "exact":
                continue
            if parsed.get("temp_type") != "max":
                continue
            brackets.append({
                "threshold": parsed.get("threshold"),
                "threshold_high": parsed.get("threshold_high"),
                "token_id": parsed.get("token_id"),
                "yes_price": parsed.get("yes_price"),
                "market_slug": parsed.get("market_slug", ""),
            })
    logger.info("[LAG-STUDY] %s: fetched %d bracket markets", city, len(brackets))
    return brackets


def detect_lock_event(city, icao, local_tz, brackets,
                      lock_threshold=LAG_STUDY_LOCK_THRESHOLD,
                      lock_min_hour_local=LAG_STUDY_LOCK_MIN_HOUR_LOCAL):
    """Check if running max is locked for a city. Returns lock event dict or None."""
    tracker = RunningMaxTracker(icao=icao, local_tz=local_tz)
    tracker.refresh_from_metar()
    if tracker.running_max <= -900:
        return None

    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_hour = now_utc.astimezone(ZoneInfo(local_tz)).hour
    except Exception:
        local_hour = (now_utc.hour - 6) % 24

    if local_hour < lock_min_hour_local:
        return None

    p_lock = compute_lock_probability(float(local_hour), tracker.margin, city)
    if p_lock < lock_threshold:
        return None

    winner = determine_winning_bracket(tracker.running_max, brackets)
    if winner is None:
        return None

    quote = fetch_orderbook_quote(winner["token_id"])
    if not quote or quote.get("ask") is None:
        return None

    return {
        "city": city,
        "date": tracker.current_local_date,
        "icao": icao,
        "running_max_c": round(tracker.running_max, 2),
        "margin_c": tracker.margin,
        "local_hour": local_hour,
        "lock_probability": round(p_lock, 4),
        "winning_bracket_token": winner["token_id"],
        "price_at_lock": quote.get("ask"),
        "bid_at_lock": quote.get("bid", 0),
        "timestamp_utc": now_utc.isoformat(),
    }


def track_price_convergence(token_id, entry_price, quote_fn, intervals=PRICE_INTERVALS):
    """Track price convergence after a lock event.

    Calls quote_fn(token_id, elapsed_seconds) at each interval.
    In production, main() handles real time.sleep() between intervals.
    In tests, quote_fn is a mock that returns pre-set prices per elapsed time.
    """
    prices_after = {}
    for label, delay_s in intervals:
        q = quote_fn(token_id, delay_s)
        if q:
            prices_after[label] = {"bid": q.get("bid"), "ask": q.get("ask")}
        else:
            prices_after[label] = None

    prices_list = [entry_price] + [
        (p.get("bid", 0) if p else 0) for p in prices_after.values()
    ]
    time_to_050 = None
    time_to_090 = None
    for i, p in enumerate(prices_list):
        if p and p >= 0.50 and time_to_050 is None:
            time_to_050 = 0 if i == 0 else intervals[i-1][1]
        if p and p >= 0.90 and time_to_090 is None:
            time_to_090 = 0 if i == 0 else intervals[i-1][1]

    return {
        **prices_after,
        "time_to_050": time_to_050,
        "time_to_090": time_to_090,
    }


def _track_convergence_thread(lock_event, recorded, output_file):
    """Background thread: track price convergence after a lock event.

    Runs independently of the main poll loop so other cities are not blocked.
    Sleeps for real wall-clock intervals between price fetches, then writes
    the final result to the JSONL output file.
    """
    token_id = lock_event["winning_bracket_token"]
    entry_price = lock_event["price_at_lock"]
    city = lock_event["city"]

    convergence = {}
    for label, delay_s in PRICE_INTERVALS:
        time.sleep(delay_s)
        try:
            q = fetch_orderbook_quote(token_id)
            convergence[label] = {"bid": q.get("bid"), "ask": q.get("ask")} if q else None
            bid = q.get("bid", 0) if q else 0
            logger.info("[LAG-STUDY] %s %s: bid=%.3f", city, label, bid)
        except Exception as e:
            logger.warning("[LAG-STUDY] %s %s: fetch error: %s", city, label, e)
            convergence[label] = None

    # Compute time metrics
    prices_list = [entry_price] + [
        (p.get("bid", 0) if p else 0) for p in convergence.values()
    ]
    time_to_050 = None
    time_to_090 = None
    for i, p in enumerate(prices_list):
        if p and p >= 0.50 and time_to_050 is None:
            time_to_050 = 0 if i == 0 else PRICE_INTERVALS[i-1][1]
        if p and p >= 0.90 and time_to_090 is None:
            time_to_090 = 0 if i == 0 else PRICE_INTERVALS[i-1][1]

    result = {
        **lock_event,
        "prices_after_lock": convergence,
        "time_to_050": time_to_050,
        "time_to_090": time_to_090,
    }

    try:
        with open(output_file, "a") as f:
            f.write(json.dumps(result) + "\n")
    except Exception as e:
        logger.error("[LAG-STUDY] %s: failed to write result: %s", city, e)


def main():
    """Main lag study loop. Runs continuously, polling US cities.

    Lock detection runs at 60s cadence across ALL cities.
    Price convergence tracking runs in background threads (one per lock event),
    so a 3-hour convergence sleep for one city does NOT block detection for others.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger.info("[LAG-STUDY] Starting running-max lag study (US cities)")

    os.makedirs(os.path.dirname(LAG_STUDY_OUTPUT_FILE), exist_ok=True)
    recorded = set()  # (city, date) pairs with active or completed convergence tracking
    active_threads = []  # Track background convergence threads

    duration_hours = 168
    if "--duration-hours" in sys.argv:
        idx = sys.argv.index("--duration-hours")
        duration_hours = int(sys.argv[idx + 1])

    end_time = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

    while datetime.now(timezone.utc) < end_time:
        # Clean up finished threads
        active_threads = [t for t in active_threads if t.is_alive()]

        for city, tz in US_CITIES.items():
            city_info = TARGET_CITIES.get(city)
            if not city_info:
                continue
            icao = city_info.get("icao")
            if not icao:
                continue

            record_key = (city, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            if record_key in recorded:
                continue

            try:
                # Fetch REAL Polymarket brackets for this city
                brackets = fetch_city_brackets(city)
                if not brackets:
                    logger.debug("[LAG-STUDY] %s: no bracket markets found", city)
                    continue

                # Detect lock event
                lock_event = detect_lock_event(city, icao, tz, brackets)
                if not lock_event:
                    continue

                logger.info("[LAG-STUDY] Lock detected: %s max=%.1fC bracket=%s price=%.2f (%d active threads)",
                            city, lock_event["running_max_c"],
                            lock_event["winning_bracket_token"],
                            lock_event["price_at_lock"],
                            len(active_threads))

                # Mark as recorded so we don't re-trigger for this city+date
                recorded.add(record_key)

                # Spawn background thread for price convergence tracking
                # This does NOT block the main loop — other cities continue polling
                t = threading.Thread(
                    target=_track_convergence_thread,
                    args=(lock_event, recorded, LAG_STUDY_OUTPUT_FILE),
                    daemon=True,
                    name=f"convergence-{city}-{record_key[1]}",
                )
                t.start()
                active_threads.append(t)

            except Exception as e:
                logger.error("[LAG-STUDY] Error for %s: %s", city, e)

        time.sleep(LAG_STUDY_POLL_INTERVAL_SECONDS)

    # Wait for all convergence threads to finish before exiting
    logger.info("[LAG-STUDY] Duration complete, waiting for %d convergence threads...", len(active_threads))
    for t in active_threads:
        t.join(timeout=12000)  # Max 3.3h grace period for last thread

    logger.info("[LAG-STUDY] Completed after %d hours", duration_hours)


if __name__ == "__main__":
    main()

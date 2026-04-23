"""
Background 'Gudang Data Warmer' service to pre-populate the SQLite warehouse.
Ensures discovery cycles stay fast by having weather data ready in advance.
"""

import time
import threading
import logging
from datetime import datetime, timedelta, timezone
from market_discovery_internal.config import (
    TARGET_CITIES, WARMER_429_COOLDOWN_SECONDS,
    WARMER_POLITENESS_DELAY_SECONDS, WARMER_HISTORICAL_THROTTLE_SECONDS,
)
from market_discovery_internal.database_manager import db
from market_discovery_internal.forecasting import (
    _fetch_bulk_historical_weather, _fetch_bulk_forecasts
)

logger = logging.getLogger(__name__)

class GudangDataWarmer:
    def __init__(self, check_interval_hours=2):
        self.check_interval_hours = check_interval_hours
        self._stop_event = threading.Event()
        self._last_429_time = 0
        self._last_429_lock = threading.Lock()

    @property
    def is_running(self):
        return not self._stop_event.is_set()

    def start(self):
        """Start the background warming loop."""
        self._stop_event.clear()
        logger.info(f"GudangDataWarmer started. Interval: {self.check_interval_hours}h")
        
        while not self._stop_event.is_set():
            try:
                # [MODUL L] Process Watchdog Update
                db.update_heartbeat("warmer")
                
                # Cooldown check for 429s (silent period if tripped)
                with self._last_429_lock:
                    in_cooldown = (time.time() - self._last_429_time) < WARMER_429_COOLDOWN_SECONDS
                if in_cooldown:
                    if self._stop_event.wait(60):
                        break
                    continue

                self.warm_all_cities()
                
                logger.debug(f"Warming cycle complete. Sleeping for {self.check_interval_hours}h...")
                if self._stop_event.wait(self.check_interval_hours * 3600):
                    break
            except Exception as e:
                logger.error(f"GudangDataWarmer loop error: {e}")
                if self._stop_event.wait(300):
                    break

    def warm_all_cities(self):
        """Warm both historical and forecast data for all target cities."""
        cities = list(TARGET_CITIES.keys())
        today = datetime.now(timezone.utc).date()
        
        # 1. Warm Forecasts (Today + next 3 days)
        for i in range(4):
            target_date = (today + timedelta(days=i)).isoformat()
            logger.info(f"[WARMER] Warming forecasts for {target_date}...")
            try:
                # [MODUL U] Bulk Fetch: Collapse N requests into 1
                res = _fetch_bulk_forecasts(cities, target_date)
                # [FIX-S8] Validate fetched temperatures before caching.
                # Reject extreme/garbage values that could poison probability calculations.
                if isinstance(res, dict):
                    for _city, _data in list(res.items()):
                        if isinstance(_data, dict):
                            _temp = _data.get("forecast_temp")
                            if _temp is not None and (float(_temp) < -60 or float(_temp) > 60):
                                logger.warning("[WARMER] Rejected extreme temp %.1f°C for %s on %s",
                                               float(_temp), _city, target_date)
                                res.pop(_city, None)
                # Polite Sleep to avoid 429 during bulk burst
                time.sleep(WARMER_POLITENESS_DELAY_SECONDS)
            except Exception as e:
                if "429" in str(e):
                    self._last_429_time = time.time()
                    logger.warning("[WARMER] 429 Tripped during Forecast bulk. Cooldown active.")
                    return
                logger.error(f"[WARMER] Forecast warm error for {target_date}: {e}")

        # 2. Warm Historical Averages (Today + next 14 days)
        for i in range(15):
            target_date = (today + timedelta(days=i)).isoformat()
            month_day = target_date[5:]
            
            cities_needed = []
            for city in cities:
                if not db.get_weather(city, month_day):
                    cities_needed.append(city)
            
            if not cities_needed:
                continue
                
            logger.info(f"[WARMER] Warming historical ({len(cities_needed)} cities) for {month_day}...")
            try:
                _fetch_bulk_historical_weather(cities_needed, target_date)
                # [MODUL L] Aggressive Throttling for historical fetches
                time.sleep(WARMER_HISTORICAL_THROTTLE_SECONDS) 
            except Exception as e:
                if "429" in str(e):
                    self._last_429_time = time.time()
                    logger.warning("[WARMER] 429 Tripped during Historical bulk. Cooldown active.")
                    return
                logger.error(f"[WARMER] Historical warm error for {target_date}: {e}")

    def stop(self):
        """Gracefully stop the warmer."""
        self._stop_event.set()
        logger.info("GudangDataWarmer stopping...")

# Singleton instance for module-level access
warmer = GudangDataWarmer()

import time
import threading
from datetime import datetime, timedelta, timezone
from market_discovery_internal.config import TARGET_CITIES
from market_discovery_internal.database_manager import db
from market_discovery_internal.forecasting import fetch_forecast, _fetch_historical_average

class GudangDataWarmer:
    """
    Background service that pre-populates the SQLite Warehouse with 
    historical data and future forecasts to enable 'Instant Filtering'.
    """
    
    def __init__(self, interval_hours=2):
        self.interval_hours = interval_hours
        self.running = False
        self._thread = None

    def start(self):
        if not self.running:
            self.running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            print("[WARMER] Gudang Data Warmer service started.")

    def _run_loop(self):
        while self.running:
            try:
                self.perform_warming_cycle()
            except Exception as e:
                print(f"[WARMER] Warming cycle error: {e}")
            
            print(f"[WARMER] Cycle complete. Sleeping for {self.interval_hours} hours.")
            time.sleep(self.interval_hours * 3600)

    def perform_warming_cycle(self):
        print(f"--- [WARMER] Warming Cycle Start: {datetime.now().strftime('%H:%M:%S')} ---")
        # [MODUL L] Update Heartbeat Pulse
        db.update_heartbeat("warmer")
        
        # We warm the next 7 days for all cities
        dates = [(datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]
        cities = list(TARGET_CITIES.keys())
        
        total_tasks = len(cities) * len(dates)
        processed = 0

        for city in cities:
            for date in dates:
                try:
                    month_day = date[5:]
                    
                    # 1. Warm Historical (Priming for Wave 1 & 2)
                    hist = db.get_weather(city, month_day)
                    if not hist:
                        # _fetch_historical_average (allow_live=True) saves to DB
                        _fetch_historical_average(city, date, allow_live=True)
                        # Ultra-Stealth Throttle: Archive API is stricter than Forecast API
                        time.sleep(25.0) 

                    # 2. Warm Forecast (Discovery Cache)
                    # TTL 2 hours for warmer (more relaxed than discovery cycle)
                    cached = db.get_cached_forecast(city, date, ttl_seconds=7200)
                    if not cached:
                        # fetch_forecast already saves verified result to DB
                        fetch_forecast(city, date)
                        # Standard Throttle
                        time.sleep(5.0)

                    processed += 1
                    if processed % 10 == 0:
                        print(f"[WARMER] Progress: {processed}/{total_tasks} data points warmed.")
                except Exception as e:
                    if "429" in str(e):
                        print(f"[WARMER] Circuit Breaker Tripped (429 detected). Aborting cycle for safety: {e}")
                        return
                    print(f"[WARMER] Point-specific error ({city}/{date}): {e}")

        print(f"--- [WARMER] Warming Cycle Complete: {datetime.now().strftime('%H:%M:%S')} ---")

# Singleton instance
warmer = GudangDataWarmer()

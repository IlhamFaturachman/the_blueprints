import time
import threading
from datetime import datetime, timedelta, timezone
from market_discovery_internal.config import TARGET_CITIES
from market_discovery_internal.database_manager import db
from market_discovery_internal.forecasting import (
    _fetch_bulk_historical_weather, _fetch_bulk_forecasts
)

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
        
        # We warm the next 10 days for all cities (increased window for buffer)
        dates = [(datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(11)]
        cities = list(TARGET_CITIES.keys())
        
        for date in dates:
            # [MODUL L] Heartbeat Pulse per date (Granular monitoring)
            db.update_heartbeat("warmer")
            
            try:
                month_day = date[5:]
                
                # 1. Identify missing historical data for this date across all cities
                missing_hist_cities = []
                for city in cities:
                    if not db.get_weather(city, month_day):
                        missing_hist_cities.append(city)
                
                if missing_hist_cities:
                    print(f"[WARMER] Fetching bulk historical for {len(missing_hist_cities)} cities on {date}...")
                    _fetch_bulk_historical_weather(missing_hist_cities, date)
                    # Aggregation Muzzle: Only 1 request per date instead of 31.
                    time.sleep(30.0) 
                
                # 2. Identify missing forecasts for this date across all cities
                missing_forecast_cities = []
                for city in cities:
                    # TTL 2 hours for warmer
                    if not db.get_cached_forecast(city, date, ttl_seconds=7200):
                        missing_forecast_cities.append(city)
                
                if missing_forecast_cities:
                    print(f"[WARMER] Fetching bulk forecast for {len(missing_forecast_cities)} cities on {date}...")
                    _fetch_bulk_forecasts(missing_forecast_cities, date)
                    time.sleep(10.0)

            except Exception as e:
                if "429" in str(e):
                    print(f"[WARMER] Circuit Breaker Tripped (429 detected). Cooling down cycle: {e}")
                    time.sleep(300) # Wait 5 mins before next date
                    continue
                print(f"[WARMER] Date-specific error ({date}): {e}")

        print(f"--- [WARMER] Warming Cycle Complete: {datetime.now().strftime('%H:%M:%S')} ---")

# Singleton instance
warmer = GudangDataWarmer()
